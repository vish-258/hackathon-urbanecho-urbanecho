"""Read-only YAMNet inference on saved WAV audio; independent of noise alerts.

Only this worker-side module imports the optional ML runtime. Scores are
multilabel model outputs, not calibrated probabilities or sound-pressure levels.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import threading
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace

from app.audio import AudioValidationError, validate_wav
from app.classification_contract import (
    CATEGORIES, CLASS_MAP_SHA256, MAPPING_SHA256, MAPPING_VERSION, MODEL_SAMPLE_RATE,
    MODEL_SHA256, MODEL_VERSION, SCORE_THRESHOLD,
)

ASSETS = Path(__file__).parent / "assets" / "yamnet"
DEFAULT_MODEL_PATH = Path("/opt/urbanecho-models/yamnet.tflite")
WINDOW_SAMPLES = 15600  # 0.975 s waveform yields YAMNet's 0.96 s feature patch.
HOP_SAMPLES = 7680     # 0.48 s between feature patches.
_RUNNER_LOCK = threading.Lock()
_RUNNERS: dict[str, "_Runner"] = {}


class ClassificationError(RuntimeError):
    """Safe, bounded diagnostic suitable for a stored job failure."""


def _sha256(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


@lru_cache(maxsize=1)
def _assets():
    try:
        if _sha256(ASSETS / "yamnet_class_map.csv") != CLASS_MAP_SHA256:
            raise ClassificationError("YAMNet class map checksum mismatch")
        if _sha256(ASSETS / "category_mapping.v1.json") != MAPPING_SHA256:
            raise ClassificationError("YAMNet category mapping checksum mismatch")
        with (ASSETS / "yamnet_class_map.csv").open(newline="") as source:
            labels = list(csv.DictReader(source))
        mapping = json.loads((ASSETS / "category_mapping.v1.json").read_text())
        if (len(labels) != 521 or any(int(row["index"]) != index for index, row in enumerate(labels))
                or mapping["mapping_version"] != MAPPING_VERSION or mapping["threshold"] != SCORE_THRESHOLD
                or set(mapping["categories"]) != set(CATEGORIES)):
            raise ClassificationError("YAMNet category mapping is incompatible")
        groups = {category: tuple(row["index"] for row in mapping["categories"][category]) for category in CATEGORIES}
        indices = [index for group in groups.values() for index in group]
        if sorted(indices) != list(range(521)):
            raise ClassificationError("YAMNet category mapping is incomplete")
        for group in mapping["categories"].values():
            for row in group:
                official = labels[row["index"]]
                if row["mid"] != official["mid"] or row["label"] != official["display_name"]:
                    raise ClassificationError("YAMNet category labels do not match the class map")
        return labels, groups
    except ClassificationError:
        raise
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ClassificationError("YAMNet classification assets are unavailable or invalid") from error


def summarize_scores(scores) -> dict:
    """Deterministically map 521 averaged official scores to product categories."""
    if len(scores) != 521 or any(not math.isfinite(float(score)) or not 0 <= float(score) <= 1 for score in scores):
        raise ClassificationError("YAMNet returned invalid class scores")
    labels, groups = _assets()
    category_scores = {category: float(max(scores[index] for index in groups[category])) for category in CATEGORIES}
    candidates = [category for category in CATEGORIES if category != "other" and category_scores[category] >= SCORE_THRESHOLD]
    # A road-vehicle parent score must not hide a qualifying horn/siren child.
    if any(category in candidates for category in ("horn", "siren")):
        candidates = [category for category in candidates if category != "traffic"]
    primary = max(candidates, key=lambda category: category_scores[category]) if candidates else "other"
    reason = None if candidates else "no_supported_category_above_threshold"
    # Do not force a supported label when stronger evidence is outside our
    # product taxonomy; a generic engine, aircraft or alarm remains Other.
    if candidates and category_scores["other"] >= category_scores[primary]:
        primary, reason = "other", "unmapped_class_dominant"
    return {
        "primary_category": primary,
        "category_scores": category_scores,
        "top_labels": [{"class_index": index, "label": labels[index]["display_name"], "score": float(scores[index])}
                       for index in sorted(range(521), key=lambda index: (-float(scores[index]), index))[:5]],
        "confidence_status": "classified" if primary != "other" else "uncertain",
        "uncertainty_reason": reason,
    }


class _Runner:
    def __init__(self, model_path: Path):
        try:
            if _sha256(model_path) != MODEL_SHA256:
                raise ClassificationError("YAMNet model checksum mismatch")
        except OSError as error:
            raise ClassificationError("YAMNet model file is unavailable") from error
        _assets()
        try:
            import numpy as np
            from ai_edge_litert.interpreter import Interpreter
            from scipy.signal import resample_poly  # Validate preprocessing before job claims.
            self.np = np
            self.interpreter = Interpreter(model_path=str(model_path), num_threads=1)
            self.interpreter.allocate_tensors()
            inputs, outputs = self.interpreter.get_input_details(), self.interpreter.get_output_details()
            if (len(inputs) != 1 or list(inputs[0]["shape"]) != [WINDOW_SAMPLES]
                    or inputs[0]["dtype"] != np.float32 or len(outputs) != 1
                    or list(outputs[0]["shape"]) != [1, 521] or outputs[0]["dtype"] != np.float32):
                raise ClassificationError("YAMNet model tensor contract is incompatible")
            self.input_index, self.output_index = inputs[0]["index"], outputs[0]["index"]
        except ClassificationError:
            raise
        except Exception as error:
            raise ClassificationError("YAMNet inference runtime could not initialize") from error
        self.lock = threading.Lock()

    def scores(self, waveform):
        np = self.np
        starts = list(range(0, len(waveform) - WINDOW_SAMPLES + 1, HOP_SAMPLES))
        if not starts:
            raise ClassificationError("Recording is shorter than one YAMNet window")
        # Cover the last samples without inventing silence by zero-padding a tail.
        final_start = len(waveform) - WINDOW_SAMPLES
        if starts[-1] != final_start:
            starts.append(final_start)
        total = np.zeros(521, dtype=np.float64)
        with self.lock:
            try:
                for start in starts:
                    window = np.ascontiguousarray(waveform[start:start + WINDOW_SAMPLES], dtype=np.float32)
                    self.interpreter.set_tensor(self.input_index, window)
                    self.interpreter.invoke()
                    result = self.interpreter.get_tensor(self.output_index)[0]
                    if not np.isfinite(result).all() or np.any(result < 0) or np.any(result > 1):
                        raise ClassificationError("YAMNet returned invalid class scores")
                    total += result
            except ClassificationError:
                raise
            except Exception as error:
                raise ClassificationError("YAMNet could not classify this recording") from error
        return (total / len(starts)).tolist(), len(starts)


def _runner(model_path: Path | str | None = None):
    path = Path(model_path) if model_path is not None else DEFAULT_MODEL_PATH
    key = str(path.resolve())
    with _RUNNER_LOCK:
        if key not in _RUNNERS:
            # A process normally has one configured model; do not retain arbitrary
            # model paths if an administrative test intentionally changes it.
            _RUNNERS.clear()
            _RUNNERS[key] = _Runner(path)
        return _RUNNERS[key]


def prepare_model(*, model_path: Path | str | None = None) -> None:
    """Load and verify the cached model before claiming any recording jobs."""
    _runner(model_path)


def _waveform(path: Path):
    try:
        import numpy as np
        from scipy.signal import resample_poly
    except ImportError as error:
        raise ClassificationError("YAMNet audio preprocessing runtime is unavailable") from error
    # Hard bounds also cover deployments configured above the normal 60 s limit.
    limits = SimpleNamespace(max_upload_bytes=100_000_000, max_duration_seconds=600,
                             allowed_sample_rates=(16000, 32000, 44100, 48000))
    try:
        info = validate_wav(path, limits, allow_pcm16=True)
        with path.open("rb") as source:
            source.seek(info.data_offset)
            raw = source.read(info.data_bytes)
        if len(raw) != info.data_bytes:
            raise ClassificationError("Recording audio is truncated")
        if info.sample_width == 2:
            samples = np.frombuffer(raw, dtype="<i2").astype(np.int32)
        else:
            packed = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3)
            samples = packed[:, 2].astype(np.int32)
            np.left_shift(samples, 8, out=samples)
            np.bitwise_or(samples, packed[:, 1], out=samples)
            np.left_shift(samples, 8, out=samples)
            np.bitwise_or(samples, packed[:, 0], out=samples)
            np.bitwise_xor(samples, 0x800000, out=samples)
            np.subtract(samples, 0x800000, out=samples)
            del packed
        full_scale = 1 << (info.sample_width * 8 - 1)
        clipped = int(np.count_nonzero((samples <= -full_scale) | (samples >= full_scale - 1)))
        waveform = samples.astype(np.float32)
        waveform /= np.float32(full_scale)
        del samples, raw
        # Check variation without allocating a float64 copy of a long clip.
        # This is only a quality check; the model still receives original DC.
        center = float(np.mean(waveform, dtype=np.float64))
        squared_deviation = 0.0
        for start in range(0, len(waveform), 65536):
            delta = np.subtract(waveform[start:start + 65536], center, dtype=np.float64)
            squared_deviation += float(np.dot(delta, delta))
        centered_rms = math.sqrt(squared_deviation / len(waveform))
        if info.sample_rate != MODEL_SAMPLE_RATE:
            divisor = math.gcd(info.sample_rate, MODEL_SAMPLE_RATE)
            waveform = resample_poly(waveform, MODEL_SAMPLE_RATE // divisor, info.sample_rate // divisor).astype(np.float32)
        # Resampling can overshoot slightly; bound model input only. Originals and
        # stored sound levels never use this in-memory resampled waveform.
        waveform = np.clip(waveform, -1.0, 1.0).astype(np.float32, copy=False)
        return waveform, info, centered_rms, clipped
    except ClassificationError:
        raise
    except (AudioValidationError, OSError, ValueError, OverflowError) as error:
        raise ClassificationError("Recording is not a supported intact mono PCM16 or PCM24 WAV") from error


def infer_recording(path: Path | str, *, model_path: Path | str | None = None) -> dict:
    """Classify audio without rewriting it or participating in threshold evaluation."""
    waveform, info, centered_rms, clipped = _waveform(Path(path))
    base = {
        "model_version": MODEL_VERSION, "mapping_version": MAPPING_VERSION,
        "model_sha256": MODEL_SHA256, "score_threshold": SCORE_THRESHOLD,
        "input_sample_rate": info.sample_rate, "model_sample_rate": MODEL_SAMPLE_RATE,
        "input_duration_seconds": info.duration_seconds,
        "analyzed_duration_seconds": 0.0, "window_count": 0,
        "score_aggregation": "mean_frame_scores_then_max_member_class",
        "warnings": ["clipped_input"] if clipped else [],
        "clipped_samples": clipped,
    }
    reason = "recording_too_short" if len(waveform) < WINDOW_SAMPLES else "silent_or_flat_input" if centered_rms <= 1e-6 else None
    if reason:
        return {**base, "primary_category": "other", "category_scores": {category: 0.0 for category in CATEGORIES},
                "top_labels": [], "confidence_status": "no_usable_audio", "uncertainty_reason": reason}
    scores, frames = _runner(model_path).scores(waveform)
    result = summarize_scores(scores)
    if clipped:
        result.update(confidence_status="uncertain", uncertainty_reason="clipped_input")
    return {**base, **result, "analyzed_duration_seconds": info.duration_seconds, "window_count": frames}


classify_audio = infer_recording

"""Bounded, read-only inference over the actual recorded span of an incident.

The caller supplies ordered, non-overlapping source-frame slices, excluding
playback context, and sets break_before at every missing interval. Contiguous
slices are joined before windowing; there is no inference across a gap. At
non-16-kHz rates each complete source window is polyphase-resampled in memory.
No zero-filled model windows, original edits, or per-record label voting occur.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace

from app.audio import AudioValidationError, validate_wav
from app.classification import ClassificationError, HOP_SAMPLES, WINDOW_SAMPLES, _runner, summarize_scores
from app.classification_contract import (
    CATEGORIES, MAPPING_VERSION, MODEL_SAMPLE_RATE, MODEL_SHA256, MODEL_VERSION, SCORE_THRESHOLD,
)

MAX_SEGMENTS = 10000
READ_FRAMES = 8192
_LIMITS = SimpleNamespace(max_upload_bytes=100_000_000, max_duration_seconds=600,
                          allowed_sample_rates=(16000, 32000, 44100, 48000))


def _decode(raw, width, np):
    if width == 2:
        return np.frombuffer(raw, dtype='<i2').astype(np.int32)
    packed = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3)
    samples = packed[:, 2].astype(np.int32)
    np.left_shift(samples, 8, out=samples)
    np.bitwise_or(samples, packed[:, 1], out=samples)
    np.left_shift(samples, 8, out=samples)
    np.bitwise_or(samples, packed[:, 0], out=samples)
    np.bitwise_xor(samples, 0x800000, out=samples)
    np.subtract(samples, 0x800000, out=samples)
    return samples


class _Run:
    """One continuous input timeline; storage stays below a window plus a block."""

    def __init__(self, rate, width, accumulator):
        self.rate, self.width, self.accumulator = rate, width, accumulator
        self.np = accumulator.np
        self.window_frames = math.ceil(WINDOW_SAMPLES * rate / MODEL_SAMPLE_RATE)
        self.hop_frames = HOP_SAMPLES * rate // MODEL_SAMPLE_RATE
        self.buffer = self.np.empty(0, dtype=self.np.int32)
        self.buffer_start = self.total_frames = self.next_start = 0
        self.last_window_start = None
        self.covered_until = self.covered_frames = 0

    def append(self, samples):
        self.buffer = self.np.concatenate((self.buffer, samples))
        self.total_frames += len(samples)
        while self.next_start + self.window_frames <= self.total_frames:
            self.classify_window(self.next_start)
            self.next_start += self.hop_frames
        keep_from = max(self.buffer_start, self.total_frames - self.window_frames)
        if keep_from > self.buffer_start:
            self.buffer = self.buffer[keep_from - self.buffer_start:].copy()
            self.buffer_start = keep_from

    def classify_window(self, start):
        np = self.np
        self.last_window_start = start
        offset = start - self.buffer_start
        native = self.buffer[offset:offset + self.window_frames]
        if len(native) != self.window_frames:
            raise ClassificationError('Incident window is incomplete')
        full_scale = 1 << (self.width * 8 - 1)
        if float(np.std(native, dtype=np.float64)) / full_scale <= 1e-6:
            self.accumulator.silent_windows += 1
            return
        waveform = native.astype(np.float32) / np.float32(full_scale)
        if self.rate != MODEL_SAMPLE_RATE:
            divisor = math.gcd(self.rate, MODEL_SAMPLE_RATE)
            waveform = self.accumulator.resample_poly(
                waveform, MODEL_SAMPLE_RATE // divisor, self.rate // divisor)
        # At 44.1 kHz the native 975-ms boundary lies between two samples.
        # Round up the source span, then take exactly one full model window.
        if len(waveform) < WINDOW_SAMPLES:
            raise ClassificationError('Incident model window is incomplete')
        waveform = np.clip(waveform[:WINDOW_SAMPLES], -1.0, 1.0).astype(np.float32, copy=False)
        values, count = self.accumulator.runner().scores(waveform)
        if count != 1 or len(values) != 521:
            raise ClassificationError('Incident model returned an invalid window result')
        values = np.asarray(values, dtype=np.float64)
        if not np.isfinite(values).all() or np.any(values < 0) or np.any(values > 1):
            raise ClassificationError('YAMNet returned invalid class scores')
        self.accumulator.scores += values
        self.accumulator.windows += 1
        end = start + self.window_frames
        self.covered_frames += max(0, end - max(start, self.covered_until))
        self.covered_until = end

    def finish(self):
        if self.total_frames < self.window_frames:
            self.accumulator.short_fragments += 1
            self.accumulator.short_duration += self.total_frames / self.rate
        else:
            final_start = self.total_frames - self.window_frames
            if self.last_window_start != final_start:
                self.classify_window(final_start)
        self.accumulator.analyzed_duration += self.covered_frames / self.rate


class _Accumulator:
    def __init__(self, np, resample_poly, model_path):
        self.np, self.resample_poly, self.model_path = np, resample_poly, model_path
        self.scores = np.zeros(521, dtype=np.float64)
        self.windows = self.silent_windows = self.short_fragments = 0
        self.short_duration = self.analyzed_duration = 0.0
        self._model = None

    def runner(self):
        if self._model is None:
            self._model = _runner(self.model_path)
        return self._model


def infer_segments(segments, *, model_path=None) -> dict:
    """Infer ordered half-open source-frame segments, without reading context.

    Each item is {path, start_frame, end_frame, break_before}. All files must
    have the same supported PCM format. The caller establishes temporal order
    and gap boundaries from persisted capture times; WAV files alone cannot.
    Invalid input fails explicitly, never silently drops part of an incident.
    """
    try:
        import numpy as np
        from scipy.signal import resample_poly
    except ImportError as error:
        raise ClassificationError('YAMNet audio preprocessing runtime is unavailable') from error
    if not isinstance(segments, (list, tuple)) or len(segments) > MAX_SEGMENTS:
        raise ClassificationError('Incident audio requires at most 10000 ordered segments')
    state = _Accumulator(np, resample_poly, model_path)
    run = None
    audio_format = None
    input_duration = 0.0
    clipped = runs = 0
    try:
        for segment in segments:
            if not isinstance(segment, Mapping) or type(segment.get('break_before')) is not bool:
                raise ClassificationError('Incident audio segment metadata is invalid')
            start, end = segment.get('start_frame'), segment.get('end_frame')
            if type(start) is not int or type(end) is not int or not 0 <= start < end:
                raise ClassificationError('Incident audio frame range is invalid')
            path = Path(segment['path'])
            info = validate_wav(path, _LIMITS, allow_pcm16=True)
            if end > info.frame_count:
                raise ClassificationError('Incident audio frame range exceeds its recording')
            current_format = (info.sample_rate, info.sample_width)
            if audio_format is not None and audio_format != current_format:
                raise ClassificationError('Incident recordings have incompatible PCM formats')
            audio_format = current_format
            if run is None or segment['break_before']:
                if run is not None:
                    run.finish()
                run = _Run(*audio_format, state)
                runs += 1
            input_duration += (end - start) / info.sample_rate
            full_scale = 1 << (info.sample_width * 8 - 1)
            with path.open('rb') as source:
                source.seek(info.data_offset + start * info.sample_width)
                remaining = end - start
                while remaining:
                    frames = min(remaining, READ_FRAMES)
                    raw = source.read(frames * info.sample_width)
                    if len(raw) != frames * info.sample_width:
                        raise ClassificationError('Incident recording audio is truncated')
                    samples = _decode(raw, info.sample_width, np)
                    clipped += int(np.count_nonzero((samples <= -full_scale) | (samples >= full_scale - 1)))
                    run.append(samples)
                    remaining -= frames
        if run is not None:
            run.finish()
    except ClassificationError:
        raise
    except (AudioValidationError, OSError, ValueError, TypeError, KeyError, OverflowError) as error:
        raise ClassificationError('Incident audio is not an intact supported mono PCM WAV segment') from error
    base = {
        'model_version': MODEL_VERSION, 'mapping_version': MAPPING_VERSION,
        'model_sha256': MODEL_SHA256, 'score_threshold': SCORE_THRESHOLD,
        'input_sample_rate': audio_format[0] if audio_format else 0, 'model_sample_rate': MODEL_SAMPLE_RATE,
        'input_duration_seconds': input_duration,
        'analyzed_duration_seconds': state.analyzed_duration, 'window_count': state.windows,
        'score_aggregation': 'mean_frame_scores_then_max_member_class',
        'warnings': ['clipped_input'] if clipped else [], 'clipped_samples': clipped,
        'segment_count': len(segments), 'contiguous_run_count': runs,
        'short_fragment_count': state.short_fragments, 'short_fragment_duration_seconds': state.short_duration,
        'silent_window_count': state.silent_windows,
        'excluded_duration_seconds': max(0.0, input_duration - state.analyzed_duration),
    }
    if not state.windows:
        reason = ('no_recorded_audio' if not segments else 'incident_fragments_too_short'
                  if not state.silent_windows else 'silent_or_flat_input')
        return {**base, 'primary_category': 'other', 'category_scores': {key: 0.0 for key in CATEGORIES},
                'top_labels': [], 'confidence_status': 'no_usable_audio', 'uncertainty_reason': reason}
    result = summarize_scores((state.scores / state.windows).tolist())
    if clipped:
        result.update(confidence_status='uncertain', uncertainty_reason='clipped_input')
    return {**base, **result}

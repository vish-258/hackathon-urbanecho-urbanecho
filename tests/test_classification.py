"""Classifier tests are file-only: no microphone, credentials or database."""
from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path
import struct
import threading
import wave

import pytest

from app import classification as classifier
from app.classification_contract import CATEGORIES, MAPPING_VERSION, MODEL_SHA256, MODEL_VERSION


def write_wav(path, samples, *, rate=16000, width=2, channels=1):
    with wave.open(str(path), 'wb') as output:
        output.setnchannels(channels)
        output.setsampwidth(width)
        output.setframerate(rate)
        output.writeframes(b''.join(int(sample).to_bytes(width, 'little', signed=width != 1) for sample in samples))
    # Python's wave writer omits the RIFF padding byte for odd PCM24 payloads.
    if path.stat().st_size % 2:
        data = bytearray(path.read_bytes()) + b'\0'
        struct.pack_into('<I', data, 4, len(data) - 8)
        path.write_bytes(data)
    return path


def sine(path, *, rate=16000, width=2, seconds=1, frequency=440):
    scale = (1 << (width * 8 - 1)) * 0.2
    return write_wav(path, (int(scale * math.sin(2 * math.pi * frequency * i / rate))
                            for i in range(round(rate * seconds))), rate=rate, width=width)


@pytest.fixture
def preprocessing():
    np = pytest.importorskip('numpy')
    pytest.importorskip('scipy')
    return np


def scores(**labels):
    official, _ = classifier._assets()
    values = [0.0] * 521
    for label, score in labels.items():
        index = next(i for i, row in enumerate(official) if row['display_name'] == label)
        values[index] = score
    return values


@pytest.mark.parametrize(('category', 'label'), [
    ('traffic', 'Traffic noise, roadway noise'), ('horn', 'Vehicle horn, car horn, honking'),
    ('siren', 'Siren'), ('construction', 'Jackhammer'), ('music', 'Music'),
    ('animal', 'Cat'), ('voice', 'Speech'), ('other', 'Engine'),
])
def test_explicit_taxonomy_for_all_categories(category, label):
    result = classifier.summarize_scores(scores(**{label: .75}))
    assert result['primary_category'] == category
    assert result['category_scores'][category] == .75
    assert result['top_labels'][0]['label'] == label
    assert result['confidence_status'] == ('uncertain' if category == 'other' else 'classified')


@pytest.mark.parametrize('specific', ['Vehicle horn, car horn, honking', 'Siren'])
def test_horn_and_siren_are_not_hidden_by_road_parent(specific):
    result = classifier.summarize_scores(scores(**{'Traffic noise, roadway noise': .95, specific: .3}))
    assert result['primary_category'] == ('siren' if specific == 'Siren' else 'horn')
    # A competing supported sound still wins by evidence, so horn is not a
    # blanket priority over speech/music/etc. in mixed audio.
    result = classifier.summarize_scores(scores(**{'Traffic noise, roadway noise': .95, specific: .3, 'Speech': .8}))
    assert result['primary_category'] == 'voice'


@pytest.mark.parametrize('other_score', [.8, .3])
def test_unmapped_dominance_or_tie_is_other_uncertain(other_score):
    result = classifier.summarize_scores(scores(**{'Engine': other_score, 'Music': .3}))
    assert result['primary_category'] == 'other'
    assert result['confidence_status'] == 'uncertain'
    assert result['uncertainty_reason'] == 'unmapped_class_dominant'


def test_threshold_is_explicit_and_scores_are_not_normalized_probabilities():
    result = classifier.summarize_scores(scores(**{'Music': .249, 'Speech': .24}))
    assert result['primary_category'] == 'other'
    assert result['uncertainty_reason'] == 'no_supported_category_above_threshold'
    result = classifier.summarize_scores(scores(**{'Music': .25, 'Speech': .1}))
    assert result['primary_category'] == 'music'
    result = classifier.summarize_scores(scores(**{'Music': .8, 'Speech': .7}))
    assert sum(result['category_scores'].values()) == pytest.approx(1.5)


@pytest.mark.parametrize('bad_scores', [[0.] * 520, [float('nan')] * 521, [float('inf')] * 521,
                                       [-.01] * 521, [1.01] * 521])
def test_invalid_runtime_scores_rejected(bad_scores):
    with pytest.raises(classifier.ClassificationError, match='invalid class scores'):
        classifier.summarize_scores(bad_scores)


@pytest.mark.parametrize('rate', [16000, 32000, 44100, 48000])
@pytest.mark.parametrize('width', [2, 3])
def test_pcm16_and_pcm24_supported_rates_resample_without_editing(tmp_path, preprocessing, rate, width):
    np = preprocessing
    path = sine(tmp_path / 'original.wav', rate=rate, width=width)
    original = path.read_bytes()
    waveform, info, rms, clipped = classifier._waveform(path)
    assert info.sample_rate == rate and info.sample_width == width
    assert info.duration_seconds == 1
    assert waveform.dtype == np.float32 and waveform.shape == (16000,)
    assert rms == pytest.approx(.2 / math.sqrt(2), abs=.0001)
    assert float(np.max(waveform)) == pytest.approx(.2, abs=.001)
    assert float(np.min(waveform)) == pytest.approx(-.2, abs=.001)
    assert clipped == 0
    assert path.read_bytes() == original


@pytest.mark.parametrize('width', [2, 3])
def test_signed_packed_samples_and_rail_detection(tmp_path, preprocessing, width):
    scale = 1 << (width * 8 - 1)
    path = write_wav(tmp_path / 'rails.wav', [-scale, -1, 0, 1, scale - 1], width=width)
    waveform, _, _, clipped = classifier._waveform(path)
    assert waveform.tolist() == pytest.approx([-1, -1 / scale, 0, 1 / scale, (scale - 1) / scale])
    assert clipped == 2


def test_downsampling_filters_energy_above_new_nyquist(tmp_path, preprocessing):
    np = preprocessing
    high = classifier._waveform(sine(tmp_path / 'high.wav', rate=48000, frequency=12000))[0]
    low = classifier._waveform(sine(tmp_path / 'low.wav', rate=48000, frequency=1000))[0]
    assert np.std(high[100:-100]) < np.std(low[100:-100]) * .01


@pytest.mark.parametrize(('samples', 'reason'), [
    ([0] * 16000, 'silent_or_flat_input'), ([1234] * 16000, 'silent_or_flat_input'),
    ([100, -100] * 7700, 'recording_too_short'),
])
def test_no_usable_audio_does_not_invoke_model(tmp_path, preprocessing, monkeypatch, samples, reason):
    def unexpected(_):
        pytest.fail('unusable audio should not invoke YAMNet')
    monkeypatch.setattr(classifier, '_runner', unexpected)
    result = classifier.infer_recording(write_wav(tmp_path / 'unusable.wav', samples))
    assert result['confidence_status'] == 'no_usable_audio'
    assert result['uncertainty_reason'] == reason
    assert result['primary_category'] == 'other' and result['top_labels'] == []
    assert result['window_count'] == 0 and result['analyzed_duration_seconds'] == 0


def test_clipping_keeps_scores_but_marks_estimate_uncertain(tmp_path, preprocessing, monkeypatch):
    class Runner:
        def scores(self, waveform):
            return scores(**{'Speech': .8}), 2
    monkeypatch.setattr(classifier, '_runner', lambda _: Runner())
    samples = [100, -100] * 8000
    samples[123] = 32767
    path = write_wav(tmp_path / 'clipped.wav', samples)
    before = path.read_bytes()
    result = classifier.infer_recording(path)
    assert result['primary_category'] == 'voice'
    assert result['confidence_status'] == 'uncertain'
    assert result['uncertainty_reason'] == 'clipped_input'
    assert result['warnings'] == ['clipped_input'] and result['clipped_samples'] == 1
    assert result['model_version'] == MODEL_VERSION and result['mapping_version'] == MAPPING_VERSION
    assert result['model_sha256'] == MODEL_SHA256 and result['analyzed_duration_seconds'] == 1
    assert path.read_bytes() == before


@pytest.mark.parametrize('kind', ['truncated', 'oversized_header', 'stereo', 'unsupported_rate', 'pcm8', 'too_long'])
def test_malformed_or_unsupported_audio_fails_safely(tmp_path, preprocessing, kind):
    path = tmp_path / 'do-not-leak-private-path.wav'
    if kind == 'stereo':
        write_wav(path, [100, -100] * 16000, channels=2)
    elif kind == 'unsupported_rate':
        write_wav(path, [100, -100] * 8000, rate=22050)
    elif kind == 'pcm8':
        write_wav(path, [128] * 16000, width=1)
    elif kind == 'too_long':
        # A sparse, structurally valid file exercises the duration bound before
        # reading or allocating a large waveform.
        data_size = (600 * 16000 + 1) * 2
        header = struct.pack('<4sI4s4sIHHIIHH4sI', b'RIFF', data_size + 36, b'WAVE', b'fmt ',
                             16, 1, 1, 16000, 32000, 2, 16, b'data', data_size)
        with path.open('wb') as output:
            output.write(header)
            output.truncate(data_size + 44)
    else:
        sine(path)
        data = bytearray(path.read_bytes())
        if kind == 'truncated':
            data = data[:-1]
        else:
            struct.pack_into('<I', data, 40, 0xfffffff0)
        path.write_bytes(data)
    with pytest.raises(classifier.ClassificationError) as error:
        classifier.infer_recording(path)
    assert str(error.value) == 'Recording is not a supported intact mono PCM16 or PCM24 WAV'
    assert 'private-path' not in str(error.value)


@pytest.mark.parametrize(('length', 'expected_starts'), [
    (15600, [0]), (16000, [0, 400]), (23280, [0, 7680]), (24000, [0, 7680, 8400]),
])
def test_fixed_windows_cover_tail_without_padding(preprocessing, length, expected_starts):
    np = preprocessing
    runner = object.__new__(classifier._Runner)
    runner.np, runner.lock = np, threading.Lock()
    runner.input_index, runner.output_index = 4, 5
    starts = []
    class Interpreter:
        def set_tensor(self, index, samples):
            assert index == 4 and samples.dtype == np.float32 and samples.shape == (15600,)
            starts.append(int(samples[0]))
        def invoke(self):
            pass
        def get_tensor(self, index):
            assert index == 5
            return np.full((1, 521), len(starts) / 10, dtype=np.float32)
    runner.interpreter = Interpreter()
    averaged, count = runner.scores(np.arange(length, dtype=np.float32))
    assert starts == expected_starts and count == len(starts)
    assert averaged[0] == pytest.approx(sum(range(1, count + 1)) / 10 / count)


def test_missing_and_changed_model_files_fail_before_runtime_import(tmp_path):
    path = tmp_path / 'private-model-path.tflite'
    with pytest.raises(classifier.ClassificationError, match='^YAMNet model file is unavailable$'):
        classifier.prepare_model(model_path=path)
    path.write_bytes(b'not the pinned official model')
    with pytest.raises(classifier.ClassificationError, match='^YAMNet model checksum mismatch$'):
        classifier.prepare_model(model_path=path)


def test_actual_official_yamnet_inference_keeps_original_intact(tmp_path, preprocessing):
    model = os.environ.get('YAMNET_TEST_MODEL_PATH') or os.environ.get('CLASSIFICATION_MODEL_PATH')
    if not model:
        if classifier.DEFAULT_MODEL_PATH.is_file():
            model = str(classifier.DEFAULT_MODEL_PATH)
        else:
            pytest.skip('Install the classifier model or set CLASSIFICATION_MODEL_PATH to exercise the pinned runtime.')
    pytest.importorskip('ai_edge_litert')
    path = sine(tmp_path / 'SYNTHETIC-sine-wave.wav')
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    classifier.prepare_model(model_path=model)
    cached_runner = classifier._runner(model)
    result = classifier.infer_recording(path, model_path=model)
    assert classifier._runner(model) is cached_runner
    assert result['top_labels'][0]['label'] == 'Sine wave'
    assert result['top_labels'][0]['score'] > .5
    assert result['primary_category'] == 'other' and result['confidence_status'] == 'uncertain'
    assert result['window_count'] == 2 and result['analyzed_duration_seconds'] == 1
    assert result['warnings'] == []
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before

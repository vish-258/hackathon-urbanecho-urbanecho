"""Incident inference joins actual audio, not saved per-record sound labels."""
from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path
import struct
import wave

import pytest

from app import classification
from app import incident_classification as incidents


@pytest.fixture
def np():
    module = pytest.importorskip('numpy')
    pytest.importorskip('scipy')
    return module


def wav(path, samples, rate=16000, width=2):
    with wave.open(str(path), 'wb') as output:
        output.setnchannels(1)
        output.setsampwidth(width)
        output.setframerate(rate)
        output.writeframes(b''.join(int(value).to_bytes(width, 'little', signed=True) for value in samples))
    if path.stat().st_size % 2:
        raw = bytearray(path.read_bytes()) + b'\0'
        struct.pack_into('<I', raw, 4, len(raw) - 8)
        path.write_bytes(raw)
    return path


def sine(seconds=1, rate=16000, width=2):
    scale = .2 * (1 << (width * 8 - 1))
    return [int(scale * math.sin(2 * math.pi * 440 * frame / rate)) for frame in range(round(seconds * rate))]


def segment(path, count, *, start=0, gap=False):
    return {'path': path, 'start_frame': start, 'end_frame': start + count, 'break_before': gap}


class FakeRunner:
    def __init__(self, retain=True):
        self.windows = []
        self.count = 0
        self.retain = retain

    def scores(self, waveform):
        assert waveform.shape == (15600,)
        self.count += 1
        if self.retain:
            self.windows.append(waveform.copy())
        scores = [0.0] * 521
        scores[0] = .8  # Speech
        return scores, 1


@pytest.fixture
def runner(monkeypatch, np):
    fake = FakeRunner()
    monkeypatch.setattr(incidents, '_runner', lambda _: fake)
    return fake


def test_contiguous_upload_boundaries_have_identical_windows_to_whole_recording(tmp_path, runner, np):
    samples = sine(4)
    # Nonstationary second/third seconds ensure a boundary mistake is observable.
    samples[16000:32000] = [value // 2 for value in samples[16000:32000]]
    samples[32000:48000] = [-value for value in samples[32000:48000]]
    whole = wav(tmp_path / 'whole.wav', samples)
    parts = [wav(tmp_path / f'part-{i}.wav', samples[i * 16000:(i + 1) * 16000]) for i in range(4)]
    before = [hashlib.sha256(path.read_bytes()).hexdigest() for path in [whole, *parts]]
    whole_result = incidents.infer_segments([segment(whole, len(samples))])
    whole_windows = runner.windows[:]
    runner.windows.clear()
    split_result = incidents.infer_segments([segment(path, 16000) for path in parts])
    assert whole_result['window_count'] == split_result['window_count'] == 8
    assert split_result['analyzed_duration_seconds'] == split_result['input_duration_seconds'] == 4
    assert split_result['contiguous_run_count'] == 1 and split_result['segment_count'] == 4
    for actual, expected in zip(runner.windows, whole_windows, strict=True):
        np.testing.assert_array_equal(actual, expected)
    # The window starting at 0.48 s contains samples from two uploads.
    np.testing.assert_allclose(runner.windows[1], np.array(samples[7680:23280], dtype=np.float32) / 32768)
    assert before == [hashlib.sha256(path.read_bytes()).hexdigest() for path in [whole, *parts]]


def test_only_supplied_incident_frames_are_read_not_playback_context(tmp_path, runner, np):
    core = sine()
    path = wav(tmp_path / 'with-context.wav', [32767] * 16000 + core + [-32768] * 16000)
    result = incidents.infer_segments([segment(path, 16000, start=16000)])
    assert result['input_duration_seconds'] == result['analyzed_duration_seconds'] == 1
    assert result['clipped_samples'] == 0 and result['warnings'] == []
    np.testing.assert_array_equal(runner.windows[0], np.array(core[:15600], dtype=np.float32) / 32768)


def test_short_fragments_join_only_when_explicitly_contiguous(tmp_path, runner):
    path = wav(tmp_path / 'short.wav', sine(.6))
    result = incidents.infer_segments([segment(path, 9600), segment(path, 9600, gap=True)])
    assert result['confidence_status'] == 'no_usable_audio'
    assert result['uncertainty_reason'] == 'incident_fragments_too_short'
    assert result['short_fragment_count'] == 2
    assert result['input_duration_seconds'] == pytest.approx(1.2)
    assert result['short_fragment_duration_seconds'] == pytest.approx(1.2)
    assert result['analyzed_duration_seconds'] == 0 and runner.count == 0
    joined = incidents.infer_segments([segment(path, 9600), segment(path, 9600)])
    assert joined['confidence_status'] == 'classified' and joined['window_count'] == 2
    assert joined['analyzed_duration_seconds'] == pytest.approx(1.2)


def test_gaps_restart_window_grid_and_tail_is_emitted_per_run(tmp_path, runner):
    left = wav(tmp_path / 'left.wav', sine(1))
    right = wav(tmp_path / 'right.wav', [value // 3 for value in sine(1)])
    result = incidents.infer_segments([segment(left, 16000), segment(right, 16000, gap=True)])
    assert result['contiguous_run_count'] == 2 and result['window_count'] == 4
    assert result['analyzed_duration_seconds'] == 2
    assert max(abs(runner.windows[1])) > .19
    assert max(abs(runner.windows[2])) < .07


def test_average_weights_actual_windows_not_recordings_or_runs(tmp_path, monkeypatch, np):
    class Varying:
        count = 0
        def scores(self, waveform):
            self.count += 1
            values = [0.] * 521
            values[0] = .1 if self.count <= 6 else .9
            return values, 1
    model = Varying()
    monkeypatch.setattr(incidents, '_runner', lambda _: model)
    long = wav(tmp_path / 'long.wav', sine(3))
    short = wav(tmp_path / 'short.wav', sine(1))
    result = incidents.infer_segments([segment(long, 48000), segment(short, 16000, gap=True)])
    assert result['window_count'] == 8
    assert result['category_scores']['voice'] == pytest.approx(.3)
    assert result['analyzed_duration_seconds'] == 4


@pytest.mark.parametrize('value', [0, 1234, 32767])
def test_flat_or_silent_windows_never_produce_a_confident_label(tmp_path, runner, value):
    path = wav(tmp_path / 'flat.wav', [value] * 32000)
    result = incidents.infer_segments([segment(path, 32000)])
    assert result['confidence_status'] == 'no_usable_audio'
    assert result['uncertainty_reason'] == 'silent_or_flat_input'
    assert result['analyzed_duration_seconds'] == 0 and result['excluded_duration_seconds'] == 2
    assert result['silent_window_count'] > 0 and runner.count == 0
    assert result['clipped_samples'] == (32000 if value == 32767 else 0)


def test_clipping_is_counted_once_despite_overlapping_windows(tmp_path, runner):
    samples = sine(2)
    samples[15500] = 32767
    path = wav(tmp_path / 'clipped.wav', samples)
    result = incidents.infer_segments([segment(path, 16000), segment(path, 16000, start=16000)])
    assert result['primary_category'] == 'voice' and result['confidence_status'] == 'uncertain'
    assert result['uncertainty_reason'] == 'clipped_input'
    assert result['warnings'] == ['clipped_input'] and result['clipped_samples'] == 1


def test_partial_silent_region_coverage_is_union_of_windows_actually_analyzed(tmp_path, runner):
    path = wav(tmp_path / 'partially-silent.wav', [0] * 32000 + sine(1))
    result = incidents.infer_segments([segment(path, 48000)])
    assert 0 < result['analyzed_duration_seconds'] < result['input_duration_seconds'] == 3
    assert result['silent_window_count'] > 0
    assert result['analyzed_duration_seconds'] + result['excluded_duration_seconds'] == pytest.approx(3)


@pytest.mark.parametrize('rate', [16000, 32000, 44100, 48000])
@pytest.mark.parametrize('width', [2, 3])
def test_supported_formats_join_seamlessly_before_resampling(tmp_path, runner, np, rate, width):
    samples = sine(2, rate, width)
    whole = wav(tmp_path / 'whole.wav', samples, rate, width)
    a = wav(tmp_path / 'a.wav', samples[:rate], rate, width)
    b = wav(tmp_path / 'b.wav', samples[rate:], rate, width)
    incidents.infer_segments([segment(whole, rate * 2)])
    expected = runner.windows[:]
    runner.windows.clear()
    result = incidents.infer_segments([segment(a, rate), segment(b, rate)])
    assert result['input_sample_rate'] == rate and result['model_sample_rate'] == 16000
    assert result['input_duration_seconds'] == result['analyzed_duration_seconds'] == 2
    assert result['window_count'] == len(expected)
    for actual, wanted in zip(runner.windows, expected, strict=True):
        np.testing.assert_array_equal(actual, wanted)
        assert actual.dtype == np.float32


@pytest.mark.parametrize('change', ['rate', 'width'])
def test_mixed_format_is_rejected_instead_of_silent_conversion(tmp_path, runner, change):
    a = wav(tmp_path / 'a.wav', sine())
    rate, width = (32000, 2) if change == 'rate' else (16000, 3)
    b = wav(tmp_path / 'b.wav', sine(1, rate, width), rate, width)
    with pytest.raises(classification.ClassificationError, match='incompatible PCM formats'):
        incidents.infer_segments([segment(a, 16000), segment(b, rate, gap=True)])


@pytest.mark.parametrize('metadata', [
    {'start_frame': -1}, {'end_frame': 16001}, {'end_frame': 0},
    {'start_frame': True}, {'end_frame': 1.5}, {'break_before': 1}, {'path': None},
])
def test_invalid_source_ranges_fail_explicitly(tmp_path, runner, metadata):
    path = wav(tmp_path / 'original.wav', sine())
    item = segment(path, 16000)
    item.update(metadata)
    with pytest.raises(classification.ClassificationError):
        incidents.infer_segments([item])


def test_missing_or_malformed_files_do_not_leak_paths(tmp_path, runner):
    path = tmp_path / 'private-device-file.wav'
    for malformed in (False, True):
        if malformed:
            path.write_bytes(b'not WAV')
        with pytest.raises(classification.ClassificationError) as error:
            incidents.infer_segments([segment(path, 16000)])
        assert 'private-device-file' not in str(error.value)


def test_empty_manifest_has_an_explicit_no_data_result(runner):
    result = incidents.infer_segments([])
    assert result['uncertainty_reason'] == 'no_recorded_audio'
    assert result['confidence_status'] == 'no_usable_audio'
    assert result['segment_count'] == result['window_count'] == 0
    assert result['input_duration_seconds'] == result['analyzed_duration_seconds'] == 0


def test_segment_limit_fails_instead_of_truncating(runner):
    with pytest.raises(classification.ClassificationError, match='at most 10000'):
        incidents.infer_segments([{}] * 10001)


def test_long_run_keeps_decode_and_window_storage_bounded(tmp_path, monkeypatch, np):
    model = FakeRunner(retain=False)
    monkeypatch.setattr(incidents, '_runner', lambda _: model)
    original_append = incidents._Run.append
    max_buffer = 0
    def checked_append(self, samples):
        nonlocal max_buffer
        assert len(samples) <= incidents.READ_FRAMES
        original_append(self, samples)
        max_buffer = max(max_buffer, len(self.buffer))
        assert len(self.buffer) <= self.window_frames
    monkeypatch.setattr(incidents._Run, 'append', checked_append)
    path = wav(tmp_path / 'repeated-source.wav', sine())
    result = incidents.infer_segments([segment(path, 16000) for _ in range(240)])
    assert result['input_duration_seconds'] == result['analyzed_duration_seconds'] == 240
    assert result['window_count'] > 490 and max_buffer <= 15600


def test_actual_model_across_upload_boundary_matches_whole_run(tmp_path, np):
    model = os.environ.get('YAMNET_TEST_MODEL_PATH') or os.environ.get('CLASSIFICATION_MODEL_PATH')
    if not model:
        if classification.DEFAULT_MODEL_PATH.is_file():
            model = str(classification.DEFAULT_MODEL_PATH)
        else:
            pytest.skip('Install the model or set CLASSIFICATION_MODEL_PATH for real inference.')
    pytest.importorskip('ai_edge_litert')
    samples = sine(2)
    whole = wav(tmp_path / 'SYNTHETIC-whole.wav', samples)
    a = wav(tmp_path / 'SYNTHETIC-a.wav', samples[:16000])
    b = wav(tmp_path / 'SYNTHETIC-b.wav', samples[16000:])
    before = [hashlib.sha256(path.read_bytes()).hexdigest() for path in (whole, a, b)]
    reference = incidents.infer_segments([segment(whole, 32000)], model_path=model)
    result = incidents.infer_segments([segment(a, 16000), segment(b, 16000)], model_path=model)
    assert result['top_labels'][0]['label'] == 'Sine wave'
    assert result['top_labels'] == reference['top_labels']
    assert result['category_scores'] == reference['category_scores']
    assert result['window_count'] == 4 and result['analyzed_duration_seconds'] == 2
    assert before == [hashlib.sha256(path.read_bytes()).hexdigest() for path in (whole, a, b)]

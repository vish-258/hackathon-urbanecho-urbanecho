import hashlib
import io

import pytest

from scripts import fetch_yamnet


def test_download_verifies_checksum_and_reuses_verified_model(tmp_path, monkeypatch):
    model = b'synthetic-model-fixture'
    monkeypatch.setattr(fetch_yamnet, 'MODEL_SHA256', hashlib.sha256(model).hexdigest())
    calls = []
    def download(url, timeout):
        calls.append(url)
        return io.BytesIO(model)
    monkeypatch.setattr(fetch_yamnet.urllib.request, 'urlopen', download)
    destination = tmp_path / 'models' / 'yamnet.tflite'
    fetch_yamnet.fetch_model(destination)
    fetch_yamnet.fetch_model(destination)
    assert destination.read_bytes() == model
    assert calls == [fetch_yamnet.MODEL_URL]


def test_bad_download_is_not_installed_or_left_behind(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch_yamnet.urllib.request, 'urlopen', lambda *args, **kwargs: io.BytesIO(b'wrong'))
    with pytest.raises(ValueError, match='pinned checksum'):
        fetch_yamnet.fetch_model(tmp_path / 'yamnet.tflite')
    assert list(tmp_path.iterdir()) == []


def test_existing_wrong_model_is_preserved_and_rejected(tmp_path):
    destination = tmp_path / 'yamnet.tflite'
    destination.write_bytes(b'old-or-corrupt')
    with pytest.raises(ValueError, match='refusing to overwrite'):
        fetch_yamnet.fetch_model(destination)
    assert destination.read_bytes() == b'old-or-corrupt'


def test_oversized_download_is_removed(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch_yamnet, 'MAX_MODEL_BYTES', 4)
    monkeypatch.setattr(fetch_yamnet.urllib.request, 'urlopen', lambda *args, **kwargs: io.BytesIO(b'too-large'))
    with pytest.raises(ValueError, match='size bound'):
        fetch_yamnet.fetch_model(tmp_path / 'yamnet.tflite')
    assert list(tmp_path.iterdir()) == []

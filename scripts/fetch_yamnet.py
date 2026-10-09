"""Fetch the official pinned YAMNet model at image-build/setup time, never audio."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import tempfile
import urllib.request

from app.classification_contract import MODEL_SHA256, MODEL_URL

MAX_MODEL_BYTES = 20_000_000


def fetch_model(output: Path) -> Path:
    if output.exists():
        if hashlib.sha256(output.read_bytes()).hexdigest() == MODEL_SHA256:
            return output
        raise ValueError('Existing model checksum differs; refusing to overwrite it')
    output.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with urllib.request.urlopen(MODEL_URL, timeout=90) as source:
            with tempfile.NamedTemporaryFile(dir=output.parent, delete=False) as target:
                temp_path = Path(target.name)
                digest = hashlib.sha256()
                length = 0
                while block := source.read(1024 * 1024):
                    length += len(block)
                    if length > MAX_MODEL_BYTES:
                        raise ValueError('Model download exceeded its size bound')
                    digest.update(block)
                    target.write(block)
        if digest.hexdigest() != MODEL_SHA256:
            raise ValueError('Downloaded model did not match the pinned checksum')
        temp_path.chmod(0o644)
        temp_path.replace(output)
        return output
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('/opt/urbanecho-models/yamnet.tflite'))
    args = parser.parse_args()
    fetch_model(args.output)
    print('Official YAMNet model downloaded and checksum verified.')


if __name__ == '__main__':
    main()

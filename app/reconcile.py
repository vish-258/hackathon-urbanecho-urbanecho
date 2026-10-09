"""Inspect or remove uncommitted files while excluding concurrent uploads.

Run ``python -m app.reconcile`` to inspect, then add ``--delete-orphans`` to
remove files which have no audio_chunks reference. Committed audio is never
deleted. Database and audio backups must be restored from a consistent point.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal, wait_for_database
from app.models import AudioChunk
from app.storage import lock_storage, remove_audio, resolve_audio_path


def reconcile(settings: Any | None = None, *, delete_orphans: bool = False) -> dict:
    settings = settings or get_settings()
    root = Path(settings.audio_root)
    with SessionLocal() as session, session.begin():
        lock_storage(session, exclusive=True)
        references = set(session.scalars(select(AudioChunk.file_path)).all())
        candidates = set()
        for directory, pattern in ((root / ".staging", r"\.staging/[0-9a-f]{32}\.part"),
                                   (root / "originals", r"originals/[0-9a-f]{2}/[0-9a-f]{32}\.wav")):
            if directory.exists():
                for path in directory.rglob("*"):
                    reference = path.relative_to(root).as_posix()
                    if path.is_file() and not path.is_symlink() and re.fullmatch(pattern, reference):
                        candidates.add(reference)
        orphans = sorted(candidates - references)
        missing = sorted(reference for reference in references if not resolve_audio_path(reference, settings).is_file())
        if delete_orphans:
            for reference in orphans:
                remove_audio(reference, settings)
        return {"orphan_files": orphans, "missing_committed_files": missing,
                "deleted_count": len(orphans) if delete_orphans else 0}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delete-orphans", action="store_true")
    arguments = parser.parse_args()
    wait_for_database()
    print(json.dumps(reconcile(delete_orphans=arguments.delete_orphans), indent=2))


if __name__ == "__main__":
    main()

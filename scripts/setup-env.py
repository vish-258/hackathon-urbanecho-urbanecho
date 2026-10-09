#!/usr/bin/env python3
"""Create local secrets without putting credentials in source or terminal output."""
import os
import secrets
from pathlib import Path

root = Path(__file__).resolve().parents[1]
path = root / ".env"
if path.exists():
    raise SystemExit(".env already exists; kept existing settings and credentials.")
text = (root / ".env.example").read_text()
for key in ("POSTGRES_PASSWORD", "APP_DB_PASSWORD", "ADMIN_TOKEN"):
    lines = text.splitlines()
    matches = [i for i, line in enumerate(lines) if line.startswith(key + "=")]
    if not matches:
        raise SystemExit(f"Missing {key} in .env.example")
    lines[matches[0]] = f"{key}={secrets.token_urlsafe(36)}"
    text = "\n".join(lines) + "\n"
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w") as handle:
    handle.write(text)
print("Created .env with random credentials (permissions 0600).")

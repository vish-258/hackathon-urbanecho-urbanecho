#!/usr/bin/env python3
"""Idempotently create three uncalibrated demo stations through the real API."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.request
import uuid

NAMESPACE = uuid.UUID('6b0b72c2-097f-4292-95c5-e5b36c14162f')
STATIONS = [
    ('Demo Central Park', 12.9716, 77.5946),
    ('Demo Library', 12.9750, 77.5990),
    ('Demo Riverside', 12.9840, 77.6070),
]


def request(base: str, token: str, path: str, payload: dict | None = None):
    req = urllib.request.Request(
        base.rstrip('/') + path,
        data=None if payload is None else json.dumps(payload).encode(),
        headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
        method='GET' if payload is None else 'POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f'{req.method} {path}: HTTP {exc.code}: {exc.read().decode()}') from exc


def save_private(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix('.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as handle:
        json.dump(value, handle, indent=2)
        handle.write('\n')
    temporary.replace(path)
    path.chmod(0o600)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default=os.environ.get('API_URL', 'http://localhost:8000'))
    parser.add_argument('--credentials', type=Path, default=Path('.local/demo-devices.json'))
    args = parser.parse_args()
    token = os.environ.get('ADMIN_TOKEN')
    if not token:
        parser.error('Set ADMIN_TOKEN to the administrator token from your .env file.')
    state = json.loads(args.credentials.read_text()) if args.credentials.exists() else {'devices': []}
    stored = {item['id']: item for item in state['devices']}
    locations = []
    offset = 0
    while True:
        page = request(args.url, token, f'/locations?limit=100&offset={offset}')
        locations.extend(page['items'])
        offset += len(page['items'])
        if offset >= page['total'] or not page['items']:
            break
    existing = {item['name']: item for item in locations}
    for name, latitude, longitude in STATIONS:
        location = existing.get(name) or request(args.url, token, '/locations', {
            'name': name, 'latitude': latitude, 'longitude': longitude,
            'timezone': 'Asia/Kolkata', 'threshold_value': 75.0,
            'threshold_type': 'spl_z_leq', 'interval_seconds': 1,
        })
        device_id = str(uuid.uuid5(NAMESPACE, name))
        try:
            device = request(args.url, token, f'/devices/{device_id}')
        except RuntimeError as exc:
            if 'HTTP 404:' not in str(exc):
                raise
            device = request(args.url, token, '/devices', {
                'id': device_id, 'location_id': location['id'],
                'microphone_model': 'INMP441', 'calibration': None,
            })
            stored[device_id] = {
                'id': device_id, 'name': name, 'location_id': location['id'],
                'token': device['token'],
            }
            save_private(args.credentials, {'devices': list(stored.values())})
        if device_id not in stored:
            raise RuntimeError(
                f'Demo device {device_id} already exists but its one-time token is absent '
                f'from {args.credentials}. Restore that credentials file; existing tokens '
                'cannot be recovered from their hashes.'
            )
        print(f'{name}: location={location["id"]}, device={device_id}')
    save_private(args.credentials, {'devices': list(stored.values())})
    print(f'Device tokens saved privately to {args.credentials}. All demo devices remain uncalibrated.')


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, urllib.error.URLError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)

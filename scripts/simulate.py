#!/usr/bin/env python3
"""Send synthetic signed 24-bit PCM WAV audio through the device upload API."""
from __future__ import annotations
import argparse
from datetime import datetime, timedelta, timezone
import io
import json
import math
from pathlib import Path
import time
import urllib.error
import urllib.request
import uuid
import wave


def pcm24_wav(sample_rate: int, duration: float, amplitude: float, frequency: float = 1000) -> bytes:
    samples = bytearray()
    for index in range(round(sample_rate * duration)):
        value = round((2**23 - 1) * amplitude * math.sin(2 * math.pi * frequency * index / sample_rate))
        samples.extend(value.to_bytes(3, 'little', signed=True))
    output = io.BytesIO()
    with wave.open(output, 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(3)
        wav.setframerate(sample_rate)
        wav.writeframes(samples)
    result = output.getvalue()
    if len(samples) % 2:
        result += b"\0"
        result = result[:4] + (len(result) - 8).to_bytes(4, "little") + result[8:]
    return result


def upload(base: str, device: dict, metadata: dict, audio: bytes):
    boundary = 'noise-' + uuid.uuid4().hex
    body = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="metadata"\r\n'
        'Content-Type: application/json\r\n\r\n'
    ).encode() + json.dumps(metadata).encode() + (
        f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="synthetic.wav"\r\n'
        'Content-Type: audio/wav\r\n\r\n'
    ).encode() + audio + f'\r\n--{boundary}--\r\n'.encode()
    req = urllib.request.Request(base.rstrip('/') + '/audio', data=body, method='POST', headers={
        'Authorization': f'Bearer {device["token"]}',
        'Content-Type': f'multipart/form-data; boundary={boundary}',
    })
    with urllib.request.urlopen(req, timeout=60) as response:
        return response.status, json.load(response)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://localhost:8000')
    parser.add_argument('--credentials', type=Path, default=Path('.local/demo-devices.json'))
    parser.add_argument('--device-index', type=int, default=0)
    parser.add_argument('--chunks', type=int, default=3)
    parser.add_argument('--sample-rate', type=int, choices=[16000, 32000, 44100, 48000], default=16000)
    parser.add_argument('--duration', type=float, default=1.0)
    parser.add_argument('--amplitude', type=float, default=0.15, help='Peak digital amplitude, 0 through 1; not a physical SPL.')
    parser.add_argument('--realtime', action='store_true', help='Wait one chunk duration between uploads.')
    args = parser.parse_args()
    if args.chunks < 1 or args.duration <= 0 or not 0 <= args.amplitude <= 1:
        parser.error('chunks and duration must be positive; amplitude must be between 0 and 1')
    devices = json.loads(args.credentials.read_text())['devices']
    if not 0 <= args.device_index < len(devices):
        parser.error('--device-index is outside the saved device list')
    device = devices[args.device_index]
    audio = pcm24_wav(args.sample_rate, args.duration, args.amplitude)
    session = 'sim-' + uuid.uuid4().hex
    # A non-realtime batch represents historical capture, avoiding future timestamps.
    started = datetime.now(timezone.utc) - timedelta(seconds=args.duration * args.chunks)
    for sequence in range(args.chunks):
        metadata = {
            'device_id': device['id'], 'chunk_id': f'{session}-{sequence}',
            'captured_at': (started + timedelta(seconds=sequence * args.duration)).isoformat(),
            'session_id': session, 'sequence': sequence,
        }
        status, result = upload(args.url, device, metadata, audio)
        print(json.dumps({'http_status': status, 'chunk_id': metadata['chunk_id'], **result}))
        if args.realtime and sequence + 1 < args.chunks:
            time.sleep(args.duration)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, urllib.error.URLError) as exc:
        if isinstance(exc, urllib.error.HTTPError):
            print(exc.read().decode())
        raise SystemExit(str(exc))

#!/usr/bin/env python3
"""Exercise live incidents through real WAV uploads using TEST-ONLY calibration."""
from __future__ import annotations
import argparse
from datetime import datetime, timedelta, timezone
import io
import json
import math
import os
from pathlib import Path
import tempfile
import time
import urllib.error
import uuid

# These scripts also work with the host's Python 3.9 without project dependencies.
from seed import request, save_private
from simulate import pcm24_wav, upload


def digital_rms(audio):
    import wave
    with wave.open(io.BytesIO(audio), 'rb') as wav:
        data = wav.readframes(wav.getnframes())
    samples = [int.from_bytes(data[i:i+3], 'little', signed=True) for i in range(0, len(data), 3)]
    count = len(samples)
    rms = math.sqrt(count * sum(value * value for value in samples) - sum(samples)**2) / count
    return 20 * math.log10(rms / (2**23))


def wait_processed(base, admin, identity):
    for _ in range(200):
        chunk = request(base, admin, '/audio/' + identity)
        if chunk['status'] == 'completed':
            return chunk['measurements'][-1]
        if chunk['status'] == 'failed':
            raise RuntimeError('Processing failed: ' + str(chunk['job']['last_error']))
        time.sleep(.1)
    raise RuntimeError('Worker did not complete within 20 seconds; inspect worker logs.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://localhost:8000')
    parser.add_argument('--scenario', choices=['sequence', 'invalid', 'delayed', 'future', 'silence'], default='sequence')
    parser.add_argument('--credentials', type=Path, default=Path('.local/live-demo-device.json'))
    parser.add_argument('--pause', type=float, default=.5, help='Seconds between completed uploads for watching the demo.')
    args = parser.parse_args()
    if args.pause < 0:
        parser.error('--pause must be nonnegative')
    admin = os.environ.get('ADMIN_TOKEN')
    if not admin:
        parser.error('Load your .env first: set -a; . ./.env; set +a')
    unique = uuid.uuid4().hex[:8]
    base_time = datetime.now(timezone.utc)
    location = request(args.url, admin, '/locations', {
        'name': 'SYNTHETIC live demo ' + unique,
        'latitude': 12.9716, 'longitude': 77.5946, 'timezone': 'Asia/Kolkata',
        'threshold_value': 60, 'threshold_type': 'spl_z_leq', 'weighting': 'Z',
        'channel_policy': 'mono', 'interval_seconds': 1, 'recovery_count': 3,
    })
    levels = [55, 60, 70, 80, 60, 59, 58]
    audio = {value: pcm24_wav(16000, 1, math.sqrt(2) * 10**((value-100)/20)) for value in levels}
    # Match the quantized 60 fixture exactly, so its true canonical value is 60.
    # This is a labelled numerical fixture, never INMP441 hardware calibration.
    synthetic_offset = 60.0 - digital_rms(audio[60])
    calibration = {'method': 'spl_z_leq', 'version': 'SYNTHETIC-DEMO-ONLY-' + unique,
                   'weighting': 'Z', 'channel_policy': 'mono', 'status': 'valid',
                   'sample_rate': 16000, 'offset_db': synthetic_offset,
                   'calibrated_at': (base_time-timedelta(days=1)).isoformat(),
                   'valid_until': (base_time+timedelta(days=1)).isoformat()}
    dev = request(args.url, admin, '/devices', {'location_id': location['id'], 'calibration': calibration})
    save_private(args.credentials, {'devices': [{'id': dev['id'], 'token': dev['token'],
                                                'location_id': location['id'], 'name': location['name']}]})
    print('TEST ONLY: synthetic calibration exercises software; it does not calibrate a microphone.', flush=True)
    print('Location: ' + location['id'] + '; device: ' + dev['id'], flush=True)
    session = 'live-demo-' + unique
    started = base_time - timedelta(seconds=10)
    if args.scenario == 'sequence':
        cases = [(index, value, audio[value], started+timedelta(seconds=index)) for index,value in enumerate(levels)]
    else:
        cases = [(0, 70, audio[70], started), (1, 60, audio[60], started+timedelta(seconds=1))]
        value = None
        data = pcm24_wav(16000, 1, 1.0 if args.scenario == 'invalid' else 0.0 if args.scenario == 'silence' else .01)
        captured = (base_time-timedelta(seconds=600) if args.scenario == 'delayed' else
                    base_time+timedelta(seconds=60) if args.scenario == 'future' else started+timedelta(seconds=2))
        cases.append((2, value, data, captured))
    for sequence, nominal, data, captured in cases:
        meta = {'device_id': dev['id'], 'chunk_id': f'{session}-{sequence}', 'session_id': session,
                'sequence': sequence, 'captured_at': captured.isoformat()}
        status, accepted = upload(args.url, dev, meta, data)
        if status != 202:
            raise RuntimeError('Unexpected upload acknowledgement: ' + str(status))
        result = wait_processed(args.url, admin, accepted['id'])
        evaluation = result.get('evaluation') or {}
        print(json.dumps({'nominal_test_level': nominal, 'measured_level': result['value_db'],
                          'evaluation': evaluation.get('status'), 'diagnostic': evaluation.get('diagnostic'),
                          'breach': evaluation.get('breach'), 'audio_id': accepted['id']}), flush=True)
        if args.pause:
            time.sleep(args.pause)
    incidents = request(args.url, admin, '/incidents?device_id=' + dev['id'])
    if args.scenario == 'sequence':
        if incidents['total'] != 1 or incidents['items'][0]['status'] != 'resolved' or incidents['items'][0]['breach_count'] != 2:
            raise RuntimeError('Sequence failed expected one resolved incident with two breaches: ' + json.dumps(incidents))
        measurements = request(args.url, admin, '/measurements?device_id=' + dev['id'])
        events = request(args.url, admin, '/events?device_id=' + dev['id'])['items']
        if (measurements['total'] != 7 or incidents['items'][0]['recovery_streak'] != 3 or
                sum(event['event_type'] == 'incident.opened' for event in events) != 1 or
                sum(event['event_type'] == 'incident.resolved' for event in events) != 1 or
                abs(incidents['items'][0]['peak_db'] - 80) > .001):
            raise RuntimeError('Sequence measurement, peak, recovery, or notification counts did not match.')
        print('PASS: seven readings, peak 80 (display), one opening notification, two breaches, '
              'three-reading recovery and one resolution notification.', flush=True)
    else:
        if incidents['total'] != 1 or incidents['items'][0]['status'] not in ('active','recovering'):
            raise RuntimeError('Diagnostic sample incorrectly resolved the incident.')
        print('PASS: ' + args.scenario + ' sample did not resolve the incident.', flush=True)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, urllib.error.URLError) as exc:
        if isinstance(exc, urllib.error.HTTPError):
            print(exc.read().decode())
        raise SystemExit(str(exc))

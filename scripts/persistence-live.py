"""Internal fixture for the isolated container-recreation test; never production seed."""
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import time

from scripts.seed import request
from scripts.simulate import pcm24_wav, upload

BASE = 'http://localhost:8000'
PATH = Path('/data/audio/live-persistence-fixture.json')
ADMIN = os.environ['ADMIN_TOKEN']


def completed(identity):
    for _ in range(200):
        state = request(BASE, ADMIN, '/audio/' + identity)
        if state['status'] == 'completed':
            return state
        if state['status'] == 'failed':
            raise AssertionError(state['job']['last_error'])
        time.sleep(.1)
    raise AssertionError('worker failed to process persistence fixture')


def add_chunk(dev, sequence, started, amplitude):
    audio = pcm24_wav(16000, 1, amplitude)
    status, result = upload(BASE, dev, {
        'device_id': dev['id'], 'chunk_id': 'persist-live-' + str(sequence),
        'session_id': 'persist-live', 'sequence': sequence,
        'captured_at': (started + timedelta(seconds=sequence)).isoformat(),
    }, audio)
    assert status == 202
    completed(result['id'])
    return {'id': result['id'], 'checksum': hashlib.sha256(audio).hexdigest()}


def prepare():
    now = datetime.now(timezone.utc)
    loc = request(BASE, ADMIN, '/locations', {
        'name': 'SYNTHETIC restart recovery fixture', 'latitude': 12.9716, 'longitude': 77.5946,
        'timezone': 'UTC', 'threshold_value': 60, 'threshold_type': 'spl_z_leq',
        'interval_seconds': 1, 'recovery_count': 3,
    })
    dev = request(BASE, ADMIN, '/devices', {'location_id': loc['id'], 'calibration': {
        'method': 'spl_z_leq', 'version': 'SYNTHETIC-RESTART-TEST-ONLY', 'offset_db': 100,
        'sample_rate': 16000, 'calibrated_at': (now-timedelta(days=1)).isoformat(),
        'valid_until': (now+timedelta(days=1)).isoformat(),
    }})
    start = now-timedelta(seconds=5)
    chunks = [add_chunk(dev, i, start, level) for i, level in enumerate([.5,.001,.001])]
    incident = request(BASE, ADMIN, '/incidents?device_id=' + dev['id'])['items'][0]
    assert incident['status'] == 'recovering' and incident['recovery_streak'] == 2
    events = request(BASE, ADMIN, '/events?device_id=' + dev['id'])['items']
    PATH.write_text(json.dumps({'device': dev, 'incident_id': incident['id'], 'started': start.isoformat(),
                               'chunks': chunks, 'event_ids': [event['event_id'] for event in events]}))
    PATH.chmod(0o600)
    print('Prepared one incident with two committed recovery readings and durable events.')


def verify():
    import urllib.request
    data = json.loads(PATH.read_text())
    dev = data['device']
    incident = request(BASE, ADMIN, '/incidents/' + data['incident_id'])
    assert incident['status'] == 'recovering' and incident['recovery_streak'] == 2
    events = request(BASE, ADMIN, '/events?device_id=' + dev['id'])['items']
    assert set(data['event_ids']) <= {event['event_id'] for event in events}
    for chunk in data['chunks']:
        req = urllib.request.Request(BASE+'/audio/'+chunk['id']+'/file', headers={'Authorization':'Bearer '+ADMIN})
        with urllib.request.urlopen(req, timeout=10) as response:
            assert hashlib.sha256(response.read()).hexdigest() == chunk['checksum']
    add_chunk(dev, 3, datetime.fromisoformat(data['started']), .001)
    incident = request(BASE, ADMIN, '/incidents/' + data['incident_id'])
    assert incident['status'] == 'resolved' and incident['closed_reason'] == 'valid_recovery'
    events = request(BASE, ADMIN, '/events?device_id=' + dev['id'])['items']
    assert sum(event['event_type']=='incident.opened' for event in events) == 1
    assert sum(event['event_type']=='incident.resolved' for event in events) == 1
    print('PASS: incident, recovery streak, event IDs and original audio survived container recreation; third normal resolved once.')


if __name__ == '__main__':
    {'prepare': prepare, 'verify': verify}[sys.argv[1]]()

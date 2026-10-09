"""Strict API configuration, effective policies and assignment history."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json

import pytest
from sqlalchemy import select

from app.models import Measurement, MeasurementEvaluation, ThresholdVersion
from test_integration import (CAPTURED, SYNTHETIC_CALIBRATION, accept, device, location,
                              metadata, process, upload)

pytestmark = pytest.mark.integration


@pytest.mark.parametrize('override', [
    {'threshold_value': None}, {'threshold_value': True}, {'threshold_value': '60'},
    {'threshold_value': float('nan')}, {'threshold_value': float('inf')}, {'threshold_value': -float('inf')},
    {'interval_seconds': True}, {'interval_seconds': '1'}, {'interval_seconds': 0},
    {'interval_seconds': -1}, {'interval_seconds': 61}, {'recovery_count': 0},
    {'recovery_count': True}, {'weighting': 'A'}, {'channel_policy': 'stereo'},
    {'latitude': True}, {'latitude': '12'}, {'longitude': -181}, {'threshold_type': 'dBA'},
])
def test_strict_location_configuration(client, admin_headers, override):
    body = dict(name='Strict configuration', latitude=12.0, longitude=77.0, timezone='UTC',
                threshold_value=60, threshold_type='spl_z_leq', interval_seconds=1)
    body.update(override)
    # Raw JSON allows sending deliberately invalid NaN/Infinity for server validation.
    response = client.post('/locations', headers={**admin_headers, 'Content-Type': 'application/json'},
                           content=json.dumps(body))
    assert response.status_code == 422, response.text


def test_threshold_edit_is_versioned_optimistic_and_effective_at_capture(client, admin_headers, db, settings, fake_clock):
    loc = location(client, admin_headers)
    dev = device(client, admin_headers, loc['id'], calibration=SYNTHETIC_CALIBRATION)
    current = client.get(f'/locations/{loc["id"]}/threshold', headers=admin_headers).json()
    assert current['current']['revision'] == current['latest_revision'] == 1
    payload = dict(threshold_value=100, threshold_type='spl_z_leq', interval_seconds=1,
                   recovery_count=3, expected_revision=1)
    changed = client.patch(f'/locations/{loc["id"]}/threshold', headers=admin_headers, json=payload)
    assert changed.status_code == 200, changed.text
    assert changed.json()['revision'] == 2
    assert client.patch(f'/locations/{loc["id"]}/threshold', headers=admin_headers, json=payload).status_code == 409
    # Applicable rule is determined by capture timestamp, independent of upload order.
    before = accept(client, dev, meta=metadata(dev, captured_at=(fake_clock.now()-timedelta(seconds=1)).isoformat()))
    process(settings)
    after = accept(client, dev, meta=metadata(dev, sequence=1, captured_at=fake_clock.now().isoformat()))
    process(settings)
    old_result = client.get(f'/audio/{before["id"]}', headers=admin_headers).json()['measurements'][0]
    new_result = client.get(f'/audio/{after["id"]}', headers=admin_headers).json()['measurements'][0]
    assert old_result['evaluation']['threshold_version_id'] == current['current']['id']
    assert old_result['breach'] is True and new_result['breach'] is False
    assert new_result['evaluation']['threshold_version_id'] == changed.json()['id']
    incident = client.get('/incidents', headers=admin_headers).json()['items'][0]
    assert incident['status'] == 'closed' and incident['closed_reason'] == 'threshold_changed'
    assert client.get(f'/incidents/{incident["id"]}', headers=admin_headers).json()['threshold_version']['revision'] == 1
    assert client.get(f'/locations/{loc["id"]}/threshold/versions', headers=admin_headers).json()['total'] == 2


def test_concurrent_threshold_edits_one_wins(client, admin_headers):
    loc = location(client, admin_headers)
    def edit(value):
        return client.patch(f'/locations/{loc["id"]}/threshold', headers=admin_headers, json={
            'threshold_value': value, 'threshold_type': 'spl_z_leq', 'interval_seconds': 1, 'expected_revision': 1})
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(edit, [60, 80]))
    assert sorted(row.status_code for row in results) == [200, 409]
    versions = client.get(f'/locations/{loc["id"]}/threshold/versions', headers=admin_headers).json()
    assert versions['total'] == 2


def test_retroactive_threshold_and_unversioned_patch_rejected(client, admin_headers, fake_clock):
    loc = location(client, admin_headers)
    body = {'threshold_value': 60, 'threshold_type': 'spl_z_leq', 'interval_seconds': 1}
    assert client.patch(f'/locations/{loc["id"]}/threshold', headers=admin_headers, json=body).status_code == 422
    body.update(expected_revision=1, effective_at=(fake_clock.now()-timedelta(seconds=1)).isoformat())
    assert client.patch(f'/locations/{loc["id"]}/threshold', headers=admin_headers, json=body).status_code == 422


def test_reassignment_history_and_explicit_policy_close(client, admin_headers, settings, fake_clock):
    old = location(client, admin_headers)
    new = location(client, admin_headers, name='New coordinates', latitude=22.5, longitude=88.3)
    dev = device(client, admin_headers, old['id'], calibration=SYNTHETIC_CALIBRATION)
    first = accept(client, dev, meta=metadata(dev, captured_at=(fake_clock.now()-timedelta(seconds=1)).isoformat()))
    process(settings)
    update = client.patch(f'/devices/{dev["id"]}', headers=admin_headers,
                          json={'location_id': new['id'], 'expected_revision': 1})
    assert update.status_code == 200, update.text
    assert update.json()['assignment_revision'] == 2
    incidents = client.get('/incidents', headers=admin_headers).json()['items']
    assert incidents[0]['status'] == 'active'  # Editing does not prove recovery.
    late = accept(client, dev, meta=metadata(dev, chunk_id='late-before-move', sequence=10,
                    captured_at=(fake_clock.now()-timedelta(seconds=2)).isoformat()))
    process(settings)
    old_audio = client.get(f'/audio/{late["id"]}', headers=admin_headers).json()
    assert old_audio['location_id'] == old['id']
    assert old_audio['location_snapshot']['longitude'] == old['longitude']
    assert not old_audio['measurements'][0]['evaluation']['live']
    accept(client, dev, meta=metadata(dev, chunk_id='after-move', sequence=11,
                                    captured_at=fake_clock.now().isoformat()))
    process(settings)
    incidents = client.get('/incidents', headers=admin_headers).json()['items']
    assert len(incidents) == 2
    previous = next(item for item in incidents if item['location_id'] == old['id'])
    current = next(item for item in incidents if item['location_id'] == new['id'])
    assert previous['status'] == 'closed' and previous['closed_reason'] == 'device_reassigned'
    assert current['previous_incident_id'] == previous['id']
    assert client.get(f'/audio/{first["id"]}', headers=admin_headers).json()['location_id'] == old['id']


def test_strict_metadata_and_forged_attribution_rejected(client, admin_headers):
    loc = location(client, admin_headers)
    dev = device(client, admin_headers, loc['id'])
    for override in ({'sequence': True}, {'sequence': '1'}, {'location_id': loc['id']}, {'device_id': 'not-a-uuid'}):
        payload = {**metadata(dev), **override}
        assert upload(client, dev, meta=payload).status_code == 422


def test_history_filters_reject_invalid_ranges_and_status(client, admin_headers):
    assert client.get('/incidents?status=anything', headers=admin_headers).status_code == 422
    assert client.get('/incidents?limit=0', headers=admin_headers).status_code == 422
    assert client.get('/events?event_type=secret.internal', headers=admin_headers).status_code == 422
    assert client.get('/measurements?since=2026-03-01T00:00:00Z&until=2026-01-01T00:00:00Z', headers=admin_headers).status_code == 422

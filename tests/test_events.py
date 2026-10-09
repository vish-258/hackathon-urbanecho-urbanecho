"""Real PostgreSQL tests for durable replay, snapshot races and independent clients."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
from threading import Event
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text

from test_integration import device, location

pytestmark = pytest.mark.integration


def add_event(location_id, device_id=None, **payload):
    from app.db import SessionLocal
    from app.events import emit_event
    with SessionLocal() as session:
        result = emit_event(session, 'location.status_changed', payload,
                            location_id=UUID(str(location_id)), device_id=UUID(str(device_id)) if device_id else None)
        session.commit()
        return result


def get_snapshot(client, headers, **params):
    response = client.get('/locations/status', headers=headers, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_snapshot_authentication_pagination_and_filter_validation(client, admin_headers):
    first = location(client, admin_headers, name='One')
    location(client, admin_headers, name='Two')
    dev = device(client, admin_headers, first['id'])
    assert client.get('/locations/status').status_code == 401
    assert client.get('/locations/status', headers={'Authorization': f'Bearer {dev["token"]}'}).status_code == 403
    snap = get_snapshot(client, admin_headers, limit=1, offset=1)
    assert snap['total'] == 2 and len(snap['items']) == 1
    filtered = get_snapshot(client, admin_headers, location_id=first['id'])
    assert [row['id'] for row in filtered['items']] == [first['id']]
    assert filtered['items'][0]['data_status'] == 'unknown'
    assert client.get('/locations/status?limit=201', headers=admin_headers).status_code == 422
    assert client.get(f'/locations/status?location_id={uuid4()}', headers=admin_headers).status_code == 404


def test_stream_denies_missing_device_and_url_credentials(client, admin_headers):
    loc = location(client, admin_headers)
    dev = device(client, admin_headers, loc['id'])
    assert client.get('/events/stream').status_code == 401
    assert client.get('/events/stream', headers={'Authorization': f'Bearer {dev["token"]}'}).status_code == 403
    assert client.get('/events/stream', params={'token': admin_headers['Authorization'][7:]}).status_code == 401
    response = client.get('/events/stream', headers=admin_headers)
    assert response.status_code == 409
    assert response.json()['error']['message']['resync_required'] is True


@pytest.mark.parametrize('bad', ['x', '00000000-0000-0000-0000-000000000000:-1',
                                   '00000000-0000-0000-0000-000000000000:01',
                                   '00000000-0000-0000-0000-000000000000:1.1',
                                   '00000000-0000-0000-0000-000000000000:99999999999999999999'])
def test_malformed_cursor_is_explicit(client, admin_headers, bad):
    response = client.get('/events/stream', headers=admin_headers, params={'cursor': bad})
    assert response.status_code == 400
    assert response.json()['error']['message']['code'] == 'malformed_cursor'


def test_unknown_future_conflicting_and_missing_event_cursors(client, admin_headers, privileged):
    from app.events import read_events
    loc = location(client, admin_headers)
    snap = get_snapshot(client, admin_headers)
    epoch, position = snap['cursor'].split(':')
    for value in [f'{uuid4()}:0', f'{epoch}:{int(position) + 200}']:
        assert client.get('/events/stream', headers=admin_headers, params={'cursor': value}).status_code == 409
    response = client.get('/events/stream', headers={**admin_headers, 'Last-Event-ID': snap['cursor']},
                          params={'cursor': f'{epoch}:{int(position) + 1}'})
    assert response.status_code == 400
    event = add_event(loc['id'])
    with privileged.begin() as connection:
        connection.execute(text('UPDATE event_clock SET last_position=last_position+1 WHERE id=1'))
    with pytest.raises(HTTPException) as raised:
        read_events(f'{epoch}:{event.pointer + 1}')
    assert raised.value.status_code == 409 and raised.value.detail['code'] == 'unknown_cursor'


def test_expiry_and_fresh_snapshot_after_idle(client, admin_headers, settings, monkeypatch):
    from app import clock
    from app.events import read_events
    anchor = datetime(2026, 10, 9, tzinfo=timezone.utc)
    monkeypatch.setattr(clock, 'now', lambda: anchor)
    loc = location(client, admin_headers)
    before = get_snapshot(client, admin_headers)['cursor']
    add_event(loc['id'])
    later = anchor + timedelta(seconds=settings.event_replay_seconds + 1)
    monkeypatch.setattr(clock, 'now', lambda: later)
    with pytest.raises(HTTPException) as raised:
        read_events(before)
    assert raised.value.status_code == 410
    after = get_snapshot(client, admin_headers)['cursor']
    assert read_events(after) == []  # Current state works even after a long quiet period.


def test_two_clients_replay_reconnect_filters_and_safe_payload(client, admin_headers, settings):
    from app.events import encode_sse, read_events
    a = location(client, admin_headers, name='A')
    b = location(client, admin_headers, name='B')
    dev = device(client, admin_headers, a['id'])
    cursor = get_snapshot(client, admin_headers)['cursor']
    event_a = add_event(a['id'], dev['id'], noise_status='normal', latitude=12, longitude=77,
                        token='DO-NOT-PUBLISH', credential_hash='SECRET', file_path='/private/file',
                        calibration={'secret': 'PRIVATE'}, database_url='PASSWORD')
    add_event(b['id'], noise_status='excessive')
    one, two = read_events(cursor), read_events(cursor)
    assert one == two and len(one) == 2
    assert read_events(one[0]['cursor']) == one[1:]
    assert read_events(cursor, location_id=UUID(a['id'])) == one[:1]
    assert read_events(cursor, device_id=UUID(dev['id'])) == one[:1]
    body = encode_sse(one[0])
    assert f'"event_id":"{event_a.id}"' in body and f'id: {one[0]["cursor"]}' in body
    assert not any(secret in body for secret in ['DO-NOT-PUBLISH', 'SECRET', '/private/file', 'PASSWORD', 'calibration'])
    assert client.get('/events/stream', headers=admin_headers,
                      params={'cursor': cursor, 'location_id': str(uuid4())}).status_code == 404


def test_transaction_rollback_leaves_no_event_or_counter_hole(client, admin_headers):
    from app.db import SessionLocal
    from app.events import emit_event, read_events
    loc = location(client, admin_headers)
    snap = get_snapshot(client, admin_headers)
    start = int(snap['cursor'].split(':')[1])
    with SessionLocal() as session:
        emit_event(session, 'location.status_changed', {}, location_id=UUID(loc['id']))
        session.rollback()
    assert read_events(snap['cursor']) == []
    saved = add_event(loc['id'])
    assert saved.pointer == start + 1


def test_lower_pointer_cannot_commit_after_higher_pointer(client, admin_headers):
    from app.db import SessionLocal
    from app.events import emit_event, read_events
    loc = location(client, admin_headers)
    cursor = get_snapshot(client, admin_headers)['cursor']
    first_allocated, allow_first_commit, second_started = Event(), Event(), Event()
    def first_writer():
        with SessionLocal() as session:
            result = emit_event(session, 'location.status_changed', {'transition_reason': 'first'}, location_id=UUID(loc['id']))
            first_allocated.set()
            assert allow_first_commit.wait(5)
            session.commit()
            return result.pointer
    def second_writer():
        second_started.set()
        return add_event(loc['id'], transition_reason='second').pointer
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(first_writer)
        second = None
        try:
            assert first_allocated.wait(5)
            second = pool.submit(second_writer)
            assert second_started.wait(5)
            # First event is uncommitted. Second cannot obtain a replay position yet.
            assert read_events(cursor) == []
            assert not second.done()
        finally:
            allow_first_commit.set()
        first_position = first.result(timeout=5)
        second_position = second.result(timeout=5)
    assert second_position == first_position + 1
    records = read_events(cursor)
    assert [row['data']['transition_reason'] for row in records] == ['first', 'second']
    assert read_events(records[0]['cursor']) == records[1:]


def test_events_during_snapshot_load_are_replayed(client, admin_headers, monkeypatch):
    import app.live_api as live
    from app.events import read_events
    loc = location(client, admin_headers)
    lock_acquired, release_snapshot, writer_started = Event(), Event(), Event()
    original = live.sweep_freshness
    def paused(session, settings):
        result = original(session, settings)
        lock_acquired.set()
        assert release_snapshot.wait(5)
        return result
    monkeypatch.setattr(live, 'sweep_freshness', paused)
    def writer():
        writer_started.set()
        return add_event(loc['id'], transition_reason='during_snapshot')
    with ThreadPoolExecutor(max_workers=2) as pool:
        loading = pool.submit(live.snapshot)
        writing = None
        try:
            assert lock_acquired.wait(5)
            writing = pool.submit(writer)
            assert writer_started.wait(5)
            assert not writing.done()
        finally:
            release_snapshot.set()
        snap = loading.result(timeout=5)
        saved = writing.result(timeout=5)
    assert [row['data']['event_id'] for row in read_events(snap['cursor'])] == [str(saved.id)]


def test_slow_and_disconnected_generators_release_database_connections(client, admin_headers, settings):
    from app.events import read_events
    from app.live_api import stream_events
    loc = location(client, admin_headers)
    cursor = get_snapshot(client, admin_headers)['cursor']
    add_event(loc['id'], transition_reason='initial')
    class Request:
        disconnected = False
        async def is_disconnected(self):
            return self.disconnected
    async def exercise():
        request = Request()
        slow = stream_events(request, cursor, settings=settings)
        quick = stream_events(Request(), cursor, settings=settings)
        assert await anext(slow) == ': connected\n\n'
        assert await anext(quick) == ': connected\n\n'
        slow_first = await anext(slow)
        assert await anext(quick) == slow_first
        # Pause this client after a yielded record. A new writer can still commit.
        with ThreadPoolExecutor(max_workers=1) as pool:
            saved = pool.submit(add_event, loc['id'], transition_reason='while_slow').result(timeout=5)
        assert str(saved.id) in json.dumps(read_events(cursor))
        assert str(saved.id) in await anext(quick)
        request.disconnected = True
        with pytest.raises(StopAsyncIteration):
            await anext(slow)
        await quick.aclose()
    asyncio.run(exercise())


def test_demo_is_same_origin_and_has_no_saved_credentials(client):
    response = client.get('/demo')
    assert response.status_code == 200
    assert "default-src 'self'" in response.headers['content-security-policy']
    script = client.get('/demo/demo.js').text
    assert 'localStorage' not in script and 'sessionStorage' not in script
    assert "Authorization: `Bearer ${token}`" in script
    assert client.get('/demo/private.txt').status_code == 404


def test_stale_snapshot_preserves_unresolved_incident_and_separate_devices(client, admin_headers, settings, monkeypatch):
    from app import clock
    from app.db import SessionLocal
    from app.events import lock_event_clock, read_events, sweep_freshness
    from app.models import Device, Incident, StreamState, ThresholdVersion
    anchor = datetime(2026, 10, 9, 10, tzinfo=timezone.utc)
    monkeypatch.setattr(clock, 'now', lambda: anchor)
    loc = location(client, admin_headers)
    dev_a = device(client, admin_headers, loc['id'])
    dev_b = device(client, admin_headers, loc['id'])
    with SessionLocal() as session:
        lock_event_clock(session)
        threshold = session.scalar(select(ThresholdVersion).where(ThresholdVersion.location_id == UUID(loc['id'])))
        states = []
        for dev, noise, value in [(dev_a, 'excessive', 80), (dev_b, 'normal', 55)]:
            device_row = session.get(Device, UUID(dev['id']))
            state = StreamState(device_id=device_row.id, location_id=UUID(loc['id']),
                assignment_id=device_row.current_assignment_id, stream_key='spl_z_leq|Z|mono|1',
                threshold_version_id=threshold.id, watermark=anchor, window_end=anchor + timedelta(seconds=1),
                observed_at=anchor, last_received_at=anchor, last_value=value,
                noise_status=noise, data_status='fresh', recovery_streak=0, updated_at=anchor)
            session.add(state)
            session.flush()
            states.append(state)
        incident = Incident(device_id=UUID(dev_a['id']), location_id=UUID(loc['id']),
            stream_id=states[0].id, threshold_version_id=threshold.id, started_at=anchor,
            threshold_value=75, threshold_type='spl_z_leq', peak_db=80, latest_db=80,
            status='active', location_snapshot={'latitude': 12.9716, 'longitude': 77.5946},
            last_occurrence_at=anchor, breach_count=1, recovery_streak=0)
        session.add(incident)
        session.commit()
        incident_id = str(incident.id)
    before = get_snapshot(client, admin_headers)
    assert before['items'][0]['noise_status'] == 'excessive'
    assert before['items'][0]['data_status'] == 'fresh'
    assert len(before['items'][0]['streams']) == 2
    monkeypatch.setattr(clock, 'now', lambda: anchor + timedelta(seconds=settings.data_stale_seconds + 1))
    after = get_snapshot(client, admin_headers)
    assert after['items'][0]['data_status'] == 'stale'
    assert after['items'][0]['noise_status'] == 'excessive'
    assert after['items'][0]['unresolved_incident_ids'] == [incident_id]
    assert after['incidents'][0]['status'] == 'active'
    stale_events = read_events(before['cursor'])
    assert len(stale_events) == 2
    assert all(row['data']['data_status'] == 'stale' for row in stale_events)
    assert all(row['event_type'] == 'location.status_changed' for row in stale_events)
    with SessionLocal() as session:
        assert sweep_freshness(session, settings) == 0
        session.commit()


def test_invalid_live_event_exposes_diagnostic_immediately_and_clears_on_valid_data(client, admin_headers, monkeypatch):
    from app import clock
    from app.events import read_events
    from tests.live_helpers import BASE, create_stream, put_reading
    monkeypatch.setattr(clock, 'now', lambda: BASE + timedelta(seconds=5))
    stream = create_stream()
    put_reading(stream, 0, 70)
    put_reading(stream, 1, 60)
    before = get_snapshot(client, admin_headers, location_id=str(stream.location_id))
    outcome = put_reading(stream, 2, 50, result={'quality_status': 'clipped'})
    assert outcome.diagnostic == 'quality_clipped'
    received = read_events(before['cursor'])
    current = next(item['data'] for item in received if item['event_type'] == 'location.status_changed')
    assert current['data_status'] == 'invalid'
    assert current['diagnostic'] == 'quality_clipped'
    assert current['noise_status'] == 'excessive'
    assert not any(item['event_type'] == 'incident.resolved' for item in received)
    invalid_snapshot = get_snapshot(client, admin_headers, location_id=str(stream.location_id))
    persisted = invalid_snapshot['items'][0]['streams'][0]
    assert persisted['data_status'] == 'invalid' and persisted['diagnostic'] == 'quality_clipped'
    assert persisted['measurement_type'] == 'spl_z_leq' and persisted['weighting'] == 'Z'
    assert persisted['calibration_status'] == 'calibrated' and persisted['interval_seconds'] == 1
    put_reading(stream, 3, 59)
    recovered_events = read_events(invalid_snapshot['cursor'])
    valid = next(item['data'] for item in recovered_events if item['event_type'] == 'location.status_changed')
    assert valid['data_status'] == 'fresh' and valid['diagnostic'] is None
    assert valid['incident_status'] == 'recovering'  # One valid reading cannot resolve.


@pytest.mark.parametrize('age,expected_freshness', [(30, 'fresh'), (31, 'stale'), (120, 'stale')])
def test_eligible_delayed_reading_commits_correct_freshness_without_transient_fresh_event(
    client, admin_headers, monkeypatch, age, expected_freshness,
):
    from app import clock
    from app.events import read_events
    from tests.live_helpers import BASE, create_stream, put_reading
    monkeypatch.setattr(clock, 'now', lambda: BASE + timedelta(seconds=age))
    stream = create_stream()
    initial = get_snapshot(client, admin_headers, location_id=str(stream.location_id))
    evaluated = put_reading(stream, 0, 70)
    assert evaluated.status == 'eligible_live' and evaluated.breach is True
    delivered = read_events(initial['cursor'])
    assert {item['event_type'] for item in delivered} == {'incident.opened', 'location.status_changed'}
    assert all(item['data']['data_status'] == expected_freshness for item in delivered)
    committed = get_snapshot(client, admin_headers, location_id=str(stream.location_id))
    assert committed['items'][0]['data_status'] == expected_freshness
    assert committed['items'][0]['noise_status'] == 'excessive'
    assert committed['incidents'][0]['status'] == 'active'
    assert committed['cursor'] == delivered[-1]['cursor']  # No corrective freshness event was needed.
    # Display staleness must not interrupt valid capture-contiguous recovery.
    for sequence, value in enumerate([60, 59, 58], 1):
        put_reading(stream, sequence, value)
    subsequent = read_events(committed['cursor'])
    assert len([item for item in subsequent if item['event_type'] == 'incident.resolved']) == 1
    if age == 120:
        assert all(item['data']['data_status'] == 'stale' for item in subsequent)

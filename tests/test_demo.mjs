import test from 'node:test';
import assert from 'node:assert/strict';
import {initialState, applySnapshot, applyEvent, sseParser, ageData, formatLevel} from '../app/static/state.mjs';
const epoch = 'c38f4b87-b77c-4a9c-b7d9-d91da94bfb17';
const snapshot = () => ({cursor: `${epoch}:0`, items: [
  {id: 'loc-a', name: 'A', noise_status: 'unknown', data_status: 'unknown', streams: []},
  {id: 'loc-b', name: 'B', noise_status: 'unknown', data_status: 'unknown', streams: []}], incidents: []});
const event = (type, extra = {}) => ({event_id: 'evt-1', event_type: type, incident_id: 'incident-a',
  device_id: 'device-a', location_id: 'loc-a', stream_id: 'stream-a', assignment_id: 'assignment-a',
  incident_status: 'active', noise_status: 'excessive', data_status: 'fresh', measurement_value: 70,
  ...extra});
test('one opening popup under duplicate delivery, with correct location marker state', () => {
  const state = applySnapshot(initialState(), snapshot());
  assert.equal(applyEvent(state, event('incident.opened'), `${epoch}:1`).kind, 'opening');
  assert.equal(applyEvent(state, event('incident.opened'), `${epoch}:1`), null);
  assert.equal(state.feed.length, 1);
  assert.equal(state.locations.get('loc-a').noise_status, 'excessive');
  assert.equal(state.locations.get('loc-b').noise_status, 'unknown');
});
test('continuing incidents update values without repeated popups', () => {
  const state = applySnapshot(initialState(), snapshot());
  applyEvent(state, event('incident.opened'), `${epoch}:1`);
  assert.equal(applyEvent(state, event('incident.updated', {event_id: 'evt-2', peak_db: 80}), `${epoch}:2`), null);
  assert.equal(state.incidents.get('incident-a').peak_db, 80);
});
test('genuine resolution emits recovery and policy closure does not', () => {
  const state = applySnapshot(initialState(), snapshot());
  applyEvent(state, event('incident.opened'), `${epoch}:1`);
  const result = applyEvent(state, event('incident.resolved', {event_id: 'evt-2', incident_status: 'resolved', noise_status: 'normal'}), `${epoch}:2`);
  assert.equal(result.kind, 'recovery');
  assert.equal(state.locations.get('loc-a').noise_status, 'normal');
  assert.equal(applyEvent(state, event('incident.closed', {event_id: 'evt-3', incident_status: 'closed', transition_reason: 'threshold_changed'}), `${epoch}:3`), null);
});
test('staleness is independent and never hides an unresolved incident', () => {
  const state = applySnapshot(initialState(), snapshot());
  applyEvent(state, event('incident.opened'), `${epoch}:1`);
  applyEvent(state, event('location.status_changed', {event_id: 'evt-2', data_status: 'stale'}), `${epoch}:2`);
  assert.equal(state.locations.get('loc-a').noise_status, 'excessive');
  assert.equal(state.locations.get('loc-a').data_status, 'stale');
  assert.equal(state.incidents.get('incident-a').status, 'active');
});
test('normal readings from a second device cannot hide the first device incident', () => {
  const state = applySnapshot(initialState(), snapshot());
  applyEvent(state, event('incident.opened'), `${epoch}:1`);
  applyEvent(state, event('location.status_changed', {event_id: 'evt-2', incident_id: null,
    device_id: 'device-b', stream_id: 'stream-b', noise_status: 'normal'}), `${epoch}:2`);
  assert.equal(state.locations.get('loc-a').noise_status, 'excessive');
  assert.equal(state.locations.get('loc-a').streams.length, 2);
});
test('snapshot seeds existing incidents without popups after a resync', () => {
  const initial = snapshot();
  initial.incidents = [{id: 'incident-a', location_id: 'loc-a', status: 'active'}];
  const state = applySnapshot(initialState(), initial);
  assert.equal(state.feed.length, 0);
  assert.equal(applyEvent(state, event('incident.opened'), `${epoch}:1`), null);
});
test('old replay cannot regress cursor, values or incident state', () => {
  const state = applySnapshot(initialState(), snapshot());
  applyEvent(state, event('incident.updated', {event_id: 'evt-2', peak_db: 80}), `${epoch}:2`);
  assert.equal(applyEvent(state, event('incident.opened', {peak_db: 70}), `${epoch}:1`), null);
  assert.equal(state.cursor, `${epoch}:2`);
  assert.equal(state.incidents.get('incident-a').peak_db, 80);
});
test('SSE parser handles fragmented CRLF frames, comments, and repeated delivery', () => {
  const seen = [], parse = sseParser(record => seen.push(record));
  const frame = `: heartbeat\r\n\r\nid: ${epoch}:1\r\nevent: incident.opened\r\ndata: {"event_id":"evt-1"}\r\n\r\n`;
  for (const char of frame) parse(char);
  assert.equal(seen.length, 1);
  assert.equal(seen[0].id, `${epoch}:1`);
  assert.equal(seen[0].type, 'incident.opened');
  assert.equal(seen[0].data.event_id, 'evt-1');
});
test('SSE multiline JSON and explicit resync control frames', () => {
  const seen = [], parse = sseParser(record => seen.push(record));
  parse('event: stream.resync_required\ndata: {"resync_required":\ndata: true}\n\n');
  assert.equal(seen[0].type, 'stream.resync_required');
  assert.equal(seen[0].data.resync_required, true);
});

test('client data ages to stale while disconnected without falsely resolving noise', () => {
  const state = applySnapshot(initialState(), snapshot());
  applyEvent(state, event('incident.opened', {measured_at: '2026-10-09T00:00:00Z'}), `${epoch}:1`);
  ageData(state, Date.parse('2026-10-09T00:00:31Z'), 30);
  assert.equal(state.locations.get('loc-a').data_status, 'stale');
  assert.equal(state.locations.get('loc-a').noise_status, 'excessive');
  assert.equal(state.incidents.get('incident-a').status, 'active');
});
test('an unobserved second device keeps location freshness uncertain', () => {
  const seed = snapshot();
  seed.items[0].devices = [{id: 'device-a', enabled: true, assignment_id: 'assignment-a'},
    {id: 'device-b', enabled: true, assignment_id: 'assignment-b'}];
  const state = applySnapshot(initialState(), seed);
  applyEvent(state, event('incident.opened'), `${epoch}:1`);
  assert.equal(state.locations.get('loc-a').data_status, 'stale');
});
test('policy closure retires the old stream without a recovery notification', () => {
  const state = applySnapshot(initialState(), snapshot());
  applyEvent(state, event('incident.opened'), `${epoch}:1`);
  const result = applyEvent(state, event('incident.closed', {event_id: 'evt-2',
    incident_status: 'closed', transition_reason: 'device_reassigned'}), `${epoch}:2`);
  assert.equal(result, null);
  assert.equal(state.locations.get('loc-a').noise_status, 'unknown');
  assert.equal(state.locations.get('loc-a').data_status, 'unknown');
  assert.equal(state.locations.get('loc-a').streams.length, 0);
});

test('a newly registered station appears at its event coordinates', () => {
  const state = applySnapshot(initialState(), snapshot());
  applyEvent(state, event('location.status_changed', {location_id: 'loc-c', stream_id: null,
    incident_id: null, location_name: 'New station', latitude: 12.97, longitude: 77.59,
    noise_status: 'unknown', data_status: 'unknown'}), `${epoch}:1`);
  assert.equal(state.locations.get('loc-c').name, 'New station');
  assert.equal(state.locations.get('loc-c').latitude, 12.97);
  assert.equal(state.locations.get('loc-c').noise_status, 'unknown');
});

test('live device registration and disable update roster freshness without recovery', () => {
  const state = applySnapshot(initialState(), snapshot());
  applyEvent(state, event('incident.opened'), `${epoch}:1`);
  applyEvent(state, event('location.status_changed', {event_id: 'evt-2', stream_id: null,
    incident_id: null, device_id: 'device-b', assignment_id: 'assignment-b', device_enabled: true,
    transition_reason: 'device_registered'}), `${epoch}:2`);
  assert.equal(state.locations.get('loc-a').data_status, 'stale');
  applyEvent(state, event('location.status_changed', {event_id: 'evt-3', stream_id: null,
    incident_id: null, device_id: 'device-b', transition_reason: 'device_removed'}), `${epoch}:3`);
  assert.equal(state.locations.get('loc-a').data_status, 'fresh');
  applyEvent(state, event('location.status_changed', {event_id: 'evt-4', stream_id: null,
    incident_id: null, device_enabled: false, transition_reason: 'device_configured'}), `${epoch}:4`);
  assert.equal(state.locations.get('loc-a').data_status, 'stale');
  assert.equal(state.locations.get('loc-a').noise_status, 'excessive');
});

test('levels display explicit measurement units and never imply physical calibration for dBFS', () => {
  assert.equal(formatLevel(-12.125, 'dbfs_rms'), '-12.13 dBFS');
  assert.equal(formatLevel(60.0001, 'spl_z_leq'), '60.00 dB SPL (Z)');
  assert.equal(formatLevel(60, null), '60.00 (unknown method)');
  assert.equal(formatLevel(null, 'spl_z_leq'), '—');
  assert.equal(formatLevel('60', 'spl_z_leq'), 'Unavailable');
});
test('live stream metadata keeps method and calibration attached to the rendered value', () => {
  const state = applySnapshot(initialState(), snapshot());
  applyEvent(state, event('location.status_changed', {measurement_type: 'dbfs_rms', weighting: 'none',
    interval_seconds: 1, calibration_status: 'not_required', measurement_value: -20,
    received_at: '2026-10-09T00:00:02Z',
    incident_id: null, noise_status: 'normal'}), `${epoch}:1`);
  const stream = state.locations.get('loc-a').streams[0];
  assert.equal(formatLevel(stream.measurement_value, stream.measurement_type), '-20.00 dBFS');
  assert.equal(stream.calibration_status, 'not_required');
  assert.equal(stream.weighting, 'none');
  assert.equal(stream.interval_seconds, 1);
  assert.equal(stream.received_at, '2026-10-09T00:00:02Z');
});
test('invalid data stays distinguishable from stale until the freshness window expires', () => {
  const state = applySnapshot(initialState(), snapshot());
  applyEvent(state, event('location.status_changed', {measured_at: '2026-10-09T00:00:00Z'}), `${epoch}:1`);
  applyEvent(state, event('location.status_changed', {event_id:'evt-2', data_status: 'invalid',
    measured_at: '2026-10-09T00:00:10Z', diagnostic: 'quality_clipped'}), `${epoch}:2`);
  ageData(state, Date.parse('2026-10-09T00:00:15Z'), 30);
  assert.equal(state.locations.get('loc-a').data_status, 'invalid');
  assert.equal(state.locations.get('loc-a').streams[0].diagnostic, 'quality_clipped');
  ageData(state, Date.parse('2026-10-09T00:00:31Z'), 30);
  assert.equal(state.locations.get('loc-a').data_status, 'stale');
});

test('legacy invalid replay preserves a known eligible reading and cannot revive stale data', () => {
  const state = applySnapshot(initialState(), snapshot());
  applyEvent(state, event('location.status_changed', {measured_at:'2026-10-09T00:00:00Z',
    measurement_value:55, measurement_type:'spl_z_leq', weighting:'Z', calibration_status:'calibrated'}), `${epoch}:1`);
  ageData(state, Date.parse('2026-10-09T00:00:31Z'), 30);
  applyEvent(state, event('location.status_changed', {event_id:'evt-2', data_status:'invalid',
    measured_at:'2026-10-09T00:00:31Z', measurement_value:-1, measurement_type:'dbfs_rms',
    weighting:'none', calibration_status:'not_required', diagnostic:'measurement_type_mismatch'}), `${epoch}:2`);
  const stream = state.locations.get('loc-a').streams[0];
  assert.equal(stream.data_status,'stale');
  assert.equal(stream.measured_at,'2026-10-09T00:00:00Z');
  assert.equal(stream.measurement_value,55);
  assert.equal(stream.measurement_type,'spl_z_leq');
  assert.equal(stream.weighting,'Z');
  assert.equal(stream.calibration_status,'calibrated');
  assert.equal(stream.diagnostic,'measurement_type_mismatch');
});

test('legacy invalid replay without eligible history never invents a usable reading', () => {
  const state = applySnapshot(initialState(), snapshot());
  applyEvent(state, event('location.status_changed', {data_status:'invalid',
    measured_at:'2026-10-09T00:00:29Z', diagnostic:'quality_clipped'}), `${epoch}:1`);
  const stream = state.locations.get('loc-a').streams[0];
  assert.equal(stream.data_status,'stale');
  assert.equal(stream.measured_at,undefined);
  assert.equal(stream.measurement_value,undefined);
  assert.equal(stream.diagnostic,'quality_clipped');
});

test('valid measurement clears the previous invalid-data diagnostic', () => {
  const state = applySnapshot(initialState(), snapshot());
  applyEvent(state, event('location.status_changed', {data_status: 'invalid', diagnostic: 'quality_clipped'}), `${epoch}:1`);
  applyEvent(state, event('location.status_changed', {event_id: 'evt-2', data_status: 'fresh',
    transition_reason: 'measurement_evaluated', diagnostic: null}), `${epoch}:2`);
  assert.equal(state.locations.get('loc-a').streams[0].diagnostic, null);
});

test('a new interval or measurement type replaces an old normal stream before it ages stale', () => {
  for (const replacement of [{interval_seconds: 2}, {measurement_type: 'dbfs_rms'}]) {
    const seed = snapshot();
    seed.items[0].devices = [{id: 'device-a', enabled: true, assignment_id: 'assignment-a'}];
    const state = applySnapshot(initialState(), seed);
    applyEvent(state, event('location.status_changed', {incident_id: null, noise_status: 'normal',
      measured_at: '2026-10-09T00:00:00Z', measurement_type: 'spl_z_leq', interval_seconds: 1}), `${epoch}:1`);
    applyEvent(state, event('location.status_changed', {event_id: 'evt-2', incident_id: null,
      stream_id: 'new-stream', noise_status: 'normal', measured_at: '2026-10-09T00:00:25Z', ...replacement}), `${epoch}:2`);
    ageData(state, Date.parse('2026-10-09T00:00:31Z'), 30);
    assert.deepEqual(state.locations.get('loc-a').streams.map(stream => stream.id), ['new-stream']);
    assert.equal(state.locations.get('loc-a').data_status, 'fresh');
    // A later stale transition for an old stream must not reinstate it either.
    applyEvent(state, event('location.status_changed', {event_id: 'evt-3', incident_id: null,
      noise_status: 'normal', data_status: 'stale', measured_at: '2026-10-09T00:00:00Z'}), `${epoch}:3`);
    assert.deepEqual(state.locations.get('loc-a').streams.map(stream => stream.id), ['new-stream']);
    assert.equal(state.locations.get('loc-a').data_status, 'fresh');
  }
});

test('old streams with unresolved incidents survive until closure, independently of newer streams', () => {
  const seed = snapshot();
  seed.items[0].devices = [{id: 'device-a', enabled: true, assignment_id: 'assignment-a'}];
  const state = applySnapshot(initialState(), seed);
  applyEvent(state, event('incident.opened', {measured_at: '2026-10-09T00:00:00Z'}), `${epoch}:1`);
  applyEvent(state, event('location.status_changed', {event_id: 'evt-2', stream_id: 'new-stream',
    incident_id: null, noise_status: 'normal', measured_at: '2026-10-09T00:00:25Z'}), `${epoch}:2`);
  ageData(state, Date.parse('2026-10-09T00:00:31Z'), 30);
  assert.equal(state.locations.get('loc-a').streams.length, 2);
  assert.equal(state.locations.get('loc-a').noise_status, 'excessive');
  assert.equal(state.locations.get('loc-a').data_status, 'stale');
  applyEvent(state, event('incident.closed', {event_id: 'evt-3', incident_status: 'closed',
    transition_reason: 'threshold_changed'}), `${epoch}:3`);
  assert.equal(state.incidents.has('incident-a'), false);
  assert.deepEqual(state.locations.get('loc-a').streams.map(stream => stream.id), ['new-stream']);
  assert.equal(state.locations.get('loc-a').noise_status, 'normal');
  assert.equal(state.locations.get('loc-a').data_status, 'fresh');
});

test('device removal retires normal data and keeps only unresolved old-location evidence', () => {
  for (const hasIncident of [false, true]) {
    const seed = snapshot();
    seed.items[0].devices = [{id: 'device-a', enabled: true, assignment_id: 'assignment-a'}];
    const state = applySnapshot(initialState(), seed);
    applyEvent(state, event(hasIncident ? 'incident.opened' : 'location.status_changed', {
      incident_id: hasIncident ? 'incident-a' : null, noise_status: hasIncident ? 'excessive' : 'normal',
      measured_at: '2026-10-09T00:00:00Z'}), `${epoch}:1`);
    applyEvent(state, event('location.status_changed', {event_id: 'evt-2', stream_id: null,
      incident_id: null, transition_reason: 'device_removed'}), `${epoch}:2`);
    assert.equal(state.locations.get('loc-a').devices.length, 0);
    assert.equal(state.locations.get('loc-a').streams.length, hasIncident ? 1 : 0);
    if (hasIncident) {
      // The closure belongs to the former location but carries the new chunk's
      // assignment. It must not put the device back on the old location roster.
      applyEvent(state, event('incident.closed', {event_id: 'evt-3', incident_status: 'closed',
        assignment_id: 'assignment-b', transition_reason: 'device_reassigned'}), `${epoch}:3`);
      assert.equal(state.locations.get('loc-a').devices.length, 0);
      assert.equal(state.locations.get('loc-a').streams.length, 0);
      assert.equal(state.incidents.size, 0);
    }
  }
});

test('location reconfiguration installs the replacement assignment and retires its old normal stream', () => {
  const seed = snapshot();
  seed.items[0].devices = [{id: 'device-a', enabled: true, assignment_id: 'assignment-a'}];
  const state = applySnapshot(initialState(), seed);
  applyEvent(state, event('location.status_changed', {incident_id: null, noise_status: 'normal'}), `${epoch}:1`);
  applyEvent(state, event('location.status_changed', {event_id: 'evt-2', stream_id: null,
    incident_id: null, assignment_id: 'replacement-assignment', transition_reason: 'location_configured'}), `${epoch}:2`);
  assert.equal(state.locations.get('loc-a').streams.length, 0);
  assert.equal(state.locations.get('loc-a').devices[0].assignment_id, 'replacement-assignment');
  applyEvent(state, event('location.status_changed', {event_id: 'evt-3', stream_id: 'replacement-stream',
    incident_id: null, noise_status: 'normal', assignment_id: 'replacement-assignment'}), `${epoch}:3`);
  assert.equal(state.locations.get('loc-a').data_status, 'fresh');
});

test('a first measurement can display a location whose roster has not arrived yet', () => {
  const state = applySnapshot(initialState(), snapshot());
  applyEvent(state, event('location.status_changed', {location_id: 'loc-c', incident_id: null,
    latitude: 12.97, longitude: 77.59, noise_status: 'normal'}), `${epoch}:1`);
  assert.equal(state.locations.get('loc-c').streams.length, 1);
  assert.equal(state.locations.get('loc-c').data_status, 'fresh');
});

test('long-running monitoring keeps bounded caches and no completed incidents; old cursor replay stays rejected', () => {
  const state = applySnapshot(initialState(), snapshot());
  for (let index = 0; index < 3000; index++) {
    const id = `incident-${index}`;
    assert.equal(applyEvent(state, event('incident.opened', {event_id: `open-${index}`, incident_id: id}),
      `${epoch}:${index * 2 + 1}`).kind, 'opening');
    assert.equal(applyEvent(state, event('incident.resolved', {event_id: `resolved-${index}`, incident_id: id,
      incident_status: 'resolved', noise_status: 'normal'}), `${epoch}:${index * 2 + 2}`).kind, 'recovery');
  }
  assert.equal(state.incidents.size, 0);
  assert.ok(state.seen.size <= 2048);
  assert.ok(state.notified.size <= 2048);
  assert.equal(state.feed.length, 100);
  assert.equal(state.seen.has('open-0'), false);
  assert.equal(state.notified.has('incident-0'), false);
  assert.equal(applyEvent(state, event('incident.opened', {event_id: 'open-0', incident_id: 'incident-0'}), `${epoch}:1`), null);
  assert.equal(state.incidents.size, 0);
  assert.equal(state.cursor, `${epoch}:6000`);
});

test('fresh snapshots release tracking history and suppress openings for every existing incident', () => {
  const state = applySnapshot(initialState(), snapshot());
  applyEvent(state, event('incident.opened'), `${epoch}:1`);
  const next = snapshot();
  next.cursor = `${epoch}:10000`;
  next.incidents = Array.from({length: 2100}, (_, index) => ({id: `existing-${index}`,
    location_id: 'loc-a', stream_id: `existing-stream-${index}`, status: 'active'}));
  next.incidents.push({id: 'already-completed', location_id: 'loc-a', status: 'resolved'});
  applySnapshot(state, next);
  assert.equal(state.seen.size, 0);
  assert.equal(state.feed.length, 0);
  assert.ok(state.notified.size <= 2048);
  assert.equal(state.incidents.size, 2100);
  assert.equal(state.notified.has('existing-0'), false);
  // Even more open incidents than the recent-ID cache does not repeat popups.
  assert.equal(applyEvent(state, event('incident.opened', {event_id: 'existing-opening',
    incident_id: 'existing-0', stream_id: 'existing-stream-0'}), `${epoch}:10001`), null);
  assert.equal(applyEvent(state, event('incident.opened'), `${epoch}:1`), null);
  assert.equal(state.incidents.has('incident-a'), false);
});

test('events from a different history cannot replace a snapshot cursor or replay old alerts', () => {
  const state = applySnapshot(initialState(), snapshot());
  const otherEpoch = 'adc08ac7-1e7f-4a51-8019-aedc64c01e14';
  assert.equal(applyEvent(state, event('incident.opened'), `${otherEpoch}:1`), null);
  assert.equal(state.cursor, `${epoch}:0`);
  assert.equal(state.incidents.size, 0);
  applySnapshot(state, {...snapshot(), cursor: `${otherEpoch}:0`});
  assert.equal(applyEvent(state, event('incident.opened'), `${otherEpoch}:1`).kind, 'opening');
});

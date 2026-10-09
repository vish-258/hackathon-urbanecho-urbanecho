import test from 'node:test';
import assert from 'node:assert/strict';
import {condition, latest, formatLevel, freshnessLabel, deviceReporting} from '../app/static/application/ui.mjs';
import {initialState, applySnapshot, applyEvent, ageData} from '../app/static/state.mjs';

const epoch = 'c38f4b87-b77c-4a9c-b7d9-d91da94bfb17';
const normal = () => ({id:'location-a', name:'Simulated A', noise_status:'normal', data_status:'fresh',
  unresolved_incident_ids:[], devices:[{id:'device-a', assignment_id:'assignment-a', enabled:true}],
  streams:[{id:'stream-a', device_id:'device-a', assignment_id:'assignment-a', noise_status:'normal',
    data_status:'fresh', measured_at:'2026-10-09T12:00:00Z', measurement_value:55, measurement_type:'spl_z_leq'}]});
const snapshot = () => ({cursor:`${epoch}:0`, items:[normal()], incidents:[]});
const event = (type, extra={}) => ({event_id:'event-a',event_type:type,incident_id:'incident-a',
  location_id:'location-a',device_id:'device-a',assignment_id:'assignment-a',stream_id:'stream-a',
  incident_status:'active',noise_status:'excessive',data_status:'fresh',measurement_value:75,
  measurement_type:'spl_z_leq',measured_at:'2026-10-09T12:00:01Z',threshold_value:60,...extra});

test('a station without readings cannot be presented as normal', () => {
  const status=condition({streams:[],unresolved_incident_ids:[],data_status:'unknown',noise_status:'unknown'});
  assert.equal(status.label,'No readings yet');
  assert.equal(status.tone,'stale');
  assert.equal(formatLevel(null,'spl_z_leq'),'No eligible reading');
});

test('a previously normal station turns gray when data gets old', () => {
  const location=normal();
  location.data_status='stale';
  location.streams[0].data_status='stale';
  assert.equal(condition(location).label,'No recent data');
  assert.equal(condition(location).tone,'stale');
});

test('a fresh device alongside a silent replay device shows partial reporting, not no recent data', () => {
  const location=normal();
  location.devices.push({id:'device-b',assignment_id:'assignment-b',enabled:true});
  location.data_status='stale';
  assert.equal(freshnessLabel(location),'Some devices not reporting');
  assert.equal(condition(location).label,'Some devices not reporting');
  assert.equal(condition(location).tone,'stale');
  Object.assign(location,{noise_status:'excessive',unresolved_incident_ids:['incident-a']});
  assert.equal(condition(location).label,'Excessive noise');
  assert.equal(condition(location).incident,true);
  assert.equal(freshnessLabel(location),'Some devices not reporting');
  location.streams[0].data_status='stale';
  assert.equal(freshnessLabel(location),'No recent data');
});

test('one unusable reading keeps a device reporting but flags attention; staleness does not', () => {
  const location=normal(), device={id:'device-a', current_assignment_id:'assignment-a', enabled:true};
  assert.deepEqual(deviceReporting(device,location),{reporting:true,attention:false});
  location.streams[0].data_status='invalid';
  assert.deepEqual(deviceReporting(device,location),{reporting:true,attention:true});
  location.streams[0].data_status='stale';
  assert.deepEqual(deviceReporting(device,location),{reporting:false,attention:false});
  location.streams[0].data_status='invalid';
  assert.equal(deviceReporting({...device,enabled:false},location).reporting,false);
  assert.equal(deviceReporting({...device,current_assignment_id:'new-assignment'},location).reporting,false);
  assert.equal(deviceReporting(device,undefined).reporting,false);
});

test('an unusable latest sample with usable history still counts toward partial location reporting', () => {
  const location=normal();
  location.devices.push({id:'device-b',assignment_id:'assignment-b',enabled:true});
  location.data_status='stale';
  location.streams[0].data_status='invalid';
  assert.equal(freshnessLabel(location),'Some devices not reporting');
  assert.equal(condition(location).label,'Some devices not reporting');
  location.streams[0].data_status='stale';
  assert.equal(freshnessLabel(location),'No recent data');
});

test('clipped observations do not extend eligible freshness, replace units, or resolve an incident', () => {
  const state=applySnapshot(initialState(),snapshot());
  const device={id:'device-a',current_assignment_id:'assignment-a',enabled:true};
  const good={measurement_value:75,measurement_type:'spl_z_leq',weighting:'Z',interval_seconds:1,
    calibration_status:'calibrated',measured_at:'2026-10-09T12:00:00Z',received_at:'2026-10-09T12:00:01Z'};
  applyEvent(state,event('incident.opened',{...good,eligible_reading:good}),`${epoch}:1`);
  applyEvent(state,event('location.status_changed',{event_id:'event-b',data_status:'invalid',
    measured_at:'2026-10-09T12:00:29Z',measurement_value:-1,measurement_type:'dbfs_rms',
    weighting:'none',calibration_status:'not_required',diagnostic:'quality_clipped',
    eligible_reading:good}),`${epoch}:2`);
  const location=state.locations.get('location-a'), stream=location.streams[0];
  for (const [key,value] of Object.entries(good)) assert.equal(stream[key],value,key);
  assert.equal(stream.diagnostic,'quality_clipped');
  ageData(state,Date.parse('2026-10-09T12:00:30Z'),30);
  assert.deepEqual(deviceReporting(device,location),{reporting:true,attention:true});
  ageData(state,Date.parse('2026-10-09T12:00:31Z'),30);
  assert.deepEqual(deviceReporting(device,location),{reporting:false,attention:false});
  assert.equal(freshnessLabel(location),'No recent data');
  assert.equal(state.incidents.get('incident-a').status,'active');
  const normalReading={...good,measurement_value:55,measured_at:'2026-10-09T12:00:32Z'};
  applyEvent(state,event('incident.updated',{event_id:'event-c',incident_status:'recovering',
    noise_status:'recovering',diagnostic:null,recovery_streak:1,eligible_reading:normalReading}),`${epoch}:3`);
  assert.deepEqual(deviceReporting(device,location),{reporting:true,attention:false});
  assert.equal(stream.measurement_value,55);
  assert.equal(stream.measured_at,normalReading.measured_at);
  assert.equal(stream.diagnostic,null);
  assert.equal(state.incidents.get('incident-a').status,'recovering');
});

test('disabled devices and former assignments cannot make a stale location appear partly reporting', () => {
  const location=normal();
  location.data_status='stale';
  location.devices[0].enabled=false;
  assert.equal(freshnessLabel(location),'No recent data');
  location.devices[0].enabled=true;
  location.devices[0].assignment_id='new-assignment';
  assert.equal(freshnessLabel(location),'No recent data');
});

test('stale excessive data retains both the unresolved and freshness conditions', () => {
  const location=normal();
  Object.assign(location,{data_status:'stale',noise_status:'excessive',unresolved_incident_ids:['incident-a']});
  assert.deepEqual(condition(location),{label:'Excessive noise',tone:'stale',incident:true,stale:true});
});

test('recovery remains an unresolved incident until the server confirms it', () => {
  const location=normal();
  Object.assign(location,{noise_status:'recovering',unresolved_incident_ids:['incident-a']});
  assert.equal(condition(location).label,'Recovering');
  assert.equal(condition(location).incident,true);
  assert.equal(condition(location).tone,'danger');
});

test('recent invalid readings remain distinct from stale data during an incident', () => {
  const location=normal();
  Object.assign(location,{data_status:'invalid',noise_status:'excessive',unresolved_incident_ids:['incident-a']});
  const status=condition(location);
  assert.equal(status.incident,true);
  assert.equal(status.invalid,true);
  assert.equal(status.stale,false);
  assert.equal(status.label,'Excessive noise');
  assert.equal(status.tone,'stale');
});

test('recent invalid readings without an incident request attention, not reconnection', () => {
  const location=normal();
  location.data_status='invalid';
  const status=condition(location);
  assert.equal(status.label,'Reading needs attention');
  assert.equal(status.invalid,true);
  assert.equal(status.stale,false);
});

test('latest reading follows capture time across devices without changing stream order', () => {
  const location=normal();
  const newer={id:'stream-b',device_id:'device-b',measured_at:'2026-10-09T12:00:05Z',measurement_value:-12.5,measurement_type:'dbfs_rms'};
  location.streams.push(newer,{id:'unmeasured'});
  const originalOrder=location.streams.map(s=>s.id);
  assert.equal(latest(location),newer);
  assert.deepEqual(location.streams.map(s=>s.id),originalOrder);
  assert.equal(formatLevel(latest(location).measurement_value,latest(location).measurement_type),'-12.50 dBFS');
});

test('SPL display preserves Z weighting and does not imply A weighting', () => {
  assert.equal(formatLevel(75,'spl_z_leq'),'75.00 dB SPL (Z)');
  assert.equal(formatLevel('75','spl_z_leq'),'No eligible reading');
});

test('live repeated excessive readings update one incident and notify only once', () => {
  const state=applySnapshot(initialState(),snapshot());
  const opening=event('incident.opened',{peak_db:75});
  assert.equal(applyEvent(state,opening,`${epoch}:1`).kind,'opening');
  assert.equal(applyEvent(state,opening,`${epoch}:1`),null);
  assert.equal(applyEvent(state,event('incident.updated',{event_id:'event-b',measurement_value:80,peak_db:80}),`${epoch}:2`),null);
  assert.equal(state.incidents.size,1);
  assert.equal(state.incidents.get('incident-a').peak_db,80);
  assert.equal(condition(state.locations.get('location-a')).label,'Excessive noise');
});

test('reconnect replay cannot turn a resolved incident back into a live alert', () => {
  const state=applySnapshot(initialState(),snapshot());
  applyEvent(state,event('incident.opened'),`${epoch}:1`);
  applyEvent(state,event('incident.resolved',{event_id:'event-b',incident_status:'resolved',noise_status:'normal',measurement_value:55}),`${epoch}:2`);
  assert.equal(applyEvent(state,event('incident.opened',{event_id:'old-replayed-id'}),`${epoch}:1`),null);
  assert.equal(state.incidents.has('incident-a'),false);
  assert.equal(condition(state.locations.get('location-a')).label,'Within threshold');
});

test('disconnected browser ages data without silently resolving an incident', () => {
  const state=applySnapshot(initialState(),snapshot());
  applyEvent(state,event('incident.opened'),`${epoch}:1`);
  ageData(state,Date.parse('2026-10-09T12:01:00Z'),30);
  assert.equal(condition(state.locations.get('location-a')).stale,true);
  assert.equal(condition(state.locations.get('location-a')).incident,true);
  assert.equal(state.incidents.get('incident-a').status,'active');
});

// The report date follows the selected location, never the browser's timezone.
const {localReportingDate, formatDailyCoverage} = await import('../app/static/application/views.mjs');

test('daily report defaults follow a location across UTC midnight', () => {
  const instant = new Date('2026-10-08T20:00:00Z');
  assert.equal(localReportingDate('Asia/Kolkata', 0, instant), '2026-10-09');
  assert.equal(localReportingDate('Asia/Kolkata', -1, instant), '2026-10-08');
  assert.equal(localReportingDate('America/Los_Angeles', -1, instant), '2026-10-07');
});

test('daily report calendar subtraction survives short and long DST days', () => {
  assert.equal(localReportingDate('America/New_York', -1, new Date('2026-03-09T04:10:00Z')), '2026-03-08');
  assert.equal(localReportingDate('America/New_York', -1, new Date('2026-11-02T05:10:00Z')), '2026-11-01');
  assert.equal(localReportingDate('Pacific/Auckland', -1, new Date('2026-01-01T00:00:00Z')), '2025-12-31');
});

test('daily coverage cannot round short samples to zero or partial days to complete', () => {
  assert.notEqual(formatDailyCoverage(100 / 86400), '0%');
  assert.equal(formatDailyCoverage(.00001), '<0.0001%');
  assert.equal(formatDailyCoverage(99.99999), '>99.9999%');
  assert.equal(formatDailyCoverage(100), '100%');
  assert.equal(formatDailyCoverage(0), '0%');
  assert.equal(formatDailyCoverage(null), 'Unavailable');
});

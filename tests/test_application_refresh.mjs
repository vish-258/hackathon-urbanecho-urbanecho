import test from 'node:test';
import assert from 'node:assert/strict';
import { addLiveEvent, liveChanges, mergeChanges, locationChanges, coalesceAsync } from '../app/static/application/refresh.mjs';
import { deviceLabel } from '../app/static/application/ui.mjs';
import { mountView } from '../app/static/application/views.mjs';

const reading = location => ({ event_type: 'location.status_changed', location_id: location, measurement_id: 'measurement-1' });
const batch = event => addLiveEvent(liveChanges(), event);
const settle = async () => { for (let i = 0; i < 30; i++) await Promise.resolve(); };

test('a burst retains affected identities and does not refresh another location', () => {
  const changes = liveChanges();
  for (let i = 0; i < 10000; i++) addLiveEvent(changes, { ...reading('other'), measurement_id: `m-${i}` });
  assert.equal(changes.locations.size, 1);
  assert.equal(changes.measurements.size, 1);
  assert.deepEqual(locationChanges(changes, 'selected'), { summary: false, history: false, supporting: false, incidents: false });
  assert.deepEqual(locationChanges(changes, 'other'), { summary: true, history: true, supporting: false, incidents: false });
  addLiveEvent(changes, { event_type: 'incident.opened', location_id: 'selected', incident_id: 'incident-1' });
  assert.equal(locationChanges(changes, 'selected').incidents, true);
  assert.equal(locationChanges(changes, 'selected').history, false);
  const configuration = batch({ location_id: 'selected', transition_reason: 'threshold_configured' });
  assert.equal(locationChanges(configuration, 'selected').supporting, false);
  configuration.metadata = true;
  mergeChanges(changes, configuration);
  assert.equal(locationChanges(changes, 'selected').supporting, true);
  assert.equal(locationChanges(changes, 'other').supporting, false);
});

test('overlapping refresh requests run once at a time with one trailing refresh', async () => {
  let calls = 0, active = 0, maximum = 0, release;
  const request = coalesceAsync(async () => {
    calls++; active++; maximum = Math.max(maximum, active);
    if (calls === 1) await new Promise(resolve => { release = resolve; });
    active--;
  });
  const first = request();
  await settle();
  for (let i = 0; i < 100; i++) request();
  assert.equal(calls, 1);
  release(); await first;
  assert.equal(calls, 2);
  assert.equal(maximum, 1);
});

test('leaving a route cancels a queued refresh and failures do not wedge the queue', async () => {
  const controller = new AbortController();
  let calls = 0, release;
  const request = coalesceAsync(async () => { calls++; await new Promise(resolve => { release = resolve; }); }, controller.signal);
  const first = request(); await settle(); request(); controller.abort(); release(); await first;
  await request(); assert.equal(calls, 1);
  let failures = 0;
  const retry = coalesceAsync(async () => { if (!failures++) throw new Error('temporary failure'); });
  await assert.rejects(retry(), /temporary failure/);
  await retry(); assert.equal(failures, 2);
});

test('readable device codes take precedence while unlabelled historical devices remain identifiable', () => {
  const devices = [{ id: 'uuid-12345', external_id: 'UE-001' }];
  assert.equal(deviceLabel({ device_id: 'uuid-12345', device_external_id: 'UE-002' }, devices), 'UE-002');
  assert.equal(deviceLabel({ device_id: 'uuid-12345' }, devices), 'UE-001');
  assert.equal(deviceLabel({ device_id: 'uuid-12345' }), 'uuid-123');
});

// A small DOM facade lets these tests exercise the actual view's network and
// control flow without a browser, server or database. Layout remains a browser check.
class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.attributes = {}; this.listeners = {}; this.className = ''; this.value = ''; this._text = ''; }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child.textContent ?? String(child)).join(' '); }
  append(...children) { this.children.push(...children); if (this.tag === 'select' && !this.value && children[0]) this.value = children[0].value; }
  replaceChildren(...children) { this._text = ''; this.children = []; this.append(...children); }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  removeAttribute(name) { delete this.attributes[name]; if (name === 'src') this.src = ''; }
  focus() { document.activeElement = this; }
  addEventListener(name, handler) { (this.listeners[name] ||= []).push(handler); }
  get lastChild() { return this.children.at(-1); }
  all() { return this.children.flatMap(child => child instanceof Element ? [child, ...child.all()] : []); }
  querySelectorAll(selector) { return this.all().filter(node => node.tag === selector); }
  querySelector(selector) {
    const [first, ...rest] = selector.split(' '), [tag, className] = first.split('.');
    const found = this.all().find(node => (!tag || node.tag === tag) && (!className || node.className.split(' ').includes(className)));
    return rest.length ? found?.querySelector(rest.join(' ')) : found;
  }
  async click() { for (const handler of this.listeners.click || []) await handler({ button: 0 }); }
}

function useDOM(t) {
  const original = globalThis.document;
  globalThis.document = { createElement: tag => new Element(tag), createElementNS: (_, tag) => new Element(tag) };
  t.after(() => { globalThis.document = original; });
  t.mock.timers.enable({ apis: ['setTimeout'] });
}

function fixture() {
  const location = { id: 'selected', name: 'Test location', latitude: 12, longitude: 77, timezone: 'UTC', threshold_type: 'spl_z_leq' };
  const device = { id: 'device-1', external_id: 'UE-001', location_id: location.id, enabled: true, current_assignment_id: 'assignment', microphone_model: 'INMP441' };
  const listeners = new Set(), requests = [], controller = new AbortController();
  const ctx = { locations: [location], devices: [device], signal: controller.signal, navigate() {},
    state: { locations: new Map([[location.id, { devices: [{ id: device.id }], streams: [], noise_status: 'unknown' }]]) },
    onLive(handler) { listeners.add(handler); return () => listeners.delete(handler); },
    async api(path) {
      requests.push(path);
      const url = new URL(path, 'http://localhost');
      if (url.pathname === '/locations/selected') return { ...location };
      if (url.pathname === '/locations/selected/threshold') return { current: { threshold_type: 'spl_z_leq', threshold_value: 60, interval_seconds: 1, revision: 1 } };
      if (url.pathname === '/devices/device-1') return { ...device };
      if (url.pathname === '/devices') return { items: [{ ...device }], total: 1 };
      if (url.pathname === '/recordings') return { items: [], total: 0 };
      if (url.pathname.endsWith('/threshold/versions')) return { items: [], total: 0 };
      if (url.pathname === '/measurements') {
        const offset = Number(url.searchParams.get('offset'));
        return { total: 500, items: Array.from({ length: Math.min(200, 500 - offset) }, (_, index) => ({ id: `reading-${offset + index}`, device_id: device.id, location_id: location.id, measured_at: new Date(Date.now() - (offset + index) * 1000).toISOString(), measurement_type: 'spl_z_leq', quality_status: 'invalid', value_db: null })) };
      }
      if (url.pathname === '/daily-summaries') return { summaries: [], reporting_date: '2026-10-08', timezone: 'UTC' };
      if (url.pathname === '/incidents') return { items: [], total: 0 };
      throw new Error(`Unexpected request: ${path}`);
    },
  };
  return { ctx, requests, emit: event => { const changes = batch(event); for (const handler of listeners) handler(changes); }, listeners, controller };
}

test('incident history keeps horizontal position and the focused record through live updates', async t => {
  useDOM(t);
  const oldLocation = globalThis.location;
  globalThis.location = { hash: '#incidents' };
  t.after(() => { globalThis.location = oldLocation; });
  const { ctx, emit } = fixture();
  const record = id => ({ id, location_id: 'selected', device_id: 'device-1', status: 'active', started_at: '2026-10-10T00:00:00Z', threshold_type: 'dbfs_rms', threshold_value: -30, latest_db: -25, peak_db: -25 });
  let items = [record('first')];
  ctx.api = async () => ({ items, total: items.length });
  const root = new Element('main');
  const cleanup = await mountView(root, { page: 'incidents' }, ctx);
  t.after(cleanup);
  const original = root.querySelector('.view-table-wrap');
  assert.ok(original);
  original.scrollLeft = 340;
  original.querySelectorAll('a').at(-1).focus();
  const refresh = async () => { emit({ event_type: 'incident.updated', location_id: 'selected', incident_id: 'first' }); t.mock.timers.tick(1100); await settle(); };
  items = [record('newer'), record('first')];
  await refresh();
  assert.equal(root.querySelector('.view-table-wrap').scrollLeft, 340);
  assert.equal(document.activeElement.href, '#/incident/first');
  assert.equal(document.activeElement.textContent, 'View incident');
  items = [record('newer')];
  await refresh();
  assert.equal(document.activeElement, root.querySelector('.view-table-wrap'));
  const search = root.querySelector('input'); search.focus();
  await refresh();
  assert.equal(document.activeElement, search, 'a live update does not steal filter focus');
});

test('actual location view refreshes only its latest page and relevant panels', async t => {
  useDOM(t);
  const { ctx, requests, emit, listeners } = fixture();
  const root = new Element('main');
  const cleanup = await mountView(root, { page: 'location', id: 'selected' }, ctx);
  t.after(cleanup);
  assert.equal(root.all().some(node => node.className === 'error-box'), false);
  const count = path => requests.filter(item => item.split('?')[0] === path).length;
  assert.equal(count('/measurements'), 1);
  const nonContactRequests = () => requests.filter(path => !path.startsWith('/devices?')).length;
  const initial = nonContactRequests();
  emit(reading('elsewhere')); t.mock.timers.tick(1100); await settle();
  assert.equal(nonContactRequests(), initial);
  for (let i = 0; i < 100; i++) emit(reading('selected'));
  t.mock.timers.tick(1100); await settle();
  assert.equal(count('/measurements'), 2);
  assert.equal(count('/devices/device-1'), 1);
  assert.equal(count('/locations/selected/threshold'), 1);
  assert.equal(count('/incidents'), 1);

  const older = root.all().find(node => node.tag === 'button' && /^Load .*older readings/.test(node.textContent));
  await older.click();
  assert.equal(count('/measurements'), 4); // Explicitly requested two saved pages.
  const beforeLive = nonContactRequests();
  emit(reading('selected')); t.mock.timers.tick(1100); await settle();
  assert.equal(nonContactRequests(), beforeLive);
  assert.match(root.textContent, /New readings are available/);
  const refresh = root.all().find(node => node.tag === 'button' && node.textContent === 'New readings · Refresh latest');
  await refresh.click(); await settle();
  assert.equal(count('/measurements'), 5);
  emit(reading('selected')); t.mock.timers.tick(1100); await settle();
  assert.equal(count('/measurements'), 6);

  emit({ event_type: 'incident.opened', location_id: 'selected', incident_id: 'incident-1' });
  t.mock.timers.tick(1100); await settle();
  assert.equal(count('/incidents'), 2);
  assert.equal(count('/devices/device-1'), 1);
  assert.equal(count('/locations/selected/threshold'), 1);
  const configuration = batch({ location_id: 'selected', transition_reason: 'threshold_configured' });
  configuration.metadata = true;
  ctx.locations[0].name = 'Updated location';
  for (const handler of listeners) handler(configuration);
  t.mock.timers.tick(1100); await settle();
  assert.equal(count('/devices/device-1'), 2);
  assert.equal(count('/locations/selected/threshold'), 2);
  assert.equal(root.querySelector('h1').textContent, 'Updated location');
  cleanup(); assert.equal(listeners.size, 0);
});

test('actual view coalesces reading bursts while a history response is slow', async t => {
  useDOM(t);
  const { ctx, requests, emit } = fixture();
  const cleanup = await mountView(new Element('main'), { page: 'location', id: 'selected' }, ctx);
  t.after(cleanup);
  const api = ctx.api;
  let release, hold = true;
  ctx.api = async (path, options) => {
    const result = await api(path, options);
    if (hold && path.startsWith('/measurements?')) { hold = false; await new Promise(resolve => { release = resolve; }); }
    return result;
  };
  const count = () => requests.filter(path => path.startsWith('/measurements?')).length;
  emit(reading('selected')); t.mock.timers.tick(1100); await settle();
  assert.equal(count(), 2);
  for (let i = 0; i < 100; i++) emit(reading('selected'));
  t.mock.timers.tick(10000); await settle();
  assert.equal(count(), 2);
  release(); await settle(); t.mock.timers.tick(1100); await settle();
  assert.equal(count(), 3);
});

test('location device contact updates separately from missing calibration without resetting history controls', async t => {
  useDOM(t);
  const { ctx, requests } = fixture();
  let now = Date.parse('2026-10-10T00:00:00Z'), contact = new Date(now).toISOString();
  ctx.serverNow = () => now;
  const api = ctx.api;
  ctx.api = async (path, options) => {
    if (path.startsWith('/devices?')) { requests.push(path); return { items: [{ ...ctx.devices[0], last_contact_at: contact }], total: 1 }; }
    return api(path, options);
  };
  const root = new Element('main');
  const cleanup = await mountView(root, { page: 'location', id: 'selected' }, ctx);
  t.after(cleanup); await settle();
  const panel = root.all().find(node => node.className === 'view-device-row');
  assert.match(panel.textContent, /Connection Connected/);
  assert.match(panel.textContent, /Readings Calibration required/);
  assert.doesNotMatch(panel.textContent, /Last usable reading received: Not received/);
  assert.match(root.textContent, /Noise condition Not evaluated/);
  const chart = root.querySelector('div.chart-area');
  const measurement = root.all().find(node => node.tag === 'select' && node.attributes['aria-label'] === 'Measurement type');
  measurement.value = 'dbfs_rms';
  const historyRequests = () => requests.filter(path => path.startsWith('/measurements?')).length;
  const previousHistory = historyRequests();
  now += 2000; contact = new Date(now).toISOString();
  t.mock.timers.tick(2000); await settle();
  const updated = root.all().find(node => node.className === 'view-device-row');
  assert.equal(updated.all().find(node => node.title === contact)?.title, contact);
  assert.match(updated.textContent, /Connection Connected/);
  assert.match(updated.textContent, /Readings Calibration required/);
  assert.equal(root.querySelector('div.chart-area'), chart);
  assert.equal(measurement.value, 'dbfs_rms');
  assert.equal(historyRequests(), previousHistory);
  cleanup();
  const stoppedRequests = requests.length, stoppedText = root.textContent;
  now += 60000; t.mock.timers.tick(60000); await settle();
  assert.equal(requests.length, stoppedRequests);
  assert.equal(root.textContent, stoppedText);
});

test('location contact ages out independently and leaving the route aborts a pending contact poll', async t => {
  useDOM(t);
  const { ctx, requests } = fixture();
  let now = Date.parse('2026-10-10T00:00:00Z'), polls = 0, release, pendingSignal;
  const contact = new Date(now).toISOString();
  ctx.serverNow = () => now;
  const api = ctx.api;
  ctx.api = async (path, options) => {
    if (path.startsWith('/devices?')) {
      requests.push(path); polls++;
      if (polls === 2) { pendingSignal = options.signal; await new Promise(resolve => { release = resolve; }); }
      return { items: [{ ...ctx.devices[0], last_contact_at: polls === 1 ? contact : new Date(now).toISOString() }], total: 1 };
    }
    return api(path, options);
  };
  const root = new Element('main');
  const cleanup = await mountView(root, { page: 'location', id: 'selected' }, ctx);
  t.after(cleanup); await settle();
  assert.match(root.textContent, /Connection Connected/);
  now += 31000; t.mock.timers.tick(1000); await settle();
  assert.match(root.textContent, /Connection No recent contact/);
  assert.match(root.textContent, /Readings Calibration required/);
  t.mock.timers.tick(1000); await settle();
  assert.equal(polls, 2);
  cleanup();
  assert.equal(pendingSignal.aborted, true);
  const saved = root.textContent;
  release(); await settle(); t.mock.timers.tick(10000); await settle();
  assert.equal(root.textContent, saved);
  assert.equal(polls, 2);
});

test('incident details show device code and UUID and ignore unrelated live readings', async t => {
  useDOM(t);
  const { ctx, requests, emit } = fixture();
  ctx.api = async path => {
    requests.push(path);
    if (path.endsWith('/analysis')) return { status: 'completed', provisional: false, audio: { available: false }, classification: { status: 'completed', primary_category: 'other', confidence_status: 'no_usable_audio' } };
    if (path.startsWith('/incidents/incident-1/measurements')) return { items: [], total: 0 };
    assert.equal(path, '/incidents/incident-1');
    return { id: 'incident-1', location_id: 'selected', device_id: 'device-1', device_external_id: 'UE-001', location_snapshot: { name: 'Test location', timezone: 'UTC' }, status: 'active', peak_db: 75, latest_db: 72, threshold_value: 60, threshold_type: 'spl_z_leq', breach_count: 2, started_at: '2026-10-09T12:00:00Z' };
  };
  const root = new Element('main');
  const cleanup = await mountView(root, { page: 'incident', id: 'incident-1' }, ctx);
  t.after(cleanup);
  assert.match(root.textContent, /Device code UE-001/);
  assert.match(root.textContent, /Internal device ID device-1/);
  assert.equal(requests.length, 3);
  emit(reading('selected'));
  emit({ event_type: 'incident.updated', location_id: 'selected', incident_id: 'incident-other' });
  t.mock.timers.tick(1100); await settle();
  assert.equal(requests.length, 3);
  emit({ event_type: 'incident.updated', location_id: 'selected', incident_id: 'incident-1' });
  t.mock.timers.tick(1100); await settle();
  assert.equal(requests.length, 5);
});

test('location recordings remain playable when no sound measurements are eligible', async t => {
  useDOM(t);
  const { ctx } = fixture(), files = [], api = ctx.api;
  ctx.api = async (path, options) => path.startsWith('/recordings/saved-audio/file?') ? (files.push(path), new Blob(['wav'])) : path.startsWith('/recordings?') ? { total: 1, items: [{ id: 'saved-audio', device_id: 'device-1', captured_at: '2026-10-10T01:02:03Z', duration_seconds: 10, target_duration_seconds: 10, kind: 'recording_group', file_available: true, revision: 'revision-1', calibration_present: false, location_snapshot: { name: 'Test location' }, status: 'ready' }] } : api(path, options);
  ctx.audioFile = async id => { files.push(id); return new Blob(['wav'], { type: 'audio/wav' }); };
  const root = new Element('main');
  const cleanup = await mountView(root, { page: 'location', id: 'selected' }, ctx);
  t.after(cleanup);
  assert.match(root.textContent, /No eligible readings in this range/);
  assert.match(root.textContent, /Uncalibrated audio/);
  assert.equal(files.length, 0);
  const listen = root.all().find(node => node.tag === 'button' && node.textContent === 'Listen' && !node.disabled);
  await listen.click();
  assert.deepEqual(files, ['/recordings/saved-audio/file?revision=revision-1']);
  assert.equal(root.all().find(node => node.tag === 'audio').hidden, false);
  cleanup();
  assert.equal(root.all().find(node => node.tag === 'audio').src, '');
});

test('related readings open whole incident audio without exposing one-second clip players', async t => {
  useDOM(t);
  const { ctx, emit } = fixture(), files = [], rawFiles = [];
  const classification = { status: 'completed', primary_category: 'traffic', confidence_status: 'classified' };
  ctx.api = async (path, options) => {
    if (options?.responseType === 'blob') { files.push(path); return new Blob(['whole incident wav']); }
    if (path.endsWith('/analysis')) return { status: 'completed', provisional: false, revision: 'incident-revision', audio: { available: true, revision: 'incident-revision', duration_seconds: 18, window_duration_seconds: 18, coverage_percent: 100, context_before_seconds: 5, context_after_seconds: 5, recording_count: 18 }, classification: { status: 'completed', primary_category: 'voice', confidence_status: 'classified' } };
    if (path.endsWith('/measurements?limit=25&offset=0')) return { items: [{ id: 'measurement', audio_chunk_id: 'one-second-source', device_id: 'device-1', measured_at: '2026-10-10T01:02:03Z', captured_at: '2026-10-10T01:02:03Z', duration_seconds: 1, interval_seconds: 1, measurement_type: 'dbfs_rms', value_db: -20, quality_status: 'good', calibration_snapshot: null, classification }], total: 1 };
    assert.equal(path, '/incidents/incident-1');
    return { id: 'incident-1', location_id: 'selected', device_id: 'device-1', status: 'active', threshold_type: 'dbfs_rms', peak_db: -20, latest_db: -20, started_at: '2026-10-10T01:02:03Z', location_snapshot: { name: 'Test location', timezone: 'Asia/Kolkata' } };
  };
  ctx.audioFile = async id => { rawFiles.push(id); return new Blob(['raw wav']); };
  const root = new Element('main');
  const cleanup = await mountView(root, { page: 'incident', id: 'incident-1' }, ctx);
  t.after(cleanup);
  await settle();
  const controls = () => root.all().filter(node => node.tag === 'button' && node.textContent === 'Listen to whole incident');
  assert.equal(controls().length, 1);
  assert.equal(root.all().some(node => node.tag === 'button' && node.textContent === 'Listen'), false);
  assert.equal(root.all().some(node => node.tag === 'th' && node.textContent === 'Recording'), false);
  assert.equal(root.all().some(node => node.tag === 'audio' && node.attributes['aria-label'] === 'Saved recording playback'), false);
  assert.doesNotMatch(root.textContent, /Traffic · model estimate/);
  assert.match(root.textContent, /1s interval/); // Threshold evaluation still uses one-second measurements.
  assert.equal(files.length, 0);
  await controls()[0].click();
  const audio = root.all().find(node => node.tag === 'audio' && node.attributes['aria-label'] === 'Incident audio playback'), source = audio.src;
  assert.deepEqual(files, ['/incidents/incident-1/audio/file?revision=incident-revision']);
  assert.deepEqual(rawFiles, []);
  assert.match(source, /^blob:/);
  assert.match(root.textContent, /Voice · model estimate/);
  await controls()[0].click();
  assert.equal(audio.src, source); assert.equal(files.length, 1);
  emit({ event_type: 'incident.updated', location_id: 'selected', incident_id: 'incident-1' });
  t.mock.timers.tick(1100); await settle();
  assert.equal(root.all().find(node => node.tag === 'audio' && node.attributes['aria-label'] === 'Incident audio playback'), audio);
  assert.equal(audio.src, source); assert.equal(files.length, 1);
  await root.all().find(node => node.tag === 'button' && node.textContent === 'Refresh readings').click();
  assert.equal(audio.src, source); assert.equal(files.length, 1); assert.deepEqual(rawFiles, []);
  cleanup(); assert.equal(audio.src, '');
});


test('public daily reports allow saved refresh without generation controls or editing instructions', async t => {
  useDOM(t);
  const { ctx, requests } = fixture(); ctx.readOnly = true;
  const methods = [], api = ctx.api;
  ctx.api = (path, options) => { methods.push(options?.method || 'GET'); return api(path, options); };
  const root = new Element('main');
  const cleanup = await mountView(root, { page: 'reports', params: new URLSearchParams('location=selected&date=2026-10-08') }, ctx);
  t.after(cleanup);
  assert.equal(root.all().some(node => node.tag === 'button' && /Generate|Recalculate/.test(node.textContent)), false);
  assert.match(root.textContent, /No saved summary is available for this day/);
  assert.doesNotMatch(root.textContent, /Choose Generate|Recalculate to/);
  const refresh = root.all().find(node => node.tag === 'button' && node.textContent === 'Refresh saved results');
  assert.ok(refresh); await refresh.click();
  assert.equal(requests.filter(path => path.startsWith('/daily-summaries?')).length, 2);
  assert.deepEqual(methods, ['GET', 'GET']);
});

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
  addEventListener(name, handler) { (this.listeners[name] ||= []).push(handler); }
  get lastChild() { return this.children.at(-1); }
  all() { return this.children.flatMap(child => child instanceof Element ? [child, ...child.all()] : []); }
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
    state: { locations: new Map([[location.id, { devices: [{ id: device.id }], streams: [] }]]) },
    onLive(handler) { listeners.add(handler); return () => listeners.delete(handler); },
    async api(path) {
      requests.push(path);
      const url = new URL(path, 'http://localhost');
      if (url.pathname === '/locations/selected') return { ...location };
      if (url.pathname === '/locations/selected/threshold') return { current: { threshold_type: 'spl_z_leq', threshold_value: 60, interval_seconds: 1, revision: 1 } };
      if (url.pathname === '/devices/device-1') return { ...device };
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

test('actual location view refreshes only its latest page and relevant panels', async t => {
  useDOM(t);
  const { ctx, requests, emit, listeners } = fixture();
  const root = new Element('main');
  const cleanup = await mountView(root, { page: 'location', id: 'selected' }, ctx);
  t.after(cleanup);
  assert.equal(root.all().some(node => node.className === 'error-box'), false);
  const count = path => requests.filter(item => item.split('?')[0] === path).length;
  assert.equal(count('/measurements'), 1);
  const initial = requests.length;
  emit(reading('elsewhere')); t.mock.timers.tick(1100); await settle();
  assert.equal(requests.length, initial);
  for (let i = 0; i < 100; i++) emit(reading('selected'));
  t.mock.timers.tick(1100); await settle();
  assert.equal(count('/measurements'), 2);
  assert.equal(count('/devices/device-1'), 1);
  assert.equal(count('/locations/selected/threshold'), 1);
  assert.equal(count('/incidents'), 1);

  const older = root.all().find(node => node.tag === 'button' && /^Load .*older readings/.test(node.textContent));
  await older.click();
  assert.equal(count('/measurements'), 4); // Explicitly requested two saved pages.
  const beforeLive = requests.length;
  emit(reading('selected')); t.mock.timers.tick(1100); await settle();
  assert.equal(requests.length, beforeLive);
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

test('incident details show device code and UUID and ignore unrelated live readings', async t => {
  useDOM(t);
  const { ctx, requests, emit } = fixture();
  ctx.api = async path => {
    requests.push(path);
    if (path.startsWith('/incidents/incident-1/measurements')) return { items: [], total: 0 };
    assert.equal(path, '/incidents/incident-1');
    return { id: 'incident-1', location_id: 'selected', device_id: 'device-1', device_external_id: 'UE-001', location_snapshot: { name: 'Test location', timezone: 'UTC' }, status: 'active', peak_db: 75, latest_db: 72, threshold_value: 60, threshold_type: 'spl_z_leq', breach_count: 2, started_at: '2026-10-09T12:00:00Z' };
  };
  const root = new Element('main');
  const cleanup = await mountView(root, { page: 'incident', id: 'incident-1' }, ctx);
  t.after(cleanup);
  assert.match(root.textContent, /Device code UE-001/);
  assert.match(root.textContent, /Internal device ID device-1/);
  assert.equal(requests.length, 2);
  emit(reading('selected'));
  emit({ event_type: 'incident.updated', location_id: 'selected', incident_id: 'incident-other' });
  t.mock.timers.tick(1100); await settle();
  assert.equal(requests.length, 2);
  emit({ event_type: 'incident.updated', location_id: 'selected', incident_id: 'incident-1' });
  t.mock.timers.tick(1100); await settle();
  assert.equal(requests.length, 4);
});

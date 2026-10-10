import test from 'node:test';
import assert from 'node:assert/strict';
import { createRecordingPlayer, createRecordingsBrowser } from '../app/static/application/recordings.mjs';
import { classificationNode } from '../app/static/application/classification.mjs';

class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.listeners = {}; this.attributes = {}; this._text = ''; this.value = ''; this.pauses = 0; this.loads = 0; this.plays = 0; this.isConnected = true; }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child.textContent || '').join(' '); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { for (const child of this.all()) { child.isConnected = false; if (document.activeElement === child) document.activeElement = null; } this._text = ''; this.children = children; }
  setAttribute(name, value) { this.attributes[name] = value; }
  getAttribute(name) { return this.attributes[name]; }
  removeAttribute(name) { delete this.attributes[name]; if (name === 'src') this.src = ''; }
  addEventListener(name, handler) { (this.listeners[name] ||= []).push(handler); }
  async fire(name) { for (const handler of this.listeners[name] || []) await handler({}); }
  async click() { if (!this.disabled) await this.fire('click'); }
  all() { return this.children.flatMap(child => [child, ...child.all()]); }
  pause() { this.pauses++; }
  load() { this.loads++; }
  play() { this.plays++; }
  focus(options) { document.activeElement = this; this.focusOptions = options; }
  scrollIntoView(options) { this.scrollOptions = options; }
}

const settle = async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); };
const recording = (id = 'audio-1') => ({ id, device_id: 'device-1', captured_at: '2026-10-10T01:02:03Z', duration_seconds: 1, calibration_present: false, location_snapshot: { name: 'Room2', timezone: 'Asia/Kolkata' }, status: 'completed' });

function fixture(t) {
  const original = globalThis.document;
  const elements = [];
  globalThis.document = { activeElement: null, createElement(tag) { const node = new Element(tag); elements.push(node); return node; }, querySelectorAll: selector => elements.filter(node => node.isConnected && selector === '[data-recording-id]' && Object.hasOwn(node.attributes, 'data-recording-id')) };
  t.after(() => { globalThis.document = original; });
  const controller = new AbortController(), calls = [], created = [], revoked = [];
  const ctx = { signal: controller.signal, devices: [{ id: 'device-1', external_id: 'ESP-MAC1' }], serverNow: () => Date.parse('2026-10-10T01:02:30Z'),
    async audioFile(id, { signal }) { calls.push({ id, signal }); return new Blob(['audio'], { type: 'audio/wav' }); },
  };
  const urls = { createObjectURL(blob) { created.push(blob); return `blob:saved-${created.length}`; }, revokeObjectURL(url) { revoked.push(url); } };
  const player = createRecordingPlayer(ctx, { timezone: 'Asia/Kolkata', urls });
  t.after(() => player.dispose());
  return { ctx, player, calls, created, revoked, controller, audio: player.element.all().find(item => item.tag === 'audio') };
}

test('Listen loads only the chosen authenticated file and exposes native controls without autoplay', async t => {
  const f = fixture(t);
  assert.equal(f.calls.length, 0);
  const listen = f.player.listenButton(recording());
  assert.match(listen.attributes['aria-label'], /ESP-MAC1/);
  await listen.click();
  assert.equal(f.calls[0].id, 'audio-1');
  assert.equal(f.audio.controls, true); assert.equal(f.audio.preload, 'none');
  assert.equal(f.audio.src, 'blob:saved-1'); assert.equal(f.audio.plays, 0);
  assert.match(f.player.element.textContent, /Uncalibrated audio/);
  assert.match(f.player.element.textContent, /Press play/);
  await f.player.listen(recording('audio-2'));
  assert.deepEqual(f.revoked, ['blob:saved-1']);
  assert.equal(f.calls[0].signal.aborted, true);
  assert.equal(f.audio.src, 'blob:saved-2');
  await f.player.element.all().find(item => item.tag === 'button').click();
  assert.deepEqual(f.revoked, ['blob:saved-1', 'blob:saved-2']);
  assert.equal(f.audio.src, ''); assert.equal(f.player.element.hidden, true);
});

test('opening a recording moves focus to its player, honors reduced motion, and returns focus on close', async t => {
  const f = fixture(t), original = globalThis.matchMedia;
  globalThis.matchMedia = query => ({ matches: query === '(prefers-reduced-motion: reduce)' });
  t.after(() => { globalThis.matchMedia = original; });
  const listen = f.player.listenButton(recording()); listen.focus();
  await listen.click();
  assert.equal(document.activeElement, f.player.element);
  assert.deepEqual(f.player.element.focusOptions, { preventScroll: true });
  assert.deepEqual(f.player.element.scrollOptions, { behavior: 'auto', block: 'nearest' });
  assert.equal(f.audio.plays, 0);
  await f.player.element.all().find(item => item.tag === 'button' && item.textContent === 'Close recording').click();
  assert.equal(document.activeElement, listen);
  assert.equal(f.player.element.hidden, true);
});

test('superseded and aborted media responses cannot replace the selected recording or retain an object URL', async t => {
  const f = fixture(t); let release, oldSignal;
  f.ctx.audioFile = (id, { signal }) => id === 'old' ? new Promise(resolve => { release = resolve; oldSignal = signal; }) : Promise.resolve(new Blob(['new']));
  const old = f.player.listen(recording('old')); await settle();
  await f.player.listen(recording('new'));
  assert.equal(oldSignal.aborted, true);
  release(new Blob(['old'])); await old;
  assert.equal(f.created.length, 1); assert.equal(f.audio.src, 'blob:saved-1');
  f.controller.abort();
  assert.deepEqual(f.revoked, ['blob:saved-1']); assert.equal(f.audio.src, '');
  await f.player.listen(recording('ignored'));
  assert.equal(f.created.length, 1);
});

test('recording errors, empty audio, simulation, and missing links have clear states', async t => {
  const f = fixture(t);
  f.ctx.audioFile = async () => { throw new Error('Original audio unavailable; restore storage from backup'); };
  await f.player.listen(recording());
  assert.match(f.player.element.textContent, /Recording unavailable/); assert.equal(f.audio.hidden, true);
  f.ctx.audioFile = async () => new Blob([]);
  await f.player.listen(recording());
  assert.match(f.player.element.textContent, /no audio available/); assert.equal(f.created.length, 0);
  f.ctx.audioFile = async () => new Blob(['wav']);
  await f.player.listen({ ...recording(), calibration_version: 'SIMULATED-demo', calibration_present: true });
  assert.match(f.player.element.textContent, /SIMULATED/); assert.match(f.player.element.textContent, /Calibration profile saved/);
  await f.audio.fire('error'); assert.match(f.player.element.textContent, /browser could not play/);
  assert.equal(f.player.listenButton({ device_id: 'device-1' }).disabled, true);
});


test('recording playback does not invent capture spacing for older metadata', async t => {
  const f = fixture(t);
  for (const interval of [undefined, null, 0, -1000, NaN]) {
    await f.player.listen({ ...recording(), capture_interval_ms: interval });
    assert.match(f.player.element.textContent, /1 second/);
    assert.doesNotMatch(f.player.element.textContent, /Capture spacing/);
  }
  await f.player.listen({ ...recording(), capture_interval_ms: 1000 });
  assert.match(f.player.element.textContent, /Capture spacing: 1 second ·/);
});

test('recording list aborts in-flight loads and displays retryable list failures', async t => {
  const f = fixture(t); let release, signal;
  f.ctx.api = (_, options) => new Promise(resolve => { release = resolve; signal = options.signal; });
  const browser = createRecordingsBrowser(f.ctx, { locationId: 'room2', player: f.player });
  browser.dispose(); assert.equal(signal.aborted, true);
  const before = browser.element.textContent;
  release({ items: [recording()], total: 1 }); await browser.loaded;
  assert.equal(browser.element.textContent, before);
  f.ctx.api = async () => { throw new Error('Connection lost'); };
  const failed = createRecordingsBrowser(f.ctx, { locationId: 'room2', player: f.player });
  t.after(failed.dispose); await failed.loaded;
  assert.match(failed.element.textContent, /could not be loaded.*Connection lost/);
  assert.equal(failed.element.all().find(item => item.tag === 'button' && item.textContent === 'Refresh recordings').disabled, false);
});

const soundEstimate = category => ({ status: 'completed', primary_category: category, confidence_status: 'classified', model_version: 'YAMNet/test', mapping_version: 'v1', top_labels: [{ label: 'Road motor vehicle', score: .78123 }, { label: 'Speech', score: .12345 }], classified_at: '2026-10-10T01:03:00Z' });

test('sound estimates label every category and expose original scores without presenting probabilities', t => {
  fixture(t);
  for (const category of ['traffic', 'horn', 'siren', 'construction', 'music', 'animal', 'voice', 'other']) {
    const node = classificationNode(soundEstimate(category));
    assert.match(node.textContent, new RegExp(`${category[0].toUpperCase()}${category.slice(1)} · model estimate`));
    assert.match(node.textContent, /Road motor vehicle · score 0\.781/);
    assert.match(node.textContent, /not calibrated probabilities or sound levels/);
    assert.equal(node.all().some(item => item.tag === 'details'), true);
    assert.doesNotMatch(node.textContent, /78[%]|confidence: 0/i);
  }
  assert.match(classificationNode({ status: 'pending', queued: false }).textContent, /Waiting for sound estimate/);
  assert.match(classificationNode({ status: 'processing' }).textContent, /Analysing sound/);
  const uncertain = classificationNode({ ...soundEstimate('other'), confidence_status: 'uncertain', uncertainty_reason: 'below_score_threshold' });
  assert.match(uncertain.textContent, /Other \/ uncertain/); assert.match(uncertain.textContent, /below score threshold/);
  const failed = classificationNode({ status: 'failed', error: 'Model temporarily unavailable' });
  assert.match(failed.textContent, /Sound estimate unavailable/); assert.match(failed.textContent, /recording remains available/);
  assert.match(failed.textContent, /Model temporarily unavailable/);
});




test('switching or closing a recording aborts classification polling and discards late estimates', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const f = fixture(t); let release, signal, polls = 0;
  f.ctx.api = (_, options) => { polls++; signal = options.signal; return new Promise(resolve => { release = resolve; }); };
  await f.player.listen({ ...recording('first'), classification: { status: 'pending' } });
  t.mock.timers.tick(3000); await settle(); assert.equal(polls, 1);
  await f.player.listen({ ...recording('second'), classification: soundEstimate('horn') });
  assert.equal(signal.aborted, true);
  release(soundEstimate('traffic')); await settle();
  assert.match(f.player.element.textContent, /Horn · model estimate/);
  assert.doesNotMatch(f.player.element.textContent, /Traffic · model estimate/);
  await f.player.listen({ ...recording('third'), classification: { status: 'processing' } });
  f.controller.abort(); t.mock.timers.tick(30000); await settle();
  assert.equal(polls, 1); assert.equal(f.audio.src, '');
});

test('failed classifications can be refreshed explicitly without replaying audio', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const f = fixture(t); let polls = 0;
  f.ctx.api = async () => { polls++; return { ...soundEstimate('other'), confidence_status: 'uncertain' }; };
  await f.player.listen({ ...recording(), classification: { status: 'failed', error: 'Unavailable' } });
  const source = f.audio.src;
  t.mock.timers.tick(10000); await settle(); assert.equal(polls, 0);
  await f.player.element.all().find(item => item.tag === 'button' && item.textContent === 'Refresh sound estimate').click();
  assert.match(f.player.element.textContent, /Other \/ uncertain/);
  assert.equal(f.audio.src, source); assert.equal(f.calls.length, 1); assert.equal(polls, 1);
});


test('old measurement metadata cannot clear a completed estimate, while a new model can become pending', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const f = fixture(t);
  await f.player.listen({ ...recording(), classification: soundEstimate('siren') });
  const source = f.audio.src;
  f.player.updateClassification(recording());
  f.player.updateClassification({ ...recording(), classification: { status: 'pending', model_version: 'YAMNet/test', mapping_version: 'v1' } });
  assert.match(f.player.element.textContent, /Siren · model estimate/);
  f.player.updateClassification({ ...recording(), classification: { status: 'pending', model_version: 'YAMNet/new', mapping_version: 'v2' } });
  assert.match(f.player.element.textContent, /Waiting for sound estimate/);
  assert.equal(f.audio.src, source); assert.equal(f.calls.length, 1);
});


test('retrying a failed classification queues only that recording then updates without resetting playback', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const f = fixture(t), requests = [];
  f.ctx.api = async (path, options) => {
    requests.push({ path, ...options });
    return options.method === 'POST' ? { status: 'pending', queued: true, worker_status: 'ready' } : soundEstimate('horn');
  };
  await f.player.listen({ ...recording(), classification: { status: 'failed', worker_status: 'ready' } });
  const retry = f.player.element.all().find(item => item.tag === 'button' && item.textContent === 'Retry sound classification');
  const source = f.audio.src, pauses = f.audio.pauses;
  assert.equal(retry.hidden, false); assert.equal(retry.disabled, false);
  await retry.click();
  assert.equal(requests.length, 1); assert.equal(requests[0].path, '/audio/audio-1/classification'); assert.equal(requests[0].method, 'POST');
  assert.equal(retry.hidden, true); assert.match(f.player.element.textContent, /Waiting for sound estimate/);
  t.mock.timers.tick(3000); await settle();
  assert.match(f.player.element.textContent, /Horn · model estimate/); assert.equal(retry.hidden, true);
  assert.equal(f.audio.src, source); assert.equal(f.audio.pauses, pauses); assert.equal(f.calls.length, 1); assert.equal(f.audio.plays, 0);
  assert.equal(requests.length, 2); assert.equal(requests[1].method, undefined);
});

test('classification retry is unavailable while disabled and failures remain inline with audio intact', async t => {
  const f = fixture(t), requests = [];
  f.ctx.api = async (path, options) => { requests.push({ path, ...options }); throw Error('temporarily unavailable'); };
  await f.player.listen({ ...recording(), classification: { status: 'failed', worker_status: 'disabled' } });
  const retry = f.player.element.all().find(item => item.tag === 'button' && item.textContent === 'Retry sound classification');
  assert.equal(retry.hidden, false); assert.equal(retry.disabled, true); await retry.click(); assert.equal(requests.length, 0);
  f.player.updateClassification({ ...recording(), classification: { status: 'failed', worker_status: 'ready' } });
  const source = f.audio.src;
  await retry.click();
  assert.equal(requests.length, 1); assert.equal(requests[0].method, 'POST');
  assert.match(f.player.element.textContent, /could not be retried/); assert.equal(retry.disabled, false);
  assert.equal(f.audio.src, source); assert.equal(f.calls.length, 1);
});

test('switching or disposing a recording aborts retries and ignores their late responses', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const f = fixture(t), requests = [];
  f.ctx.api = (path, options) => new Promise(resolve => { requests.push({ path, ...options, resolve }); });
  const retry = () => f.player.element.all().find(item => item.tag === 'button' && item.textContent === 'Retry sound classification');
  await f.player.listen({ ...recording('first'), classification: { status: 'failed' } });
  const pending = retry().click(); await settle(); assert.equal(retry().disabled, true);
  await f.player.listen({ ...recording('second'), classification: soundEstimate('music') });
  assert.equal(requests[0].signal.aborted, true);
  requests[0].resolve({ status: 'pending' }); await pending;
  assert.match(f.player.element.textContent, /Music · model estimate/); assert.equal(retry().hidden, true);
  await f.player.listen({ ...recording('third'), classification: { status: 'failed' } });
  const later = retry().click(); await settle(); f.player.dispose();
  assert.equal(requests[1].signal.aborted, true); const text = f.player.element.textContent;
  requests[1].resolve({ status: 'pending' }); await later;
  t.mock.timers.tick(30000); await settle();
  assert.equal(f.player.element.textContent, text); assert.equal(requests.length, 2); assert.equal(f.audio.src, '');
});


const group = (id = 'group-1', overrides = {}) => ({ ...recording(id), kind: 'recording_group', status: 'ready', file_available: true, revision: 'revision-1', target_duration_seconds: 10, duration_seconds: 10, recording_count: 10, expected_recordings: 10, missing_sequences: [], issues: [], ...overrides });
function mockGroups(f, respond) {
  const requests = [];
  f.ctx.api = async (path, options) => {
    requests.push({ path, ...options });
    return options?.responseType === 'blob' ? new Blob(['group audio']) : await respond(path, options);
  };
  return requests;
}
const control = (browser, label) => browser.element.all().find(item => item.tag === 'button' && item.textContent === label);

test('group recordings paginate a fixed snapshot, filter devices and play only the revisioned group file without classification', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const f = fixture(t), requests = mockGroups(f, () => ({ items: [group()], total: 11 }));
  const browser = createRecordingsBrowser(f.ctx, { locationId: 'room2', timezone: 'Asia/Kolkata', devices: f.ctx.devices, player: f.player }); t.after(browser.dispose); await browser.loaded;
  assert.match(browser.element.textContent, /10-second recording/); assert.match(browser.element.textContent, /Uncalibrated audio/);
  assert.match(browser.element.textContent, /Ready to listen/); assert.doesNotMatch(browser.element.textContent, /Original recording|Waiting for sound estimate/);
  let query = new URL(requests[0].path, 'http://localhost').searchParams;
  assert.equal(new URL(requests[0].path, 'http://localhost').pathname, '/recordings');
  assert.equal(query.get('location_id'), 'room2'); assert.equal(query.get('limit'), '10');
  const boundary = query.get('until'); assert.equal(query.get('received_until'), boundary);
  await control(browser, 'Next recordings →').click();
  query = new URL(requests[1].path, 'http://localhost').searchParams;
  assert.equal(query.get('offset'), '10'); assert.equal(query.get('until'), boundary); assert.equal(query.get('received_until'), boundary);
  await control(browser, 'Listen').click();
  assert.equal(requests.at(-1).path, '/recordings/group-1/file?revision=revision-1'); assert.equal(requests.at(-1).responseType, 'blob');
  assert.equal(f.calls.length, 0); assert.equal(f.audio.src, 'blob:saved-1'); assert.equal(f.audio.plays, 0);
  assert.match(f.player.element.textContent, /10-second recording/); assert.match(f.player.element.textContent, /Sound categories are shown on incident details/);
  const device = browser.element.all().find(item => item.tag === 'select'); device.value = 'device-1'; await device.fire('change'); await settle();
  query = new URL(requests.at(-1).path, 'http://localhost').searchParams;
  assert.equal(query.get('offset'), '0'); assert.equal(query.get('device_id'), 'device-1');
  assert.equal(f.audio.src, 'blob:saved-1'); assert.equal(f.created.length, 1);
  assert.equal(requests.some(item => item.path.includes('/classification')), false);
});

test('collecting, incomplete and unavailable groups cannot be played or presented as complete audio', async t => {
  const f = fixture(t), requests = mockGroups(f, () => ({ items: [
    group('collecting', { status: 'collecting', duration_seconds: 4, file_available: false }),
    group('partial', { status: 'partial', duration_seconds: 7, missing_sequences: [3, 4, 5], file_available: true }),
    group('no-audio', { status: 'no_audio', duration_seconds: 0, file_available: false }),
    group('failed', { status: 'failed', file_available: false }),
    group('not-built', { file_available: false }),
  ], total: 5 }));
  const browser = createRecordingsBrowser(f.ctx, { locationId: 'room2', player: f.player }); t.after(browser.dispose); await browser.loaded;
  assert.match(browser.element.textContent, /Collecting · 4 of 10 seconds received/);
  assert.match(browser.element.textContent, /Incomplete recording · 7 of 10 seconds received/);
  assert.match(browser.element.textContent, /3 missing portion/); assert.match(browser.element.textContent, /not replaced with silence/);
  assert.match(browser.element.textContent, /No usable audio/); assert.match(browser.element.textContent, /Recording unavailable/);
  const listens = browser.element.all().filter(item => item.tag === 'button' && item.textContent === 'Listen');
  assert.equal(listens.length, 5); assert.equal(listens.every(item => item.disabled), true);
  for (const listen of listens) await listen.click();
  await f.player.listen(group('partial', { status: 'partial' }));
  assert.equal(requests.length, 1); assert.equal(f.created.length, 0);
});

test('newest recording groups refresh every ten seconds with a new boundary without resetting active playback', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] }); const f = fixture(t);
  let now = Date.parse('2026-10-10T01:02:30Z'), revision = 'revision-1'; f.ctx.serverNow = () => now;
  const requests = mockGroups(f, () => ({ items: [group('group-1', { revision })], total: 1 }));
  const browser = createRecordingsBrowser(f.ctx, { locationId: 'room2', player: f.player }); t.after(browser.dispose); await browser.loaded;
  await control(browser, 'Listen').click(); const source = f.audio.src, loads = f.audio.loads;
  const firstBoundary = new URL(requests[0].path, 'http://localhost').searchParams.get('until');
  now += 10000; revision = 'revision-2'; t.mock.timers.tick(9999); await settle(); assert.equal(requests.length, 2);
  t.mock.timers.tick(1); await settle(); assert.equal(requests.length, 3);
  const query = new URL(requests[2].path, 'http://localhost').searchParams;
  assert.notEqual(query.get('until'), firstBoundary); assert.equal(query.get('received_until'), query.get('until'));
  assert.equal(f.audio.src, source); assert.equal(f.audio.loads, loads); assert.equal(f.created.length, 1);
  assert.equal(requests.some(item => item.path.includes('/classification')), false);
  await control(browser, 'Listen').click(); assert.equal(requests.at(-1).path, '/recordings/group-1/file?revision=revision-2');
  assert.deepEqual(f.revoked, [source]);
});

test('recording polls keep focus on the same recording and fall back to the list when it disappears', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] }); const f = fixture(t);
  let items = [group('selected')]; mockGroups(f, () => ({ items, total: items.length }));
  const browser = createRecordingsBrowser(f.ctx, { locationId: 'room2', player: f.player }); t.after(browser.dispose); await browser.loaded;
  const first = control(browser, 'Listen'); first.focus();
  items = [group('newest'), group('selected')]; t.mock.timers.tick(10000); await settle();
  const selected = browser.element.all().find(node => node.attributes['data-recording-id'] === 'selected');
  assert.notEqual(selected, first); assert.equal(document.activeElement, selected);
  assert.deepEqual(selected.focusOptions, { preventScroll: true });
  items = [group('newest')]; t.mock.timers.tick(10000); await settle();
  assert.equal(document.activeElement.attributes['aria-label'], 'Saved recordings');
  const outside = new Element('button'); outside.focus(); t.mock.timers.tick(10000); await settle();
  assert.equal(document.activeElement, outside, 'background updates must not steal outside focus');
});

test('closing a recording returns focus to its replacement control after a background refresh', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] }); const f = fixture(t);
  let items = [group()]; mockGroups(f, () => ({ items, total: items.length }));
  const browser = createRecordingsBrowser(f.ctx, { locationId: 'room2', player: f.player }); t.after(browser.dispose); await browser.loaded;
  const original = control(browser, 'Listen'); await original.click();
  const source = f.audio.src; t.mock.timers.tick(10000); await settle();
  assert.equal(f.audio.src, source); assert.equal(document.activeElement, f.player.element);
  await f.player.element.all().find(item => item.tag === 'button' && item.textContent === 'Close recording').click();
  assert.notEqual(control(browser, 'Listen'), original);
  assert.equal(document.activeElement, control(browser, 'Listen'));
  await control(browser, 'Listen').click(); items = []; t.mock.timers.tick(10000); await settle();
  await f.player.element.all().find(item => item.tag === 'button' && item.textContent === 'Close recording').click();
  assert.equal(document.activeElement.attributes['aria-label'], 'Saved recordings');
});

test('automatic recording refresh leaves paging controls usable while its request is pending', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] }); const f = fixture(t); let complete;
  mockGroups(f, () => ({ items: [group()], total: 11 }));
  const browser = createRecordingsBrowser(f.ctx, { locationId: 'room2', player: f.player }); t.after(browser.dispose); await browser.loaded;
  const next = control(browser, 'Next recordings →'); next.focus();
  f.ctx.api = () => new Promise(resolve => { complete = resolve; });
  t.mock.timers.tick(10000); await settle();
  assert.equal(next.disabled, false); assert.equal(control(browser, 'Refresh recordings').disabled, false);
  assert.equal(document.activeElement, next);
  complete({ items: [group()], total: 11 }); await settle();
  assert.equal(document.activeElement, next);
});

test('older pages keep snapshot membership while collecting recordings become ready', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] }); const f = fixture(t); let ready = false;
  const requests = mockGroups(f, () => ({ items: [group('older', ready ? {} : { status: 'collecting', file_available: false, duration_seconds: 8 })], total: 11 }));
  const browser = createRecordingsBrowser(f.ctx, { locationId: 'room2', player: f.player }); t.after(browser.dispose); await browser.loaded;
  await control(browser, 'Next recordings →').click(); const fixed = requests.at(-1).path;
  ready = true; f.ctx.serverNow = () => Date.parse('2026-10-10T01:05:30Z'); t.mock.timers.tick(10000); await settle();
  assert.equal(requests.at(-1).path, fixed); assert.equal(!!control(browser, 'Listen').disabled, false);
  assert.match(browser.element.textContent, /Ready to listen/);
});

test('group refresh failures retain the last list and retry with bounded backoff; disposal stops polling', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] }); const f = fixture(t); let polls = 0;
  mockGroups(f, () => { if (++polls === 2) throw Error('temporary'); return { items: [group()], total: 1 }; });
  const browser = createRecordingsBrowser(f.ctx, { locationId: 'room2', player: f.player }); t.after(browser.dispose); await browser.loaded;
  t.mock.timers.tick(10000); await settle(); assert.equal(polls, 2); assert.match(browser.element.textContent, /Saved recordings remain below/);
  assert.equal(browser.element.all().filter(item => item.tag === 'article').length, 1);
  t.mock.timers.tick(19999); await settle(); assert.equal(polls, 2);
  t.mock.timers.tick(1); await settle(); assert.equal(polls, 3); assert.doesNotMatch(browser.element.textContent, /retrying/);
  browser.dispose(); t.mock.timers.tick(60000); await settle(); assert.equal(polls, 3);
});

test('group playback refuses obsolete revisions, and aborting navigation ignores late file bytes', async t => {
  const f = fixture(t); let path;
  f.ctx.api = async value => { path = value; const error = Error('changed'); error.status = 409; throw error; };
  await f.player.listen(group()); assert.equal(path, '/recordings/group-1/file?revision=revision-1');
  assert.match(f.player.element.textContent, /changed or is not ready/); assert.equal(f.created.length, 0);
  let release, signal; f.ctx.api = (_, options) => { signal = options.signal; return new Promise(resolve => { release = resolve; }); };
  const loading = f.player.listen(group()); await settle(); f.controller.abort(); assert.equal(signal.aborted, true);
  release(new Blob(['late'])); await loading; assert.equal(f.created.length, 0); assert.equal(f.calls.length, 0);
});

test('recording groups preserve device and simulation labels and render issue text safely', async t => {
  const f = fixture(t); f.ctx.devices.push({ id: 'device-2', external_id: 'ESP-MAC2' });
  mockGroups(f, () => ({ items: [group('one'), group('two', { device_id: 'device-2', source_kind: 'simulated', issues: ['<img src=x onerror=alert(1)>'] }), group('three', { source_kind: 'mixed', calibration_status: 'mixed' })], total: 3 }));
  const browser = createRecordingsBrowser(f.ctx, { locationId: 'room2', player: f.player }); t.after(browser.dispose); await browser.loaded;
  const rows = browser.element.all().filter(item => item.tag === 'article');
  assert.match(rows[0].textContent, /ESP-MAC1/); assert.doesNotMatch(rows[0].textContent, /SIMULATED|ESP-MAC2/);
  assert.match(rows[1].textContent, /SIMULATED · ESP-MAC2/); assert.match(rows[1].textContent, /<img src=x/);
  assert.equal(rows[1].all().some(item => item.tag === 'img'), false);
  assert.match(rows[2].textContent, /Includes SIMULATED audio/); assert.match(rows[2].textContent, /Mixed calibration status/);
});

test('legacy normal recordings remain individual files without classification polling', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] }); const f = fixture(t); let calls = 0; f.ctx.api = async () => { calls++; };
  await f.player.listen({ ...recording(), classification: { status: 'not_requested' } });
  assert.equal(f.calls[0].id, 'audio-1'); assert.match(f.player.element.textContent, /Sound categories are shown on incident details/);
  t.mock.timers.tick(60000); await settle(); assert.equal(calls, 0);
});

test('legacy disabled classifiers do not poll and unavailable classifiers retry slowly while playback stays intact', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] }); const f = fixture(t); let calls = 0;
  f.ctx.api = async () => { calls++; return soundEstimate('animal'); };
  await f.player.listen({ ...recording(), classification: { status: 'pending', worker_status: 'disabled' } });
  t.mock.timers.tick(60000); await settle(); assert.equal(calls, 0); assert.match(f.player.element.textContent, /classification disabled/);
  f.player.updateClassification({ ...recording(), classification: { status: 'pending', worker_status: 'unavailable' } });
  const source = f.audio.src;
  t.mock.timers.tick(29999); await settle(); assert.equal(calls, 0);
  t.mock.timers.tick(1); await settle(); assert.equal(calls, 1); assert.match(f.player.element.textContent, /Animal · model estimate/); assert.equal(f.audio.src, source);
});

import test from 'node:test';
import assert from 'node:assert/strict';
import { createIncidentAudio } from '../app/static/application/incident-audio.mjs';

class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.listeners = {}; this.attributes = {}; this._text = ''; this.loads = 0; this.pauses = 0; this.plays = 0; }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child.textContent || '').join(' '); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { if (this.all().includes(document.activeElement)) document.activeElement = null; this._text = ''; this.children = children; }
  setAttribute(name, value) { this.attributes[name] = value; }
  removeAttribute(name) { delete this.attributes[name]; if (name === 'src') this.src = ''; }
  addEventListener(name, handler) { (this.listeners[name] ||= []).push(handler); }
  async click() { if (!this.disabled) for (const handler of this.listeners.click || []) await handler({}); }
  all() { return this.children.flatMap(child => [child, ...child.all()]); }
  querySelectorAll(selector) { return this.all().filter(node => node.tag === selector); }
  querySelector(selector) { return this.querySelectorAll(selector)[0]; }
  pause() { this.pauses++; }
  load() { this.loads++; }
  play() { this.plays++; }
  focus(options) { this.focused = true; document.activeElement = this; this.focusOptions = options; }
  scrollIntoView() { this.scrolled = true; }
}
const settle = async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); };
const saved = (revision = 'version-1') => ({ status: 'completed', provisional: false, revision, generated_at: '2026-10-10T01:03:00Z', worker_status: 'ready', classification: { status: 'completed', primary_category: 'traffic', confidence_status: 'classified', classified_at: '2026-10-10T01:03:00Z', top_labels: [{ label: 'Vehicle', score: 0.812 }] }, audio: { available: true, revision, started_at: '2026-10-10T01:02:00Z', ended_at: '2026-10-10T01:02:20Z', duration_seconds: 4, window_duration_seconds: 20, coverage_percent: 20, context_before_seconds: 5, context_after_seconds: 5, recording_count: 4, gap_count: 1, excluded_count: 1, source_kind: 'physical', gaps: [{ started_at: '2026-10-10T01:02:02Z', ended_at: '2026-10-10T01:02:18Z', duration_seconds: 16 }] } });
function fixture(t, first = saved()) {
  const document = globalThis.document; globalThis.document = { createElement: tag => new Element(tag) }; t.after(() => { globalThis.document = document; });
  t.mock.timers.enable({ apis: ['setTimeout'] });
  let current = first;
  const calls = [], created = [], revoked = [], controller = new AbortController();
  const ctx = { signal: controller.signal, async api(path, options) { calls.push({ path, options }); return options?.responseType === 'blob' ? new Blob(['wav']) : current; } };
  const urls = { createObjectURL(blob) { created.push(blob); return `blob:incident-${created.length}`; }, revokeObjectURL(url) { revoked.push(url); } };
  const panel = createIncidentAudio(ctx, { incidentId: 'incident-1', urls }); t.after(panel.dispose);
  panel.setIncident({ location_snapshot: { timezone: 'UTC' }, ended_at: '2026-10-10T01:02:15Z' });
  return { panel, ctx, calls, created, revoked, controller, setCurrent: value => { current = value; }, button: label => panel.element.all().find(item => item.tag === 'button' && item.textContent === label), audio: panel.element.all().find(item => item.tag === 'audio') };
}

test('incident player explains partial coverage and classifies only the incident, with revision-bound authenticated media and no autoplay', async t => {
  const f = fixture(t); await f.panel.loaded;
  assert.equal(f.calls.length, 1); assert.equal(f.created.length, 0);
  assert.match(f.panel.element.textContent, /Partial incident audio/);
  assert.match(f.panel.element.textContent, /4 s playable audio · 20 s elapsed window · 20% coverage/);
  assert.match(f.panel.element.textContent, /Gaps are skipped in playback; no silence is added/);
  assert.match(f.panel.element.textContent, /Listening context: 5 s before and 5 s after/);
  assert.match(f.panel.element.textContent, /Incident sound estimate Traffic · model estimate/);
  assert.match(f.panel.element.textContent, /context before and after is excluded/);
  assert.match(f.panel.element.textContent, /scores are not calibrated probabilities/);
  await f.button('Listen to incident audio').click();
  assert.equal(f.calls[1].path, '/incidents/incident-1/audio/file?revision=version-1');
  assert.equal(f.calls[1].options.responseType, 'blob');
  assert.equal(f.audio.src, 'blob:incident-1'); assert.equal(f.audio.controls, true); assert.equal(f.audio.preload, 'none'); assert.equal(f.audio.plays, 0);
  t.mock.timers.tick(60000); await settle(); assert.equal(f.calls.length, 2);
});

test('provisional analysis refreshes until finalized while keeping the chosen audio revision intact', async t => {
  const f = fixture(t, { ...saved(), provisional: true }); await f.panel.loaded;
  assert.match(f.panel.element.textContent, /Waiting for recordings after the incident/);
  await f.button('Listen to incident audio').click(); const loads = f.audio.loads;
  f.setCurrent(saved('version-2')); t.mock.timers.tick(10000); await settle();
  assert.equal(f.audio.src, 'blob:incident-1'); assert.equal(f.audio.loads, loads); assert.equal(f.created.length, 1);
  assert.match(f.panel.element.textContent, /New audio is available/);
  await f.button('Load newer incident audio').click();
  assert.equal(f.calls.at(-1).path, '/incidents/incident-1/audio/file?revision=version-2');
  assert.deepEqual(f.revoked, ['blob:incident-1']); assert.equal(f.audio.src, 'blob:incident-2');
  await f.button('Close incident audio').click(); assert.equal(f.audio.src, '');
  assert.deepEqual(f.revoked, ['blob:incident-1', 'blob:incident-2']);
  const count = f.calls.length; t.mock.timers.tick(60000); await settle(); assert.equal(f.calls.length, count);
});

test('analysis refresh preserves open evidence disclosures and their keyboard focus without resetting playback', async t => {
  const f = fixture(t, { ...saved(), provisional: true }); await f.panel.loaded;
  await f.button('Listen to incident audio').click(); const source = f.audio.src, loads = f.audio.loads;
  const disclosure = label => f.panel.element.querySelectorAll('details').find(node => node.querySelector('summary').textContent === label);
  const gap = disclosure('Missing audio intervals'), estimate = disclosure('YAMNet details');
  gap.open = true; estimate.open = true; gap.querySelector('summary').focus();
  f.setCurrent(saved('version-2')); t.mock.timers.tick(10000); await settle();
  assert.equal(disclosure('Missing audio intervals').open, true); assert.equal(disclosure('YAMNet details').open, true);
  assert.equal(document.activeElement, disclosure('Missing audio intervals').querySelector('summary'));
  assert.deepEqual(document.activeElement.focusOptions, { preventScroll: true });
  assert.equal(f.audio.src, source); assert.equal(f.audio.loads, loads);
  const outside = new Element('button'); outside.focus(); await f.panel.refresh();
  assert.equal(document.activeElement, outside);
  disclosure('Missing audio intervals').querySelector('summary').focus();
  const complete = saved('version-3'); complete.audio = { ...complete.audio, gap_count: 0, excluded_count: 0, gaps: [] };
  f.setCurrent(complete); await f.panel.refresh();
  assert.equal(document.activeElement, f.panel.element, 'removed evidence disclosure leaves focus in its panel');
});

test('closing incident audio returns focus to its available listening action', async t => {
  const f = fixture(t); await f.panel.loaded;
  await f.button('Listen to incident audio').click();
  f.button('Close incident audio').focus(); await f.button('Close incident audio').click();
  assert.equal(document.activeElement, f.button('Listen to incident audio'));
  assert.equal(f.audio.hidden, true); assert.equal(f.audio.src, '');
});

test('an active snapshot end is not a closure and recorded coverage is distinct from model input duration', async t => {
  const result = saved(); result.provisional = true;
  result.audio = { ...result.audio, incident_ended_at: '2026-10-10T01:02:20Z', incident_closed_at: null, core_coverage_seconds: 3, core_window_duration_seconds: 10 };
  result.classification.analyzed_duration_seconds = 1.92;
  const f = fixture(t, result); f.panel.setIncident({ ended_at: null, location_snapshot: { timezone: 'UTC' } }); await f.panel.loaded;
  assert.match(f.panel.element.textContent, /Incident in progress · provisional audio/);
  assert.doesNotMatch(f.panel.element.textContent, /Waiting for recordings after the incident/);
  assert.match(f.panel.element.textContent, /Incident audio: 3 s of 10 s recorded during the incident itself/);
  assert.match(f.panel.element.textContent, /Sound model analysed 1.92 s of incident audio/);
  assert.doesNotMatch(f.panel.element.textContent, /estimate covers 3/);
  result.audio.incident_closed_at = '2026-10-10T01:02:20Z'; await f.panel.refresh();
  assert.match(f.panel.element.textContent, /Waiting for recordings after the incident/);
});

test('pending processing and retries update categories without replacing audio; unavailable service backs off', async t => {
  const f = fixture(t, { ...saved(), status: 'failed', provisional: true, error: 'Analysis failed' }); await f.panel.loaded;
  t.mock.timers.tick(60000); await settle(); assert.equal(f.calls.length, 1);
  await f.button('Listen to incident audio').click(); const source = f.audio.src;
  f.setCurrent({ ...saved(), status: 'pending', classification: { status: 'pending' } });
  await f.button('Recalculate incident audio').click();
  assert.equal(f.calls.at(-1).options.method, 'POST'); assert.equal(f.audio.src, source);
  f.setCurrent({ ...saved(), status: 'pending', worker_status: 'unavailable', worker_error: 'Model needs attention', classification: { status: 'pending' } });
  t.mock.timers.tick(3000); await settle(); assert.match(f.panel.element.textContent, /Model needs attention/);
  const count = f.calls.length; t.mock.timers.tick(29999); await settle(); assert.equal(f.calls.length, count);
  f.setCurrent(saved()); t.mock.timers.tick(1); await settle(); assert.equal(f.calls.length, count + 1);
  assert.equal(f.audio.src, source); assert.equal(f.created.length, 1); assert.match(f.panel.element.textContent, /Traffic · model estimate/);
});

test('route disposal aborts analysis and media and ignores late responses without leaking object URLs', async t => {
  const f = fixture(t); await f.panel.loaded;
  let releaseMedia, releaseAnalysis, mediaSignal, analysisSignal;
  f.ctx.api = (path, { signal, responseType }) => new Promise(resolve => { if (responseType === 'blob') { releaseMedia = resolve; mediaSignal = signal; } else { releaseAnalysis = resolve; analysisSignal = signal; } });
  const loading = f.button('Listen to incident audio').click(), refreshing = f.panel.refresh(); await settle();
  f.controller.abort(); const text = f.panel.element.textContent;
  assert.equal(mediaSignal.aborted, true); assert.equal(analysisSignal.aborted, true);
  releaseMedia(new Blob(['late'])); releaseAnalysis(saved('late')); await Promise.all([loading, refreshing]);
  assert.equal(f.created.length, 0); assert.equal(f.panel.element.textContent, text);
  t.mock.timers.tick(60000); await settle(); assert.equal(f.panel.element.textContent, text);
});

test('superseded analysis replies cannot overwrite newer metadata and revision conflicts require explicit reload', async t => {
  const f = fixture(t); await f.panel.loaded; let release, oldSignal;
  f.ctx.api = (path, { signal }) => new Promise(resolve => { release = resolve; oldSignal = signal; });
  const old = f.panel.refresh(); await settle();
  f.ctx.api = async () => saved('new'); await f.panel.refresh(); assert.equal(oldSignal.aborted, true);
  release(saved('old')); await old;
  f.ctx.api = async path => { assert.equal(path, '/incidents/incident-1/audio/file?revision=new'); const error = new Error('changed'); error.status = 409; throw error; };
  await f.button('Listen to incident audio').click();
  assert.match(f.panel.element.textContent, /saved audio changed/); assert.equal(f.created.length, 0);
});

test('no data, simulation, limits and unsafe label strings have explicit text-only states', async t => {
  const first = saved(); first.audio = { ...first.audio, available: false, duration_seconds: 0, source_kind: 'mixed', truncated: true, truncation_reason: '<img src=x onerror=bad>' };
  first.classification = { status: 'completed', primary_category: 'other', confidence_status: 'no_usable_audio', top_labels: [{ label: '<script>bad()</script>', score: 0.1 }] };
  const f = fixture(t, first); await f.panel.loaded;
  assert.match(f.panel.element.textContent, /No recorded audio/); assert.match(f.panel.element.textContent, /Includes SIMULATED audio/);
  assert.match(f.panel.element.textContent, /not the entire incident window/); assert.match(f.panel.element.textContent, /Other \/ uncertain/);
  assert.match(f.panel.element.textContent, /<script>bad/); assert.equal(f.panel.element.all().some(item => ['img', 'script'].includes(item.tag)), false);
  assert.equal(f.button('Listen to incident audio').disabled, true);
});

test('disabled processing does not poll and repeated transport failures retry with bounded delay', async t => {
  const f = fixture(t, { status: 'pending', worker_status: 'disabled', audio: { available: false } }); await f.panel.loaded;
  t.mock.timers.tick(60000); await settle(); assert.equal(f.calls.length, 1);
  assert.equal(f.button('Generate incident audio').disabled, true); assert.match(f.panel.element.textContent, /switched off/);
  f.setCurrent({ status: 'pending', worker_status: 'ready', audio: { available: false } }); await f.panel.refresh();
  let calls = 0; f.ctx.api = async () => { calls++; throw new Error('Temporarily offline'); };
  t.mock.timers.tick(3000); await settle(); assert.equal(calls, 1);
  t.mock.timers.tick(5999); await settle(); assert.equal(calls, 1);
  t.mock.timers.tick(1); await settle(); assert.equal(calls, 2);
  assert.match(f.panel.element.textContent, /Incident audio update unavailable/);
});

test('an unbuilt pending incident can be queued manually without waiting for background discovery', async t => {
  const f = fixture(t, { status: 'pending', provisional: true, worker_status: 'ready', audio: { available: false } }); await f.panel.loaded;
  assert.equal(f.button('Generate incident audio').disabled, false);
  f.setCurrent({ status: 'processing', provisional: true, worker_status: 'ready', audio: { available: false } });
  await f.button('Generate incident audio').click();
  assert.equal(f.calls.at(-1).path, '/incidents/incident-1/analysis'); assert.equal(f.calls.at(-1).options.method, 'POST');
  assert.equal(f.button('Generate incident audio').disabled, true);
});


test('opening whole incident audio from related readings preserves a loaded revision and playback position', async t => {
  const f = fixture(t); await f.panel.loaded;
  await f.panel.open();
  assert.equal(f.panel.element.focused, true); assert.equal(f.panel.element.scrolled, true);
  assert.equal(f.audio.src, 'blob:incident-1'); assert.equal(f.audio.plays, 0);
  assert.equal(f.calls.at(-1).path, '/incidents/incident-1/audio/file?revision=version-1');
  f.audio.currentTime = 2;
  const loads = f.audio.loads, source = f.audio.src;
  f.setCurrent(saved('version-2')); await f.panel.refresh();
  await f.panel.open();
  assert.equal(f.audio.src, source); assert.equal(f.audio.currentTime, 2);
  assert.equal(f.audio.loads, loads); assert.equal(f.created.length, 1); assert.deepEqual(f.revoked, []);
  assert.equal(f.audio.plays, 0);
  assert.match(f.panel.element.textContent, /New audio is available/);
  await f.button('Load newer incident audio').click();
  assert.equal(f.audio.src, 'blob:incident-2');
});

test('opening pending or unavailable incident audio reveals its state without raw audio fallback or autoplay', async t => {
  const f = fixture(t, { status: 'pending', worker_status: 'ready', audio: { available: false } });
  await f.panel.loaded;
  await f.panel.open();
  assert.equal(f.panel.element.focused, true); assert.equal(f.panel.element.scrolled, true);
  assert.match(f.panel.element.textContent, /Incident audio queued/);
  assert.equal(f.created.length, 0); assert.equal(f.audio.plays, 0);
  assert.ok(f.calls.every(call => call.path === '/incidents/incident-1/analysis'));
  f.setCurrent({ status: 'completed', provisional: false, audio: { available: false } });
  await f.panel.open();
  assert.match(f.panel.element.textContent, /No recorded audio/);
  assert.equal(f.created.length, 0); assert.equal(f.audio.plays, 0);
  assert.ok(f.calls.every(call => call.path === '/incidents/incident-1/analysis'));
});

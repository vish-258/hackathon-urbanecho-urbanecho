import test from 'node:test';
import assert from 'node:assert/strict';
import {locateCurrentPosition} from '../app/static/application/geolocation.mjs';

const NOW = Date.parse('2026-10-10T10:00:00Z');
const position = (coords = {}, timestamp = NOW) => ({
  coords: {latitude: 12.9, longitude: 77.6, accuracy: 15, ...coords}, timestamp,
});
function browser(t, {signal} = {}) {
  t.mock.timers.enable({apis: ['setTimeout']});
  const calls = [];
  const geolocation = {getCurrentPosition(success, failure, options) { calls.push({success, failure, options}); }};
  const lookup = () => locateCurrentPosition({geolocation, secureContext: true, now: () => NOW, signal});
  return {calls, lookup};
}
const settle = async () => { for (let i = 0; i < 5; i++) await Promise.resolve(); };
const hasCode = code => error => { assert.equal(error.code, code); assert.ok(error.message.length > 20); return true; };

test('returns a fresh browser position with accuracy and no inferred coordinates', async t => {
  const {calls, lookup} = browser(t);
  const result = lookup();
  assert.deepEqual(calls[0].options, {enableHighAccuracy: true, timeout: 12000, maximumAge: 0});
  calls[0].success(position());
  assert.deepEqual(await result, {latitude: 12.9, longitude: 77.6, accuracy: 15, timestamp: NOW});
  t.mock.timers.tick(24001);
  await settle();
  assert.equal(calls.length, 1, 'successful lookup must clear its retry deadline');
});

test('accepts legitimate zero coordinates and boundary coordinates', async t => {
  const {calls, lookup} = browser(t);
  for (const coords of [{latitude: 0, longitude: 0, accuracy: 0}, {latitude: -90, longitude: 180}]) {
    const result = lookup();
    calls.at(-1).success(position(coords));
    assert.equal((await result).latitude, coords.latitude);
  }
});

test('permission denial is actionable and does not trigger another permission attempt', async t => {
  const {calls, lookup} = browser(t);
  const result = lookup();
  calls[0].failure({code: 1});
  await assert.rejects(result, hasCode('permission_denied'));
  t.mock.timers.tick(24001);
  await settle();
  assert.equal(calls.length, 1);
});

test('insecure and unsupported browsers fail before requesting a position', async () => {
  let calls = 0;
  const geolocation = {getCurrentPosition() { calls++; }};
  await assert.rejects(locateCurrentPosition({geolocation, secureContext: false}), hasCode('insecure_context'));
  await assert.rejects(locateCurrentPosition({geolocation: null, secureContext: true}), hasCode('unsupported'));
  assert.equal(calls, 0);
});

for (const code of [2, 3]) {
  test(`browser ${code === 2 ? 'unavailable' : 'timeout'} retries once with lower accuracy`, async t => {
    const {calls, lookup} = browser(t);
    const result = lookup();
    calls[0].failure({code});
    await settle();
    assert.equal(calls.length, 2);
    assert.deepEqual(calls[1].options, {enableHighAccuracy: false, timeout: 12000, maximumAge: 0});
    calls[1].success(position({accuracy: 80}));
    assert.equal((await result).accuracy, 80);
    t.mock.timers.tick(24001);
    await settle();
    assert.equal(calls.length, 2);
  });
}

test('both ignored browser callbacks reach an independent bounded deadline', async t => {
  const {calls, lookup} = browser(t);
  const result = lookup();
  const rejected = assert.rejects(result, hasCode('timeout'));
  t.mock.timers.tick(12000);
  await settle();
  assert.equal(calls.length, 2);
  t.mock.timers.tick(12000);
  await rejected;
  calls[0].success(position());
  calls[1].success(position());
  await settle();
  assert.equal(calls.length, 2);
});

test('late result from timed-out first attempt cannot replace the fallback result', async t => {
  const {calls, lookup} = browser(t);
  const result = lookup();
  t.mock.timers.tick(12000);
  await settle();
  calls[0].success(position({latitude: 1}));
  calls[0].failure({code: 1});
  calls[1].success(position({latitude: 2}));
  assert.equal((await result).latitude, 2);
});

test('fallback failure is returned without an endless retry', async t => {
  const {calls, lookup} = browser(t);
  const result = lookup();
  calls[0].failure({code: 2});
  await settle();
  calls[1].failure({code: 2});
  await assert.rejects(result, hasCode('unavailable'));
  t.mock.timers.tick(60000);
  await settle();
  assert.equal(calls.length, 2);
});

test('malformed positions never become default zero coordinates or trigger a retry', async t => {
  const {calls, lookup} = browser(t);
  for (const bad of [null, {}, position({latitude: NaN}), position({longitude: Infinity}),
    position({latitude: '12.9'}), position({latitude: 91}), position({longitude: -181}),
    position({accuracy: -1}), position({accuracy: undefined}), position({}, '2026-10-10')]) {
    const result = lookup();
    const before = calls.length;
    calls.at(-1).success(bad);
    await assert.rejects(result, hasCode('invalid_position'));
    assert.equal(calls.length, before);
  }
});

test('old or future timestamps are rejected while freshness limits are accepted', async t => {
  const {calls, lookup} = browser(t);
  for (const timestamp of [NOW - 120001, NOW + 5001]) {
    const result = lookup();
    calls.at(-1).success(position({}, timestamp));
    await assert.rejects(result, hasCode('stale_position'));
  }
  for (const timestamp of [NOW - 120000, NOW + 5000]) {
    const result = lookup();
    calls.at(-1).success(position({}, timestamp));
    assert.equal((await result).timestamp, timestamp);
  }
});

test('aborting an active lookup clears listeners and prevents late callbacks or retry', async t => {
  const controller = new AbortController();
  const add = t.mock.method(controller.signal, 'addEventListener');
  const remove = t.mock.method(controller.signal, 'removeEventListener');
  const {calls, lookup} = browser(t, {signal: controller.signal});
  const result = lookup();
  controller.abort();
  await assert.rejects(result, error => error.name === 'AbortError' && error.code === 'aborted');
  assert.equal(add.mock.callCount(), 1);
  assert.equal(remove.mock.callCount(), 1);
  calls[0].success(position());
  calls[0].failure({code: 3});
  t.mock.timers.tick(60000);
  await settle();
  assert.equal(calls.length, 1);
});

test('aborted input does not request browser access', async t => {
  const controller = new AbortController();
  controller.abort();
  const {calls, lookup} = browser(t, {signal: controller.signal});
  await assert.rejects(lookup(), error => error.name === 'AbortError');
  assert.equal(calls.length, 0);
});

test('aborting during fallback removes both attempts’ listeners', async t => {
  const controller = new AbortController();
  const remove = t.mock.method(controller.signal, 'removeEventListener');
  const {calls, lookup} = browser(t, {signal: controller.signal});
  const result = lookup();
  calls[0].failure({code: 2});
  await settle();
  controller.abort();
  await assert.rejects(result, error => error.name === 'AbortError');
  assert.equal(remove.mock.callCount(), 2);
  calls[1].success(position());
  t.mock.timers.tick(60000);
  await settle();
  assert.equal(calls.length, 2);
});

test('success clears its abort listener', async t => {
  const controller = new AbortController();
  const remove = t.mock.method(controller.signal, 'removeEventListener');
  const {calls, lookup} = browser(t, {signal: controller.signal});
  const result = lookup();
  calls[0].success(position());
  await result;
  assert.equal(remove.mock.callCount(), 1);
  controller.abort();
  t.mock.timers.tick(60000);
  await settle();
  assert.equal(calls.length, 1);
});

test('synchronous browser security errors are treated as denial and clean up', async t => {
  t.mock.timers.enable({apis: ['setTimeout']});
  let calls = 0;
  const geolocation = {getCurrentPosition() { calls++; throw new DOMException('Blocked', 'SecurityError'); }};
  await assert.rejects(locateCurrentPosition({geolocation, secureContext: true}), hasCode('permission_denied'));
  t.mock.timers.tick(60000);
  await settle();
  assert.equal(calls, 1);
});

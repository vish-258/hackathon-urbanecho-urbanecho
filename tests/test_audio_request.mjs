import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';

// Exercise the application's real request function without starting its UI/SSE.
const source = readFileSync(new URL('../app/static/application/app.mjs', import.meta.url), 'utf8');
const requestFunction = source.slice(source.indexOf('async function api('), source.indexOf('async function allPages('));
function requestHarness(fetch, {localAccess = false, localSession = async () => {}, headers = {Authorization: 'Bearer test-only'}} = {}) {
  return new Function('fetch', 'localAccess', 'localSession', 'accessHeaders', 'controller', `${requestFunction}; return api;`)(
    fetch, localAccess, localSession, () => headers, new AbortController());
}

test('audio uses authenticated same-origin fetch and returns original bytes as a Blob', async () => {
  const bytes = new Uint8Array([82,73,70,70,1,2,3,4]);
  let request;
  const api = requestHarness(async (path, options) => {
    request = {path, options}; return new Response(bytes, {headers:{'Content-Type':'audio/wav'}});
  });
  const controller = new AbortController();
  const result = await api('/audio/chunk/file', {responseType:'blob', signal:controller.signal});
  assert.deepEqual(new Uint8Array(await result.arrayBuffer()), bytes);
  assert.equal(result.type, 'audio/wav');
  assert.equal(request.path, '/audio/chunk/file');
  assert.equal(request.options.credentials, 'same-origin');
  assert.equal(request.options.cache, 'no-store');
  assert.equal(request.options.headers.Authorization, 'Bearer test-only');
  assert.equal(request.options.signal, controller.signal);
  assert.equal('responseType' in request.options, false);
});

test('expired local audio session renews once and retains binary response and abort signal', async () => {
  let calls = 0, renewals = 0;
  const controller = new AbortController();
  const api = requestHarness(async (_, options) => {
    calls++; assert.equal(options.headers['X-Soundwatch-Local'], '1');
    assert.equal(options.signal, controller.signal);
    return calls === 1 ? new Response('{}', {status:401}) : new Response('wav', {headers:{'Content-Type':'audio/wav'}});
  }, {localAccess:true, localSession:async()=>{renewals++;}, headers:{'X-Soundwatch-Local':'1'}});
  assert.equal(await (await api('/audio/chunk/file', {responseType:'blob', signal:controller.signal})).text(), 'wav');
  assert.equal(calls, 2); assert.equal(renewals, 1);
});

test('missing recording and rejected authentication remain errors rather than playable blobs', async () => {
  const missing = requestHarness(async () => new Response(JSON.stringify({error:{message:'Original audio unavailable; restore storage from backup'}}), {status:503}));
  await assert.rejects(missing('/audio/chunk/file', {responseType:'blob'}), /Original audio unavailable/);
  let calls = 0, renewals = 0;
  const denied = requestHarness(async () => {calls++; return new Response('{}', {status:401});}, {localAccess:true, localSession:async()=>{renewals++;}});
  await assert.rejects(denied('/audio/chunk/file', {responseType:'blob'}), error => error.status === 401);
  assert.equal(calls, 2); assert.equal(renewals, 1);
});

test('ordinary JSON API requests retain their existing response type', async () => {
  const api = requestHarness(async () => new Response('{"items":[]}'));
  assert.deepEqual(await api('/devices'), {items:[]});
});

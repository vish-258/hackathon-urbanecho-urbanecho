import {initialState, applySnapshot, applyEvent, sseParser, ageData, formatLevel} from '/demo/state.mjs';
const $ = id => document.getElementById(id);
const state = initialState();
let token = '', controller = null, generation = 0, staleSeconds = 30, serverOffset = 0, readyNotice = '';
const text = (tag, content, className) => { const node = document.createElement(tag); node.textContent = content; if (className) node.className = className; return node; };
const short = value => value ? value.slice(0, 8) : '—';
const badge = (value, type = value) => text('span', value, `badge ${type}`);
const names = id => state.locations.get(id)?.name || short(id);
function render() {
  $('location-count').textContent = state.locations.size;
  const active = [...state.incidents.values()].filter(item => ['active', 'recovering'].includes(item.status));
  $('incident-count').textContent = active.length;
  $('stale-count').textContent = [...state.locations.values()].filter(item => item.data_status !== 'fresh').length;
  $('active-label').textContent = active.length ? `${active.length} OPEN` : 'NO OPEN INCIDENTS';
  $('locations').replaceChildren();
  for (const location of state.locations.values()) {
    const box = text('article', '', 'station');
    box.dataset.locationId = location.id;
    box.append(text('h3', location.name), badge(location.noise_status), badge(location.data_status));
    box.append(text('small', `${location.latitude.toFixed(5)}, ${location.longitude.toFixed(5)}`));
    for (const stream of location.streams) {
      const row = text('div', `Device ${short(stream.device_id)} · ${formatLevel(stream.measurement_value, stream.measurement_type)}`, 'device');
      row.append(text('span', `${stream.noise_status} · ${stream.data_status} · recovery ${stream.recovery_streak || 0}`));
      if (stream.measurement_type) row.append(text('span', `${stream.interval_seconds}s interval · ${stream.calibration_status || 'calibration unknown'}`));
      if (stream.diagnostic) row.append(text('span', stream.diagnostic));
      box.append(row);
    }
    $('locations').append(box);
  }
  if (!state.locations.size) $('locations').append(text('p', 'No locations registered yet. Use the API or demo simulator to add a station.', 'empty'));
  $('incidents').replaceChildren();
  for (const incident of active) {
    const box = text('article', '', `incident ${incident.status}`);
    box.dataset.incidentId = incident.id;
    box.append(text('strong', `${names(incident.location_id)} · ${incident.status}`));
    box.append(text('div', `Latest ${formatLevel(incident.latest_db, incident.measurement_type || incident.threshold_type)} · Peak ${formatLevel(incident.peak_db, incident.measurement_type || incident.threshold_type)}`));
    box.append(text('small', `Device ${short(incident.device_id)} · ${incident.breach_count || 1} breaches · ${incident.recovery_streak || 0} recovery readings`));
    box.append(text('small', `Rule ${short(incident.threshold_version_id)} · ${incident.threshold_type || incident.measurement_type || ''}`));
    $('incidents').append(box);
  }
  if (!active.length) $('incidents').append(text('p', 'No unresolved incidents in this snapshot.', 'empty'));
  $('feed').replaceChildren();
  for (const event of state.feed) {
    const row = text('li', '');
    const when = event.event_created_at ? new Date(event.event_created_at).toLocaleTimeString() : '';
    row.append(text('time', when), text('b', event.event_type), text('span', `${names(event.location_id)} · ${event.transition_reason || ''}`));
    $('feed').append(row);
  }
  if (!state.feed.length) $('feed').append(text('li', 'Waiting for new events. Snapshot history does not generate popups.', 'empty'));
  drawMap();
}
function svgElement(tag, attributes) {
  const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
  return node;
}
function drawMap() {
  $('map').querySelectorAll('g').forEach(node => node.remove());
  const locations = [...state.locations.values()];
  if (!locations.length) return;
  const lats = locations.map(item => item.latitude), lons = locations.map(item => item.longitude);
  const minLat = Math.min(...lats), maxLat = Math.max(...lats), minLon = Math.min(...lons), maxLon = Math.max(...lons);
  for (const location of locations) {
    const x = maxLon === minLon ? 360 : 65 + 560 * (location.longitude - minLon) / (maxLon - minLon);
    const y = maxLat === minLat ? 170 : 270 - 210 * (location.latitude - minLat) / (maxLat - minLat);
    const group = svgElement('g', {'data-location-id': location.id});
    group.append(svgElement('circle', {cx: x, cy: y, r: 10, class: location.noise_status}));
    if (location.data_status !== 'fresh') group.append(svgElement('circle', {cx: x, cy: y, r: 16, class: 'stale-ring'}));
    const title = svgElement('title', {}); title.textContent = `${location.name}: ${location.noise_status}, data ${location.data_status}`; group.append(title);
    const label = svgElement('text', {x, y: y + 32, 'text-anchor': 'middle'}); label.textContent = location.name.slice(0, 25); group.append(label);
    $('map').append(group);
  }
}
function notify(result) {
  if (!result) return;
  const {kind, event} = result;
  const box = text('div', '', `toast ${kind}`);
  const close = text('button', '×'); close.type = 'button'; close.setAttribute('aria-label', 'Dismiss notification'); close.onclick = () => box.remove();
  box.append(close, text('strong', kind === 'opening' ? 'Noise incident opened' : 'Noise returned to normal'));
  box.append(text('div', `${names(event.location_id)} · ${formatLevel(event.measurement_value, event.measurement_type)}`));
  $('toasts').append(box);
  setTimeout(() => box.remove(), 12000);
}
function disconnect() {
  generation += 1; controller?.abort(); controller = null; token = ''; $('toasts').replaceChildren();
  $('disconnect').disabled = true; $('connect').disabled = false;
  $('connection').textContent = '○ Disconnected';
}
async function loadSnapshot(signal) {
  const response = await fetch('/locations/status?limit=200', {headers: {Authorization: `Bearer ${token}`}, signal, cache: 'no-store'});
  if (!response.ok) throw new Error(response.status === 403 || response.status === 401 ? 'Administrator token was not accepted.' : `Snapshot unavailable (${response.status}).`);
  const snapshot = await response.json(); staleSeconds = snapshot.data_stale_seconds;
  serverOffset = Date.parse(snapshot.as_of) - Date.now(); applySnapshot(state, snapshot); render();
  readyNotice = snapshot.total > 200 ? `Showing the first 200 of ${snapshot.total} locations. Use API filters for larger deployments.` : 'Current snapshot loaded. Opening alerts appear once; continued breaches update the same incident.';
  $('notice').textContent = readyNotice;
}
async function connectLoop(signal, run) {
  let needsSnapshot = true;
  while (!signal.aborted && run === generation) {
    try {
      if (needsSnapshot) { await loadSnapshot(signal); needsSnapshot = false; }
      $('connection').textContent = '○ Connecting';
      const response = await fetch('/events/stream', {headers: {Authorization: `Bearer ${token}`, 'Last-Event-ID': state.cursor}, signal, cache: 'no-store'});
      if ([409, 410].includes(response.status)) { needsSnapshot = true; $('notice').textContent = 'Replay history changed or expired. Resynchronizing current state…'; continue; }
      if ([401, 403].includes(response.status)) throw new Error('Administrator token was not accepted.');
      if (!response.ok) throw new Error(`Live stream unavailable (${response.status}).`);
      $('connection').textContent = '● Live'; $('notice').textContent = readyNotice;
      const reader = response.body.getReader(), decoder = new TextDecoder();
      let resync = false;
      const parse = sseParser(frame => {
        if (frame.type === 'stream.resync_required') { resync = true; return; }
        notify(applyEvent(state, frame.data, frame.id)); render();
      });
      try {
        while (!signal.aborted) {
          const {done, value} = await reader.read();
          if (done) break;
          parse(decoder.decode(value, {stream: true}));
          if (resync) { needsSnapshot = true; await reader.cancel(); break; }
        }
      } finally { reader.releaseLock(); }
      if (resync) continue;
      throw new Error('Connection interrupted. Replaying missed events when reconnected…');
    } catch (error) {
      if (signal.aborted || run !== generation) return;
      $('connection').textContent = '○ Reconnecting'; $('notice').textContent = error.message;
      if (error.message.includes('token was not accepted')) { disconnect(); return; }
      await new Promise(resolve => { const timer = setTimeout(resolve, 1500); signal.addEventListener('abort', () => {clearTimeout(timer); resolve();}, {once: true}); });
    }
  }
}
$('connect-form').addEventListener('submit', event => {
  event.preventDefault(); const value = $('token').value.trim(); if (!value) return;
  disconnect(); Object.assign(state, initialState()); render(); token = value; $('token').value = ''; controller = new AbortController();
  $('connect').disabled = true; $('disconnect').disabled = false;
  void connectLoop(controller.signal, generation);
});
$('disconnect').addEventListener('click', disconnect);
window.addEventListener('pagehide', disconnect);

setInterval(() => { if (state.cursor) { ageData(state, Date.now() + serverOffset, staleSeconds); render(); } }, 1000);

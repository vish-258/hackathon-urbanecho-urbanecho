// Pure state and framing logic shared by the browser and Node's built-in tests.
export function initialState() {
  return {locations: new Map(), incidents: new Map(), seen: new Set(), notified: new Set(), cursor: null, feed: []};
}
export function applySnapshot(state, snapshot) {
  state.locations = new Map(snapshot.items.map(item => [item.id, structuredClone(item)]));
  state.incidents = new Map(snapshot.incidents.map(item => [item.id, structuredClone(item)]));
  // Existing incidents populate the map without replaying historical popups.
  for (const incident of snapshot.incidents) state.notified.add(incident.id);
  state.cursor = snapshot.cursor;
  return state;
}
export function aggregate(location, incidents) {
  const active = [...incidents.values()].filter(item => item.location_id === location.id && ['active', 'recovering'].includes(item.status));
  location.noise_status = active.some(item => item.status === 'active') ? 'excessive'
    : active.length ? 'recovering' : location.streams.some(item => item.noise_status === 'normal') ? 'normal' : 'unknown';
  location.unresolved_incident_ids = active.map(item => item.id);
  const missing = (location.devices || []).some(device => !device.enabled || !location.streams.some(stream => stream.device_id === device.id && stream.assignment_id === device.assignment_id));
  location.data_status = !location.streams.length ? 'unknown'
    : missing || location.streams.some(item => item.data_status === 'stale') ? 'stale'
    : location.streams.some(item => item.data_status === 'invalid') ? 'invalid' : 'fresh';
}
export function applyEvent(state, event, cursor) {
  if (!event.event_id || state.seen.has(event.event_id)) return null;
  // At-least-once delivery can repeat older events on reconnect; never regress state.
  if (cursor && state.cursor) {
    const [epoch, position] = cursor.split(':');
    const [oldEpoch, oldPosition] = state.cursor.split(':');
    if (epoch === oldEpoch && BigInt(position) <= BigInt(oldPosition)) return null;
  }
  state.seen.add(event.event_id);
  if (cursor) state.cursor = cursor;
  const incidentId = event.incident_id;
  if (incidentId && event.event_type.startsWith('incident.')) {
    const previous = state.incidents.get(incidentId) || {id: incidentId};
    state.incidents.set(incidentId, {...previous, ...event, id: incidentId,
      status: event.incident_status || previous.status, closed_reason: event.transition_reason,
      threshold_type: event.measurement_type || previous.threshold_type});
  }
  if (!state.locations.has(event.location_id) && Number.isFinite(event.latitude) && Number.isFinite(event.longitude)) {
    state.locations.set(event.location_id, {id: event.location_id, name: event.location_name || event.location_id.slice(0, 8),
      latitude: event.latitude, longitude: event.longitude, streams: [], devices: [],
      noise_status: 'unknown', data_status: 'unknown', unresolved_incident_ids: []});
  }
  const location = state.locations.get(event.location_id);
  if (location && event.device_id && ['device_registered', 'device_configured', 'device_reassigned', 'device_removed'].includes(event.transition_reason)) {
    location.devices = (location.devices || []).filter(device => device.id !== event.device_id);
    if (event.transition_reason !== 'device_removed') location.devices.push({id: event.device_id,
      enabled: event.device_enabled !== false, assignment_id: event.assignment_id});
    aggregate(location, state.incidents);
  }
  if (location && event.stream_id) {
    let stream = location.streams.find(item => item.id === event.stream_id);
    if (!stream) {
      stream = {id: event.stream_id, device_id: event.device_id, assignment_id: event.assignment_id};
      location.streams.push(stream);
    }
    for (const key of ['noise_status', 'data_status', 'measurement_value', 'measurement_type', 'weighting', 'interval_seconds', 'calibration_status', 'measured_at', 'threshold_version_id', 'recovery_streak', 'diagnostic']) {
      if (event[key] !== undefined && event[key] !== null) stream[key] = event[key];
    }
    if (Object.hasOwn(event, 'diagnostic')) stream.diagnostic = event.diagnostic;
    else if (event.transition_reason === 'measurement_evaluated') stream.diagnostic = null;
    if (event.event_type === 'incident.closed' && ['threshold_changed', 'device_reassigned'].includes(event.transition_reason)) {
      location.streams = location.streams.filter(item => item.id !== event.stream_id);
    }
    aggregate(location, state.incidents);
  }
  state.feed.unshift(event);
  state.feed.length = Math.min(state.feed.length, 100);
  if (event.event_type === 'incident.opened' && !state.notified.has(incidentId)) {
    state.notified.add(incidentId);
    return {kind: 'opening', event};
  }
  if (event.event_type === 'incident.resolved') return {kind: 'recovery', event};
  return null;
}
export function sseParser(onEvent) {
  let buffer = '';
  return chunk => {
    buffer += chunk;
    // CRLF can itself straddle network chunks. Normalize only complete frames.
    let match;
    while ((match = /\r?\n\r?\n/.exec(buffer))) {
      const frame = buffer.slice(0, match.index);
      buffer = buffer.slice(match.index + match[0].length);
      let id = null, type = 'message';
      const data = [];
      for (const line of frame.split(/\r?\n/)) {
        if (!line || line.startsWith(':')) continue;
        const colon = line.indexOf(':');
        const field = colon < 0 ? line : line.slice(0, colon);
        let value = colon < 0 ? '' : line.slice(colon + 1);
        if (value.startsWith(' ')) value = value.slice(1);
        if (field === 'id' && !value.includes('\0')) id = value;
        if (field === 'event') type = value;
        if (field === 'data') data.push(value);
      }
      if (data.length) onEvent({id, type, data: JSON.parse(data.join('\n'))});
    }
    if (buffer.length > 1_000_000) throw new Error('Oversized event frame');
  };
}

export function ageData(state, currentTime, staleSeconds) {
  for (const location of state.locations.values()) {
    for (const stream of location.streams) {
      if (['fresh', 'invalid'].includes(stream.data_status) && stream.measured_at &&
          currentTime - Date.parse(stream.measured_at) > staleSeconds * 1000) stream.data_status = 'stale';
    }
    aggregate(location, state.incidents);
  }
}

export function formatLevel(value, method) {
  if (value === null || value === undefined) return '—';
  if (typeof value !== 'number' || !Number.isFinite(value)) return 'Unavailable';
  const unit = method === 'dbfs_rms' ? 'dBFS' : method === 'spl_z_leq' ? 'dB SPL (Z)' : '(unknown method)';
  return `${value.toFixed(2)} ${unit}`;
}

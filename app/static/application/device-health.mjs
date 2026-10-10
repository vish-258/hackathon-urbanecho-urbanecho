import {deviceReporting} from './ui.mjs';

function currentStreams(device, current) {
  return (current?.streams || []).filter(stream => stream.device_id === device.id && stream.assignment_id === device.current_assignment_id);
}

// Contact proves communication, not a valid sound measurement. Only current
// assignment readings may supplement the device's persisted contact timestamp.
export function deviceConnection(device, current, {contact, now = Date.now(), staleSeconds = 30} = {}) {
  const contacts = [contact, device.last_contact_at, ...currentStreams(device, current).map(stream => stream.received_at)]
    .filter(value => Number.isFinite(Date.parse(value)));
  const past = contacts.filter(value => Date.parse(value) <= now);
  const latest = (past.length ? past : contacts).sort((a, b) => Date.parse(b) - Date.parse(a))[0];
  const age = now - Date.parse(latest);
  const connected = !!device.enabled && Number.isFinite(age) && age >= 0 && age <= staleSeconds * 1000;
  const label = !device.enabled ? 'Disabled' : !latest ? 'No contact yet' : age < 0 ? 'Check contact time' : connected ? 'Connected' : 'No recent contact';
  return {connected, label, contact: latest, ageSeconds: Number.isFinite(age) && age >= 0 ? Math.floor(age / 1000) : null};
}

export function deviceReadingStatus(device, meta, current) {
  const health = deviceReporting(device, current);
  const streams = currentStreams(device, current);
  const latest = streams.filter(stream => Number.isFinite(Date.parse(stream.received_at)))
    .sort((a, b) => Date.parse(b.received_at) - Date.parse(a.received_at))[0];
  const needsCalibration = (meta?.current_threshold?.threshold_type ?? meta?.threshold_type) === 'spl_z_leq' && !device.calibration;
  const label = !device.enabled ? 'Disabled' : needsCalibration ? 'Calibration required'
    : health.reporting ? (health.attention ? 'Reporting · needs attention' : 'Reporting')
    : streams.length || device.last_contact_at ? 'No recent usable data' : 'Awaiting first usable reading';
  return {...health, label, needsCalibration, latest};
}

export function summarizeLocationConnection(devices, meta, current, {contacts = new Map(), now = Date.now(), staleSeconds = 30} = {}) {
  const assigned = devices.filter(device => device.location_id === meta.id);
  const enabled = assigned.filter(device => device.enabled);
  const states = enabled.map(device => {
    const connection = deviceConnection(device, current, {contact: contacts.get(device.id), now, staleSeconds});
    return {connection, reading: deviceReadingStatus({...device, last_contact_at: connection.contact}, meta, current)};
  });
  const connected = states.filter(item => item.connection.connected).length;
  const calibrationCount = states.filter(item => item.reading.needsCalibration).length;
  const times = states.map(item => item.connection.contact).filter(Boolean);
  const past = times.filter(time => Date.parse(time) <= now);
  const lastContact = (past.length ? past : times).sort((a, b) => Date.parse(b) - Date.parse(a))[0];
  const label = !assigned.length ? 'No devices' : !enabled.length ? 'Devices disabled' : connected === enabled.length ? 'Connected'
    : connected ? 'Some devices disconnected' : states.some(item => item.connection.label === 'Check contact time') ? 'Check contact time'
    : lastContact ? 'No recent contact' : 'No contact yet';
  return {connected, total: enabled.length, label, needsCalibration: calibrationCount > 0, calibrationCount,
    reporting: states.filter(item => item.reading.reporting).length,
    attention: states.filter(item => item.reading.attention).length, lastContact};
}

// Contact-only polling deliberately does not overwrite configuration or live
// measurement state. The independent age clock still runs if a request hangs.
export function startDeviceContactMonitor({api, signal, contacts = new Map(), now = () => Date.now(), onUpdate = () => {}, onError = () => {}}) {
  const controller = new AbortController();
  let pollTimer, ageTimer;
  function stop() {
    controller.abort();
    clearTimeout(pollTimer); clearTimeout(ageTimer);
    signal?.removeEventListener('abort', stop);
  }
  signal?.addEventListener('abort', stop, {once: true});
  if (signal?.aborted) { stop(); return stop; }
  function ageContacts() {
    if (controller.signal.aborted) return;
    onUpdate();
    if (!controller.signal.aborted) ageTimer = setTimeout(ageContacts, 1000);
  }
  async function poll() {
    if (controller.signal.aborted) return;
    try {
      const items = [];
      let offset = 0;
      while (true) {
        const page = await api(`/devices?limit=200&offset=${offset}`, {signal: controller.signal});
        if (controller.signal.aborted) return;
        items.push(...page.items); offset += page.items.length;
        if (offset >= page.total || !page.items.length) break;
      }
      for (const item of items) {
        const time = Date.parse(item.last_contact_at), previous = Date.parse(contacts.get(item.id));
        if (Number.isFinite(time) && (!Number.isFinite(previous) || time > previous || previous > now())) contacts.set(item.id, item.last_contact_at);
      }
      onError(null); onUpdate();
    } catch (error) {
      if (!controller.signal.aborted) onError(error);
    } finally {
      if (!controller.signal.aborted) pollTimer = setTimeout(poll, 2000);
    }
  }
  ageTimer = setTimeout(ageContacts, 1000);
  void poll();
  return stop;
}

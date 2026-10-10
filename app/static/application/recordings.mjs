import { el, button, field, select, formatTime, deviceLabel, empty } from './ui.mjs';
import { classificationNode, classificationPending, classificationPollDelay } from './classification.mjs';

const PAGE_SIZE = 10;
const recordingId = recording => recording.audio_chunk_id || recording.id;
const isGroup = recording => recording?.kind === 'recording_group';
const readyGroup = recording => recording.status === 'ready' && recording.file_available === true && !!recording.revision;
const groupLength = recording => `${Number.isFinite(recording.target_duration_seconds) ? recording.target_duration_seconds : 10}-second recording`;
const sourcePrefix = recording => recording.source_kind === 'mixed' ? 'Includes SIMULATED audio · ' : isSimulated(recording) ? 'SIMULATED · ' : '';
const lengthLabel = recording => Number.isFinite(recording.duration_seconds) ? `${recording.duration_seconds.toLocaleString(undefined, { maximumFractionDigits: 2 })} ${recording.duration_seconds === 1 ? 'second' : 'seconds'}` : 'Duration unavailable';
const timingLabel = recording => {
  const interval = recording.capture_interval_ms;
  if (!Number.isFinite(interval) || interval <= 0) return lengthLabel(recording);
  const seconds = interval / 1000;
  return `${lengthLabel(recording)} · Capture spacing: ${seconds.toLocaleString(undefined, { maximumFractionDigits: 3 })} ${seconds === 1 ? 'second' : 'seconds'}`;
};
const isSimulated = recording => recording.source_kind === 'simulated' || /SIMULATED|SYNTHETIC/i.test(`${recording.location_snapshot?.name || ''} ${recording.calibration_version || recording.calibration_snapshot?.version || ''}`);
const calibrationLabel = recording => recording.calibration_status === 'mixed' ? 'Mixed calibration status' : recording.calibration_present === false || recording.calibration_status === 'calibration_required' || (Object.hasOwn(recording, 'calibration_snapshot') && !recording.calibration_snapshot)
  ? 'Uncalibrated audio' : recording.calibration_present || recording.calibration_snapshot ? 'Calibration profile saved' : 'Calibration not specified';

// A single player per view keeps authenticated recordings out of public URLs.
// Media is fetched only after Listen, and native controls start playback.
export function createRecordingPlayer(ctx, { timezone, urls = URL } = {}) {
  const element = el('div', '', 'recording-player'); element.hidden = true;
  element.tabIndex = -1; element.setAttribute('role', 'region'); element.setAttribute('aria-label', 'Saved recording');
  const title = el('strong'), status = el('p', '', 'muted small'); status.setAttribute('role', 'status');
  const audio = el('audio'); audio.controls = true; audio.preload = 'none'; audio.hidden = true;
  audio.setAttribute('aria-label', 'Saved recording playback');
  const classification = el('div'), classificationFeedback = el('p', '', 'muted small');
  classificationFeedback.setAttribute('role', 'status');
  const close = button('Close recording', () => clear(true), 'secondary');
  const refreshEstimate = button('Refresh sound estimate', () => refreshClassification(), 'secondary');
  const retryEstimate = button('Retry sound classification', () => retryClassification(), 'secondary'); retryEstimate.hidden = true;
  element.append(title, status, audio, classification, classificationFeedback, close, refreshEstimate, retryEstimate);
  let request, url, disposed = false, selected, returnFocus, returnRegion, classificationTimer, classificationRequest, classificationFailures = 0;
  function stopClassification() { clearTimeout(classificationTimer); classificationRequest?.abort(); classificationRequest = null; }
  function renderClassification() {
    classification.replaceChildren(classificationNode(isGroup(selected) ? { status: 'not_requested' } : selected?.classification, timezone || selected?.location_snapshot?.timezone));
    updateClassificationActions();
  }
  function updateClassificationActions() {
    refreshEstimate.hidden = isGroup(selected) || selected?.classification?.status === 'not_requested';
    retryEstimate.hidden = isGroup(selected) || selected?.classification?.status !== 'failed';
    retryEstimate.disabled = refreshEstimate.disabled || selected?.classification?.worker_status === 'disabled';
    retryEstimate.title = selected?.classification?.worker_status === 'disabled' ? 'Automatic sound classification is switched off.' : '';
  }
  function scheduleClassification() {
    clearTimeout(classificationTimer);
    const delay = isGroup(selected) ? null : classificationPollDelay(selected?.classification);
    if (!disposed && Number.isFinite(delay)) classificationTimer = setTimeout(refreshClassification, Math.max(delay, Math.min(30000, 3000 * 2 ** Math.min(classificationFailures, 4))));
  }
  function refreshClassification() { return requestClassification(false); }
  function retryClassification() {
    if (selected?.classification?.status !== 'failed' || selected.classification.worker_status === 'disabled') return;
    return requestClassification(true);
  }
  async function requestClassification(retry) {
    if (disposed || !selected || isGroup(selected) || ctx.signal?.aborted || typeof ctx.api !== 'function') return;
    stopClassification(); const own = new AbortController(); classificationRequest = own;
    const id = recordingId(selected); refreshEstimate.disabled = true; updateClassificationActions();
    if (retry) classificationFeedback.textContent = 'Requesting another sound estimate…';
    try {
      const result = await ctx.api(`/audio/${encodeURIComponent(id)}/classification`, { signal: own.signal, ...(retry ? { method: 'POST' } : {}) });
      if (disposed || own.signal.aborted || recordingId(selected || {}) !== id) return;
      selected = { ...selected, classification: result }; classificationFailures = 0; classificationFeedback.textContent = ''; renderClassification();
    } catch (error) {
      if (!disposed && !own.signal.aborted) {
        classificationFailures++;
        classificationFeedback.textContent = retry ? 'Sound classification could not be retried. Your recording is unchanged; try again.' : 'Sound estimate update unavailable. Use Refresh sound estimate to try again.';
      }
    } finally {
      if (!disposed && !own.signal.aborted) { refreshEstimate.disabled = false; updateClassificationActions(); scheduleClassification(); }
    }
  }
  function updateClassification(recording) {
    if (!selected || isGroup(selected) || recordingId(selected) !== recordingId(recording) || disposed) return;
    if (!recording.classification) return;
    const sameModel = (!recording.classification.model_version || !selected.classification?.model_version || recording.classification.model_version === selected.classification.model_version)
      && (!recording.classification.mapping_version || !selected.classification?.mapping_version || recording.classification.mapping_version === selected.classification.mapping_version);
    if (sameModel && selected.classification?.status === 'completed' && classificationPending(recording.classification)) return;
    if (JSON.stringify(selected.classification) === JSON.stringify(recording.classification)) return;
    stopClassification(); selected = { ...selected, classification: recording.classification };
    classificationFailures = 0; classificationFeedback.textContent = ''; refreshEstimate.disabled = false; renderClassification(); scheduleClassification();
  }
  function release() {
    request?.abort(); request = null;
    stopClassification(); selected = null; classificationFailures = 0; classificationFeedback.textContent = ''; refreshEstimate.disabled = false; updateClassificationActions();
    audio.pause?.(); audio.removeAttribute('src'); audio.load?.(); audio.hidden = true;
    if (url) { urls.revokeObjectURL(url); url = null; }
  }
  function clear(restoreFocus = false) {
    release(); element.hidden = true; status.textContent = '';
    if (restoreFocus && returnFocus) {
      const target = returnFocus.isConnected ? returnFocus : [...document.querySelectorAll('[data-recording-id]')].find(control => control.getAttribute('data-recording-id') === returnFocus.getAttribute('data-recording-id') && !control.disabled);
      (target || (returnRegion?.isConnected ? returnRegion : null))?.focus({ preventScroll: true });
    }
    returnFocus = null; returnRegion = null;
  }
  async function listen(recording, trigger = null, fallback = null) {
    if (disposed || ctx.signal?.aborted || (isGroup(recording) && !readyGroup(recording))) return;
    release(); element.hidden = false; returnFocus = trigger; returnRegion = fallback;
    element.scrollIntoView?.({ behavior: globalThis.matchMedia?.('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'nearest' });
    element.focus?.({ preventScroll: true });
    const own = new AbortController(); request = own;
    selected = recording; renderClassification(); scheduleClassification();
    title.textContent = `${sourcePrefix(recording)}${deviceLabel(recording, ctx.devices)} · ${formatTime(recording.captured_at || recording.measured_at, timezone || recording.location_snapshot?.timezone)} · ${isGroup(recording) ? groupLength(recording) : timingLabel(recording)} · ${calibrationLabel(recording)}`;
    status.textContent = 'Loading saved recording…';
    try {
      const blob = isGroup(recording)
        ? await ctx.api(`/recordings/${encodeURIComponent(recording.id)}/file?${new URLSearchParams({ revision: recording.revision })}`, { signal: own.signal, responseType: 'blob' })
        : await ctx.audioFile(recordingId(recording), { signal: own.signal });
      if (disposed || own.signal.aborted || request !== own) return;
      if (!blob?.size) throw new Error('This recording has no audio available.');
      url = urls.createObjectURL(blob); audio.src = url; audio.hidden = false; audio.load?.();
      status.textContent = 'Ready. Press play to listen. Playback volume is not a calibrated sound-level measurement.';
    } catch (error) {
      if (!disposed && !own.signal.aborted && request === own) status.textContent = isGroup(recording) && error.status === 409 ? 'This recording changed or is not ready. Refresh recordings, then choose Listen again.' : `Recording unavailable. ${error.message || 'Please try again.'}`;
    }
  }
  audio.addEventListener('error', () => {
    if (!disposed && url) status.textContent = 'This browser could not play the recording. Try another recording or browser; the saved original is unchanged.';
  });
  function listenButton(recording, fallback = null) {
    const control = button('Listen', () => listen(recording, control, fallback), 'secondary recording-listen');
    control.setAttribute('data-recording-id', recordingId(recording) || '');
    control.setAttribute('aria-label', `Listen to ${deviceLabel(recording, ctx.devices)} recording captured ${formatTime(recording.captured_at || recording.measured_at, timezone || recording.location_snapshot?.timezone)}`);
    if (isGroup(recording) && !readyGroup(recording)) { control.disabled = true; control.title = 'Only complete continuous recordings are available to listen to.'; }
    if (!recordingId(recording)) { control.disabled = true; control.title = 'No saved recording is linked to this reading.'; }
    return control;
  }
  function dispose() { disposed = true; clear(); ctx.signal?.removeEventListener('abort', dispose); }
  ctx.signal?.addEventListener('abort', dispose, { once: true });
  if (ctx.signal?.aborted) dispose();
  return { element, listen, listenButton, clear, dispose, updateClassification };
}

function groupStatus(recording) {
  const received = Number.isFinite(recording.duration_seconds) ? recording.duration_seconds.toLocaleString(undefined, { maximumFractionDigits: 2 }) : '0';
  const target = recording.target_duration_seconds || 10;
  if (readyGroup(recording)) return 'Ready to listen';
  if (recording.status === 'collecting') return `Collecting · ${received} of ${target} seconds received`;
  if (recording.status === 'partial') return `Incomplete recording · ${received} of ${target} seconds received · missing or incompatible audio`;
  if (recording.status === 'no_audio') return 'No usable audio';
  if (recording.status === 'failed') return 'Recording unavailable';
  return 'Preparing recording';
}

export function createRecordingsBrowser(ctx, { locationId, timezone, devices = [], player }) {
  const element = el('div', '', 'recordings-browser'), controls = el('div', '', 'view-controls');
  const device = select([['', 'All devices'], ...devices.map(item => [item.id, item.external_id || item.id])]);
  device.setAttribute('aria-label', 'Recording device');
  const feedback = el('p', '', 'muted small'); feedback.setAttribute('role', 'status');
  const list = el('div', '', 'recordings-list'), footer = el('div', '', 'view-pagination'), count = el('span', '', 'muted');
  list.tabIndex = -1; list.setAttribute('role', 'region'); list.setAttribute('aria-label', 'Saved recordings');
  const listenControls = new Map();
  let offset = 0, total = 0, boundary, request, disposed = false, timer, failures = 0;
  const refresh = button('Refresh recordings', () => { offset = 0; boundary = null; return load(); }, 'secondary');
  const previous = button('← Previous recordings', () => { offset = Math.max(0, offset - PAGE_SIZE); if (offset === 0) boundary = null; return load(); }, 'secondary');
  const next = button('Next recordings →', () => { offset += PAGE_SIZE; return load(); }, 'secondary');
  const pages = el('div', '', 'view-pagination-buttons'); pages.append(previous, next);
  footer.append(count, pages); controls.append(field('Device', device), refresh);
  element.append(el('p', 'Each ready recording contains 10 seconds of continuous sound. Incomplete recordings are labelled and cannot be played. The newest page updates every 10 seconds; older pages keep their place. Sound categories are shown on incident details.', 'muted small'), controls, feedback, list, footer);
  async function load({ automatic = false } = {}) {
    if (disposed || ctx.signal?.aborted) return;
    clearTimeout(timer);
    if (!automatic) failures = 0;
    request?.abort(); const own = new AbortController(); request = own;
    if (automatic && offset === 0) boundary = null;
    boundary ??= new Date(ctx.serverNow?.() ?? Date.now()).toISOString();
    const query = new URLSearchParams({ location_id: locationId, limit: String(PAGE_SIZE), offset: String(offset), until: boundary, received_until: boundary });
    if (device.value) query.set('device_id', device.value);
    if (!automatic) feedback.textContent = 'Loading saved recordings…';
    if (!automatic) { refresh.disabled = true; previous.disabled = true; next.disabled = true; }
    try {
      const result = await ctx.api(`/recordings?${query}`, { signal: own.signal });
      if (disposed || own.signal.aborted || request !== own) return;
      const focusedId = [...listenControls].find(([, control]) => control === document.activeElement)?.[0];
      total = result.total; failures = 0; list.replaceChildren(); listenControls.clear();
      for (const item of result.items) {
        const recording = { ...item, kind: 'recording_group' };
        const row = el('article', '', 'recording-row'), description = el('div', '', 'recording-description');
        description.append(el('strong', `${sourcePrefix(recording)}${deviceLabel(recording, ctx.devices)}`), el('span', formatTime(recording.captured_at, timezone)), el('small', `${groupLength(recording)} · ${calibrationLabel(recording)} · ${recording.location_snapshot?.name || 'Saved location'}`, 'muted'), el('span', groupStatus(recording)));
        const missing = Array.isArray(recording.missing_sequences) ? recording.missing_sequences.length : 0;
        if (missing && recording.status !== 'collecting') description.append(el('small', `${missing} missing portion(s). Missing sound is not replaced with silence.`, 'muted'));
        if (recording.issues?.length) description.append(el('small', `Recording notes: ${recording.issues.map(issue => String(issue).replaceAll('_', ' ')).join('; ')}`, 'muted'));
        const listen = player.listenButton(recording, list); listenControls.set(recording.id, listen);
        row.append(description, listen); list.append(row);
      }
      if (!result.items.length) list.append(empty('No saved 10-second recordings yet', 'Recordings will appear here as your device sends continuous audio. Individual readings remain available in the history below.'));
      count.textContent = total ? `${offset + 1}–${Math.min(offset + result.items.length, total)} of ${total} recordings` : '0 recordings';
      feedback.textContent = '';
      if (focusedId) {
        const replacement = listenControls.get(focusedId);
        (replacement && !replacement.disabled ? replacement : list).focus?.({ preventScroll: true });
      }
    } catch (error) {
      if (!disposed && !own.signal.aborted && request === own) {
        failures++;
        if (!automatic) { list.replaceChildren(); total = 0; count.textContent = ''; }
        feedback.textContent = automatic ? 'Recording update unavailable; retrying. Saved recordings remain below.' : `Saved recordings could not be loaded. ${error.message || 'Please try again.'}`;
      }
    } finally {
      if (!disposed && !own.signal.aborted && request === own) {
        refresh.disabled = false; previous.disabled = offset === 0; next.disabled = offset + PAGE_SIZE >= total;
        timer = setTimeout(() => load({ automatic: true }), Math.min(30000, 10000 * 2 ** Math.min(failures, 2)));
      }
    }
  }
  device.addEventListener('change', () => { offset = 0; boundary = null; void load(); });
  function dispose() { disposed = true; clearTimeout(timer); request?.abort(); ctx.signal?.removeEventListener('abort', dispose); }
  ctx.signal?.addEventListener('abort', dispose, { once: true });
  const loaded = load();
  return { element, loaded, dispose };
}

import { el, button, badge, formatTime } from './ui.mjs';
import { classificationNode, classificationPending } from './classification.mjs';

const seconds = value => Number.isFinite(value) ? `${value.toLocaleString(undefined, { maximumFractionDigits: 2 })} s` : 'unavailable';
const isBusy = result => ['pending', 'processing'].includes(result?.status);

// Analysis metadata may change while a user listens. A loaded WAV is bound to its
// saved revision and is deliberately kept until the user chooses a newer version.
export function createIncidentAudio(ctx, { incidentId, urls = URL } = {}) {
  const element = el('section', '', 'panel view-panel incident-audio');
  element.setAttribute('tabindex', '-1');
  element.append(el('h2', 'Incident audio and sound category'), el('p', 'Listen to the whole incident with the available context before and after it. Its length follows the incident, rather than the regular 10-second recording files. The sound estimate uses the incident itself; the listening context before and after is excluded.', 'muted small'));
  const metadata = el('div'), feedback = el('p', 'Loading incident audio…', 'muted small'); feedback.setAttribute('role', 'status');
  const actions = el('div', '', 'incident-audio-actions');
  const refresh = button('Refresh incident audio', () => load(), 'secondary');
  const generate = button('Generate incident audio', () => load(true), 'secondary');
  const listen = button('Listen to incident audio', () => loadAudio(), 'primary'); listen.disabled = true;
  const close = button('Close incident audio', () => releaseAudio(), 'secondary'); close.hidden = true;
  actions.append(listen, refresh, generate, close);
  const playbackStatus = el('p', '', 'muted small'); playbackStatus.setAttribute('role', 'status');
  const playbackVersion = el('p', '', 'muted small');
  const audio = el('audio'); audio.controls = true; audio.preload = 'none'; audio.hidden = true;
  audio.setAttribute('aria-label', 'Incident audio playback');
  element.append(metadata, feedback, actions, playbackStatus, playbackVersion, audio);
  let result, incident, request, audioRequest, timer, url, loadedRevision, disposed = false, failures = 0;
  const timezone = () => incident?.location_snapshot?.timezone || ctx.locations?.find(item => item.id === incident?.location_id)?.timezone || 'UTC';
  const revision = () => result?.audio?.revision || result?.revision;
  function controls() {
    refresh.disabled = !!request;
    generate.textContent = result?.generated_at ? 'Recalculate incident audio' : 'Generate incident audio';
    generate.disabled = !!request || result?.status === 'processing' || (result?.status === 'pending' && !!revision()) || result?.worker_status === 'disabled';
    listen.disabled = !!audioRequest || !result?.audio?.available || !revision();
    const newer = !!url && loadedRevision !== revision();
    listen.textContent = newer ? 'Load newer incident audio' : 'Listen to incident audio';
    playbackVersion.textContent = newer ? 'New audio is available. Your current playback stays unchanged until you load the newer version.' : url ? 'Loaded the saved version you selected. Updates will not interrupt playback.' : '';
    close.hidden = !url && !audioRequest;
  }
  function render() {
    const saved = result?.audio || {}, provisional = result?.provisional;
    let label = 'Waiting for incident processing';
    if (result?.status === 'failed') label = 'Incident audio processing failed';
    else if (result?.status === 'processing') label = 'Processing incident audio';
    else if (result?.status === 'pending') label = 'Incident audio queued';
    else if (provisional) label = incident?.ended_at || saved.incident_closed_at ? 'Waiting for recordings after the incident' : 'Incident in progress · provisional audio';
    else if (result?.status === 'completed') label = saved.available ? (saved.gap_count > 0 || saved.truncated || saved.coverage_percent < 99.99 ? 'Partial incident audio' : 'Incident audio ready') : 'No recorded audio';
    const nodes = [badge(label, 'neutral')];
    if (result?.worker_status === 'disabled' && (isBusy(result) || provisional)) nodes.push(el('p', 'Incident sound processing is switched off. Any saved audio below remains available.', 'muted small'));
    else if (result?.worker_status === 'unavailable' && (isBusy(result) || provisional)) nodes.push(el('p', result.worker_error || 'Incident sound processing is temporarily unavailable. The app will check again.', 'muted small'));
    if (provisional) nodes.push(el('p', 'This is a provisional result. It will be updated while the incident continues and after the listening context arrives.', 'muted small'));
    if (result?.error) nodes.push(el('p', String(result.error), 'muted small'));
    if (saved.source_kind === 'simulated' || saved.source_kind === 'mixed') nodes.push(badge(saved.source_kind === 'simulated' ? 'SIMULATED AUDIO' : 'Includes SIMULATED audio', 'warning'));
    if (saved.started_at || saved.ended_at) nodes.push(el('p', `Listening window: ${formatTime(saved.started_at, timezone())} – ${formatTime(saved.ended_at, timezone())}`, 'muted small'));
    if (Number.isFinite(saved.duration_seconds)) {
      const coverage = Number.isFinite(saved.coverage_percent) ? `${saved.coverage_percent.toLocaleString(undefined, { maximumFractionDigits: 2 })}%` : 'unavailable';
      nodes.push(el('p', `${seconds(saved.duration_seconds)} playable audio · ${seconds(saved.window_duration_seconds)} elapsed window · ${coverage} coverage · ${saved.recording_count || 0} saved recording(s)`, 'incident-audio-coverage'));
      nodes.push(el('p', `Listening context: ${seconds(saved.context_before_seconds)} before and ${seconds(saved.context_after_seconds)} after. Playback volume does not establish calibrated sound-level accuracy.`, 'muted small'));
      if (Number.isFinite(saved.core_coverage_seconds) && Number.isFinite(saved.core_window_duration_seconds)) nodes.push(el('p', `Incident audio: ${seconds(saved.core_coverage_seconds)} of ${seconds(saved.core_window_duration_seconds)} recorded during the incident itself.`, 'muted small'));
    }
    if (saved.gap_count || saved.excluded_count || saved.truncated) {
      nodes.push(el('p', `${saved.gap_count || 0} missing interval(s) · ${saved.excluded_count || 0} recording(s) excluded. Gaps are skipped in playback; no silence is added. Playback can be shorter than the elapsed incident window.`, 'report-coverage-note'));
      if (saved.truncated) nodes.push(el('p', `Audio is limited: ${saved.truncation_reason || 'the saved assembly reached its size limit'}. This is not the entire incident window.`, 'report-coverage-note'));
      if (saved.gaps?.length) {
        const details = el('details'), list = el('ul'); details.append(el('summary', 'Missing audio intervals'));
        for (const gap of saved.gaps.slice(0, 20)) list.append(el('li', `${formatTime(gap.started_at, timezone())} – ${formatTime(gap.ended_at, timezone())} (${seconds(gap.duration_seconds)})`));
        details.append(list); if (saved.gap_count > 20 || saved.gaps_list_truncated) details.append(el('p', `Showing the first ${Math.min(20, saved.gaps.length)} missing intervals.`, 'muted small'));
        nodes.push(details);
      }
    }
    if (result?.status === 'completed' && !saved.available) nodes.push(el('p', 'There is no usable saved audio for this incident window. Missing recordings cannot be reconstructed.', 'muted small'));
    if (result?.classification) {
      nodes.push(classificationNode(result.classification, timezone(), { scope: 'incident' }));
      if (Number.isFinite(result.classification.analyzed_duration_seconds)) nodes.push(el('p', `Sound model analysed ${seconds(result.classification.analyzed_duration_seconds)} of incident audio. Context audio and unusable segments are excluded.`, 'muted small'));
      if (result.classification.classified_at) nodes.push(el('p', `Sound estimate saved: ${formatTime(result.classification.classified_at, timezone())}`, 'muted small'));
    }
    if (result?.generated_at) nodes.push(el('p', `Audio last generated: ${formatTime(result.generated_at, timezone())}`, 'muted small'));
    metadata.replaceChildren(...nodes); controls();
  }
  function schedule() {
    clearTimeout(timer);
    if (disposed || ctx.signal?.aborted || result?.worker_status === 'disabled') return;
    if (!failures && result?.status === 'failed') return;
    if (!failures && !isBusy(result) && !result?.provisional && !classificationPending(result?.classification)) return;
    let delay = result?.worker_status === 'unavailable' ? 30000 : isBusy(result) || classificationPending(result?.classification) ? 3000 : 10000;
    if (failures) delay = Math.max(delay, Math.min(30000, 3000 * 2 ** Math.min(failures, 4)));
    timer = setTimeout(() => load(false, true), delay);
  }
  async function load(recalculate = false, automatic = false) {
    if (disposed || ctx.signal?.aborted) return;
    clearTimeout(timer); request?.abort(); const own = new AbortController(); request = own; controls();
    if (!automatic) feedback.textContent = recalculate ? 'Requesting incident audio and sound analysis…' : 'Refreshing incident audio…';
    try {
      const next = await ctx.api(`/incidents/${encodeURIComponent(incidentId)}/analysis`, { signal: own.signal, ...(recalculate ? { method: 'POST' } : {}) });
      if (disposed || own.signal.aborted || request !== own) return;
      result = next; failures = 0; feedback.textContent = ''; render();
    } catch (error) {
      if (!disposed && !own.signal.aborted && request === own) { failures++; feedback.textContent = `Incident audio update unavailable. ${error.message || 'Please try Refresh incident audio.'}`; }
    } finally {
      if (!disposed && request === own && !own.signal.aborted) { request = null; controls(); schedule(); }
    }
  }
  function releaseAudio() {
    audioRequest?.abort(); audioRequest = null;
    audio.pause?.(); audio.removeAttribute('src'); audio.load?.(); audio.hidden = true;
    if (url) { urls.revokeObjectURL(url); url = null; }
    loadedRevision = null; playbackStatus.textContent = ''; controls();
  }
  async function loadAudio() {
    if (disposed || ctx.signal?.aborted || !result?.audio?.available || !revision()) return;
    releaseAudio(); const own = new AbortController(); audioRequest = own;
    const selectedRevision = revision(); controls(); playbackStatus.textContent = 'Loading saved incident audio…';
    try {
      const query = new URLSearchParams({ revision: selectedRevision });
      const blob = await ctx.api(`/incidents/${encodeURIComponent(incidentId)}/audio/file?${query}`, { signal: own.signal, responseType: 'blob' });
      if (disposed || own.signal.aborted || audioRequest !== own) return;
      if (!blob?.size) throw new Error('No audio samples were returned.');
      url = urls.createObjectURL(blob); loadedRevision = selectedRevision;
      audio.src = url; audio.hidden = false; audio.load?.(); playbackStatus.textContent = 'Ready. Press play to listen to the saved incident audio.';
    } catch (error) {
      if (!disposed && !own.signal.aborted && audioRequest === own) {
        playbackStatus.textContent = error.status === 409 ? 'The saved audio changed before it could load. Refresh incident audio, then choose Listen again.' : `Incident audio unavailable. ${error.message || 'Please try again.'}`;
      }
    } finally {
      if (!disposed && audioRequest === own && !own.signal.aborted) { audioRequest = null; controls(); }
    }
  }
  audio.addEventListener('error', () => { if (!disposed && url) playbackStatus.textContent = 'This browser could not play the incident audio. The saved originals are unchanged.'; });
  async function open() {
    if (disposed || ctx.signal?.aborted) return;
    element.scrollIntoView?.({ block: 'start' });
    element.focus?.({ preventScroll: true });
    // A shortcut reveals the existing player without resetting a loaded version.
    // Pending/unavailable audio stays explicit; never fall back to a raw clip.
    if (!result?.audio?.available || !revision()) await load();
    if (!url && !audioRequest) await loadAudio();
  }
  function dispose() { if (disposed) return; disposed = true; clearTimeout(timer); request?.abort(); request = null; releaseAudio(); ctx.signal?.removeEventListener('abort', dispose); }
  ctx.signal?.addEventListener('abort', dispose, { once: true });
  const loaded = ctx.signal?.aborted ? (dispose(), Promise.resolve()) : load();
  return { element, loaded, open, refresh: () => load(), setIncident(record) { incident = record; if (result && !disposed) render(); }, dispose };
}

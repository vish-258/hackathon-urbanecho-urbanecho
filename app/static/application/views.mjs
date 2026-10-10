import { el, button, formatLevel, formatTime, short, badge, field, empty, locationName, errorBox, freshnessLabel, deviceLabel } from './ui.mjs';
import { liveChanges, mergeChanges, locationChanges, coalesceAsync } from './refresh.mjs';
import { deviceConnection, deviceReadingStatus, startDeviceContactMonitor } from './device-health.mjs';
import { createRecordingPlayer, createRecordingsBrowser } from './recordings.mjs';
import { classificationNode } from './classification.mjs';
import { createIncidentAudio } from './incident-audio.mjs';

const PAGE_SIZE = 25;
const CHART_PAGE = 200;
const SVG_NS = 'http://www.w3.org/2000/svg';
const SERIES_COLORS = ['var(--chart-series-1)', 'var(--chart-series-2)', 'var(--chart-series-3)', 'var(--chart-series-4)', 'var(--chart-series-5)', 'var(--chart-series-6)'];
const SERIES_DASHES = ['', '7 3', '2 3', '9 3 2 3', '4 3', '1 3'];
const isNumber = value => typeof value === 'number' && Number.isFinite(value);
const readable = value => String(value || 'unavailable').replaceAll('_', ' ');
const unit = method => method === 'spl_z_leq' ? 'dB SPL (Z)' : method === 'dbfs_rms' ? 'dBFS RMS' : 'dB';

// Shift a calendar date in the location's timezone, rather than subtracting
// 24 hours from an instant (which is wrong around midnight and DST changes).
export function localReportingDate(timezone, offset = -1, now = new Date()) {
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone: timezone || 'UTC', year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(now);
  const part = name => parts.find(item => item.type === name).value;
  const calendar = new Date(`${part('year')}-${part('month')}-${part('day')}T12:00:00Z`);
  calendar.setUTCDate(calendar.getUTCDate() + offset);
  return calendar.toISOString().slice(0, 10);
}

export function formatDailyCoverage(value) {
  if (!isNumber(value)) return 'Unavailable';
  if (value === 0) return '0%';
  if (value > 0 && value < .0001) return '<0.0001%';
  // Never round incomplete coverage up to 100%, or a short recording down to 0%.
  if (value < 100 && value > 99.9999) return '>99.9999%';
  return `${new Intl.NumberFormat(undefined, { maximumFractionDigits: 4 }).format(value)}%`;
}

function recordedTime(seconds) {
  if (!isNumber(seconds)) return 'Unavailable';
  if (seconds === 0) return '0 seconds';
  if (seconds < .001) return '<0.001 seconds';
  if (seconds < 60) return `${new Intl.NumberFormat(undefined, { maximumFractionDigits: 3 }).format(seconds)} seconds`;
  const hours = Math.floor(seconds / 3600), minutes = Math.floor(seconds % 3600 / 60), remainder = seconds % 60;
  return [hours ? `${hours}h` : '', minutes ? `${minutes}m` : '', remainder ? `${new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(remainder)}s` : ''].filter(Boolean).join(' ');
}

const reportPending = report => ['queued', 'processing'].includes(report?.status);
const summaryStatus = stats => stats.coverage_status === 'complete' ? 'Complete coverage' : stats.coverage_status === 'partial' ? 'Partial data' : 'No usable data';
const summarySource = definition => definition.source_kind === 'simulated' ? 'SIMULATED' : 'Recorded measurements';
const summaryLevel = (value, definition) => isNumber(value) ? formatLevel(value, definition.measurement_type) : 'No usable data';

function dailySummaryCard(summary, timezone, compact = false, devices = []) {
  const definition = summary.definition || {}, stats = summary.statistics || {};
  const panel = compact ? el('article', '', 'report-compact') : section(definition.measurement_type === 'dbfs_rms' ? 'Digital signal levels' : 'Sound pressure · Z weighting');
  const status = el('div', '', 'report-status');
  status.append(badge(summarySource(definition), definition.source_kind === 'simulated' ? 'warning' : 'neutral'), badge(summaryStatus(stats), stats.coverage_status === 'complete' ? 'good' : 'neutral'));
  if (stats.is_provisional) status.append(badge('Today · provisional', 'warning'));
  panel.append(status);
  const fields = el('div', '', 'report-metric-grid');
  fields.append(
    metric('Average of recorded time', summaryLevel(stats.average_db, definition), 'Sound-energy and duration weighted'),
    metric('Data coverage', formatDailyCoverage(stats.coverage_percent), `${recordedTime(stats.usable_duration_seconds)} of ${recordedTime(stats.day_duration_seconds)}`),
  );
  if (!compact) fields.append(
    metric('Minimum recorded level', summaryLevel(stats.minimum_db, definition), 'Lowest eligible recording level'),
    metric('Maximum recorded level', summaryLevel(stats.maximum_db, definition), 'Highest eligible recording level'),
    metric('Eligible measurements', String(stats.measurement_count ?? 0), `${stats.device_count ?? 0} device(s)`),
    metric('Saved incident starts', String(stats.incident_count ?? 0), 'Incidents that began during this local day'),
    metric('Usable recorded time', recordedTime(stats.usable_duration_seconds), 'Overlapping time counted once'),
    metric('Missing recorded time', recordedTime(stats.missing_duration_seconds), 'Missing time is never treated as silence'),
  );
  panel.append(fields);
  const generated = el('p', `Last generated: ${formatTime(summary.calculated_at, timezone)}`, 'muted report-generated');
  generated.title = summary.calculated_at || '';
  panel.append(generated);
  if (compact) return panel;
  if (definition.source_kind === 'simulated') panel.append(el('p', 'Demonstration recordings only. These values do not represent real calibrated environmental measurements.', 'view-demo-note'));
  if (stats.coverage_status !== 'complete') panel.append(el('p', stats.coverage_status === 'no_data' ? 'No usable measurements were available for this definition. No sound level has been substituted.' : 'This result describes only the recorded time shown above. It is not a fully monitored daily noise level.', 'report-coverage-note'));
  if (stats.is_provisional) panel.append(el('p', 'This local day is still in progress. Generate again after it ends to include its later recordings.', 'muted'));
  if (definition.measurement_type === 'dbfs_rms') panel.append(el('p', `Digital levels belong to device ${deviceLabel(definition, devices)} and are kept separate from other devices. They are not calibrated sound-pressure levels.`, 'muted'));
  const details = el('details', '', 'view-disclosure report-calculation');
  details.append(el('summary', 'Calculation and data quality'));
  const descriptions = el('dl', '', 'view-record-grid');
  for (const [label, value] of [
    ['Measurement', `${definition.unit || unit(definition.measurement_type)} · ${definition.channel_policy || 'mono'}`],
    ['Recording time before overlap removal', recordedTime(stats.recorded_duration_seconds)],
    ['Overlapping recording time', recordedTime(stats.overlap_seconds)],
    ['Excluded recordings', String(stats.excluded_count ?? 0)],
    ['Sound processing version', definition.processing_version || 'Unavailable'],
    ...(definition.device_id ? [['Device identity', deviceLabel(definition, devices)], ['Internal device ID', definition.device_id]] : []),
  ]) { const group = el('div'); group.append(el('dt', label), el('dd', value)); descriptions.append(group); }
  details.append(descriptions);
  const excluded = Object.entries(stats.exclusion_reasons || {}).filter(([, count]) => count > 0);
  if (excluded.length) details.append(el('p', `Excluded: ${excluded.map(([reason, count]) => `${readable(reason)} (${count})`).join('; ')}.`, 'muted'));
  details.append(el('p', 'The average uses sound energy over usable time. During overlaps, recordings from each device are averaged first, then active devices are averaged. Coverage counts each moment once. Recordings that cross midnight contribute only their time inside this local day; their measured level is assumed constant within each recording.', 'muted'));
  details.append(el('p', 'Different measurement definitions and simulated recordings stay separate. Their coverage and incident counts must not be added together. Saved incident starts are grouped by source and measurement type; the same incident may appear under more than one processing version.', 'muted'));
  panel.append(details);
  return panel;
}

function select(options, label) {
  const node = el('select');
  node.setAttribute('aria-label', label);
  for (const [value, text] of options) {
    const option = el('option', text);
    option.value = value;
    node.append(option);
  }
  return node;
}

function link(text, hash, ctx, className = 'text-link') {
  const node = el('a', text, className);
  node.href = hash;
  node.addEventListener('click', event => {
    if (!event.ctrlKey && !event.metaKey && !event.shiftKey && event.button === 0) {
      event.preventDefault();
      ctx.navigate(hash);
    }
  });
  return node;
}

function heading(title, subtitle) {
  const node = el('div', '', 'page-heading view-heading');
  node.append(el('h1', title), el('p', subtitle, 'muted'));
  return node;
}

function section(title, subtitle = '') {
  const node = el('section', '', 'panel view-panel');
  const top = el('div', '', 'view-section-heading');
  top.append(el('h2', title));
  if (subtitle) top.append(el('p', subtitle, 'muted'));
  node.append(top);
  return node;
}

function metric(label, value, detail = '') {
  const node = el('div', '', 'view-metric');
  node.append(el('span', label, 'view-metric-label'), el('strong', value, 'view-metric-value'));
  if (detail) node.append(el('span', detail, 'muted'));
  return node;
}

function statusBadge(status) {
  return badge(readable(status), status === 'active' ? 'danger' : status === 'recovering' ? 'warning' : status === 'resolved' ? 'good' : 'neutral');
}

function duration(start, end, now = Date.now()) {
  if (!start) return 'Unavailable';
  const seconds = Math.max(0, Math.floor(((end ? new Date(end).getTime() : now) - new Date(start).getTime()) / 1000));
  if (!Number.isFinite(seconds)) return 'Unavailable';
  const text = seconds < 60 ? `${seconds}s` : seconds < 3600 ? `${Math.floor(seconds / 60)}m ${seconds % 60}s` : seconds < 86400 ? `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m` : `${Math.floor(seconds / 86400)}d ${Math.floor((seconds % 86400) / 3600)}h`;
  return `${text}${end ? '' : ' · ongoing'}`;
}

function table(headers, rows, caption) {
  const wrap = el('div', '', 'view-table-wrap');
  wrap.tabIndex = 0;
  wrap.setAttribute('role', 'region');
  wrap.setAttribute('aria-label', caption);
  const node = el('table', '', 'view-table');
  const cap = el('caption', caption, 'sr-only');
  const head = el('thead');
  const tr = el('tr');
  headers.forEach(text => { const cell = el('th', text); cell.scope = 'col'; tr.append(cell); });
  head.append(tr);
  const body = el('tbody');
  rows.forEach(values => {
    const row = el('tr');
    values.forEach(value => { const cell = el('td'); typeof value === 'object' && value !== null ? cell.append(value) : cell.textContent = String(value ?? '—'); row.append(cell); });
    body.append(row);
  });
  node.append(cap, head, body);
  wrap.append(node);
  if (headers.length < 5) return wrap;
  const group = el('div', '', 'view-table-group');
  group.append(el('p', 'Scroll horizontally to see all columns.', 'table-scroll-hint'), wrap);
  return group;
}

function stack(primary, secondary) {
  const node = el('div', '', 'view-cell-stack');
  node.append(typeof primary === 'object' ? primary : el('span', primary));
  if (secondary) node.append(el('small', secondary, 'muted'));
  return node;
}

function replaceTableContent(container, content) {
  const previous = container.querySelector('.view-table-wrap');
  const links = previous ? [...previous.querySelectorAll('a')] : [];
  const focused = links.find(link => link === document.activeElement);
  const regionFocused = previous && previous === document.activeElement;
  const scrollLeft = previous?.scrollLeft || 0;
  container.replaceChildren(content);
  const next = container.querySelector('.view-table-wrap');
  if (next) next.scrollLeft = scrollLeft;
  if (focused || regionFocused) {
    const replacement = focused && next ? [...next.querySelectorAll('a')].find(link => link.href === focused.href && link.textContent === focused.textContent) : null;
    const target = replacement || next || container;
    if (target === container) target.tabIndex = -1;
    target.focus({ preventScroll: true });
  }
}

function errorMessage(error) {
  return error?.message || 'The server could not complete this request.';
}

function liveRefresh(ctx, callback, accepts = () => true) {
  let timer, running = false, dirty = false, disposed = false, pending = liveChanges();
  function schedule() {
    if (timer || running || disposed || ctx.signal?.aborted) return;
    timer = setTimeout(async () => {
      timer = null;
      if (disposed || ctx.signal?.aborted) return;
      const changes = pending; pending = liveChanges(); dirty = false; running = true;
      try { await callback(changes); }
      catch (error) { if (!disposed && !ctx.signal?.aborted) ctx.reportLiveError?.(errorMessage(error)); }
      finally { running = false; if (dirty) schedule(); }
    }, 1100);
  }
  const unsubscribe = ctx.onLive?.(changes => {
    if (!accepts(changes)) return;
    mergeChanges(pending, changes); dirty = true; schedule();
  });
  return () => { disposed = true; clearTimeout(timer); unsubscribe?.(); };
}

function safeApi(ctx, path, options = {}) {
  return ctx.api(path, { ...options, signal: ctx.signal });
}

function measurementRows(rows, ctx, timezone, player) {
  return rows.map(row => {
    const recording = { ...row, id: row.audio_chunk_id }, playback = el('div', '', 'reading-recording');
    if (player) { playback.append(player.listenButton(recording), classificationNode(row.classification, timezone, { hideUnrequested: true })); player.updateClassification(recording); }
    return [
    stack(formatTime(row.measured_at, timezone), row.measured_at),
    stack(deviceLabel(row, ctx.devices), row.location_snapshot?.name || locationName(ctx, row.location_id)),
    stack(formatLevel(row.value_db, row.measurement_type), isNumber(row.value_db) ? `${row.value_db} ${unit(row.measurement_type)}` : readable(row.calibration_status)),
    formatLevel(row.threshold_value, row.threshold_type),
    stack(readable(row.evaluation?.status || row.quality_status), row.evaluation?.diagnostic ? readable(row.evaluation.diagnostic) : `${row.interval_seconds}s interval`),
    ...(player ? [playback] : []),
  ]; });
}

function incidentRows(rows, ctx, timezone) {
  return rows.map(row => [
    stack(link(row.location_snapshot?.name || locationName(ctx, row.location_id), `#/incident/${row.id}`, ctx), `Device ${deviceLabel(row, ctx.devices)}${deviceLabel(row, ctx.devices) !== short(row.device_id) ? ` · ${short(row.device_id)}` : ''}`),
    formatTime(row.started_at, timezone),
    statusBadge(row.status),
    stack(formatLevel(row.latest_db, row.threshold_type), `Limit ${formatLevel(row.threshold_value, row.threshold_type)}`),
    formatLevel(row.peak_db, row.threshold_type),
    row.ended_at ? formatTime(row.ended_at, timezone) : 'Not resolved',
    duration(row.started_at, row.ended_at),
    link('View incident', `#/incident/${row.id}`, ctx),
  ]);
}

function incidentTable(rows, ctx, timezone) {
  return table(['Location / device', 'Started', 'Status', 'Latest / threshold', 'Peak', 'Ended', 'Duration', 'Details'], incidentRows(rows, ctx, timezone), 'Saved noise incidents');
}

function svg(tag, attributes = {}, text) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [name, value] of Object.entries(attributes)) node.setAttribute(name, String(value));
  if (text !== undefined) node.textContent = text;
  return node;
}

function tickTime(value, timezone, span) {
  const options = span > 86400000 ? { month: 'short', day: 'numeric', hour: '2-digit' } : { hour: '2-digit', minute: '2-digit', ...(span < 300000 ? { second: '2-digit' } : {}) };
  try { return new Intl.DateTimeFormat(undefined, { ...options, timeZone: timezone }).format(new Date(value)); }
  catch { return new Intl.DateTimeFormat(undefined, options).format(new Date(value)); }
}

// Null and invalid results break a series; samples separated by more than the
// expected capture interval also remain separate. No missing values are filled.
function chartNode(rows, versions, method, start, end, timezone, fit, devices = []) {
  const wrapper = el('div', '', 'noise-chart');
  const eligible = rows.filter(row => row.measurement_type === method && isNumber(row.value_db) && row.quality_status === 'good');
  if (!eligible.length) return empty('No eligible readings in this range', 'Choose another time range. Missing, invalid, or uncalibrated readings are never replaced with zero.');
  let xStart = start, xEnd = end;
  if (fit) {
    const times = rows.map(row => new Date(row.measured_at).getTime()).filter(Number.isFinite);
    const first = Math.min(...times), last = Math.max(...times);
    const margin = Math.max(1000, (last - first) * .05);
    xStart = Math.max(start, first - margin);
    xEnd = Math.min(end, Math.max(last + margin, xStart + 10000));
  }
  if (xEnd <= xStart) xEnd = xStart + 1000;
  const applicableRules = versions.filter(rule => rule.threshold_type === method && new Date(rule.effective_at).getTime() <= xEnd).sort((a, b) => new Date(a.effective_at) - new Date(b.effective_at));
  const levels = [...eligible.map(row => row.value_db), ...applicableRules.map(rule => rule.threshold_value), ...eligible.filter(row => row.threshold_type === method && isNumber(row.threshold_value)).map(row => row.threshold_value)];
  const min = Math.floor((Math.min(...levels) - 4) / 5) * 5;
  const max = Math.ceil((Math.max(...levels) + 4) / 5) * 5;
  const width = 1000, height = 310, left = 72, right = 26, top = 30, bottom = 56;
  const plotWidth = width - left - right, plotHeight = height - top - bottom;
  const x = value => left + (value - xStart) / (xEnd - xStart) * plotWidth;
  const y = value => top + (max - value) / (max - min) * plotHeight;
  const chart = svg('svg', { viewBox: `0 0 ${width} ${height}`, role: 'img', 'aria-label': `Sound-level chart in ${unit(method)}. Readings are separated by device. Dashed lines show the threshold. Missing readings appear as gaps.` });
  chart.append(svg('title', {}, `Historical sound levels · ${unit(method)}`));
  for (let index = 0; index <= 4; index++) {
    const value = min + (max - min) * index / 4;
    const py = y(value);
    chart.append(svg('line', { x1: left, y1: py, x2: width - right, y2: py, stroke: 'var(--line)', 'stroke-width': 1 }));
    chart.append(svg('text', { x: left - 13, y: py + 4, 'text-anchor': 'end', class: 'chart-axis-text' }, Number(value.toFixed(1))));
  }
  for (let index = 0; index <= 4; index++) {
    const time = xStart + (xEnd - xStart) * index / 4;
    chart.append(svg('text', { x: x(time), y: height - 28, 'text-anchor': index === 0 ? 'start' : index === 4 ? 'end' : 'middle', class: 'chart-axis-text' }, tickTime(time, timezone, xEnd - xStart)));
  }
  chart.append(svg('text', { x: left, y: 15, class: 'chart-axis-label' }, unit(method)));
  chart.append(svg('text', { x: width - right, y: height - 5, 'text-anchor': 'end', class: 'chart-axis-label' }, `Time · ${timezone || 'browser timezone'}`));
  // A rule of another measurement type ends the previous rule's line as well.
  const allRules = [...versions].sort((a, b) => new Date(a.effective_at) - new Date(b.effective_at));
  allRules.forEach((rule, index) => {
    const lineStart = Math.max(xStart, new Date(rule.effective_at).getTime());
    const lineEnd = Math.min(xEnd, index + 1 < allRules.length ? new Date(allRules[index + 1].effective_at).getTime() : xEnd);
    if (rule.threshold_type !== method || lineEnd <= lineStart) return;
    const path = svg('line', { x1: x(lineStart), y1: y(rule.threshold_value), x2: x(lineEnd), y2: y(rule.threshold_value), stroke: 'var(--danger)', 'stroke-width': 1.7, 'stroke-dasharray': '6 5' });
    path.append(svg('title', {}, `Threshold revision ${rule.revision}: ${rule.threshold_value} ${unit(method)} · effective ${rule.effective_at}`));
    chart.append(path);
  });
  const tooltip = el('div', 'Focus or hover a reading for its exact value, timestamp, and saved threshold.', 'chart-tooltip');
  tooltip.setAttribute('role', 'status');
  const legend = el('div', '', 'chart-legend');
  const deviceIds = [...new Set(rows.filter(row => row.measurement_type === method).map(row => row.device_id))];
  deviceIds.forEach((deviceId, deviceIndex) => {
    const color = SERIES_COLORS[deviceIndex % SERIES_COLORS.length];
    const dash = SERIES_DASHES[deviceIndex % SERIES_DASHES.length];
    const series = rows.filter(row => row.device_id === deviceId && row.measurement_type === method).sort((a, b) => new Date(a.measured_at) - new Date(b.measured_at));
    const parts = [];
    let previous = null;
    for (const row of series) {
      const time = new Date(row.measured_at).getTime();
      if (!isNumber(row.value_db) || row.quality_status !== 'good' || time < xStart || time > xEnd) { previous = null; continue; }
      const gap = !previous || time - new Date(previous.measured_at).getTime() > Math.max(previous.interval_seconds || 1, row.interval_seconds || 1) * 1600;
      parts.push(`${gap ? 'M' : 'L'}${x(time).toFixed(2)},${y(row.value_db).toFixed(2)}`);
      previous = row;
    }
    chart.append(svg('path', { d: parts.join(' '), fill: 'none', stroke: color, 'stroke-width': 2.4, 'stroke-dasharray': dash, 'stroke-linejoin': 'round' }));
    series.filter(row => isNumber(row.value_db) && row.quality_status === 'good').forEach(row => {
      const time = new Date(row.measured_at).getTime();
      if (time < xStart || time > xEnd) return;
      const exact = `Device ${deviceLabel({device_id: deviceId}, devices)} · ${row.value_db} ${unit(method)} · ${formatTime(row.measured_at, timezone)} (${row.measured_at}) · saved threshold ${isNumber(row.threshold_value) ? `${row.threshold_value} ${unit(row.threshold_type)}` : 'unavailable'}${row.evaluation?.diagnostic ? ` · ${readable(row.evaluation.diagnostic)}` : ''}`;
      const point = svg('circle', { cx: x(time), cy: y(row.value_db), r: 4.3, fill: color, stroke: 'var(--surface)', 'stroke-width': 1.7, tabindex: 0, role: 'img', 'aria-label': exact, class: 'chart-reading' });
      point.append(svg('title', {}, exact));
      point.addEventListener('focus', () => { tooltip.textContent = exact; });
      point.addEventListener('pointerenter', () => { tooltip.textContent = exact; });
      chart.append(point);
    });
    const item = el('span', '', 'chart-legend-item');
    const line = svg('svg', { width: 25, height: 10, 'aria-hidden': true });
    line.append(svg('line', { x1: 0, y1: 5, x2: 25, y2: 5, stroke: color, 'stroke-width': 2.4, 'stroke-dasharray': dash }));
    item.append(line, el('span', `Device ${deviceLabel({device_id: deviceId}, devices)}`));
    legend.append(item);
  });
  const thresholdLegend = el('span', '', 'chart-legend-item');
  const line = svg('svg', { width: 25, height: 10, 'aria-hidden': true });
  line.append(svg('line', { x1: 0, y1: 5, x2: 25, y2: 5, stroke: 'var(--danger)', 'stroke-width': 1.7, 'stroke-dasharray': '6 5' }));
  thresholdLegend.append(line, el('span', 'Threshold at that time'));
  legend.append(thresholdLegend);
  const viewport = el('div', '', 'chart-viewport');
  viewport.tabIndex = 0;
  viewport.setAttribute('role', 'region');
  viewport.setAttribute('aria-label', 'Sound-level chart. Scroll horizontally to explore the full time range.');
  viewport.append(chart);
  wrapper.append(el('p', 'Scroll sideways to explore the full timeline.', 'chart-scroll-hint'), viewport, legend, tooltip);
  return wrapper;
}

async function locationView(container, route, ctx) {
  let disposed = false, generation = 0, supportingGeneration = 0, rows = [], versions = [], total = 0, rangeStart, rangeEnd;
  let dataLimit = CHART_PAGE, historyDirty = false, currentThreshold = null;
  const location = await safeApi(ctx, `/locations/${encodeURIComponent(route.id)}`);
  let assignedDevices = ctx.devices.filter(device => device.location_id === location.id);
  if (ctx.signal?.aborted) return () => {};
  const title = heading(location.name, `${Number(location.latitude).toFixed(5)}, ${Number(location.longitude).toFixed(5)} · ${location.timezone}`);
  container.append(link('← All locations', '#/overview', ctx, 'text-link view-back-link'), title);
  if (/SYNTHETIC|SIMULATED|DEMO/i.test(location.name)) container.append(el('div', 'Simulated location · readings are demonstration data, not calibrated environmental measurements.', 'view-demo-note'));
  const summary = el('div', '', 'view-metric-grid');
  const chartSection = section('Sound levels over time', 'Stored measurements and the threshold that applied at the time.');
  const controls = el('div', '', 'view-controls');
  const range = select([['1', 'Last hour'], ['24', 'Last 24 hours'], ['168', 'Last 7 days']], 'Reading time range');
  const method = select([['spl_z_leq', 'Z-weighted sound pressure'], ['dbfs_rms', 'Digital signal level']], 'Measurement type');
  method.value = location.threshold_type;
  const fit = el('input'); fit.type = 'checkbox'; fit.checked = true;
  const fitLabel = el('label', '', 'view-check'); fitLabel.append(fit, el('span', 'Fit recorded window'));
  const refreshHistory = button('Refresh latest readings', () => { dataLimit = CHART_PAGE; historyDirty = false; void loadHistory(); }, 'secondary');
  controls.append(field('Time range', range), field('Measurement', method), fitLabel, refreshHistory);
  const chartArea = el('div', '', 'chart-area');
  const chartInfo = el('p', '', 'muted chart-info');
  const chartError = el('div');
  const more = button('Load older readings', async () => { dataLimit += CHART_PAGE; await loadHistory(); }, 'button secondary');
  more.hidden = true;
  const readings = el('details', '', 'view-disclosure');
  readings.append(el('summary', 'View the saved readings'));
  const readingsBody = el('div'); readings.append(readingsBody);
  chartSection.append(controls, chartError, chartArea, chartInfo, more, readings);
  const lower = el('div', '', 'view-detail-grid');
  const devicesPanel = section('Assigned devices', 'Connection tracks recent uploads or diagnostic messages. Readings show whether the sound measurements are usable. Contact is checked every 2 seconds.');
  const contactFeedback = el('p', '', 'muted small'); contactFeedback.setAttribute('role', 'status');
  const deviceContacts = new Map();
  devicesPanel.append(contactFeedback);
  const devicesBody = el('div'); devicesPanel.append(devicesBody);
  const thresholdPanel = section('Threshold history', 'Changing a setting never rewrites a saved incident.');
  const thresholdBody = el('div'); thresholdPanel.append(thresholdBody, link('Manage thresholds →', '#/management', ctx));
  lower.append(devicesPanel, thresholdPanel);
  const recent = section('Recent incidents');
  const recentBody = el('div'); recent.append(recentBody, link('Open incident history →', `#/incidents?location=${encodeURIComponent(location.id)}`, ctx));
  const daily = section('Daily summaries', `Previous completed local day · ${localReportingDate(location.timezone)} · ${location.timezone}`);
  const dailyBody = el('div');
  dailyBody.append(el('p', 'Loading saved daily summaries…', 'muted'));
  daily.append(dailyBody, link('Open daily reports / recalculate →', `#/reports?location=${encodeURIComponent(location.id)}`, ctx));
  const recordingsPanel = section('Saved 10-second recordings', 'Listen to complete continuous recordings from each device, including uncalibrated audio. Short individual recordings remain linked to their readings below.');
  const player = createRecordingPlayer(ctx, { timezone: location.timezone });
  const recordings = createRecordingsBrowser(ctx, { locationId: location.id, timezone: location.timezone, devices: assignedDevices, player });
  recordingsPanel.append(player.element, recordings.element);
  container.append(summary, recordingsPanel, chartSection, lower, recent, daily);

  function renderSummary(threshold) {
    const current = ctx.state.locations.get(location.id);
    const streams = current?.streams || [];
    const latest = [...streams].filter(item => item.measured_at).sort((a, b) => new Date(b.measured_at) - new Date(a.measured_at))[0];
    summary.replaceChildren(
      metric('Latest reading', latest ? formatLevel(latest.measurement_value, latest.measurement_type) : 'No readings yet', latest ? formatTime(latest.measured_at, location.timezone) : 'Awaiting the first recording'),
      metric('Current threshold', threshold?.current ? formatLevel(threshold.current.threshold_value, threshold.current.threshold_type) : 'Not configured', threshold?.current ? `${threshold.current.interval_seconds}s measurement · revision ${threshold.current.revision}` : ''),
      metric('Noise condition', current?.noise_status === 'unknown' || !current ? 'Not evaluated' : readable(current.noise_status), (current?.unresolved_incident_ids || []).length ? `${current.unresolved_incident_ids.length} unresolved incident(s)` : 'No unresolved incidents'),
      metric('Data freshness', freshnessLabel(current), current?.data_status === 'stale' ? 'Based on all assigned devices; check each device below.' : 'Based on capture time'),
    );
    renderDeviceStatuses();
  }

  function renderDeviceStatuses() {
    if (disposed || ctx.signal?.aborted) return;
    const current = ctx.state.locations.get(location.id);
    const now = ctx.serverNow?.() ?? Date.now(), staleSeconds = ctx.dataStaleSeconds?.() ?? 30;
    const assigned = assignedDevices;
    devicesBody.replaceChildren();
    if (!assigned.length) devicesBody.append(empty('No assigned devices', 'Register a device in Management to begin receiving recordings.'));
    for (const device of assigned) {
      const connection = deviceConnection(device, current, { contact: deviceContacts.get(device.id), now, staleSeconds });
      const reading = deviceReadingStatus({ ...device, last_contact_at: connection.contact }, { ...location, current_threshold: currentThreshold?.current }, current);
      const age = connection.ageSeconds === null ? '' : ` (${connection.ageSeconds} s ago)`;
      const item = el('div', '', 'view-device-row');
      const identity = el('div'); identity.append(el('strong', device.external_id || device.microphone_model), el('code', device.external_id ? `${device.microphone_model} · ${device.id}` : device.id, 'view-device-id'));
      const status = el('div', '', 'device-contact-status'), contact = el('div'), readings = el('div');
      contact.append(el('div', 'Connection', 'muted small'), badge(connection.label, connection.connected ? 'good' : 'stale'), el('p', `Last device contact: ${connection.contact ? formatTime(connection.contact, location.timezone) : 'Never received'}${age}`, 'muted small'));
      if (connection.contact) contact.lastChild.title = connection.contact;
      readings.append(el('div', 'Readings', 'muted small'), badge(reading.label, reading.reporting && !reading.attention && !reading.needsCalibration && device.enabled ? 'good' : 'stale'));
      if (reading.needsCalibration && device.enabled) readings.append(el('p', 'SPL readings need microphone calibration. Device contact is tracked separately.', 'muted small'));
      if (!reading.needsCalibration || reading.latest) readings.append(el('p', `Last usable reading received: ${formatTime(reading.latest?.received_at, location.timezone)}`, 'muted small'));
      status.append(contact, readings);
      item.append(identity, status);
      devicesBody.append(item);
    }
  }

  function drawHistory() {
    const selectedMethod = method.value;
    chartArea.replaceChildren(chartNode(rows, versions, selectedMethod, rangeStart, rangeEnd, location.timezone, fit.checked, ctx.devices));
    historyStatus();
    more.hidden = rows.length >= total;
    more.textContent = `Load ${Math.min(CHART_PAGE, Math.max(0, total - rows.length))} older readings`;
    readingsBody.replaceChildren(rows.length ? table(['Measured at', 'Device / location', 'Sound level', 'Saved threshold', 'Evaluation', 'Recording'], measurementRows(rows, ctx, location.timezone, player), 'Readings at this location') : empty('No saved readings', 'There are no measurements in this time range.'));
  }

  function historyStatus() {
    const mode = dataLimit > CHART_PAGE ? 'Browsing a saved snapshot; Refresh latest readings returns to live chart updates.' : 'The latest 200 readings update automatically.';
    chartInfo.textContent = `${rows.length.toLocaleString()} of ${total.toLocaleString()} saved readings loaded. ${historyDirty ? 'New readings are available. ' : ''}${mode} ${fit.checked ? 'The chart fits the loaded recorded window.' : 'The chart shows the entire selected time range.'} Gaps stay empty. Times use ${location.timezone}.`;
    refreshHistory.textContent = historyDirty ? 'New readings · Refresh latest' : 'Refresh latest readings';
  }

  const loadHistory = coalesceAsync(async () => {
    const ownGeneration = ++generation;
    const requestedRange = range.value, requestedLimit = dataLimit;
    chartError.replaceChildren();
    more.disabled = true;
    refreshHistory.disabled = true;
    rangeEnd = Date.now(); rangeStart = rangeEnd - Number(requestedRange) * 3600000;
    const query = new URLSearchParams({ location_id: location.id, since: new Date(rangeStart).toISOString(), until: new Date(rangeEnd).toISOString(), limit: String(CHART_PAGE), offset: '0' });
    try {
      const first = await safeApi(ctx, `/measurements?${query}`);
      const loaded = [...first.items];
      while (loaded.length < Math.min(first.total, requestedLimit)) {
        if (requestedRange !== range.value || requestedLimit !== dataLimit || ctx.signal?.aborted) return;
        query.set('offset', String(loaded.length));
        const page = await safeApi(ctx, `/measurements?${query}`);
        if (!page.items.length) break;
        loaded.push(...page.items);
      }
      if (disposed || ownGeneration !== generation || ctx.signal?.aborted || requestedRange !== range.value || requestedLimit !== dataLimit) return;
      rows = loaded; total = first.total;
      drawHistory();
    } catch (error) { if (!ctx.signal?.aborted && !disposed && ownGeneration === generation) chartError.append(errorBox(errorMessage(error))); }
    finally { if (!disposed && ownGeneration === generation) { more.disabled = false; refreshHistory.disabled = false; } }
  }, ctx.signal);

  const loadSupporting = coalesceAsync(async () => {
    const ownGeneration = ++supportingGeneration;
    try {
      const deviceIds = [...new Set([...(ctx.state.locations.get(location.id)?.devices || []).map(device => device.id), ...ctx.devices.filter(device => device.location_id === location.id).map(device => device.id)])];
      const [threshold, history, devices] = await Promise.all([
        safeApi(ctx, `/locations/${location.id}/threshold`),
        safeApi(ctx, `/locations/${location.id}/threshold/versions?limit=200`),
        Promise.all(deviceIds.map(id => safeApi(ctx, `/devices/${id}`))),
      ]);
      if (disposed || ctx.signal?.aborted || ownGeneration !== supportingGeneration) return;
      assignedDevices = devices.filter(device => device.location_id === location.id);
      versions = history.items;
      currentThreshold = threshold;
      renderSummary(currentThreshold);
      thresholdBody.replaceChildren();
      if (versions.length) thresholdBody.append(table(['Effective from', 'Threshold', 'Revision'], versions.slice(0, 5).map(rule => [formatTime(rule.effective_at, location.timezone), formatLevel(rule.threshold_value, rule.threshold_type), String(rule.revision)]), 'Recent threshold revisions'));
      else thresholdBody.append(empty('No threshold revisions', 'A location threshold has not been configured.'));
      if (history.total > versions.length) thresholdBody.append(el('p', `Loaded the latest ${versions.length} of ${history.total} revisions. Older threshold lines may be unavailable; each reading retains its saved threshold.`, 'muted'));
      else if (versions.length > 5) thresholdBody.append(el('p', `Showing the latest 5 of ${versions.length} revisions. All loaded revisions are used in the chart.`, 'muted'));
      if (rows.length) drawHistory();
    } catch (error) { if (!disposed && !ctx.signal?.aborted && ownGeneration === supportingGeneration) { thresholdBody.replaceChildren(errorBox(errorMessage(error))); renderSummary(null); } }
  }, ctx.signal);

  const loadIncidents = coalesceAsync(async () => {
    try {
      const incidents = await safeApi(ctx, `/incidents?location_id=${location.id}&limit=5`);
      if (disposed || ctx.signal?.aborted) return;
      recentBody.replaceChildren(incidents.items.length ? incidentTable(incidents.items, ctx, location.timezone) : empty('No incidents yet', 'Threshold breaches will appear here once they have been evaluated.'));
    } catch (error) { if (!disposed && !ctx.signal?.aborted) recentBody.replaceChildren(errorBox(errorMessage(error))); }
  }, ctx.signal);

  let dailyTimer;
  async function loadDaily() {
    try {
      const result = await safeApi(ctx, `/daily-summaries?${new URLSearchParams({ location_id: location.id, reporting_date: localReportingDate(location.timezone) })}`);
      if (disposed || ctx.signal?.aborted) return;
      dailyBody.replaceChildren();
      daily.querySelector('.view-section-heading p').textContent = `Previous completed local day · ${result.reporting_date} · ${result.timezone || location.timezone}`;
      if (reportPending(result.report)) {
        dailyBody.append(el('p', 'Summary processing is underway. Any values below are the previous saved result.', 'report-coverage-note'));
        dailyTimer = setTimeout(loadDaily, 1800);
      }
      if (result.report?.status === 'failed') dailyBody.append(errorBox(result.report.error || result.report.last_error || 'The last summary calculation failed. Open Daily reports to try again.'));
      for (const item of result.summaries || []) dailyBody.append(dailySummaryCard(item, result.timezone || location.timezone, true, ctx.devices));
      if (!result.summaries?.length && !reportPending(result.report)) dailyBody.append(empty(result.report?.status === 'completed' ? 'No usable data' : 'No saved summary yet', 'Open Daily reports to generate a summary. Missing recordings are never treated as silence.'));
    } catch (error) { if (!disposed && !ctx.signal?.aborted) dailyBody.replaceChildren(errorBox(errorMessage(error))); }
  }

  range.addEventListener('change', () => { dataLimit = CHART_PAGE; historyDirty = false; void loadHistory(); });
  method.addEventListener('change', drawHistory);
  fit.addEventListener('change', drawHistory);
  const stopLive = liveRefresh(ctx, async changes => {
    const affected = locationChanges(changes, location.id), pending = [];
    if (affected.summary) renderSummary(currentThreshold);
    if (affected.supporting) {
      Object.assign(location, ctx.locations.find(item => item.id === location.id) || {});
      title.querySelector('h1').textContent = location.name;
      title.querySelector('p.muted').textContent = `${Number(location.latitude).toFixed(5)}, ${Number(location.longitude).toFixed(5)} · ${location.timezone}`;
      pending.push(loadSupporting());
    }
    if (affected.incidents) pending.push(loadIncidents());
    if (affected.history) {
      if (dataLimit === CHART_PAGE) pending.push(loadHistory());
      else { historyDirty = true; historyStatus(); }
    }
    await Promise.all(pending);
  }, changes => Object.values(locationChanges(changes, location.id)).some(Boolean));
  chartArea.append(el('p', 'Loading saved readings…', 'muted'));
  await Promise.all([loadSupporting(), loadHistory(), loadIncidents(), loadDaily()]);
  const stopContacts = startDeviceContactMonitor({
    api: (...args) => ctx.api(...args), signal: ctx.signal, contacts: deviceContacts,
    now: () => ctx.serverNow?.() ?? Date.now(), onUpdate: renderDeviceStatuses,
    onError: error => { if (!disposed && !ctx.signal?.aborted) contactFeedback.textContent = error ? 'Contact check unavailable; retrying. Times shown are the last confirmed contact.' : ''; },
  });
  return () => { disposed = true; generation++; clearTimeout(dailyTimer); stopLive(); stopContacts(); recordings.dispose(); player.dispose(); };
}

async function incidentsView(container, route, ctx) {
  let disposed = false, offset = 0, generation = 0, total = 0;
  const queryFromHash = new URLSearchParams(location.hash.split('?')[1] || '');
  container.append(heading('Incident history', 'Follow every excessive-noise event, from its first breach to its resolution.'));
  const panel = section('Saved incidents', 'Search by location name, device code (for example UE-001), internal device ID, or microphone model. Historical location names are included.');
  const filters = el('form', '', 'view-filter-grid');
  const search = el('input'); search.type = 'search'; search.placeholder = 'Location or device code, e.g. UE-001'; search.maxLength = 200;
  const locationSelect = select([['', 'All locations'], ...ctx.locations.map(item => [item.id, item.name])], 'Filter incidents by location');
  locationSelect.value = route.location || queryFromHash.get('location') || '';
  const status = select([['', 'All statuses'], ['active', 'Active'], ['recovering', 'Recovering'], ['resolved', 'Resolved'], ['closed', 'Closed']], 'Filter incidents by status');
  const from = el('input'); from.type = 'date';
  const to = el('input'); to.type = 'date';
  const apply = el('button', 'Apply filters', 'button primary'); apply.type = 'submit';
  const reset = button('Reset', () => { filters.reset(); locationSelect.value = ''; applied = { q: '', location: '', status: '', from: '', to: '' }; offset = 0; load(); }, 'button secondary');
  const actions = el('div', '', 'view-filter-actions'); actions.append(apply, reset);
  filters.append(field('Search location or device', search), field('Location', locationSelect), field('Status', status), field('Started from · UTC', from), field('Started through · UTC', to), actions);
  const feedback = el('div'); feedback.setAttribute('role', 'status');
  const body = el('div');
  const footer = el('div', '', 'view-pagination');
  const count = el('span', '', 'muted');
  const previous = button('← Previous', () => { offset = Math.max(0, offset - PAGE_SIZE); load(); }, 'button secondary');
  const next = button('Next →', () => { offset += PAGE_SIZE; load(); }, 'button secondary');
  const pages = el('div', '', 'view-pagination-buttons'); pages.append(previous, next);
  footer.append(count, pages);
  panel.append(filters, feedback, body, footer); container.append(panel);
  let applied = { q: '', location: locationSelect.value, status: '', from: '', to: '' };
  function params() {
    const values = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset) });
    if (applied.q) values.set('q', applied.q);
    if (applied.location) values.set('location_id', applied.location);
    if (applied.status) values.set('status', applied.status);
    if (applied.from) values.set('since', `${applied.from}T00:00:00Z`);
    if (applied.to) values.set('until', `${applied.to}T23:59:59.999999Z`);
    return values;
  }
  const load = coalesceAsync(async () => {
    const ownGeneration = ++generation;
    const requestedQuery = params().toString();
    feedback.replaceChildren(); apply.disabled = true;
    try {
      const result = await safeApi(ctx, `/incidents?${requestedQuery}`);
      if (disposed || ownGeneration !== generation || ctx.signal?.aborted || requestedQuery !== params().toString()) return;
      total = result.total;
      replaceTableContent(body, result.items.length ? incidentTable(result.items, ctx, 'UTC') : empty('No matching incidents', 'Try another date range, location, or status. No results does not mean the location has recorded complete coverage.'));
      count.textContent = total ? `${offset + 1}–${Math.min(offset + PAGE_SIZE, total)} of ${total} incidents · times in UTC` : '0 incidents · times in UTC';
      previous.disabled = offset === 0;
      next.disabled = offset + PAGE_SIZE >= total;
    } catch (error) { if (!disposed && !ctx.signal?.aborted && ownGeneration === generation) feedback.append(errorBox(errorMessage(error))); }
    finally { if (!disposed && ownGeneration === generation) apply.disabled = false; }
  }, ctx.signal);
  filters.addEventListener('submit', event => {
    event.preventDefault();
    if (from.value && to.value && from.value > to.value) { feedback.replaceChildren(errorBox('The start date must be on or before the end date.')); return; }
    applied = { q: search.value.trim(), location: locationSelect.value, status: status.value, from: from.value, to: to.value };
    offset = 0; load();
  });
  const stopLive = liveRefresh(ctx, load, changes => changes?.metadata || (applied.location ? changes?.incidentLocations.has(applied.location) : !!changes?.incidents.size));
  body.append(el('p', 'Loading saved incidents…', 'muted'));
  await load();
  return () => { disposed = true; generation++; stopLive(); };
}

async function incidentView(container, route, ctx) {
  let disposed = false, offset = 0, generation = 0;
  container.append(link('← Incident history', '#/incidents', ctx, 'text-link view-back-link'));
  const title = heading('Incident detail', `Record ${route.id}`);
  const summary = el('div', '', 'view-metric-grid');
  const identity = section('Incident record');
  const detailBody = el('div'); identity.append(detailBody);
  const incidentAudio = createIncidentAudio(ctx, { incidentId: route.id });
  const readings = section('Related readings', 'Sound-level measurements used to evaluate this incident. A 1s measurement describes how the level was calculated. Listen to the whole incident using the saved audio and available context before and after it.');
  const listenIncident = button('Listen to whole incident', () => incidentAudio.open(), 'primary');
  const refreshReadings = button('Refresh readings', () => load(), 'secondary recording-refresh');
  const body = el('div');
  const feedback = el('div');
  const footer = el('div', '', 'view-pagination');
  const count = el('span', '', 'muted');
  const previous = button('← Previous', () => { offset = Math.max(0, offset - PAGE_SIZE); load(); }, 'button secondary');
  const next = button('Next →', () => { offset += PAGE_SIZE; load(); }, 'button secondary');
  const pages = el('div', '', 'view-pagination-buttons'); pages.append(previous, next); footer.append(count, pages);
  const readingActions = el('div', '', 'incident-audio-actions'); readingActions.append(listenIncident, refreshReadings);
  readings.append(feedback, readingActions, body, footer);
  container.append(title, summary, incidentAudio.element, identity, readings);
  const load = coalesceAsync(async () => {
    const ownGeneration = ++generation;
    const requestedOffset = offset;
    feedback.replaceChildren();
    try {
      const [record, related] = await Promise.all([
        safeApi(ctx, `/incidents/${route.id}`),
        safeApi(ctx, `/incidents/${route.id}/measurements?limit=${PAGE_SIZE}&offset=${requestedOffset}`),
      ]);
      if (disposed || ctx.signal?.aborted || ownGeneration !== generation || requestedOffset !== offset) return;
      const timezone = record.location_snapshot?.timezone || ctx.locations.find(item => item.id === record.location_id)?.timezone || 'UTC';
      const name = record.location_snapshot?.name || locationName(ctx, record.location_id);
      incidentAudio.setIncident(record);
      title.querySelector('h1').textContent = name;
      summary.replaceChildren(metric('Incident status', readable(record.status), record.ended_at ? `Ended ${formatTime(record.ended_at, timezone)}` : 'This event is still unresolved'), metric('Peak level', formatLevel(record.peak_db, record.threshold_type), `${record.breach_count} excessive reading(s)`), metric('Saved threshold', formatLevel(record.threshold_value, record.threshold_type), record.threshold_version ? `Revision ${record.threshold_version.revision} · ${record.threshold_version.interval_seconds}s` : 'Historical threshold snapshot'), metric('Duration', duration(record.started_at, record.ended_at), 'Elapsed incident window; not measured sound coverage'));
      const descriptions = el('dl', '', 'view-record-grid');
      for (const [label, value] of [
        ['Location', link(name, `#/location/${record.location_id}`, ctx)],
        ['Device code', record.device_external_id || ctx.devices.find(device => device.id === record.device_id)?.external_id || 'Not assigned'],
        ['Internal device ID', record.device_id],
        ['Started', formatTime(record.started_at, timezone)],
        ['Resolved / closed', record.ended_at ? formatTime(record.ended_at, timezone) : 'Not resolved'],
        ['Latest evaluated level', formatLevel(record.latest_db, record.threshold_type)],
        ['Last excessive reading', record.last_occurrence_at ? formatTime(record.last_occurrence_at, timezone) : 'Unavailable'],
        ['Recovery progress', `${record.recovery_streak || 0}${record.threshold_version ? ` of ${record.threshold_version.recovery_count}` : ''} normal readings`],
        ['Measurement definition', `${unit(record.threshold_type)} · ${record.threshold_version?.channel_policy || 'mono'}`],
        ['Record timezone', timezone],
        ...(record.closed_reason ? [['Closure reason', readable(record.closed_reason)]] : []),
      ]) {
        const group = el('div'); group.append(el('dt', label)); const description = el('dd'); typeof value === 'object' ? description.append(value) : description.textContent = value; group.append(description); descriptions.append(group);
      }
      detailBody.replaceChildren(descriptions);
      body.replaceChildren(related.association_available === false ? empty('Reading association unavailable', 'This older record has no saved stream association. The backend cannot reliably identify its exact readings.') : related.items.length ? table(['Measured at', 'Device / location', 'Sound level', 'Saved threshold', 'Evaluation'], measurementRows(related.items, ctx, timezone), 'Readings associated with this incident') : empty('No related readings', 'No eligible associated readings were returned for this record.'));
      count.textContent = related.total ? `${offset + 1}–${Math.min(offset + PAGE_SIZE, related.total)} of ${related.total} readings` : '0 associated readings';
      previous.disabled = offset === 0; next.disabled = offset + PAGE_SIZE >= related.total;
    } catch (error) { if (!disposed && !ctx.signal?.aborted && ownGeneration === generation) feedback.append(errorBox(errorMessage(error))); }
  }, ctx.signal);
  const stopLive = liveRefresh(ctx, load, changes => changes?.metadata || changes?.incidents.has(route.id));
  await load();
  return () => { disposed = true; generation++; stopLive(); incidentAudio.dispose(); };
}

async function reportsView(container, route, ctx) {
  let disposed = false, timer, generation = 0, current = null, busy = false;
  const query = route.params || new URLSearchParams(location.hash.split('?')[1] || '');
  container.append(heading('Daily reports', 'Saved sound levels, incident starts, and recording coverage for each location’s local day.'));
  if (!ctx.locations.length) {
    container.append(empty('No locations yet', 'Add a location in Management before generating its daily summary.'), link('Open Management →', '#/management', ctx));
    return () => {};
  }
  const filters = el('div', '', 'view-controls panel view-panel report-controls');
  const locationSelect = select(ctx.locations.map(item => [item.id, item.name]), 'Daily report location');
  const requestedLocation = route.location || query.get('location');
  locationSelect.value = ctx.locations.some(item => item.id === requestedLocation) ? requestedLocation : ctx.locations[0].id;
  const chosen = ctx.locations.find(item => item.id === locationSelect.value);
  const day = el('input'); day.type = 'date'; day.setAttribute('aria-label', 'Daily report date');
  day.max = localReportingDate(chosen.timezone, 0);
  day.value = /^\d{4}-\d{2}-\d{2}$/.test(query.get('date') || '') ? query.get('date') : localReportingDate(chosen.timezone);
  const generate = button('Generate summary', () => run(true), 'primary');
  const refresh = button('Refresh saved results', () => run(), 'secondary');
  const actions = el('div', '', 'report-actions'); actions.append(generate, refresh);
  const scope = el('p', `${day.value} · ${chosen.timezone} · midnight to the next local midnight${day.value === day.max ? ' · today is provisional' : ''}`, 'muted report-scope');
  filters.append(field('Location', locationSelect), field('Report date', day), actions, scope);
  const feedback = el('div', '', 'report-feedback'); feedback.setAttribute('role', 'status'); feedback.setAttribute('aria-live', 'polite');
  const body = el('div', '', 'report-results'); body.setAttribute('aria-busy', 'true');
  const explanation = el('div', '', 'report-explanation');
  explanation.append(el('strong', 'A daily result describes the recordings we actually have'), el('p', 'Sound levels are averaged using sound energy and recorded duration. Missing time is not silence. Simulated and recorded measurements appear separately, and incompatible measurements are never combined.', 'muted'));
  container.append(filters, feedback, body, explanation);

  function controls() {
    const pending = reportPending(current?.report);
    generate.disabled = busy || pending || !day.value || day.value > day.max;
    refresh.disabled = busy;
    generate.textContent = pending ? current.report.status === 'queued' ? 'Queued…' : 'Processing…' : current?.report || current?.summaries?.length ? 'Recalculate summary' : 'Generate summary';
    body.setAttribute('aria-busy', String(busy || pending));
  }
  function render(result) {
    current = result;
    feedback.replaceChildren(); body.replaceChildren();
    const report = result.report, summaries = result.summaries || [];
    const timezone = result.timezone || chosen.timezone;
    day.max = localReportingDate(timezone, 0);
    scope.textContent = `${result.reporting_date} · ${timezone} · midnight to the next local midnight${day.value === day.max ? ' · today is provisional' : ''}${timezone !== chosen.timezone ? ` · saved report timezone (location now uses ${chosen.timezone})` : ''}`;
    if (reportPending(report)) feedback.append(el('p', `${report.status === 'queued' ? 'Queued for processing.' : 'Calculating this local day’s summary.'}${summaries.length ? ' The results below are the previous saved version and will update when processing finishes.' : ' Results will appear here when processing finishes.'}`, 'report-coverage-note'));
    if (report?.status === 'failed') {
      feedback.append(errorBox(report.error || report.last_error || 'The last calculation failed. Please try Recalculate summary.'));
      if (summaries.length) feedback.append(el('p', 'The results below are the previous saved version. The failed calculation did not replace them.', 'muted'));
    }
    if (report?.completed_at && !summaries.length) feedback.append(el('p', `Last calculation: ${formatTime(report.completed_at, timezone)}`, 'muted'));
    for (const summary of summaries) body.append(dailySummaryCard(summary, timezone, false, ctx.devices));
    if (!summaries.length && !reportPending(report)) body.append(empty(report?.status === 'completed' ? 'No usable data' : report?.status === 'failed' ? 'No completed summary' : 'No saved summary yet', report?.status === 'completed' ? 'No eligible measurements were found for this location and day. No zero sound level has been substituted.' : 'Choose Generate summary to calculate the stored recordings for this local date.'));
    if (report?.source_as_of) body.append(el('p', `Source data checked: ${formatTime(report.source_as_of, timezone)}. Recalculate to include late arrivals or reprocessed measurements.`, 'muted report-generated'));
    controls();
  }
  async function run(submit = false) {
    clearTimeout(timer);
    if (disposed || ctx.signal?.aborted || busy) return;
    if (!day.value || (submit && day.value > day.max)) { feedback.replaceChildren(errorBox('Choose today or an earlier date in this location’s timezone.')); controls(); return; }
    const ownGeneration = ++generation;
    busy = true; controls();
    if (!current) body.replaceChildren(el('p', 'Loading saved daily summaries…', 'muted'));
    try {
      const result = submit ? await safeApi(ctx, '/daily-summaries/generate', { method: 'POST', body: JSON.stringify({ location_id: chosen.id, reporting_date: day.value }) }) : await safeApi(ctx, `/daily-summaries?${new URLSearchParams({ location_id: chosen.id, reporting_date: day.value })}`);
      if (disposed || ctx.signal?.aborted || ownGeneration !== generation) return;
      render(result);
      if (reportPending(result.report)) timer = setTimeout(() => run(), 1800);
    } catch (error) {
      if (!disposed && !ctx.signal?.aborted && ownGeneration === generation) {
        feedback.replaceChildren(errorBox(errorMessage(error)));
        if (!current) body.replaceChildren(empty('Could not load daily results', 'Use Refresh saved results to try again.'));
        else feedback.append(el('p', 'The displayed values are the last results loaded. Use Refresh saved results to check again.', 'muted'));
      }
    } finally { if (!disposed && ownGeneration === generation) { busy = false; controls(); } }
  }
  function choose(locationId, date) {
    ctx.navigate(`#/reports?${new URLSearchParams({ location: locationId, date })}`);
  }
  locationSelect.addEventListener('change', () => {
    const next = ctx.locations.find(item => item.id === locationSelect.value);
    choose(next.id, localReportingDate(next.timezone));
  });
  day.addEventListener('change', () => {
    if (!day.value || !day.validity.valid || day.value > day.max) { feedback.replaceChildren(errorBox('Choose today or an earlier date in this location’s timezone.')); controls(); return; }
    choose(chosen.id, day.value);
  });
  await run();
  return () => { disposed = true; generation++; clearTimeout(timer); };
}

export async function mountView(container, route, ctx) {
  const views = { location: locationView, incidents: incidentsView, incident: incidentView, reports: reportsView };
  if (!views[route.page]) { container.append(empty('Page unavailable', 'Choose a page from the navigation.')); return () => {}; }
  try { return await views[route.page](container, route, ctx); }
  catch (error) { if (!ctx.signal?.aborted) container.append(errorBox(errorMessage(error))); return () => {}; }
}

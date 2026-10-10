export const el = (tag, text = '', className = '') => { const node = document.createElement(tag); node.textContent = text; if (className) node.className = className; return node; };
export function button(text, handler, className = '') { const node = el('button', text, `button ${className}`); node.type = 'button'; if (handler) node.addEventListener('click', handler); return node; }
export const short = id => id ? String(id).slice(0, 8) : '—';
export function deviceLabel(record, devices = []) {
  return record.device_external_id || devices.find(device => device.id === record.device_id)?.external_id || short(record.device_id);
}
export const unit = method => method === 'spl_z_leq' ? 'dB SPL (Z)' : method === 'dbfs_rms' ? 'dBFS' : 'Unknown unit';
export function formatLevel(value, method) { return Number.isFinite(value) ? `${value.toFixed(2)} ${unit(method)}` : 'No eligible reading'; }
export function formatTime(value, timezone) { if (!value) return 'Not received'; try { return new Intl.DateTimeFormat(undefined, {dateStyle:'medium', timeStyle:'medium', ...(timezone ? {timeZone:timezone} : {})}).format(new Date(value)); } catch { return 'Unknown time'; } }
export const badge = (text, tone = '') => el('span', text, `badge ${tone}`);
export function field(label, input) { const node = el('label', '', 'field'); node.append(el('span', label), input); return node; }
export function empty(title, body = '') { const node = el('div', '', 'empty-state'); node.append(el('strong', title), el('p', body, 'muted')); return node; }
export function errorBox(message) { const node = el('div', message, 'error-box'); node.setAttribute('role','alert'); return node; }
export const locationName = (ctx,id) => ctx.locations.find(x=>x.id===id)?.name || ctx.state.locations.get(id)?.name || short(id);
export function latest(location) { return [...(location?.streams || [])].filter(s=>s.measured_at).sort((a,b)=>Date.parse(b.measured_at)-Date.parse(a.measured_at))[0]; }
export function freshnessLabel(location) {
 if (!location?.streams?.length) return 'No readings yet';
 if (location.data_status === 'fresh') return 'Reporting';
 if (location.data_status === 'invalid') return 'Reading needs attention';
 const hasReportingDevice = (location.devices || []).some(device =>
   deviceReporting({...device, current_assignment_id:device.assignment_id}, location).reporting);
 return hasReportingDevice ? 'Some devices not reporting' : 'No recent data';
}
// Reporting means a usable reading inside the server's stale window. The server marks
// a stream 'invalid' when its newest reading is unusable (e.g. clipped) but a usable one
// is still inside that window, so one bad second flags attention without dropping the device.
export function deviceReporting(device, location) {
 const streams = device?.enabled ? (location?.streams || []).filter(s => s.device_id === device.id && s.assignment_id === device.current_assignment_id) : [];
 return {reporting: streams.some(s => ['fresh','invalid'].includes(s.data_status)), attention: streams.some(s => s.data_status === 'invalid')};
}
export function condition(location) {
 const unresolved = (location?.unresolved_incident_ids || []).length > 0;
 const stale = !location?.data_status || ['stale','unknown'].includes(location.data_status);
 const invalid = location?.data_status === 'invalid';
 if (unresolved) return {label:location.noise_status==='recovering'?'Recovering':'Excessive noise', tone:stale||invalid?'stale':'danger', incident:true, stale, ...(invalid?{invalid:true}:{})};
 if (!location?.streams?.length) return {label:'No readings yet',tone:'stale',stale:true};
 if (invalid) return {label:'Reading needs attention',tone:'stale',stale:false,invalid:true};
 if (stale) return {label:freshnessLabel(location),tone:'stale',stale:true};
 if (location.noise_status==='normal') return {label:'Within threshold',tone:'good',stale:false};
 return {label:'Awaiting eligible reading',tone:'stale',stale:false};
}
export function statusBadges(location) { const box=el('span','','status-group'),c=condition(location); box.append(badge(c.label,c.incident?'danger':c.tone)); if(c.incident&&c.stale) box.append(badge(freshnessLabel(location),'stale')); else if(c.incident&&c.invalid) box.append(badge('Reading needs attention','stale')); return box; }
export function input(type,value='',options={}) { const node=el('input'); node.type=type; node.value=value; Object.assign(node,options); return node; }
export function select(options,value='') { const node=el('select'); for(const [v,label] of options){const o=el('option',label);o.value=v;node.append(o);} node.value=value;return node; }

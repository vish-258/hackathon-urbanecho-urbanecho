import { el, badge, formatTime } from './ui.mjs';

const CATEGORIES = { traffic: 'Traffic', horn: 'Horn', siren: 'Siren', construction: 'Construction', music: 'Music', animal: 'Animal', voice: 'Voice', other: 'Other' };
const score = value => Number.isFinite(value) ? value.toLocaleString(undefined, { minimumFractionDigits: 3, maximumFractionDigits: 3 }) : 'unavailable';
export const classificationPending = classification => ['pending', 'processing'].includes(classification?.status);
export function classificationPollDelay(classification) {
  if (!classificationPending(classification) || classification.worker_status === 'disabled') return null;
  return classification.worker_status === 'unavailable' ? 30000 : 3000;
}

export function classificationNode(classification, timezone, { scope = 'recording', hideUnrequested = false } = {}) {
  const node = el('div', '', 'recording-classification');
  const state = classification?.status;
  if (state === 'not_requested') {
    if (hideUnrequested) return el('span');
    node.append(el('p', 'Sound categories are shown on incident details.', 'muted small'));
    return node;
  }
  let label = 'Not classified', note = 'No sound estimate has been saved yet.';
  if (state === 'pending') { label = 'Waiting for sound estimate'; note = 'Background processing has not finished.'; }
  else if (state === 'processing') { label = 'Analysing sound…'; note = 'Background processing is underway.'; }
  else if (state === 'failed') { label = 'Sound estimate unavailable'; note = 'The recording remains available to listen to.'; }
  else if (state === 'completed') {
    const category = CATEGORIES[classification.primary_category] || 'Other';
    const uncertain = classification.confidence_status !== 'classified';
    label = `${category}${uncertain ? ' / uncertain' : ''} · model estimate`;
    note = uncertain ? 'The model could not identify a clear sound category.' : `Estimated sound category for ${scope === 'incident' ? 'the recorded incident sound' : 'this recording'}.`;
  }
  if (classificationPending(classification) && classification.worker_status === 'disabled') {
    label = 'Sound classification disabled'; note = 'Automatic sound estimates are switched off. Saved recordings remain available to listen to.';
  } else if (classificationPending(classification) && classification.worker_status === 'unavailable') {
    label = 'Classifier unavailable · estimate pending'; note = classification.worker_error || 'The classification service is unavailable. Saved audio is unaffected; the app will check again.';
  } else if (classificationPending(classification) && classification.worker_status === 'starting') {
    label = 'Sound classifier starting'; note = 'Saved recordings are waiting while the model starts.';
  }
  node.append(el('small', scope === 'incident' ? 'Incident sound estimate' : 'Recording sound estimate', 'muted'), badge(label, 'neutral'), el('small', note, 'muted'));
  if (classification?.refreshing) node.append(el('small', 'Updating this estimate. The category shown is from the previous saved analysis.', 'muted'));
  if (!classification || state === 'not_requested' || state === 'pending' || state === 'processing') return node;
  const details = el('details', '', 'recording-classification-details');
  details.append(el('summary', 'YAMNet details'));
  details.append(el('p', 'Model scores are not calibrated probabilities or sound levels. A category is an estimate, not proof of the source.', 'muted small'));
  if (classification.uncertainty_reason) details.append(el('p', `Why uncertain: ${String(classification.uncertainty_reason).replaceAll('_', ' ')}`, 'muted small'));
  if (state === 'failed' && classification.error) details.append(el('p', String(classification.error), 'muted small'));
  const labels = (Array.isArray(classification.top_labels) ? classification.top_labels : []).filter(item => typeof item?.label === 'string').slice(0, 5);
  if (labels.length) {
    const list = el('ul');
    for (const item of labels) list.append(el('li', `${item.label} · score ${score(item.score)}`));
    details.append(list);
  }
  if (classification.model_version) details.append(el('p', `Model: ${classification.model_version}${classification.mapping_version ? ` · Category mapping: ${classification.mapping_version}` : ''}`, 'muted small'));
  if (classification.classified_at) details.append(el('p', `Estimated: ${formatTime(classification.classified_at, timezone)}`, 'muted small'));
  node.append(details);
  return node;
}

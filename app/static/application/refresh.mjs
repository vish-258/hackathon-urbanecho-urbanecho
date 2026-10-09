// Live batches retain only the affected identities, not every received event.
export const CONFIGURATION_REASONS = new Set(['location_registered', 'location_updated', 'location_configured', 'threshold_configured', 'device_registered', 'device_configured', 'device_reassigned', 'device_removed']);

export function liveChanges() {
  return { locations: new Set(), measurements: new Set(), incidents: new Set(), incidentLocations: new Set(), configuration: new Set(), metadata: false, aged: false };
}

export function addLiveEvent(changes, event) {
  if (event.location_id) changes.locations.add(event.location_id);
  if (event.measurement_id && event.transition_reason !== 'data_stale' && event.location_id) changes.measurements.add(event.location_id);
  if (event.event_type?.startsWith('incident.')) {
    if (event.incident_id) changes.incidents.add(event.incident_id);
    if (event.location_id) changes.incidentLocations.add(event.location_id);
  }
  if (CONFIGURATION_REASONS.has(event.transition_reason) && event.location_id) changes.configuration.add(event.location_id);
  return changes;
}

export function mergeChanges(target, source) {
  for (const key of ['locations', 'measurements', 'incidents', 'incidentLocations', 'configuration']) {
    for (const id of source?.[key] || []) target[key].add(id);
  }
  target.metadata ||= !!source?.metadata;
  target.aged ||= !!source?.aged;
  return target;
}

export function locationChanges(changes, id) {
  return {
    summary: !!(changes?.aged || changes?.metadata || changes?.locations.has(id)),
    history: !!changes?.measurements.has(id),
    supporting: !!(changes?.metadata && changes.configuration.has(id)),
    incidents: !!changes?.incidentLocations.has(id),
  };
}

// Only one operation runs at a time. Requests made while it runs collapse into
// one trailing operation, which reads the newest selected filters/settings.
export function coalesceAsync(operation, signal) {
  let running = null, pending = false;
  return function request() {
    if (signal?.aborted) return Promise.resolve();
    pending = true;
    if (running) return running;
    running = (async () => {
      // Defer the first operation so synchronous calls can coalesce too.
      await Promise.resolve();
      try {
        while (pending && !signal?.aborted) {
          pending = false;
          await operation();
        }
      } finally { running = null; pending = false; }
    })();
    return running;
  };
}

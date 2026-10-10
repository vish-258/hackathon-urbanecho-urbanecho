const ATTEMPT_TIMEOUT_MS = 12_000;
const MAX_AGE_MS = 120_000;
const FUTURE_TOLERANCE_MS = 5_000;

const messages = {
  permission_denied: 'Location access was denied. Allow location access for this site and enable Location Services for your browser in your computer settings, then try again.',
  insecure_context: 'Automatic location needs a secure connection. Open UrbanEcho on localhost on this computer, or use HTTPS, then try again.',
  unsupported: 'This browser cannot request your location. Open UrbanEcho in a browser with location access, such as Chrome or Safari, then try again.',
  unavailable: 'Your browser could not determine this computer’s location. Enable Location Services for your browser and keep Wi-Fi on, then try again near the devices.',
  timeout: 'Your browser did not find a location in time. Check Location Services and this site’s location permission, keep Wi-Fi on, then try again near the devices.',
  invalid_position: 'Your browser returned an invalid location. No coordinates were changed. Try finding your location again.',
  stale_position: 'Your browser returned an old location or an incorrect timestamp. Check this computer’s clock and try finding your location again.',
  aborted: 'Location lookup was cancelled.',
};

function locationError(code) {
  const error = new Error(messages[code]);
  error.code = code;
  if (code === 'aborted') error.name = 'AbortError';
  return error;
}

function browserError(error) {
  if (error?.code === 1 || error?.name === 'SecurityError' || error?.name === 'NotAllowedError') {
    return locationError('permission_denied');
  }
  if (error?.code === 3) return locationError('timeout');
  return locationError('unavailable');
}

function validatePosition(position, now) {
  const {latitude, longitude, accuracy} = position?.coords ?? {};
  const timestamp = position?.timestamp;
  if (![latitude, longitude, accuracy, timestamp].every(Number.isFinite)
      || latitude < -90 || latitude > 90 || longitude < -180 || longitude > 180
      || accuracy < 0) {
    throw locationError('invalid_position');
  }
  const age = now() - timestamp;
  if (!Number.isFinite(age) || age > MAX_AGE_MS || age < -FUTURE_TOLERANCE_MS) {
    throw locationError('stale_position');
  }
  return {latitude, longitude, accuracy, timestamp};
}

function requestPosition({geolocation, signal, now}, enableHighAccuracy) {
  return new Promise((resolve, reject) => {
    let settled = false;
    let deadline;
    const finish = (error, position) => {
      if (settled) return;
      settled = true;
      clearTimeout(deadline);
      signal?.removeEventListener('abort', abort);
      if (error) reject(error);
      else resolve(position);
    };
    const abort = () => finish(locationError('aborted'));
    if (signal?.aborted) { abort(); return; }
    signal?.addEventListener('abort', abort, {once: true});
    // Some browsers never invoke either callback; this deadline also covers that case.
    deadline = setTimeout(() => finish(locationError('timeout')), ATTEMPT_TIMEOUT_MS);
    try {
      geolocation.getCurrentPosition(position => {
        if (settled) return;
        try { finish(null, validatePosition(position, now)); }
        catch (error) { finish(error); }
      }, error => finish(browserError(error)), {
        enableHighAccuracy,
        timeout: ATTEMPT_TIMEOUT_MS,
        maximumAge: 0,
      });
    } catch (error) {
      finish(browserError(error));
    }
  });
}

// This is the browser/computer's position, not a GPS reading from an ESP32.
// Call only for an explicit location action, while the user is near the devices.
export async function locateCurrentPosition({
  signal,
  geolocation = globalThis.navigator?.geolocation,
  secureContext = globalThis.isSecureContext,
  now = Date.now,
} = {}) {
  if (signal?.aborted) throw locationError('aborted');
  if (!secureContext) throw locationError('insecure_context');
  if (typeof geolocation?.getCurrentPosition !== 'function') throw locationError('unsupported');
  const options = {geolocation, signal, now};
  try {
    return await requestPosition(options, true);
  } catch (error) {
    if (error.code !== 'unavailable' && error.code !== 'timeout') throw error;
    return requestPosition(options, false);
  }
}

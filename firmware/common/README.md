# UrbanEcho portable device core

This C++17 library is shared by the actual ESP32 firmware in `../esp32`. It has no GPIO or ESP SDK dependency. Native tests verify the recording transport/state machine; they do not establish microphone or Wi-Fi behavior.

## Contract and memory

`include/urbanecho/device_core.hpp` provides:

- `CaptureClock`: maps a 64-bit monotonic sample timestamp to UTC, blocks capture until time is usable, rejects stale anchors, and changes the stream session after a significant clock correction. An interrupted recording is dropped, while complete queued timestamps remain immutable.
- `CaptureQueue<MaxFrames, Slots>`: fixed PCM/WAV arrays, no recording-sized heap allocation. The default two slots hold 16,000 mono samples each: 96,090 bytes of WAV buffer storage, 96,968 bytes for the entire queue in the tested native build. One slot may be uploading while the second is being filled.
- `decode_i2s_slot`: sign-extends a 24-bit value from an explicitly specified bit offset within a 32-bit DMA slot. The selected ESP32 adapter supplies offset 8 after choosing the left stereo slot; alternate boards/drivers must verify their own alignment.
- `MultipartRequest`: three spans referencing a short prefix, the original packed PCM24 WAV buffer, and a short suffix. The `file` part is `audio/wav`; `metadata` contains `device_id`, `chunk_id`, `captured_at`, `session_id`, and `sequence`. Exact Content-Length is available without a second audio copy.
- `UploadEngine`: retries identical bytes and metadata; it never renames a recording after a lost acknowledgement.

The FIFO keeps the oldest recording. If both slots are occupied, a new recording opportunity is dropped and counted. Its sequence number is still consumed, preventing missing captures from masquerading as continuous recovery. The boot identity combines an atomically persisted boot counter with a fresh 128-bit random nonce. A reset starts a new session. No default boot identity is accepted.

Captures in RAM do **not** survive power loss. Counters are serial diagnostics, not a persistent loss ledger. Long outages necessarily lose recordings with this deliberately small memory budget. Missing sound is never replaced with artificial silence.

## Retry rules

| Result | Action |
|---|---|
| 202 with valid stored-record acknowledgement | Release the uploaded buffer. |
| 200 with valid duplicate acknowledgement | Release the same buffer; count duplicate acknowledgement. |
| Lost response, network failure, 408, 429, 5xx | Retry the frozen recording with exponential backoff and jitter, up to the configured attempt limit. |
| Retry limit reached | Drop and count this recording; proceed to the next one. |
| 401 / 403 | Pause uploads and retain the FIFO head; operator must repair credentials/configuration. New capture remains bounded. |
| 409 | Reject/count this conflicting recording and continue. Never invent a new ID to bypass conflict checking. |
| 413 / 422 / other unexpected responses or redirects | Reject/count the head and pause; repair the format/server configuration. |
| 2xx with malformed/non-audio response | Treat acknowledgement as lost and retry the same recording. |

Default retry spacing is 0.75–1 seconds, then doubles to a 30-second cap; jitter stays within 75–100% of each step. Bounded Retry-After may defer a retry up to five minutes. Maximum attempts is eight. A real HTTP adapter also needs bounded socket timeouts and must forbid redirects and invalid TLS certificates.

## Concurrency and adapter responsibilities

All core mutations run on one serialized executor. `HttpTransport::begin()` and `poll()` must return quickly. A separate network task may read the frozen multipart spans, but it must finish all access before returning its completion result. The front recording cannot be released or changed while that task uses it. The provided ESP32 adapter follows this ownership protocol with one-entry FreeRTOS message queues.

The capture adapter must supply the timestamp of the first sample, keep draining DMA during network outages, identify DMA gaps, call `missed_capture()` exactly once per recognized lost capture, and consume scheduled recording opportunities even when buffers are full. It must not use upload time as capture time. Boot identity persistence, entropy, Wi-Fi, TLS trust, NTP synchronization, physical channel selection, and DMA configuration are adapter responsibilities.

## Run the native checks

From the project root, using a C++17 compiler:

```sh
firmware/common/run-tests.sh /tmp/urbanecho-firmware-tests
```

To also check generated multipart/WAV bytes against the actual backend models and WAV validator, set `PYTHON` to an interpreter containing the project's Python dependencies:

```sh
PYTHON=/path/to/project/venv/bin/python firmware/common/run-tests.sh /tmp/urbanecho-firmware-tests
```

The native suite covers signed PCM extremes/padding, timestamp synchronization/expiry/jumps, boot changes, bounded buffers/sequence gaps, immutable retry after lost acknowledgement, capture during upload, Wi-Fi/TLS gating, response validation, authentication/configuration failures, retry limits, and upload cadence. Temporary fixture recordings deliberately contain clipped extrema; they are byte-transport fixtures, not acoustic calibration data, and are never uploaded automatically.

Optional CMake support is supplied for developer environments that already have CMake. The simple shell runner does not require it.

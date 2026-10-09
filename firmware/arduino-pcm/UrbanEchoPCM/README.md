# UrbanEcho Arduino PCM client

This is the updated **C++ Arduino sketch** for the assembled classic ESP32 / ESP-WROOM-32 and INMP441 microphone. Open `UrbanEchoPCM.ino` in Arduino IDE. It adapts the supplied prototype to the UrbanEcho server's authenticated PCM compatibility endpoints, retaining its microphone pins, signed 16-bit audio, DC blocker and configurable gain. The existing [ESP-IDF firmware](../../esp32/README.md) remains a separate alternative; do not combine the two projects.

The user reports that their assembled hardware transmits to their small Python test server. That report establishes a useful starting point. This updated sketch still needs to be flashed and checked on that physical board; compilation does not establish microphone timing, sound accuracy or successful transmission to UrbanEcho.

## What to change on the device

Copy `config.example.h` to **`privateconfig.h` in this same folder**. Fill in the private copy:

| Setting | Value to use |
|---|---|
| `UE_CONFIGURED` | `true` after completing the settings below |
| `UE_WIFI_SSID`, `UE_WIFI_PASSWORD` | The device's reachable 2.4 GHz Wi-Fi network; keep these private |
| `UE_HOST` | The **UrbanEcho server computer's reachable LAN IP or hostname**, without `http://`, `https://`, port or path; never `localhost` |
| `UE_USE_HTTPS`, `UE_PORT` | Default: `true`, `8443`, matching the optional local hardware listener |
| `UE_CA_CERT` | That listener's **public CA certificate**; its certificate must match `UE_HOST` |
| `UE_DEVICE_ID` | Keep the hardware ID, for example `UE-001`; first map this ID to a registered device and location in UrbanEcho |
| `UE_DEVICE_TOKEN` | The private credential issued to **that same registered device** |
| `UE_CAPTURE_INTERVAL_MS` | `1000` for continuous one-second recordings; larger intervals intentionally leave gaps |
| `UE_UPLOAD_INTERVAL_MS` | `0` sends each available recording promptly; a positive value imposes a minimum pause between uploads and can fill the queue |
| `UE_GAIN` | `16.0f` preserves the prototype gain; reduce if the PCM output clips; changing gain changes the measurement chain |

For an explicitly isolated HTTP bench setup, use `UE_USE_HTTPS=false`, the reachable HTTP listener port, and `UE_ALLOW_HTTP_BENCH=true`. This opt-in sends the credential/audio without transport encryption. It does not expose a server automatically: a service bound only to `127.0.0.1:8000` cannot be reached by the ESP32. The default remains verified HTTPS; there is no certificate-bypass option.

Do not paste secrets into chat, include `privateconfig.h` in a shared ZIP, or share a binary compiled from real settings. Wi-Fi details and the device credential are embedded in that provisioned binary. The public source package contains placeholders only.

## Device ID determines location on the server

Keep `UE_DEVICE_ID="UE-001"` for that physical device and create its server-side mapping to the correct registered location. Use another unique ID for each other physical device. The supported external-ID characters are letters, digits, `_` and `-`, up to 32 characters. The server also accepts an existing registered UUID.

The sketch sends no coordinates or location name. UrbanEcho resolves the mapped ID, checks the matching device credential and uses its persisted location assignment at capture time. A short ID alone is not a password. Changing the board's ID without updating its server registration/token will fail authentication; moving a device to another location should update its server assignment rather than rewriting old recording history.

## Endpoint changes from the prototype

Use the **UrbanEcho server** as `UE_HOST`, instead of the standalone Python test server. Paths remain simple:

| Method and path | Purpose |
|---|---|
| `GET /ping` | A public connectivity check; a successful response alone does not prove authentication or audio processing |
| `POST /text` | Authenticated diagnostic text, at most 1,000 bytes; this is not a noise measurement |
| `POST /upload` | Authenticated one-second raw PCM upload routed into UrbanEcho's existing stored-recording and measurement pipeline |

The sketch sends `Authorization: Bearer …` and `X-Device-Id` on both POST requests. Each audio request also carries:

- `X-Session`: a fresh 32-character random identifier for this boot.
- `X-Seq`: a sequence number consumed for each scheduled recording, including a recording lost because buffers are full.
- `X-Captured-At`: the **start of capture**, in UTC ISO 8601 with a `Z` suffix, frozen before upload.
- `Content-Type: application/octet-stream` and exactly **32,000 bytes** of signed little-endian PCM16, mono, 16,000 samples/second.

The recording duration changes from the supplied sketch's 0.5 seconds to **1 second** so it can match UrbanEcho's one-second location measurement rule. This RAM profile fixes the duration at one second; cadence is configurable separately. No WAV header is sent by the device: the server wraps each accepted chunk as a recording and processes it through the normal worker. Match the physical location's expected interval to one second.

A successful acknowledgement is HTTP 200 with the matching sequence and a saved recording ID/status. Retries keep the same samples, session, sequence and timestamp. The sketch prints `saved_audio_id=…` only after validating that acknowledgement. A saved recording can still be pending or subsequently fail processing; verify its measurement in the app/server rather than equating HTTP success with a calibrated sound level.

The old test server's `{ok, seq}` reply is deliberately insufficient for this acknowledgement check: deploy the updated UrbanEcho compatibility endpoints before using this sketch. A concatenated `DEVICE_ID_session.wav` from the old test server is not a source of daily summaries. Use UrbanEcho's saved recording IDs and returned download information; do not guess a download filename from the friendly device ID.

## Wiring and board selection

| INMP441 pin | Confirmed ESP32 GPIO/power connection |
|---|---|
| VDD | 3.3 V |
| GND | GND |
| SCK/BCLK | GPIO26 |
| WS/LRCLK | GPIO25 |
| SD/DATA | GPIO33 |
| L/R | GND, selecting **left** |

The sketch deliberately uses the confirmed left slot; it does not silently switch to the other channel when the microphone is quiet. A quiet recording can be valid or indicate a wiring problem, so inspect the wiring and diagnostics. Do not connect VDD to 5 V. The exact development-board carrier revision and flash size remain to be confirmed; these instructions refer to GPIO numbers rather than header positions.

Use Arduino IDE 2 with **esp32 by Espressif Systems version 3.3.8**. Its built-in `ESP_I2S`, Wi-Fi, HTTP, SNTP and cJSON components are sufficient; no extra Arduino libraries are required. For a classic ESP-WROOM-32 carrier, start from **ESP32 Dev Module** after confirming the module type, and choose its real serial port and flash size. Select a different exact board only if its specifications match; this sketch is not an ESP32-C3/S3 pin profile.

1. Open `UrbanEchoPCM.ino`; the folder and sketch names must match.
2. Create/fill the private settings file above.
3. Select the board and USB serial port on the computer physically connected to the ESP32.
4. Verify/compile, then upload. Use a USB data cable. Follow the actual board's BOOT/reset instructions if automatic flashing fails.
5. Open Serial Monitor at **115200 baud**. The sketch waits for Wi-Fi and a recent SNTP time synchronization before capturing. A missing private configuration halts with “Not provisioned”.

Equivalent compiler command with Arduino CLI and this pinned core installed:

```sh
arduino-cli compile --fqbn esp32:esp32:esp32 --warnings all firmware/arduino-pcm/UrbanEchoPCM
```

Do not upload a compile-check profile or assume a successful compiler run detected the physical board.

## Timing, retry and buffering behavior

- A random 128-bit boot session and monotonically increasing recording sequence provide retry identity. Capture, pending and in-flight recordings share **two fixed buffers occupying 64,000 audio bytes total**. There is no allocation of a new audio buffer per recording. Both buffers being occupied means the next scheduled recording is dropped; this small classic-ESP32 RAM budget cannot hold a long outage.
- SNTP synchronizes every 15 minutes. A clock becomes unusable after one hour without a successful update. The firmware requires a recent successful update, not merely a plausible date. The network must allow DNS and NTP, including after a reboot.
- Capture timestamps are estimated from the first DMA completion time and advanced by the sample count, rather than assigned when a queued upload finally reaches the network. The first 400 ms after starting/restarting I2S is discarded. DMA overflow, a read timeout, excessive capture delay, or a clock adjustment over 250 ms discards an incomplete recording and restarts the stream; those gaps are not replaced by zeros. This is software timing logic, not a measured statement of the board's timestamp accuracy; validate it on the real hardware.
- Capture keeps draining microphone data while network uploads retry. When all buffers are occupied, new recordings are dropped and their sequence numbers consumed. The serial counters distinguish dropped captures, dropped uploads and dropped status messages. Data in RAM is lost on reboot/power loss; there is no SD-card or flash spool.
- Temporary network errors, HTTP 408/429 and server failures are retried with bounded backoff (six attempts by default). Every retry resends the immutable original request. After exhaustion, the recording is dropped and its counter increments. A permanent HTTP 4xx error pauses recording/upload until the configuration is fixed and the board restarted.
- Diagnostics use `/text` on a best-effort basis at most once per ten seconds, and only when no audio is already waiting in the upload queue. Their connect/response timeouts are 500 ms each and their TLS handshake budget is one second. They are also printed locally. A failed diagnostic request is not retained indefinitely. Serial counters are the reliable source for this firmware's drop totals; `/text` does not create a durable telemetry history. Network delays can still consume buffer capacity; this is not a lossless recorder.
- The one-second default is intended to provide contiguous input for the configured recovery rule. Longer capture/upload intervals and actual dropped packets reduce coverage and can prevent a contiguous recovery window from completing. Keep gaps visible when reviewing daily summaries.

## Physical acceptance check

Run the server's normal API and worker, configure its reachable device listener, register/map `UE-001` to the intended **physical** location, and provision that device's token before flashing.

1. Confirm the serial monitor obtains Wi-Fi/time and reports `MIC OK` with nonzero changing samples. Inspect “MIC SILENT” or clipping before trying to infer noise levels.
2. Confirm a `saved_audio_id` appears, then inspect that recording and its calculated level at the correct location. Compare capture time with the actual UTC/local time. Playback/intact bytes and plausible changing dBFS establish transmission/processing, not acoustic calibration.
3. Confirm the app shows **physical, uncalibrated dBFS** until a genuine calibration is registered. Test a physical location rule that uses this same measurement definition and one-second duration. A simulated SPL rule is incompatible with uncalibrated physical input.
4. Exercise normal, above-threshold and sustained levels; confirm the saved incident and app notification. Restore normal input for the complete configured recovery period; confirm resolution. Do not use dangerous sound levels just to exceed a threshold—choose a suitable controlled bench threshold.
5. Briefly interrupt Wi-Fi, observe bounded retries/drop counters, restore it and inspect any resulting coverage gaps. Repeated submission of the same sequence must not add another recording or another incident.
6. Generate/recalculate the location's report for the capture date and compare the saved recording count/duration and coverage with the app.

The input is **uncalibrated physical PCM16**, not simulated SPL. The DC blocker, fixed gain, microphone sensitivity, frequency response and clipping all affect the result. Trustworthy acoustic dB SPL requires a reference calibration for this exact processing chain and microphone, an agreed measurement definition/weighting, and quality validation. Changing gain or capture processing invalidates any earlier calibration assumptions.

## Troubleshooting

| Observation | Check |
|---|---|
| “Not provisioned” | Private configuration exists beside the sketch; `UE_CONFIGURED=true`; valid ID/token/host; valid CA or explicit bench opt-in |
| Waiting for Wi-Fi/time | 2.4 GHz credentials, Wi-Fi isolation, DNS/NTP reachability; no recordings are fabricated while time is unknown |
| Network error/negative HTTP code | Host/port, same reachable LAN, listener binding, firewall, certificate trust/SAN/clock; `/ping` tests only reachability |
| HTTP 401/403 | Registered hardware ID and its own credential, device enabled state; do not use the browser/admin credential |
| HTTP 400/409/413/422 | Review server diagnostics for body format, immutable retry conflict, one-second size, capture time or historical location assignment |
| HTTP 404 | Wrong host/service/path, or updated compatibility routes have not been deployed |
| HTTP 200 but no `saved_audio_id` | The server returned an unexpected acknowledgement; verify that this is UrbanEcho, not the old standalone test server |
| Repeated capture gaps | I2S data/overflow, CPU scheduling, clock adjustments, RAM headroom and wiring; use serial diagnostics to distinguish causes |
| Clipping despite modest raw signal | Gain can clip the converted 16-bit output; lower gain and reassess calibration rather than accepting saturated recordings |
| Upload succeeds but no incident | Worker/measurement status, quality, rule units/duration, threshold condition and capture freshness; transport success is not proof of alert eligibility |
| Device visible, wrong location | Correct its server-side external-ID mapping/assignment; do not upload coordinates from the sketch |

## Official implementation references

- [Espressif Arduino I2S API](https://docs.espressif.com/projects/arduino-esp32/en/latest/api/i2s.html)
- [Pinned Arduino-ESP32 3.3.8 I2S interface](https://github.com/espressif/arduino-esp32/blob/3.3.8/libraries/ESP_I2S/src/ESP_I2S.h)
- [Pinned Arduino-ESP32 3.3.8 HTTP interface](https://github.com/espressif/arduino-esp32/blob/3.3.8/libraries/HTTPClient/src/HTTPClient.h)
- [ESP-IDF I2S timing/receive interface](https://docs.espressif.com/projects/esp-idf/en/stable/esp32/api-reference/peripherals/i2s.html)

## Compilation verification — 9 October 2026

The final sketch compiled successfully with **Arduino CLI 1.5.1**, official **Arduino-ESP32 3.3.8**, and `esp32:esp32:esp32`. The toolchain archives were checked against the official registry checksums. Builds used isolated dummy settings and did not access a physical device.

| Profile | Program bytes / 1,310,720 | Static globals / 327,680 | Result |
|---|---:|---:|---|
| Configured HTTPS | 1,090,256 | 114,464 | Passed |
| Configured explicit HTTP bench | 1,087,988 | 114,464 | Passed |
| Shipped unconfigured template | 897,396 | 45,544 | Passed; deliberately halts before connecting |

The configured builds link the full capture/network runtime; the smaller unconfigured build alone would not establish memory fit. The compiler reports 213,216 bytes remaining after static globals for the configured profiles, **before** task stacks, Wi-Fi, TLS and other runtime allocations. Actual runtime headroom is still a hardware check. The final builds reported no sketch warnings; a clean upstream library compilation showed three `ESP_I2S` initializer warnings. An initial three-buffer prototype exceeded static RAM and was reduced to the two-buffer profile delivered here.

No compiled dummy binary is a deployment artifact. Physical flashing, microphone capture, Wi-Fi recovery and acoustic calibration must be recorded separately after this updated program is exercised on the actual device. See the repository's hardware compatibility report for backend adapter checks.

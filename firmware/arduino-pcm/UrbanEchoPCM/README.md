# UrbanEcho Arduino PCM client

This is the updated **C++ Arduino sketch** for the assembled classic ESP32 / ESP-WROOM-32 and INMP441 microphone. Open `UrbanEchoPCM.ino` in Arduino IDE. It adapts the supplied prototype to the UrbanEcho server's authenticated PCM compatibility endpoints, retaining its microphone pins, signed 16-bit audio, DC blocker and configurable gain. The existing [ESP-IDF firmware](../../esp32/README.md) remains a separate alternative; do not combine the two projects.

The user reports that their assembled hardware transmits to their small Python test server. That report establishes a useful starting point. The local bench follow-up below verifies firmware flashing, physical microphone uploads, live readings, a saved incident and recovery, and a saved daily summary on one board. These checks establish the software path; they do not establish acoustic accuracy.

## What to change on the device

Copy `config.example.h` to **`privateconfig.h` in this same folder**. Fill in the private copy:

| Setting | Value to use |
|---|---|
| `UE_CONFIGURED` | `true` after completing the settings below |
| `UE_USE_SAVED_WIFI` | Default: `false`. Set the macro to `true` to reuse this board's remembered Wi-Fi configuration |
| `UE_WIFI_SSID`, `UE_WIFI_PASSWORD` | The device's reachable 2.4 GHz Wi-Fi network; keep these private. Ignored when `UE_USE_SAVED_WIFI` is `true` |
| `UE_HOST` | The **UrbanEcho server computer's reachable LAN IP or hostname**, without `http://`, `https://`, port or path; never `localhost` |
| `UE_USE_HTTPS`, `UE_PORT` | Default: `true`, `8443`, matching the optional local hardware listener |
| `UE_CA_CERT` | That listener's **public CA certificate**; its certificate must match `UE_HOST` |
| Device identity | Automatic: `ESP-` followed by the factory MAC's 12 uppercase hexadecimal digits, without colons. There is no ID setting to fill in |
| `UE_DEVICE_TOKEN` | Leave empty (default) to use the token stored on the board by `scripts/provision-board.py`. A private compiled token must belong to this board's MAC-based registration |
| `UE_CAPTURE_INTERVAL_MS` | Default `1000`: continuous one-second recordings, assembled into incident audio on the server. Larger values intentionally leave gaps |
| `UE_UPLOAD_INTERVAL_MS` | `0` sends each available recording promptly; a positive value imposes a minimum pause between uploads and can fill the queue |
| `UE_GAIN` | `16.0f` preserves the prototype gain; reduce if the PCM output clips; changing gain changes the measurement chain |

Complete incident audio requires continuous capture: set **`UE_CAPTURE_INTERVAL_MS=1000`** and `UE_UPLOAD_INTERVAL_MS=0`. Keep the location's measurement interval at **1 second**. Each short upload carries real audio, and the server joins the original samples into a single incident recording; keeping uploads short preserves prompt threshold detection and bounded ESP32 RAM. Changing the example file does not change an existing `privateconfig.h` or firmware already on a board. Preserve gain, pins, Wi-Fi, TLS and credentials, then compile and upload without erasing NVS. USB is needed; this firmware has no remote interval-setting or OTA command.

The application now offers ten-second recordings assembled on the server from ten continuous uploads. Keep `UE_CAPTURE_INTERVAL_MS=1000` and `UE_UPLOAD_INTERVAL_MS=0`; the board cannot buffer a complete 320,000-byte ten-second payload safely alongside Wi-Fi/TLS. Short uploads preserve quick threshold checks and immutable retries. The ten-second server grouping does not change microphone gain, sound-level definitions, device identity or location mapping.

The optional `10000` setting covers about **10% of elapsed time**: one second recorded and nine seconds intentionally unrecorded. It cannot provide complete incident audio. Missing time is not silence and remains visible in incident and daily coverage. The two boards keep independent sample clocks; matching intervals do not synchronize their capture start times. Continuous capture remains best effort during network outages because this board has only two RAM buffers and no persistent audio spool.

If the board already connects to the intended network, add `#define UE_USE_SAVED_WIFI true` to its private configuration (or change the existing definition). The sketch calls `WiFi.begin()` with no arguments so the SDK can reuse its stored settings; it does not extract, print, or replace the saved SSID/password. Leave the Wi-Fi string declarations present; they may be empty in this mode. Complete the host and certificate settings, keep `UE_CONFIGURED=true`, and provision the device token over USB. Existing private configurations without the Wi-Fi macro retain their previous Wi-Fi behavior.

Preserve the board's NVS partition: keep **Erase All Flash Before Sketch Upload disabled** (`EraseFlash=none`) and retain a compatible partition layout. Do not erase its Wi-Fi settings. If the saved network is missing or unreachable, the device remains disconnected; it does not fall back to placeholder credentials. Supply working private Wi-Fi settings with this option disabled if needed. A damaged or incompatible NVS partition can still be reformatted by the board runtime, so confirm reconnection on the physical device after flashing. Serial output identifies the selected Wi-Fi mode, prints the device's local IP once per connection, and reports disconnection transitions; it never prints the network name or password.

For an explicitly isolated HTTP bench setup, use `UE_USE_HTTPS=false`, the reachable HTTP listener port, and `UE_ALLOW_HTTP_BENCH=true`. This opt-in sends the credential/audio without transport encryption. It does not expose a server automatically: a service bound only to `127.0.0.1:8000` cannot be reached by the ESP32. The default remains verified HTTPS; there is no certificate-bypass option.

Do not paste secrets into chat, include `privateconfig.h` in a shared ZIP, or share a binary compiled from real settings. Wi-Fi details and any nonempty compiled device credential are embedded in that binary; USB-provisioned credentials are stored separately in the board's NVS flash. A full-flash backup also contains private settings. The public source package contains placeholders only.

## One firmware for every board

With `UE_DEVICE_TOKEN` left empty, the same build can be flashed onto every supported board using the same Wi-Fi/server settings. Each board reads its factory MAC and names itself `ESP-<12 uppercase hex digits>` (for example `ESP-20500D114084`); no manual ID is needed. The identity stays the same after a restart or firmware update. Each board keeps its own token in NVS flash. A MAC address is not secret, so the per-board token is still required. Provision each new board once, from the project root, with the board on USB and no serial monitor open:

```sh
python3 scripts/provision-board.py --location "Location name from Management"
```

The script asks the board for its ID over USB, registers that ID at the location, saves a private backup in `.local/devices/<ID>.json`, sends the token to the board, and waits for its first upload. It never prints the token. Running it again for a provisioned board changes nothing. If the board loses its token (for example after a full flash erase), the script restores it from the backup; the server cannot reveal a token again. Re-flashing the firmware keeps the stored token.

Serial commands handled by the board itself, never forwarded to the server: `IDENTITY` prints the ID and whether a token is stored; `PROVISION <token>` stores a token and restarts; `FORGET` removes the stored token and restarts. An unprovisioned board prints its ID every five seconds and does not record audio. Move a provisioned board to another location in **Management → Devices → Edit mapping**; no re-provisioning is needed.

## Device ID determines location on the server

Use the identity reported by the `IDENTITY` serial command when assigning a physical board to a location. `scripts/provision-board.py` performs this registration using the board's identity automatically. Every board has its own MAC-based external ID; the database also retains a separate internal UUID for its records.

The sketch sends no coordinates or location name. UrbanEcho resolves the mapped ID, checks the matching device credential and uses its persisted location assignment at capture time. The ID alone is not a password. Moving a device to another location should update its server assignment rather than rewriting old recording history.

When upgrading an older board that used a manually configured ID, first arrange its matching MAC-based server registration and credential. An old `UE_DEVICE_ID` declaration is ignored by this firmware and may be removed from the private header. A token issued only for the old registration will not authenticate the new identity. Do not erase historical records as part of an ordinary firmware update; decide the migration of the existing device record explicitly. New boards should use the USB/NVS provisioning flow above.

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
5. With Serial Monitor closed, run the provisioning command above. Then open Serial Monitor at **115200 baud**. The sketch waits for Wi-Fi and a recent SNTP time synchronization before capturing. Missing shared settings halt with “Not configured”; a missing device token reports “UNPROVISIONED” and waits for USB provisioning.

Equivalent compiler command with Arduino CLI and this pinned core installed:

```sh
arduino-cli compile --fqbn esp32:esp32:esp32 --warnings all firmware/arduino-pcm/UrbanEchoPCM
```

Do not upload a compile-check profile or assume a successful compiler run detected the physical board.

## Timing, retry and buffering behavior

- A random 128-bit boot session and monotonically increasing recording sequence provide retry identity. Capture, pending and in-flight recordings share **two fixed buffers occupying 64,000 audio bytes total**. There is no allocation of a new audio buffer per recording. Both buffers being occupied means the next scheduled recording is dropped; this small classic-ESP32 RAM budget cannot hold a long outage.
- Each queued recording snapshots its capture interval and sends it as `X-Capture-Interval-Ms`. Retries reuse that value, timestamp, session, sequence and audio. The sequence increments once per scheduled recording, including a dropped capture, rather than once per microphone frame. The backend can distinguish planned sampling gaps from missing expected recordings.
- SNTP synchronizes every 15 minutes. A clock becomes unusable after one hour without a successful update. The firmware requires a recent successful update, not merely a plausible date. The network must allow DNS and NTP, including after a reboot.
- Capture timestamps are estimated from the first DMA completion time and advanced by the sample count, rather than assigned when a queued upload finally reaches the network. The first 400 ms after starting/restarting I2S is discarded. DMA overflow, a read timeout, excessive capture delay, or a clock adjustment over 250 ms discards an incomplete recording and restarts the stream; those gaps are not replaced by zeros. This is software timing logic, not a measured statement of the board's timestamp accuracy; validate it on the real hardware.
- Capture keeps draining microphone data while network uploads retry. When all buffers are occupied, new recordings are dropped and their sequence numbers consumed. The serial counters distinguish dropped captures, dropped uploads and dropped status messages. Data in RAM is lost on reboot/power loss; there is no SD-card or flash spool.
- Temporary network errors, HTTP 408/429 and server failures are retried with bounded backoff (six attempts by default). Every retry resends the immutable original request. After exhaustion, the recording is dropped and its counter increments. A permanent HTTP 4xx error pauses recording/upload until the configuration is fixed and the board restarted.
- Audio and diagnostic requests share one persistent, serialized HTTPS connection, avoiding a handshake for every second of audio and avoiding two simultaneous TLS allocations. Reuse occurs only after a complete, bounded acknowledgement is consumed; malformed replies, failed uploads and disconnections close the socket before retry. Certificate verification and immutable upload IDs remain enforced.
- Diagnostics use `/text` on a best-effort basis at most once per ten seconds, and only when no audio is already waiting in the upload queue. Their connect/response timeouts are 500 ms each and their TLS handshake budget is one second. They are also printed locally. A failed diagnostic request is not retained indefinitely. Serial counters are the reliable source for this firmware's drop totals; `/text` does not create a durable telemetry history. Network delays can still consume buffer capacity; this is not a lossless recorder.
- During each sampling gap the firmware drains and discards DMA samples while advancing its frame-based clock; it never uploads a backlog of microphone samples as a fresh recording. Ten-second cadence still produces one-second recordings. Recovery must use the declared capture cadence to judge consecutive scheduled readings; it does not establish that the unrecorded nine-second gaps were quiet. Missing or invalid scheduled readings can interrupt recovery. Keep these gaps visible in daily summaries.

## Physical acceptance check

Run the server's normal API and worker and configure its reachable device listener. Flash the shared firmware, then run `scripts/provision-board.py --location "Physical location name"` to register the board's MAC identity and provision its token. Keep the location separate from simulated demonstration locations.

1. Confirm the serial monitor obtains Wi-Fi/time and reports `MIC OK` with nonzero changing samples. Inspect “MIC SILENT” or clipping before trying to infer noise levels.
2. Confirm a `saved_audio_id` appears, then inspect that recording and its calculated level at the correct location. Compare capture time with the actual UTC/local time. Playback/intact bytes and plausible changing dBFS establish transmission/processing, not acoustic calibration.
3. Confirm the app shows **physical, uncalibrated dBFS** until a genuine calibration is registered. Test a physical location rule that uses this same measurement definition and one-second duration. A simulated SPL rule is incompatible with uncalibrated physical input.
4. Exercise normal, above-threshold and sustained levels; confirm the saved incident in the application. Restore normal input for the complete configured recovery period; confirm resolution. Do not use dangerous sound levels just to exceed a threshold—choose a suitable controlled bench threshold.
5. Briefly interrupt Wi-Fi, observe bounded retries/drop counters, restore it and inspect any resulting coverage gaps. Repeated submission of the same sequence must not add another recording or another incident.
6. Generate/recalculate the location's report for the capture date and compare the saved recording count/duration and coverage with the app.

The input is **uncalibrated physical PCM16**, not simulated SPL. The DC blocker, fixed gain, microphone sensitivity, frequency response and clipping all affect the result. Trustworthy acoustic dB SPL requires a reference calibration for this exact processing chain and microphone, an agreed measurement definition/weighting, and quality validation. Changing gain or capture processing invalidates any earlier calibration assumptions.

## Troubleshooting

| Observation | Check |
|---|---|
| “Not configured” | Private configuration exists beside the sketch; `UE_CONFIGURED=true`; valid Wi-Fi/host; valid CA or explicit bench opt-in |
| “UNPROVISIONED” | Close Serial Monitor and run `scripts/provision-board.py` for the intended physical location; the board needs its own stored token |
| “IDENTITY ERROR” | The board could not read its factory MAC; no recordings start. Check the board/core and reset before attempting provisioning |
| “Invalid UE_DEVICE_TOKEN” | Leave the token empty for USB provisioning, or supply the complete matching private token; malformed or oversized values are rejected before network capture |
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

## Local bench follow-up — 9 October 2026

One attached board was identified as an ESP32-D0WD-V3 revision 3.1 with 4 MB flash. A private full-flash backup was saved before installing the update. The original and new partition layouts matched; the upload preserved the NVS partition holding remembered Wi-Fi settings. Firmware upload completed with hash verification.

Both configured profiles compiled with the pinned 3.3.8 core: remembered Wi-Fi (1,089,612 program bytes; 114,472 static-global bytes) and an older private configuration without the new flag (1,088,552 program bytes; 114,472 static-global bytes). Upstream ESP_I2S initializer warnings were unchanged. No provisioned binaries or backups are included in the repository.

The board rejoined its remembered Wi-Fi without extracting the Wi-Fi password. After previous serial logs showed clipping at gain 16, the private uncalibrated bench profile used gain 1. One captured recording reported RMS 1,791, peak 16,173 and zero clipped output samples. This confirms a usable digital microphone signal, not sound-level calibration.

The initial attempt could not reach this Mac because the board and Mac were on different networks. The board was then provisioned for the Mac's network using its saved Wi-Fi setting, copied directly into the ignored private configuration without printing the password. The final physical profile compiled (1,089,624 program bytes; 114,472 static-global bytes) and was flashed with hash verification.

Verified after that correction:

- The first 41 inspected physical recordings mapped to the registered bench location and were good-quality, eligible live, one-second mono PCM16 at 16 kHz. Capture-to-receipt delay was 1.57–2.47 seconds, including the recording itself. A later check found 126 processed measurements.
- A downloaded 32,044-byte original matched its saved SHA-256 checksum. Credentials were not exposed during checks.
- One physical digital-level breach reached -26.24 dBFS against the -30 dBFS bench rule. The saved incident resolved after three normal readings; durable opening, update and resolution events were present, and the incident appeared in the app.
- The app displayed live readings for UE-001. Today's saved provisional report matched the application: 35 eligible recordings/35 usable seconds, average -34.18 dBFS, minimum -39.26, maximum -26.24, one incident and 0.0405% daily coverage. This is a snapshot; use Recalculate to include later recordings. One recording was still pending processing at that report's snapshot.

Four one-second capture gaps occurred among the first 41 inspected recordings. Transmission is working but is not lossless; the report retains missing time as missing. A controlled Wi-Fi interruption/recovery test and acoustic calibration remain pending. The temporary bench location uses explicitly labelled placeholder coordinates (0, 0), not a verified geographic position. Keep the server Mac and Docker running on the configured network while using the device; a server IP change requires updating both the device host and its matching TLS certificate.

For a read-only follow-up, use `scripts/check-device.py` with the ignored private hardware JSON, `--admin-env-file .env`, and `--verify-audio`. Select the bench location in the app; its negative dBFS values describe the digital signal and are not calibrated environmental dB SPL.

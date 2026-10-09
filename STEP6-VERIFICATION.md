# Step 6 verification — 9 October 2026

UrbanEcho's complete software demonstration works with three simulated devices and locations. The C++ firmware for the user-confirmed ESP-WROOM-32 / INMP441 wiring compiles for the actual ESP32 target. The board is on another computer, so it has not been flashed or physically tested in this session. Acoustic calibration is also pending.

## What was added

- A repeatable, foreground three-device verification runner that uses the existing registration, audio uploads, worker, threshold rules, notifications and daily-report APIs.
- An ESP-IDF C++ project with microphone capture, startup discard, Wi-Fi, time synchronization, device credentials, PCM24 WAV packing, stable upload identities, immutable retries, bounded buffering and serial drop diagnostics.
- Private device registration/configuration helpers and a read-only saved-recording checker. No physical device or location was invented.
- An optional, authenticated HTTPS device listener for a private LAN. It shares the existing ingestion implementation and excludes management pages and administrator credentials. The ordinary application remains bound to localhost; the LAN listener has not been enabled for ongoing use.
- A clearer application label for a location with both reporting and silent devices: **Some devices not reporting**. Fully stale locations still say **No recent data**.

## Verification status

| Area | Result | Limit |
|---|---|---|
| Existing backend, alerts and daily processing | 382 Python checks passed using disposable PostgreSQL/PostGIS databases | Automated fixtures are simulated |
| Frontend state and rendering logic | 34 Node checks passed | Browser inspection was also performed below |
| Live three-location software flow | Two complete runs passed against the local application | Synthetic audio and synthetic calibration only |
| Portable C++ device logic | 462 assertions across nine groups passed; C++ multipart/WAV output passed the actual backend validators | Host execution with fake transport/time adapters |
| Firmware settings generator | 13 tests and 30 subtests passed | No real Wi-Fi/device secrets used |
| ESP32 firmware compilation | Full build/link passed with ESP-IDF v5.4.3, target `esp32`, including the runtime code path | Fake credentials and reserved test endpoint; not flashed |
| Optional HTTPS device listener | Started in an isolated loopback test; CA and IP validation passed; an existing device read its own saved recording; management routes returned 404 | No ESP/Wi-Fi connection was involved; test listener stopped |
| Physical microphone / Wi-Fi / timestamps | Pending user flashing and checking the assembly | Exact carrier revision, USB bridge and flash capacity still need confirmation |
| Acoustic accuracy | Pending real calibration and independent validation | Successful transmission is not a calibrated sound measurement |

The full ESP32 compile produced a 909,520-byte test application binary. The linker reported 131,588 bytes of static DRAM and 98,434 bytes of IRAM used; runtime heap, DMA and TLS load still require the board test. The official toolchain container was `espressif/idf:v5.4.3`, image digest `sha256:9352fff95ecee99e953b8fdd4949cda6baeeae76fe23c330dca1133ef92679f9`. The shareable package contains source and templates, not the fake or provisioned binary.

Initial test runs exposed two test-harness assumptions, which were corrected: a threshold update needed its complete definition, and a concurrent worker test needed to wait for the correct recording. ESP-target compilation also found integer-format/overlap warnings that the host compiler did not flag; these were fixed. Independent firmware review corrected double-counting of a DMA drop and added the accepted server recording ID to diagnostics. Final checks passed.

## Three-location demonstration results

The existing locations and their thresholds were retained: North garden **60**, Workshop **60**, East gate **62 dB SPL (Z)**. All three names and calibration versions remain explicitly marked simulated.

| Scenario | Observed result |
|---|---|
| Normal | 55 / 55 / 57; no new incident |
| Exactly equal | Exactly 60 / 60 / 62 before rounding; no incident |
| Above threshold | 70 / 70 / 72; one new saved incident per device and three visible app notifications |
| Sustained excessive noise | Two further excessive recordings updated that same incident to three breaches |
| Identical repeated upload | HTTP 200 duplicate; same recording ID, bytes and checksum; recording/measurement/incident/event counts unchanged |
| Disconnected/stale | A 32-second sender pause crossed the configured 30-second stale boundary; the incident remained unresolved |
| Recovery | Three contiguous normal recordings were required; intermediate state was recovering, then resolved |
| Daily reports | Each run added ten eligible measurements per device and updated the existing saved summary IDs |

The browser showed the map's location names, latest readings, threshold equality without an incident, opening notifications, recovery state and resolved incident history. All three daily cards were checked against saved results. One API/worker recreation between demonstrations retained the saved reports and original recordings; the application reopened without asking for an access token.

After both runs, today's saved results were:

| Location | Average of recorded time | Eligible measurements | Usable time | Coverage | Saved incident starts |
|---|---:|---:|---:|---:|---:|
| North garden | 66.53 dB SPL (Z) | 130 | 130 s | 0.1505% | 3 |
| Workshop | 75.63 dB SPL (Z) | 124 | 124 s | 0.1435% | 3 |
| East gate | 73.97 dB SPL (Z) | 29 | 28.5 s | 0.0330% | 3 |

These include earlier compatible demo recordings and incidents. They are **simulated, partial, provisional** results for 9 October in Asia/Kolkata, not full-day environmental measurements. East gate's usable time includes the previously saved recording that crosses midnight. Separate silent historical-replay devices can keep a location's missing-data badge visible while its live simulator is reporting.

The database went from 240 recordings / 4 incidents to **300 recordings / 10 incidents**, all ten now resolved. The runner first completed the unfinished previous Workshop demonstration using normal recovery readings; its history was retained. The original four locations, seven devices, eight reports and eight summary identities remain. All **300 original WAV checksums** were checked successfully. No volumes or historical records were deleted. A private pre-change database/audio backup is in `backups/pre-step6-20261009T123353Z` and is excluded from the download.

## Repeat or continue

For the software demonstration, keep Docker running, open `http://localhost:8000/app`, and run this from the project folder:

```sh
python3 scripts/verify-simulator.py
```

See [the simulator instructions](SIMULATOR-VERIFICATION.md) for the expected sequence. Stopping the sender eventually makes devices stale; it does not erase their readings or summaries.

For the board on the other computer, use [firmware/esp32/README.md](firmware/esp32/README.md) and [the wiring/connection guide](docs/HARDWARE-INTEGRATION.md). Prepare a real device registration and local HTTPS endpoint on the backend Mac, privately transfer the device configuration and public CA, enter Wi-Fi settings, verify GPIO26/25/33 and grounded L/R, then build and flash. Only the ESP and backend must have local network connectivity; the flashing computer can be elsewhere. The initial supported profile is one second at 16 kHz, with configurable recording and upload cadence.

Record the real capture timestamps, stored-audio checksum, digital level, incident/recovery behavior, temporary Wi-Fi interruption and daily-summary result before marking hardware integration verified. Begin with uncalibrated digital dBFS. Calibrated SPL needs a measured reference procedure for this actual microphone/assembly; no simulated offset or nominal microphone sensitivity is a substitute.

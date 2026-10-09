# PCM16 device integration verification — 9 October 2026

## Delivered

- Adapted Arduino C++ sender for the user-confirmed ESP-WROOM-32/INMP441 GPIO26/25/33 wiring, using one-second PCM16 at 16 kHz.
- Registered external device IDs such as `UE-001`, mapped to locations through the existing device/location/history tables. Management now displays the mapping table and supports geographic reassignment.
- Compatibility routes `/ping`, `/text`, `/upload`, with authenticated device POSTs and public connectivity checks, durable duplicate detection, native PCM16 processing, bounded authenticated session downloads, and existing alert/daily processing integration.
- Private configuration template, endpoint guide, troubleshooting, and source package. No credentials or provisioned binaries are included.

## Executed software checks

| Check | Observed result |
|---|---|
| Full disposable PostgreSQL/PostGIS backend suite | **454 passed**, 34.91 seconds; two non-failing dependency/cache warnings. |
| Existing application and demo JavaScript suites | **34 passed**; both modified browser modules also passed syntax checking. |
| Named device registration/authentication | Unique, case-sensitive codes; unknown, mismatched-credential and disabled devices rejected; UUID compatibility retained. |
| Geographic reassignment and late arrival | Earlier captures keep their old location/threshold; new captures use the new assignment. |
| Raw PCM16 and existing PCM24 | Exact original PCM16 bytes preserved; correct RMS/full-scale and clipping; original strict PCM24 endpoint still works. |
| Retry behavior | Same device/session/sequence/request returns existing recording; conflicting reuse rejected; database-backed dedup, concurrent retry and recreated-client checks passed. |
| Measurement → incident → recovery → daily report | Integration tests exercise native PCM16 through the real processing functions/database, normal/breach/recovery, persisted notifications and daily eligibility. These are synthetic test inputs, not physical recordings. |
| Input/error coverage | Bounds, headers, timestamps, wrong formats, invalid audio, midnight/late/out-of-order data, gapped/oversized exports and credentials covered. Valid PCM sample bytes coinciding with RIFF/RF64 prefixes are accepted. |
| Local runtime after update | `/ping` and readiness return HTTP 200. Migration is `0005_device_external_id`. API and worker restarted successfully. |
| Existing data after restart | **330 original WAV files** match every recorded checksum and the pre-upgrade manifest. All database row counts unchanged: 4 locations, 7 devices, 330 recordings/measurements, 11 resolved incidents, 8 saved reports and 8 summaries. Existing summary contents and IDs also compared unchanged. |
| Existing app in browser | Reloaded local app without an administrator prompt; device mapping table, coordinates/timezones and device-ID registration field visibly present. No real device registration or geographic assignment was fabricated. |
| Network exposure | App remains loopback-only at `127.0.0.1:8000`; optional LAN hardware listener remains stopped. |

The full suite initially exposed a refactor test-hook mismatch, legacy audio-format alias handling, and lazy router inclusion on the device-only API; these were corrected before the final passing run. A review also removed false rejection of legitimate PCM sample prefixes and limited diagnostic-request waits in the firmware.

A consistent database and audio backup was created before migration under private `backups/before-pcm-20261009T135406Z/`. Backup files and credentials are excluded from shared packages. A backup archive and manifest were checked, but a full restoration drill was not performed during this update.

## Actual firmware compilation

Pinned **Arduino CLI 1.5.1**, **Espressif Arduino-ESP32 3.3.8**, target **esp32:esp32:esp32**. Official tool downloads were checksum-verified. Arduino's Intel-only ctags helper was built from the verified official source for this ARM Mac; no global toolchain settings were changed.

| Build profile | Result | Flash bytes | Global RAM bytes |
|---|---|---:|---:|
| Configured HTTPS with dummy settings | Compiled and linked | 1,090,256 / 1,310,720 | 114,464 / 327,680 |
| Explicit HTTP bench mode with dummy settings | Compiled and linked | 1,087,988 / 1,310,720 | 114,464 / 327,680 |
| Shipped unconfigured template | Compiled and linked | 897,396 / 1,310,720 | 45,544 / 327,680 |

The first real build found that three static audio buffers exceeded the classic ESP32 memory budget. The final implementation uses **two 32,000-byte buffers**, with explicit drop accounting when they are occupied. Configured builds report 213,216 bytes remaining before runtime allocations; this is **not** a measured Wi-Fi/TLS heap guarantee. Final sketch SHA256: `fd5e69a839519426e063a86ca0e19c2cbcc31069252721ea731f944dc0f93f97`.

## Not yet verified physically

No board was attached to this Mac and no firmware was flashed. The user reports successful transmission with their earlier test server; this update does not turn that report into verified UrbanEcho integration. Still pending on the actual board:

- Correct sample alignment, microphone signal, actual capture duration and timestamp accuracy.
- Stable simultaneous I²S capture/Wi-Fi/TLS operation with the bounded memory budget.
- A reachable configured listener, actual device registration and credential, successful uploads, real alert/recovery and daily report.
- Network outage/reconnection and observed drop counters.
- Acoustic calibration of the complete PCM16 gain/filter chain against an appropriate reference.

Use [the connection guide](PCM-DEVICE-INTEGRATION.md) and [sketch instructions](../firmware/arduino-pcm/UrbanEchoPCM/README.md). Source compilation and successful software tests are complete; physical operation and trustworthy sound-pressure measurement remain separate acceptance steps.

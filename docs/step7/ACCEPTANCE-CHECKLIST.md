# Step 8 — final acceptance checklist

Prepared **9 October 2026, Asia/Kolkata**. This is a plan for the next acceptance stage, not a claim that these future checks were performed. Use [Current status](CURRENT-STATUS.md) for the latest evidence and [Step 6 verification](../../STEP6-VERIFICATION.md) for recorded 9 October results.

## Readiness decisions

**Local software demonstration:** the main flow has been verified using labelled simulated recordings. It is suitable for a supervised local product demonstration with Docker and the worker running. Coverage and simulated labels must remain visible. The newest documentation-task checks are listed separately in Current status.

**Calibrated physical monitoring:** not yet ready to claim. Firmware compilation passed, but capture, flashing, Wi-Fi delivery, physical reliability and acoustic accuracy still need board evidence. These are separate from completing software documents, a presentation or a simulator demonstration.

## Software acceptance to record

| Check / gap | Existing basis | Acceptance evidence still to capture or decision to make |
|---|---|---|
| A second person can start and present the product | Documented local Compose startup and three-location simulator | Follow the supplied walkthrough on a clean supported installation; record any missing step. Keep existing data separate from the test installation. |
| Release regression | Step 6 recorded 382 backend and 34 frontend passing checks | Run the relevant suite against the exact final acceptance package in disposable databases after any code changes; archive counts, date, package identity and failures. Do not reinterpret historical numbers as a new run. |
| Visible complete flow | Two recorded simulator runs, browser checks and saved summaries | Sign off a walkthrough showing correct map/location, opening notification, sustained incident, recovery, stale state, repeat-upload deduplication and a saved daily report. Recovery should be observed in state/history; do not require an undocumented recovery toast. |
| Overnight scheduling in ordinary operation | Controlled-clock timezone/DST/retry tests and recorded startup checks | Leave the local stack running across a real location midnight and confirm yesterday is generated once after the configured grace period, while live uploads still work. |
| Recalculation and empty/invalid periods | Existing daily unit/integration tests | At acceptance, demonstrate late-data recalculation, no-data and partial-data results, stable report identities and values matching persisted measurements. |
| Restore rather than only restart | Recorded container recreation preserved database/audio/report data | Restore a matched database/audio backup into an isolated stack; verify checksums, history and reports before selecting an operational backup schedule. |
| Realistic load and disk capacity | Correctness tests; short demos only | Agree expected devices, cadence, retention and outage duration, then measure processing delay, memory/disk growth and alert/report responsiveness under that load. No supported fleet size or uptime target is established yet. |
| Browser/accessibility acceptance | Existing frontend tests and browser inspection | Check keyboard navigation, readable states, narrow screens, disconnected/reconnected app, and target browsers with the actual operators. Passing state tests is not accessibility certification. |
| Privacy and audio lifecycle | Original WAV storage and restricted routes exist | Choose who may access recordings, the minimum necessary capture/retention policy, deletion workflow and backup retention before residential deployment. Automatic retention deletion is not built. |
| Shared access and public operation | Local automatic session plus administrator/device credentials | Decide whether the pilot stays on one trusted local machine. Shared-user accounts, role/tenant restrictions and a production/public deployment require additional design and implementation if needed. |
| Monitoring and support | Health endpoints, durable failures and logs exist | Define operator responsibilities for stopped workers, full disk, expired device certificates, lost credentials and failed processing; measure whether the documented recovery steps work. |
| Alert scope | Saved incidents and in-app opening notifications exist | Confirm buyers accept in-app alerts. Email, SMS, WhatsApp, resident complaint workflows and sound-source identification are not implemented features. |

Failures should retain their date, scenario and affected version. A corrected check needs new evidence; a feature description or screenshot alone does not prove reliable operation.

## Physical milestone — separate pending sign-off

**Real microphone → ESP32 → UrbanEcho → incident alert → daily summary.**

| Pending check | Pass condition |
|---|---|
| Identify and provision the board | Confirm ESP-WROOM-32 carrier/USB bridge and flash capacity; check GPIO26 SCK, GPIO25 WS, GPIO33 SD, grounded L/R and 3.3 V; build private configuration and flash the actual board. |
| Reach the local backend securely | Enable the optional device-only listener intentionally; validate its CA/IP and device credential over reachable Wi-Fi. The management application remains local. |
| Capture and timestamp | Verify sample alignment/channel, one-second 16 kHz WAV, stored checksum and processing; compare first-sample capture time and drift against an independent clock. |
| Observe the physical flow | Confirm correct location, digital level and quality, eligible above-threshold incident, sustained updates, configured recovery, and appropriate local-date report. Start with uncalibrated dBFS. |
| Disconnect, retry and bounded loss | Interrupt Wi-Fi; confirm stale status does not resolve an incident, retries retain identifiers, duplicates do not multiply records, and drops/queue limits are reported. Document power-loss behavior. |
| Runtime stability | Measure free heap, DMA overflow, TLS behavior and actual cadence for an agreed soak period. Compilation does not prove sufficient runtime headroom. |
| Acoustic calibration | Calibrate the actual microphone, enclosure and signal chain against a suitable known reference; store applicable calibration metadata and validity. Never reuse synthetic offsets. |
| Independent accuracy | Compare against an appropriate reference instrument across intended levels/conditions and document uncertainty, placement and limitations before claiming environmental SPL accuracy. No regulatory certification is claimed. |

Keep a brief result sheet for each check: date/time and timezone, software/firmware version, device/location, configuration, expected result, actual observation, evidence link, pass/fail and next action. Do not include Wi-Fi passwords, device tokens, administrator tokens or private keys.

## Suggested order

1. Complete the current product walkthrough and independent local setup/restore acceptance so another operator can demonstrate and recover the software.
2. Run the real-board milestone using the prepared firmware, beginning with safe digital-level checks and explicit loss/timing measurements.
3. Resolve pilot requirements for calibration, audio privacy/retention, reliability and access before agreeing residential monitoring claims or a deployment scope.

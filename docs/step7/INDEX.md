# UrbanEcho: product documentation and demo pack

Completed **9 October 2026** for the working local prototype and its proposed buyer, **residential community management**. All materials focus on the product. Start with the [current status](CURRENT-STATUS.md), then use the walkthrough or presentation.

## Deliverables and remaining actions

| Deliverable | Location | Status | Remaining action |
|---|---|---|---|
| Product overview and project entry point | [README](../../README.md) | Complete | Follow its existing-installation instructions; keep private settings and volumes. |
| Plain-language current status | [CURRENT-STATUS.md](CURRENT-STATUS.md) | Complete | Keep dated evidence distinct from later runs. |
| Technical guide and editable architecture diagram | [TECHNICAL-GUIDE.md](TECHNICAL-GUIDE.md) | Complete | Consult linked hardware setup when physical testing starts. |
| Repeatable three-location demonstration | [DEMO-WALKTHROUGH.md](DEMO-WALKTHROUGH.md) | Complete; live run passed | Start Docker and run the documented preserving simulator. |
| Residential community business case | [BUSINESS-CASE.md](BUSINESS-CASE.md) | Complete proposal | Validate buyer demand, prices, retention, support effort and pilot outcomes. No customers or commercial benefits are claimed as verified. |
| Editable 11-slide presentation | [UrbanEcho-Product-and-Demo.pptx](UrbanEcho-Product-and-Demo.pptx) | Complete; every slide rendered and inspected | Open in PowerPoint or another PPTX editor; native PowerPoint itself was not used for verification. |
| Actual application demo video | [UrbanEcho-Demo.mp4](UrbanEcho-Demo.mp4) | Complete; 2m 32s, visually inspected and fully decoded | Silent recording. Enable embedded subtitles or use the narration script. |
| Narration and shot list | [DEMO-VIDEO-SCRIPT.md](DEMO-VIDEO-SCRIPT.md), [subtitles](UrbanEcho-Demo.srt) | Complete | Optional spoken narration can be recorded later; it is not included in the MP4. |
| Current verification evidence | [VERIFICATION-CHECKS.md](VERIFICATION-CHECKS.md) | Complete | Review exact checks and limitations rather than treating this as a full acceptance rerun. |
| Next acceptance plan | [ACCEPTANCE-CHECKLIST.md](ACCEPTANCE-CHECKLIST.md) | Complete plan; execution remains | Record Step 8 checks and resolve findings. |

Presentation text, tables and architecture shapes are editable. Screenshots are real application captures embedded as images. Rebuild inputs are [presentation-source.mjs](presentation-source.mjs) and [presentation-data.json](presentation-data.json); ordinary content editing can be done directly in the PPTX.

## What was verified this time

Five targeted simulator integration tests passed. A new live run used three registered devices, with only Workshop breaching its threshold, and verified normal/equal/above/sustained levels, identical retries, byte integrity, staleness, recovery and three saved daily reports. Browser checks captured the corresponding map, opening notification, incident detail, history, coverage and no-data state.

The run added 30 labelled recordings and one resolved incident. All **300 earlier originals remain unchanged**, all **330 current WAV files match their saved checksums**, all **11 incidents are resolved**, and the **eight report/eight summary identities remain unchanged**. Existing location/device mappings and thresholds were preserved. The older full-suite result of **382 backend and 34 frontend tests**, and ESP32 compilation, remain dated Step 6 evidence rather than new claims of a full rerun.

Evidence files: [before](verification-before.json), [after](verification-after.json), [comparison and route checks](verification-comparison.json), [simulator run](verification-simulator.jsonl), [targeted tests](targeted-test-result.txt), [video inspection](video-verification.json). Actual screenshots are in [assets](assets/).

## Two separate readiness decisions

**Local software demonstration: ready.** Use the existing Mac and Docker at [localhost:8000/app](http://localhost:8000/app). Inputs and calibration are simulated; daily results describe recorded coverage. No cloud deployment was made. The optional local-network HTTPS device listener is prepared but not enabled.

**Physical monitoring with trustworthy sound levels: pending.** C++ firmware previously compiled for the ESP-WROOM-32 and INMP441 with confirmed GPIO 26/25/33 wiring. The board is on another computer. Device configuration/flashing, real microphone transmission, Wi-Fi recovery, calibration and independent acoustic validation have not yet been verified.

The next physical milestone is **Real microphone → ESP32 → UrbanEcho → incident alert → daily summary.** Follow the [hardware guide](../HARDWARE-INTEGRATION.md); keep transmission success separate from sound-level accuracy.

For Step 8, prioritize an independent repeat of the software walkthrough and representative cold-start/backup-restore and failure checks. Review browser behavior, storage growth and retention before a longer pilot. Accounts/roles, community separation, alert acknowledgement, retention automation, consent and access rules for retained audio, operational support, and target-load performance are not established production capabilities. The [acceptance checklist](ACCEPTANCE-CHECKLIST.md) separates existing behaviors to verify from missing pilot capabilities.

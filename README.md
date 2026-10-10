# UrbanEcho — environmental noise monitoring

## Project overview

UrbanEcho is a working **prototype for residential community management**, available locally and as a hosted demo: it brings monitoring locations, sound readings, excessive-noise incidents and daily coverage reports into one application. The proposed use is to help community staff investigate shared-space noise and review patterns; a residential customer pilot and its benefits have not yet been validated.

**Problem:** community staff often investigate noise complaints without a consistent record of where and when the noise occurred. **Proposed solution:** connect identified microphones to mapped locations, retain their readings, flag excessive noise and provide a daily review. **Built so far:** a runnable backend, browser application, database, audio-processing worker, repeatable three-location simulator and device firmware/configuration templates.

**Two ESP32 devices have supplied real PCM16 recordings through the local HTTPS listener.** Original audio is stored as well as numerical readings. YAMNet now provides saved, fallible sound-source estimates; calibrated acoustic accuracy and regulatory compliance have not been established. The local installation retains those recordings. The separate [hosted application](https://urban-echo-uz15.onrender.com/app) uses a fresh cloud dataset with clearly labelled simulated verification data.

## Key features

| Capability | What is implemented |
|---|---|
| Device and geographic mapping | Register each Arduino board using its automatic `ESP-<MAC>` device ID against a location, coordinates and timezone; preserve earlier assignments when a device moves. |
| Audio capture and storage | Authenticated PCM16 uploads from the adapted Arduino sender, existing PCM24 WAV uploads, durable originals and duplicate-safe retries. |
| Sound-level processing | Saved numerical levels with measurement definitions, quality and calibration status; uncalibrated digital levels remain distinct from sound-pressure levels. |
| Incident audio and classification | One playable recording covers each incident and its available context. Server-side YAMNet saves Traffic, Horn, Siren, Construction, Music, Animal, Voice or Other estimates for the incident. |
| Immediate noise alerts | Per-location thresholds, sustained incident updates, configured recovery, saved event history and live homepage updates. |
| Web application | Map, latest readings, location history/charts, stale-device status, incident details and management controls. |
| Daily reports | Manual recalculation and scheduled previous-local-day processing, energy/duration averages, incident counts, coverage and clear partial/no-data states. |
| Repeatable demonstration | Three labelled simulated locations exercise normal, equal, excessive, sustained, recovery, stale and retry scenarios through the actual software pipeline. |

Earlier recorded verification, **9 October 2026**, after the priority review fixes: **461 backend tests and 49 application/demo JavaScript tests passed**. New coverage includes calibrated PCM16 recording → alert → recovery → daily summary, policy-stream changes, bounded browser state, scoped refreshes, and readable device codes. A local API check returned 200 measurements with **6 database SELECTs**, down from 603 before batching. The local app was rebuilt and restarted; checksums of all **330 original recordings** and hashes of saved locations, devices, measurements, incidents, reports and summaries were unchanged. Browser checks confirmed the chart, incident details and saved daily result load. These are software checks, not physical-board or acoustic-calibration verification. The earlier [PCM integration verification report](docs/PCM-INTEGRATION-VERIFICATION.md) records the preceding 454/34-test run and Arduino compilation; its dated results remain available.

## Business case

The initial buyer is a **residential community management committee**, with facilities managers and security supervisors as intended daily users. The [business case](docs/step7/BUSINESS-CASE.md) covers all four planning areas:

| Area | Proposal and supporting material |
|---|---|
| Go-to-market | Interview community managers, validate one three-location pilot, then use manager referrals and facilities-provider introductions. [First 100 users](docs/step7/BUSINESS-CASE.md#4-reaching-the-first-100-users). |
| Unit economics | Device/installation allowances, storage growth, calibration and ongoing support costs, with assumptions separated from quoted references. [Cost model](docs/step7/BUSINESS-CASE.md#6-device-storage-hosting-and-maintenance-economics). |
| Business model canvas | Customer segments, value, channels, relationships, revenue, activities, resources, partners and costs. [Revenue and canvas](docs/step7/BUSINESS-CASE.md#5-proposed-revenue-and-business-model). |
| Business plan | An eight-week proposed pilot and a conditional twelve-month roadmap with evidence gates. [Pilot](docs/step7/BUSINESS-CASE.md#3-pilot-design-and-measurable-value) · [Twelve-month plan](docs/step7/BUSINESS-CASE.md#7-twelve-month-plan-october-2026september-2027). |

Prices, adoption targets, savings and proposed revenue remain planning assumptions; no paying customers or field performance are claimed. The business case's PCM24 storage example is explicitly a profile calculation; the newer PCM16 sender uses a different bytes-per-sample profile.

## Deployment and first run

**Hosted demo: [UrbanEcho](https://urban-echo-uz15.onrender.com/app)** — live on free Render with a separate Supabase database and private audio storage, verified on 10 October 2026. The published SIMULATED location opens directly in read-only mode, without a sign-in screen. Use **Admin sign in** only to manage settings or generate reports; credentials are not included in the public repository or URL. The free service sleeps when idle, so the first visit can take about a minute to wake it. See [Render deployment and verification](RENDER.md) for limits and setup.

The initial hosted smoke check verified authenticated uploads, three processed recordings, byte-exact original downloads, incident recovery state and actual model classification with playable incident audio. Three subsequent quiet recordings completed valid recovery. The current hosted fixture contains **six SIMULATED recordings and one resolved incident**, with final sound analysis and six seconds of playable audio. Its location/device are labelled **SIMULATED**; local recordings and device credentials were not copied to the cloud.

**Local Docker remains supported.** Cloning the repository does not include either installation's database, audio or private settings. A new checkout starts with empty data and can create its own labelled demonstration.

For macOS or Linux, install Docker with Compose v2 and Python 3, then run from the repository root. Windows teammates should use a Linux environment such as WSL for the simulator scripts:

```sh
git clone https://github.com/vish-258/hackathon-urbanecho-urbanecho.git
cd hackathon-urbanecho-urbanecho
python3 scripts/setup-env.py
# Apple Silicon only: export COMPOSE_FILE=compose.yaml:compose.amd64.yaml
docker compose up --build
```

Open [UrbanEcho at localhost:8000/app](http://localhost:8000/app) once the services are ready. Local browser access uses the existing automatic local session. To demonstrate the software, use a second terminal in the same folder and run `python3 scripts/verify-simulator.py`, then follow [the application walkthrough](docs/step7/DEMO-WALKTHROUGH.md). Inputs and calibration in that demonstration are explicitly simulated.

For an existing installation, follow [Start or update the stack](#start-or-update-the-stack), retaining its private settings and volumes. The [hardware connection guide](docs/PCM-DEVICE-INTEGRATION.md) explains the optional separate LAN device listener. [Automated verification](#automated-verification) documents reproducible test commands.

## Team and repository

- **Team name:** Urbanecho.
- **Product/project name:** UrbanEcho.
- **Shared repository:** [vish-258/hackathon-urbanecho-urbanecho](https://github.com/vish-258/hackathon-urbanecho-urbanecho).
- **Submission branch:** `main`.
- **Visibility:** public, so organizers and judges can read the source without an invitation.

| Team account | Confirmed project relationship |
|---|---|
| [@vish-258](https://github.com/vish-258) | Repository owner |
| [@srinivasarajui](https://github.com/srinivasarajui) | Teammate; write-access invitation sent on 9 October 2026 |
| [@Rohith-1-2](https://github.com/Rohith-1-2) | Teammate; write-access invitation sent on 9 October 2026 |

Individual contribution descriptions have not yet been supplied. Add each person's actual contribution before submission; no engineering role or contribution is inferred from repository access. Teammates must accept their GitHub invitations before their write access becomes active.

See [CONTRIBUTING.md](CONTRIBUTING.md) for the shared-repository workflow, local checks and submission checklist. Use isolated demonstration data; private credentials, captured audio and database backups do not belong in the shared repository.

## Product documentation and demonstration materials

Start with the [Step 7 deliverables and readiness index](docs/step7/INDEX.md). It separates completed artifacts, historical verification evidence, checks performed during documentation, and remaining work.

- [Current product status](docs/step7/CURRENT-STATUS.md)
- [Technical and operating guide, including editable architecture diagram](docs/step7/TECHNICAL-GUIDE.md)
- [Repeatable product demonstration walkthrough](docs/step7/DEMO-WALKTHROUGH.md)
- [Residential community management business case](docs/step7/BUSINESS-CASE.md)
- [Editable product and demo presentation](docs/step7/UrbanEcho-Product-and-Demo.pptx)
- [Demo-video narration and shot list](docs/step7/DEMO-VIDEO-SCRIPT.md)
- [Recorded local demonstration: silent MP4 with subtitles](docs/step7/UrbanEcho-Demo.mp4)
- [Checks performed during Step 7](docs/step7/VERIFICATION-CHECKS.md)
- [Step 8 final acceptance checklist](docs/step7/ACCEPTANCE-CHECKLIST.md)

The detailed implementation and operating reference follows. The [Step 6 report](STEP6-VERIFICATION.md) records the earlier 9 October 2026 results of 382 backend tests, 34 frontend tests, two simulator runs, 300 original-file checksum checks and a successful ESP32 compilation. Those are dated results, not a claim that documentation edits reran every check. Use [PCM integration verification](docs/PCM-INTEGRATION-VERIFICATION.md) for the latest backend/device-code update and preservation checks.

## Monitoring application

Open the [hosted public demo](https://urban-echo-uz15.onrender.com/app) without signing in, or **http://localhost:8000/app** for the local monitoring workspace: geographic map, location charts, incident history, live alerts, daily reports, and management. The local app opens automatically on this Mac using its loopback-only session. See [APPLICATION.md](APPLICATION.md) for local setup, verified behavior, and limitations, and [the product walkthrough](docs/step7/DEMO-WALKTHROUGH.md) for the preserving three-location demonstration. Daily reports calculate and save results from the existing measurements.

### Listen to saved recordings

In **Overview → a location's Details → Saved 10-second recordings**, choose a device if needed, then **Listen** beside a ready recording. Each ready WAV contains exactly **10 seconds of continuous 16 kHz mono PCM16 audio** from that device. The newest page refreshes every ten seconds without interrupting playback; older pages retain a stable browsing snapshot. Collecting and incomplete recordings show how much audio arrived and cannot be presented as complete ten-second files.

The ESP32 continues to transmit short audio pieces because its RAM cannot hold a ten-second uncompressed recording alongside Wi-Fi and TLS. The server stores a durable recording-group manifest and assembles the ten original pieces into one real WAV when requested. This preserves fast one-second sound-level evaluation and avoids a second copy of all the audio on disk. The original pieces are retained for measurement history, daily reports and incident evidence; groups are never processed again as extra measurements. This changes the recordings offered in the app, not the network upload frequency or underlying evidence retention.

Grouping requires the same device, boot session, historical location assignment, audio format and calibration provenance, with all ten sequential pieces and continuous capture times. Gaps, invalid originals and interrupted sessions are labelled explicitly; no silence is invented. Late uploads can complete the same saved group without duplicates. The independent recording-group worker runs alongside the sound-level worker and does not depend on YAMNet. Existing continuous one-second PCM16 history is discovered in bounded batches. Other original formats remain accessible through their individual readings and original-audio API.

Existing installations need additive migration `0009_recording_groups` and the updated API/worker images. The normal Compose migration service applies it; initial historical grouping runs in the background while new uploads continue.

Ten-second release verification, **10 October 2026**: **680 backend/model tests** and **149 interface tests** passed, with a further **49 focused backend checks** covering groups, original upload/export compatibility and migrations. Both physical devices produced genuine **320,044-byte, ten-second WAV files** whose PCM samples matched their ten originals exactly. Browser players loaded ten seconds for both rooms; Room2 playback reached the end without an error, and refreshing the list preserved Room1's loaded player. Saved group IDs/revisions/WAV hashes survived API and worker restarts. All **14,893 original recordings** in the pre-update snapshot retained their metadata and file checksums; no duplicate group identities or initial measurements were found. The LAN upload listener was kept running throughout this server update. Incomplete recordings reject playback instead of inventing missing sound.

In **Incident history → View incident**, the incident player provides the whole available incident plus its configured context. **Related readings → Listen to whole incident** opens that same player; the table shows the individual sound-level measurements without separate one-second playback buttons. Incident playback length follows the event and its available context, so it can be longer or shorter than the regular ten-second files. Original pieces remain accessible through location reading history and the authenticated original-audio API. Playback is requested through the existing authenticated connection; credentials never appear in file URLs. Leaving the page stops playback and releases its browser copy. Original WAV files are unchanged.

Playback includes saved recordings that are still processing or cannot produce an eligible sound reading. Hearing audio does not establish calibrated sound-pressure accuracy: uncalibrated and simulated recordings remain labelled accordingly, and listening never changes thresholds, measurements, or incident status. `GET /recordings` lists durable groups for an administrator or the explicitly published demo location; `GET /recordings/{id}/file?revision=...` serves only a complete, integrity-checked ten-second WAV. The original `GET /audio` listing still filters by capture-time location/device and optional timezone-aware `since`/`until` timestamps (inclusive), with bounded pagination. The browser also freezes `received_until` so late uploads cannot shift an open page; Refresh resets both cutoffs. Playback uses `GET /audio/{id}/file`; the local session works only on the loopback application, while the LAN device listener retains device-token authorization.

### Sound classification with YAMNet

**Incident history → View incident** shows the incident's saved sound estimate and a single playable incident recording. Classification is triggered by saved incidents, independently of sound-level detection; ordinary background recordings are no longer classified individually. Historical per-recording estimates remain available and are labelled as recording estimates. The incident analysis joins contiguous audio before running YAMNet, so a one-second upload boundary does not cut the model's context.

Playback includes **five seconds before the incident and five seconds after its end**, where available. Classification uses the actual incident interval only. An active incident has a growing provisional snapshot, refreshed at most once every ten seconds; after recovery, the worker waits for the post-incident audio and a ten-second arrival allowance. Late arrivals change the source fingerprint and can update the saved result without adding duplicates. Use **Generate / recalculate** on the incident for an explicit refresh. A loaded player remains on its original snapshot until you load the newer revision.

Original clips are never overwritten. The saved manifest references the device's original audio for the incident's historical assignment; playback streams one assembled WAV. Missing, corrupt or incompatible audio is excluded and **reported as partial coverage**, never replaced with silence. Playback omits missing intervals, so its duration can be shorter than the clock-time window; the UI lists the gaps. Inference never joins windows across missing intervals. Overlaps are trimmed deterministically. Processing is bounded to 10,000 source clips, 512 MiB of source-file verification and two hours of playable audio per snapshot; a limit is explicitly labelled as truncated, not complete audio.

The categories are **Traffic, Horn, Siren, Construction, Music, Animal, Voice, and Other**. These are UrbanEcho's documented grouping of YAMNet's 521 AudioSet labels, not eight newly trained classes. Multiple sounds can be present. **Other / uncertain** is a valid result when no requested category has sufficient support or another sound dominates. Scores are model outputs, **not a percentage guarantee of correctness**. A one-second clip can lack enough context, and distant, overlapping or distorted audio can be misclassified. Voice means a possible vocal sound, not a transcript or identification of a speaker. Classification does not establish sound-pressure calibration, change noise thresholds, or create notifications.

All inference happens locally in the **classifier** container. It reads original WAVs through a read-only volume; it does not send recordings to Google or any other service. The official pretrained model is downloaded once during the image build and verified against a pinned SHA-256 checksum. The classifier alone installs its pinned LiteRT, NumPy and SciPy dependencies; API ingestion and the sound-level worker remain separate processes. The incident's source fingerprint, coverage, original YAMNet labels, category scores, model/mapping versions and calculation time are stored in PostgreSQL. Leases and revision checks prevent retries or outdated workers from overwriting a newer analysis. Pending, processing, unavailable and failed states are kept distinct from a completed Other result.

For an existing local installation, retain the private `.env` and Docker volumes, back up the database, then run:

```sh
docker compose build api classifier
docker compose run --rm migrate
docker compose up -d api worker classifier
```

Use the same Compose override files as your current installation (including `compose.amd64.yaml` on Apple Silicon). If the hardware listener is enabled, recreate `device-api` with its existing private listener environment and `compose.hardware.yaml` too. The additive incident-analysis migration follows `0007_recording_classification`; migrations do not change firmware on physical boards.

`CLASSIFICATION_ENABLED=false` disables classification while leaving recording and noise detection operational. Other bounded queue/retry settings are listed in `.env.example`. The normal Docker Compose setup starts the classifier automatically with at most one CPU and 1 GB RAM; its original-audio volume is read-only. Host-only installations need `requirements-classification.txt`, `python -m scripts.fetch_yamnet --output PATH`, `CLASSIFICATION_MODEL_PATH=PATH`, and a separate `python -m app.classification_worker` process. Railway's `deploy/railway/Dockerfile.application` includes the model and all runtime dependencies; its launcher supervises the API, measurement worker and classifier together on one persistent audio volume. See [RAILWAY.md](RAILWAY.md) for deployment status and checks.

Administrators can read `GET /incidents/{id}/analysis`, request recalculation with `POST /incidents/{id}/analysis`, and listen through the authenticated `GET /incidents/{id}/audio/file?revision=...`. A stale revision returns 409 rather than silently substituting different audio. `GET /classification/status` reports worker readiness. `CLASSIFICATION_SCOPE=incidents` is the default; `recordings` retains the older per-clip mode for compatibility. In incident mode, per-recording POST classification requests are rejected and unclassified ordinary clips report `not_requested`, not a permanently pending estimate. Context, refresh and settle controls are in `.env.example`.

Run the complete local classification test suite in a disposable database with `CLASSIFICATION_TESTS=1 ./scripts/test-compose.sh`; no sample audio is inserted into your real devices or locations. Ordinary `./scripts/test-compose.sh` keeps the lightweight API image and skips model-runtime-only tests. [Google's YAMNet tutorial](https://www.tensorflow.org/hub/tutorials/yamnet) explains the model, and the pinned label/model references and category mapping are in `app/assets/yamnet/` and `app/classification_contract.py`.

Earlier per-recording release verification, **10 October 2026**: **605 backend/model tests** passed in disposable PostGIS with the actual pinned LiteRT model, and **137 frontend tests** passed. Local deployment applied both additive migrations and checked all **10,619 originals captured in the pre-update preservation snapshot**, including their metadata and file checksums. Stored classifications from both physical devices survived API recreation and a classifier restart with identical results. Browser checks matched a displayed Voice estimate and its original Speech score to the saved API result, while live sound readings and new incident/recovery processing continued. No synthetic recordings were added to the application database. That release processed recording history in the background; incident mode now processes saved incidents instead. None of these checks establishes field accuracy for all eight sound categories.

### Try incident recording and classification

1. Keep the local server running and the ESP32 powered on its configured Wi-Fi. For complete time coverage, flash the continuous-capture firmware (`UE_CAPTURE_INTERVAL_MS=1000`); changing server settings alone does not update the board.
2. Open **Incident history → View incident → Incident audio and sound category → Listen to incident audio**. Check the playable duration and coverage before pressing Play. The category describes the incident interval, while playback also includes the available five-second context on each side.
3. Choose **Recalculate incident audio** to process newly arrived audio again. The existing player keeps its selected version. If a newer version changes the audio, explicitly load it; recalculation reuses the incident's saved analysis instead of creating duplicates.

The Room2 board (`ESP-20500D129238`) was flashed on **10 October 2026** with continuous capture and persistent verified HTTPS uploads, preserving its device identity and private settings. Its earlier three-minute serial check acknowledged 180 uploads with no reported dropped captures/uploads; 200-record checks before and after that deployment had exact one-second capture spacing. Replacing the LAN listener during that earlier deployment caused a seven-second recording gap; the device recovered automatically.

During the subsequent ten-second-file update, both boards were attached. Room1 (`ESP-20500D114084`) was backed up and flashed with the same compiled firmware already running on Room2, preserving its own saved credentials, MAC identity and gain of 1.0. In a simultaneous two-minute check, Room2 acknowledged 119 uploads with no new capture/upload drops; Room1 acknowledged 103 uploads, retried one temporary HTTP failure successfully, and reported eight dropped captures with no dropped uploads. Both later supplied complete ten-second recordings, but intermittent gaps remain visible, particularly on Room1. Continuous mode deliberately inserts no sampling pauses; these bounded-memory boards still cannot guarantee lossless capture during network delays or outages. Neither device is acoustically calibrated, and missing historical sound cannot be recovered.

Earlier incident release verification: the complete backend/model run passed **654 tests**; after the final format-anchor and unavailable-model fixes, **26 targeted backend, migration and dispatch checks** passed. The complete interface suite passed **147 tests**. Migration `0008_incident_analysis` was applied locally, and all **11,915 original recordings** in the preservation snapshot retained their original metadata and SHA-256 checksums. A real six-second Room2 incident produced a single **16-second WAV** (five seconds of context on each side), 100% coverage and a saved Voice estimate; assembled PCM samples matched the original sixteen clips exactly. Browser playback loaded all 16 seconds. The saved analysis, generation time, audio revision and complete WAV hash survived API container recreation unchanged; fresh physical readings continued to arrive. Repeated generation retained one analysis per incident/model/mapping, and ordinary new recordings were no longer queued for classification. These checks verify the software flow, not field accuracy of sound categories or acoustic calibration.

### Understanding thresholds and live incidents

**dBFS is the microphone's digital signal scale:** values closer to zero are louder, so **−20 dBFS is above −30 dBFS**, while −40 dBFS is below it. At a −30 threshold, only an eligible reading strictly greater than −30 opens or updates an incident. Equality does not trigger a breach. Raising the limit toward zero makes triggering harder; moving it farther below zero makes triggering easier. This scale depends on microphone gain and is not interchangeable with calibrated environmental dB SPL (Z).

The threshold form explains the selected scale and previews the comparison and recovery rule as you edit. The application has no notification popups. Watch Overview for current readings and unresolved incidents, and open Incident history for saved events and their original limits. Continued breaches update the same incident. Recovery requires the configured number of consecutive usable, compatible readings at or below the saved limit. Invalid audio and gaps cannot count as recovery. No-data status alone does not resolve an incident. Changing the current threshold never rewrites an earlier incident's saved rule.

### Continuous recording and optional sampling

The Arduino firmware now defaults to **continuous one-second audio uploads** (`UE_CAPTURE_INTERVAL_MS = 1000`, `UE_UPLOAD_INTERVAL_MS = 0`) for complete incident audio and ten-second saved recordings. **Do not set this to `10000` to obtain ten-second files**: that deliberately discards nine seconds of sound. Short uploads are transport pieces; incident playback and classification join the available original samples across those boundaries. A persistent verified HTTPS connection reduces upload overhead. This is not an outage-proof recorder: the two RAM buffers cannot retain a long Wi-Fi interruption, and reported gaps remain missing.

The earlier ten-second sampling option remains available by explicitly choosing `10000`, but it provides about **10% time coverage**: sounds within the nine-second gaps can be missed. That gap is drained rather than buffered or uploaded, so this option cannot supply complete incident recordings.

**Recording duration** and **capture spacing** are different. Leave the location's **Recording duration (seconds)** at **1** for these one-second clips. Setting that field to 10 would make one-second measurements incompatible; it does not remotely change the board's capture schedule. The spacing is configured in the board's private firmware settings and requires a USB firmware update. Changing the source or the web form alone cannot update a running board. Existing private headers override the public example, so check their `UE_CAPTURE_INTERVAL_MS` before compiling.

Updated boards send `X-Capture-Interval-Ms` with every recording; the server stores it on the immutable original and checks it during retries. For multipart `/audio` clients the equivalent optional metadata field is `capture_interval_ms`. Cadence must be a positive whole number of milliseconds, no shorter than the actual clip, and at most one hour. Saved recordings show the declared spacing. Original clients without this field keep their previous behavior and historical fingerprints.

Recovery can use consecutive scheduled samples when the declared cadence, session, consecutive sequence, rule and calibration match and the capture times agree with the schedule within 1 ms. A skipped sequence, an unexpected time gap, a cadence change or invalid intervening audio resets the recovery streak. The nine-second **planned** gap is not evidence of quiet and adds no measured duration or daily coverage. Daily summaries continue to use only the actual saved one-second windows. Intervals longer than the server's 30-second freshness window require reviewing freshness settings separately.

**Rollout order:** back up the database; build and run the existing migration service; restart API, workers and any hardware listener with that same build; then compile and upload the configured Arduino sketch to each board without erasing NVS. Preserve each board's credentials, gain and wiring. Verify new saved recordings have `duration_seconds: 1`, `capture_interval_ms: 1000`, consecutive sequence numbers and capture times one second apart. Check drop counters during a sustained run; compiling alone does not update a physical board. Other boards remain unchanged until individually flashed and checked.

### Automatic location setup

In **Management → Locations → Edit**, select **Use my current location** while beside the installed devices, allow browser location access, then review the coordinates and estimated accuracy and choose **Save location**. The same control is available when creating a location. It uses the browser’s location service on localhost or HTTPS; it does not derive geographic coordinates from a MAC address. The current ESP32/INMP441 boards have no GPS position in their upload contract.

The request is bounded, retries with a less demanding accuracy request if the first attempt cannot obtain a fix, and reports denied access, unavailable service or timeout. Invalid or stale fixes never fill the fields. A failed request leaves existing coordinates untouched and never substitutes `0,0` or an IP-based city estimate. Leaving the form cancels the request. A successful lookup fills the form; only Save applies it. Accuracy is an estimate, and broad estimates are explicitly flagged for review.

Saving uses the existing authenticated location update and applies to every device assigned to that location. Previous recordings retain their original location snapshots; future captures use the new assignment. Coordinate changes can close an older active incident as an assignment change, not a noise recovery. This is installation-time positioning, not continuous tracking: moving a device requires updating its location. Automatic positioning still requires a functioning OS/browser location provider and permission. A browser lookup cannot guarantee a fix when that provider is unavailable.

**Management → Devices** updates each device’s reporting status and last usable reading timestamp from the same live feed as Overview, without clearing unsaved edits. Connection and reading readiness are shown separately: **Connected** means contact within the server’s freshness window (30 seconds by default), while **Calibration required** explains why an uncalibrated device cannot provide the location’s configured SPL reading. Connection freshness uses the server-adjusted clock; a future contact time is flagged rather than treated as connected. A separate **Last device contact** timestamp refreshes every two seconds while the Devices tab is open, including uploads that cannot produce an eligible sound reading. Contact checks are cancelled when leaving the tab and failures retain the last confirmed time with a retry notice. A recent unusable recording is flagged as needing attention; no recent usable data is not proof that Wi-Fi is disconnected. Older assignment history cannot make a moved device appear current. The audio sender records one-second windows; processing and live delivery add a small delay, so this is live monitoring rather than instantaneous audio streaming.

Location charts automatically refresh the latest 200 readings when that location receives new measurements. **Load older readings** keeps a stable browsing snapshot; **Refresh latest readings** returns to automatic updates. Device and threshold details reload when their configuration changes. Incident search accepts the board’s registered MAC-based device ID; incident details identify the device code and internal UUID separately. Older devices without a code retain their UUID identity. Completed incidents stay in saved history while the browser retains only unresolved incidents and a bounded recent-event cache.

FastAPI, a Python audio worker, PostgreSQL 17/PostGIS, and durable original recordings. The processing flow is:

**Original WAV saved → sound level calculated → applicable threshold version selected → measurement evaluated → incident updated → durable event committed → homepage refreshed.**

Recordings below the threshold, unusable recordings accepted as valid WAV, historical arrivals, and reprocessed results remain available. Live incident history is never rebuilt from late arrivals.

The application runs locally at **http://localhost:8000/app**. The ngrok tunnel and its local setup were removed on 9 October 2026. Docker exposes the application only on this Mac at `127.0.0.1:8000`; keep Docker running.

The [independent hosted app](https://urban-echo-uz15.onrender.com/app) is live; see [RENDER.md](RENDER.md) for setup and verification. Render runs the application and its workers; Supabase stores the database and immutable originals in a private bucket. Uploads are accepted only after the original is stored remotely, and playback restores verified originals when the server's temporary cache is empty. The free service sleeps when idle, pausing background processing until it wakes.

The earlier Railway deployment remains blocked by a paid-plan account requirement. See [RAILWAY.md](RAILWAY.md) for that attempt's status.

## Simulator and physical device integration

**Arduino PCM sender:** the supplied ESP32 program is now adapted in [firmware/arduino-pcm/UrbanEchoPCM](firmware/arduino-pcm/UrbanEchoPCM/README.md). It derives its device ID automatically from the factory MAC (`ESP-<12 uppercase hex digits>`), maps that identity to a geographic location in **Management → Devices**, and uploads PCM16 through authenticated `/upload`. Leave `UE_DEVICE_TOKEN` empty and provision each board over USB with `python3 scripts/provision-board.py --location "Physical location name"`; credentials stay separate from the public MAC identity. Physical provisioning rejects simulated locations and confirms a saved audio record, not merely a diagnostic connection. Start with the [mapping, endpoint and setup guide](docs/PCM-DEVICE-INTEGRATION.md) and [verification results](docs/PCM-INTEGRATION-VERIFICATION.md). Existing UUID/PCM24 devices remain supported.

Run `python3 scripts/verify-simulator.py` from this folder while the local stack is running. It privately reads the existing credentials and exercises three registered simulated devices through real WAV uploads, processing, alerts, recovery, stale detection, duplicate retries and saved daily reports. No background sender remains after it finishes. See [SIMULATOR-VERIFICATION.md](SIMULATOR-VERIFICATION.md) for the visible sequence and repeat instructions, and [STEP6-VERIFICATION.md](STEP6-VERIFICATION.md) for performed checks.

The C++ firmware project in [firmware/esp32](firmware/esp32/README.md) targets the user-confirmed **ESP-WROOM-32 + INMP441**, with SCK GPIO26, WS GPIO25, SD GPIO33, L/R grounded, and 3.3 V power. It captures audio, establishes Wi-Fi/time, packs PCM24 WAV, and uploads with the registered device identity, stable retry identifiers and bounded buffering. Build and flash it on the computer attached to the board. This is a complete ESP-IDF project; copy the adjacent `firmware/common` directory too.

Follow [the hardware connection guide](docs/HARDWARE-INTEGRATION.md) for wiring, registration, private settings, local HTTPS, troubleshooting and the physical verification checklist. The ESP must reach the backend Mac over the local network. The optional `compose.hardware.yaml` upload listener is disabled by default; the normal application remains on localhost. No physical location, real device credential or acoustic calibration is invented by this update. On 10 October 2026, the connected ESP32/INMP441 was flashed with MAC-derived identity, provisioned over USB, and verified through saved PCM16 recordings, processing, live incidents/recovery, and a recorded-data daily report. Geographic position remains pending and acoustic accuracy remains uncalibrated; dBFS must not be presented as calibrated environmental dB SPL.

## Daily processing: use and calculation rules

Open **Daily reports**, select a location and its local calendar date, then choose **Generate summary** or **Recalculate summary**. Saved results load automatically when returning to the page; generation is explicit. The local app uses its existing automatic local session. Command-line/API clients continue to use the existing administrator authentication.

Each compatible result shows its energy average, minimum/maximum recording levels, eligible recording count, saved incident starts, usable recorded time, coverage, simulation label, and generation time. Queued/processing, partial coverage, no usable data, and failure are separate states. A report for today is provisional. Missing time never contributes a zero sound level.

The authenticated API is:

- `GET /daily-summaries?location_id=UUID&reporting_date=YYYY-MM-DD` reads saved results; omitting the date selects yesterday in the location's timezone.
- `POST /daily-summaries/generate` with `{"location_id":"UUID","reporting_date":"2026-10-08"}` queues generation and returns **202** with the report and previous saved results, if any.
- `GET /daily-summaries/jobs/UUID` reads processing status and the saved results. Future dates and nonexistent local calendar dates are rejected.

**Source and eligibility.** Reports read persisted `AudioChunk` capture times, actual durations and historical assignments; the newest `Measurement` processing revision supplies the value, definition, calibration snapshot and quality. An additive `result_order` sequence makes reprocessing order deterministic; the old receipt timestamp represents upload time, not processing time. The initial migration orders older revisions using their evaluation time, reprocessing flag, then ID as a deterministic tie-breaker. Current data had no reprocessed revisions at upgrade. A new invalid result replaces an older valid result for reporting; it never silently falls back. Failed/pending audio, missing results, silence/clipping/other bad quality, incompatible intervals/method/weighting/channel, invalid attribution and inapplicable calibration are excluded with diagnostic counts. Future or not-yet-finished recordings are excluded. Current device disablement or reassignment does not erase eligible historical data. A recording that straddles a historical assignment change is excluded because its whole-chunk level cannot be safely attributed to two places.

**Calendar days.** A day is `[local midnight, next local midnight)` converted to UTC with the saved IANA timezone; daylight-saving days can be 23 or 25 hours. Each report freezes its timezone and UTC bounds on its first request so later location edits cannot silently move its historical day. A whole-chunk equivalent level is apportioned by the duration falling on each side of midnight. This assumes uniform sound energy within that chunk; it is an approximation, not a fresh sample-by-sample analysis of the WAV. Minimum/maximum are extrema of eligible recording-level values, not waveform peaks. Today's coverage uses the entire eventual day as its denominator, including time that has not happened yet; its provisional label is therefore essential.

**Energy and duration.** For non-overlapping eligible intervals, the calculation is:

```text
average_dB = 10 × log10( Σ(duration_seconds × 10^(level_dB / 10)) / Σ(duration_seconds) )
```

The implementation scales relative to the largest level for numerical stability. Two equally long recordings at 50 and 60 dB produce about **57.40 dB**, not 55 dB. Equivalent sound level is an energy measure; see the [FHWA sound-level descriptor reference](https://www.fhwa.dot.gov/Environment/noise/resources/sound_descr.cfm). This application preserves **Z-weighted SPL** or **digital dBFS**; it does not calculate dBA, DNL, noise dose, or a regulatory compliance result.

**Overlap and multiple devices.** The timeline is split at recording boundaries. Within each segment, overlapping chunks from the same device are energy-averaged first; available devices are then given equal weight, and that segment's energy is weighted by elapsed time. This is a declared equal-sensor location summary, not a physical addition of sound sources or an assertion that every sensor represents the whole location. Coverage and usable duration use the union of intervals, so overlap never increases coverage above 100%. `recorded_duration_seconds` is the sum of eligible clipped recording durations; `overlap_seconds` is the excess of that sum over usable union time (which can exceed the elapsed overlap with three or more streams). Compatible SPL results can combine calibrated devices; **dBFS remains device-specific** because different microphone/gain chains are not physically comparable. Different methods, weightings, channel policies, processing versions, and simulation classes get separate cards and database keys. Sample intervals of different lengths may combine when their definitions match; duration provides the weight.

**Simulation and incidents.** Existing immutable location-name markers (`SIMULATED`/`SYNTHETIC`) and synthetic calibration-version markers classify demo recordings, including after reprocessing. These results never mix with unmarked recorded data. `recorded` means no saved simulation marker; it does not independently verify physical hardware. Incident counts use saved incidents whose `started_at` falls in the day, classified from their original evidence and saved location. They are scoped by source kind and measurement type (and device for dBFS), and repeat across processor-version cards: **do not sum cards' incident counts**. Historical replay may contain high sound levels but creates no new incidents; a zero historical incident count is not proof that there were no excessive sounds.

**Missing and late data.** Usable union duration divided by actual day duration is the coverage percentage. Anything less than full coverage is partial; the average describes only those recorded periods, with no extrapolation and no inserted silence. No usable data produces null average/minimum/maximum, zero usable duration and a clear `no_data` status. Late arrivals and reprocessed results appear when the report is recalculated; past finalized reports are not silently refreshed after every upload. The displayed source-check and generation times make that boundary explicit.

**Persistence and scheduling.** Migration `0004_daily_summaries` adds durable report jobs and results while retaining original audio, readings, assignments and alerts. A database uniqueness constraint prevents duplicate location/date/definition summaries. Recalculation atomically updates existing keys and removes obsolete definition groups; a failed calculation leaves the previous successful results visible. A repeatable-read snapshot keeps each calculation consistent. The existing worker runs a separate daily-processing thread, so the audio worker keeps checking live recordings. Every **60 seconds**, it checks locations and queues yesterday once after **00:05 local time**. It also finalizes an existing provisional report after that day ends. At startup it catches up the previous completed local day; older missed days require manual generation. The Mac, Docker and worker must be running. Retry leases default to 600 seconds with up to five attempts; interrupted work resumes after lease expiry. No calculation emits or rewrites live incident notifications.

Settings are `DAILY_SCHEDULE_ENABLED=true`, `DAILY_SCHEDULE_MINUTE=5` (minutes after local midnight), `DAILY_SCHEDULE_POLL_SECONDS=60`, `DAILY_JOB_LEASE_SECONDS=600`, and `DAILY_JOB_MAX_ATTEMPTS=5`. Manual processing still works when automatic scheduling is disabled.

### Repeatable historical demonstration

Inspection before this update found **220 synthetic recordings across four locations, all on 9 October 2026**, with no recordings for the previous local day. The daily demo reuses the three application-demo locations and registers dedicated labelled replay devices; it preserves their existing live devices and thresholds. If application-demo locations are absent, it creates three explicitly simulated daily-demo locations instead.

From the project folder with Docker running:

```sh
set -a
. ./.env
set +a
python3 scripts/demo-daily.py
```

This loads the private token only into the local process; never paste it into chat. With the default live window, run after 00:04 in Asia/Kolkata. The script reads the actual server freshness window from `/capabilities` and refuses replay while any sample could still be live; a longer configured window requires waiting longer or selecting older dates. Optional `--date YYYY-MM-DD` selects the first historical date; the script covers that date and its following date. It uploads real synthetic PCM24 WAV files through the existing audio pipeline, including a silent invalid sample for each location and a gate recording crossing midnight. It verifies processing and queues reports for both days. The simulated calibration is only a numerical test fixture. Repeating the same command reuses saved private device credentials, stable chunk IDs and saved report identities; it does not duplicate recordings. It never fabricates incidents or writes directly to measurement tables.

In **Daily reports**, choose **North garden**, **Workshop**, or **East gate**, then yesterday. The deliberately short samples should show **SIMULATED** and **Partial data**, not full-day monitoring. Choose today to see provisional results combined with any other compatible saved demo recordings. Choose an empty earlier day and generate to see **No usable data**. Recalculate, refresh the page, or restart API/worker: completed reports remain in PostgreSQL. See [DAILY-VERIFICATION.md](DAILY-VERIFICATION.md) for the performed checks and their results.

## Start or update the stack

Install Docker Desktop/Engine with Compose v2 and Python 3 for setup and demonstration scripts. Test overrides require Compose **2.24.4 or later**. Run these commands from this directory:

```sh
python3 scripts/setup-env.py
docker compose up --build
```

The setup script creates a private `.env` with random database passwords and an administrator token for command-line/API tools. The local application opens automatically and does not ask you for that token. It refuses to overwrite an existing `.env`; retain yours when updating. `.env.example` lists settings. Keep `.env`, device credentials, recordings, and backups private.

On **Apple Silicon**, enable AMD64 emulation for the selected PostGIS image:

```sh
export COMPOSE_FILE=compose.yaml:compose.amd64.yaml
docker compose up --build
```

You can instead add `COMPOSE_FILE=compose.yaml:compose.amd64.yaml` to `.env`. The override applies AMD64 emulation to the database; application images may run natively. To inspect available image architectures, use `docker buildx imagetools inspect postgis/postgis:17-3.5`.

- Application (opens automatically): [http://localhost:8000/app](http://localhost:8000/app)
- Original technical demo (bearer token required): [http://localhost:8000/demo](http://localhost:8000/demo)
- Interactive API documentation: [http://localhost:8000/docs](http://localhost:8000/docs)
- Readiness: [http://localhost:8000/health/ready](http://localhost:8000/health/ready)
- Liveness: [http://localhost:8000/health/live](http://localhost:8000/health/live)

For an existing installation, back up the database and audio first, then stop writers while applying migrations:

```sh
docker compose stop api worker
docker compose run --build --rm migrate
docker compose up -d --build api worker
```

Do not delete volumes when upgrading. Migrations `0002_live_incidents` and `0003_legacy_audio_guard` preserve existing recordings and history; see **Legacy data** below.

The API binds to `127.0.0.1:8000`. PostgreSQL has no host port by default. `compose.dev.yaml` optionally exposes it on `127.0.0.1` at `DEV_DB_PORT` (default 5432):

```sh
docker compose -f compose.yaml -f compose.dev.yaml up --build
# On Apple Silicon also include: -f compose.amd64.yaml
```

Named volumes `postgres_data` and `original_audio` hold database files and `/data/audio`. Database health gates migration; successful migration gates API/worker startup. Readiness checks PostGIS, migration state, schema access, and writable audio storage. The privileged migration role initializes the database. API/worker use a restricted application role that cannot create tables, roles, or extensions. Threshold versions, evaluations, and events are append-only. Editing `.env` does not rotate passwords already stored in PostgreSQL.

## Demonstrate the seven-reading sequence

1. Open `/demo`, enter the `ADMIN_TOKEN` from `.env`, and select **Connect**. The token remains in page memory; it is not stored in browser storage or placed in a URL.
2. In a second terminal, load the local environment and run:

```sh
set -a
. ./.env
set +a
python3 scripts/simulate-live.py --scenario sequence --pause 1
```

This creates a clearly named **SYNTHETIC live demo** location/device and uploads real mono PCM24 WAV files for:

```text
55 → 60 → 70 → 80 → 60 → 59 → 58
```

The fixture uses explicitly synthetic calibration to test software. It does **not** calibrate an INMP441 or demonstrate real acoustic accuracy. Its quantized equality fixture is calibrated numerically so that the canonical 60 reading equals the threshold exactly.

Expected: seven persisted results; one incident opens at 70; its peak reaches approximately 80; the first two subsequent normal readings show `recovering`; the final reading resolves the same incident. The browser shows one opening popup and one genuine recovery update. Continued breaches update values without extra opening popups. The simulator checks the resulting incident and prints `PASS`.

Additional scenarios each create an independent test location/device:

```sh
python3 scripts/simulate-live.py --scenario invalid
python3 scripts/simulate-live.py --scenario silence
python3 scripts/simulate-live.py --scenario delayed
python3 scripts/simulate-live.py --scenario future
```

These first open an incident, begin recovery, then submit clipped/silent/historical/future data. None may falsely resolve the incident. The demo shows coordinates on a simple local SVG map, device states, unresolved incidents, freshness badges, and incoming events. It uses no external map service or CDN. Initial snapshots show up to 200 locations; use API filters for larger installations. Keep this terminal's secrets private. The simulator saves its test device credentials with mode `0600` in `.local/live-demo-device.json`.

To try ordinary **uncalibrated** uploads instead:

```sh
python3 scripts/seed.py
python3 scripts/simulate.py --chunks 3
```

The idempotent seed creates three uncalibrated devices and stores their one-time tokens in `.local/demo-devices.json`. Expected results contain digital dBFS, `calibration_required`, and no physical incident. Preserve that credentials file; hashes cannot recover lost tokens. The ordinary simulator's amplitude is digital amplitude, not sound pressure.

## Threshold rules and calibration

A breach is **strictly** `canonical_value > threshold_value`. The comparison uses the stored binary64 measurement before display rounding. Equality is normal and counts toward recovery; `60.0001 > 60` remains a breach even if both display as `60.00`.

| Supported method | Weighting | Numeric measurement/threshold range | Calibration |
|---|---|---|---|
| `dbfs_rms` | `none` | −200 through 0 dBFS, inclusive | Not required; digital signal level only |
| `spl_z_leq` | `Z` | −100 through 200 dB, inclusive | Valid applicable calibration required |

Both support `channel_policy: "mono"` and configured integer intervals from 1 through 60 seconds. These are declared software acceptance ranges, not a claim that every microphone can accurately measure every value in them. Negative decibel values are supported. Null, booleans, numeric strings, NaN, and infinities are not valid threshold numbers.

Processing version `pcm24-dc-rms-quality-v2` calculates DC-removed RMS over the complete chunk. Digital level is `20*log10(RMS / 2^23)` dBFS; a full-scale sine is approximately −3.01 dBFS. Silence produces null rather than negative infinity. Clipping, silence, invalid quality, incompatible method/weighting/channel/interval, disabled devices, or invalid attribution prevent live threshold evaluation. The original remains stored, and the result exposes an evaluation diagnostic. An invalid result is never treated as a normal level or a recovery reading.

Physical `spl_z_leq` is unweighted equivalent level over the chunk. It requires a measured offset for the complete microphone, board, firmware, and acoustic chain. Calibration must have matching method, sample rate, Z weighting, mono channel policy, `status: "valid"`, a version, and a validity period covering the entire recording. The physical value is digital dBFS plus the measured offset. Missing, failed, expired, or incompatible calibration cannot produce a physical alert. No dBFS-to-SPL offset is invented.

Calibration fields are `method`, `version`, `offset_db`, `sample_rate`, `calibrated_at`, `valid_until`, and optional `weighting`, `channel_policy`, `status`, `pcm_bits`. PCM16 uploads require `pcm_bits: 16`; PCM24 accepts `pcm_bits: 24` or an omitted bit-depth field for existing calibrations. Processing, live evaluation and daily reporting use the recording's actual format to check compatibility. Use `PATCH /devices/{id}` with the current device `config_revision` as `expected_revision`. Calibration is snapshotted at ingestion and with each result; effective calibration history across later configuration changes remains follow-up work. A/C weighting, frequency-response correction, microphone calibration hardware, and certified/regulatory sound-level classification are outside this implementation.

## Versioned configuration and historical accuracy

`GET /locations/{id}/threshold` returns the currently effective rule, the latest configured rule, and `latest_revision`. Its optional `at` timestamp selects the rule for that instant. `GET /locations/{id}/threshold/versions` returns paginated immutable versions.

An administrator edits the full rule using the revision just read:

```json
{
  "expected_revision": 1,
  "threshold_value": 60,
  "threshold_type": "spl_z_leq",
  "weighting": "Z",
  "channel_policy": "mono",
  "interval_seconds": 1,
  "recovery_count": 3
}
```

Send this to `PATCH /locations/{id}/threshold`. Replace `expected_revision` with the actual latest revision. Omitted `effective_at` means server time; an explicit timezone-aware timestamp may schedule a future version. Edits cannot be retroactive and must follow the latest version's effective time. A stale revision returns **409**, so competing administrators cannot silently overwrite each other.

A measurement selects the newest version with `effective_at <= captured_at`; equality belongs to the new rule. Its evaluation stores that exact version ID. Location compatibility fields summarize the latest configured rule and may therefore describe a scheduled future rule; use the threshold endpoint's `current` field for the rule effective now.

Initial location rules and initial device assignments use **1970-01-01 UTC** as an explicit baseline assumption. Subsequent device assignments begin at server time and retain an immutable location/coordinate snapshot; the old assignment's end time is closed once. Upload attribution is derived from the authenticated device and its assignment at capture time, never from supplied location data. Device updates require `expected_revision` matching `config_revision`.

An edit alone does not prove recovery. On the first **eligible live** reading under a new rule, the prior unresolved incident closes with `threshold_changed`. Reassignment similarly closes it with `device_reassigned` on the first eligible live reading for the new assignment. The reading is evaluated under its new rule; if excessive it opens a new incident linked through `previous_incident_id`. These transitions emit `incident.closed`, never a recovery notification. Historical measurements, thresholds, incidents, and original coordinates are preserved.

## Incident recovery and timing policy

Streams are independent by **device + assignment + method + weighting + channel policy + configured interval**. Separate devices at one location never share an incident or recovery streak. The database enforces at most one unresolved (`active` or `recovering`) incident per stream.

- First eligible excessive reading opens immediately. Later excessive readings update latest level, peak, last occurrence, and breach count; peak never decreases.
- Eligible readings at or below the threshold increase the recovery streak. The default rule requires three; `recovery_count` supports 1–100. The incident is `recovering` until the count is reached, then `resolved` with reason `valid_recovery`.
- Any new excessive reading resets recovery. A newer invalid reading interrupts it without counting as normal. Sequence/time gaps, a new boot session, or calibration changes break continuity; the next eligible normal reading may start a new streak at one.
- Silence, missing uploads, disconnection, staleness, threshold edits, and device edits never resolve an incident merely because time passes.

Capture time is the first sample; receipt time is recorded separately by the server. Timestamps are timezone-aware and compared as UTC instants. Different offsets representing the same instant are equivalent.

This implementation uses a **committed watermark**, not capture-order buffering:

| Situation | Behavior |
|---|---|
| Eligible capture newer than the stream's committed watermark and within freshness | Evaluate live and advance watermark/window end atomically |
| Capture equal to watermark | Historical `duplicate_timestamp`; no state/event change |
| Older capture or out-of-order worker completion | Historical `out_of_order`; no state/event change |
| Capture starts before the last committed window ends | Historical `overlapping_window` |
| Capture more than 120 seconds old by default | Historical `old_capture` |
| Any capture after server time, including within 5-second skew tolerance | Historical diagnostic; never advances watermark |
| Future by at most configured skew tolerance | `future_clock_skew` |
| Future beyond tolerance | `future_rejected`; valid original audio/result still preserved |
| Late arrival for a prior assignment | Historical `historical_assignment` |
| Earlier capture from a superseded method stream | Historical `out_of_order_policy_stream` |

Future results are recorded once as historical diagnostics; they are not automatically promoted when the clock catches up. Correct subsequent recordings remain eligible. Historical arrivals and reprocessing never reopen/resolve current incidents or replay fresh popups.

Continuity requires the same session, the next sequence number, matching calibration snapshot/version, and a start at the preceding window end within 1 millisecond. A new boot session may restart sequence numbering at zero. Newer invalid observations retain a separate observed boundary without advancing the eligible watermark; delayed valid data preceding that boundary stays historical and cannot rebuild recovery.

**Noise status and data freshness are separate.** Default map freshness is 30 seconds from the last eligible capture, independently of the 120-second live eligibility window. Thus an eligible delayed reading can open an incident while its data is already marked stale. Location aggregation prioritizes unresolved excessive/recovering incidents; missing, disabled, stale, or invalid device data remains visible separately. Old normal readings never make stale data appear fresh or erase unresolved incidents.

## Durable transactions, retries, and SSE

The worker calculates audio outside SQL locks. Its final transaction commits the measurement, evaluation, stream watermark/recovery state, incident transition, durable event, and processing checkpoint together. Failure before commit leaves no partial transition; retry after an acknowledged or uncertain commit cannot repeat counts/events. Identical `(audio_chunk_id, result_version)` content returns its existing outcome; changed content under the same identifier produces an explicit conflict.

A singleton PostgreSQL event-clock row is locked **before** device/job/configuration locks. It allocates event positions transactionally, not with a PostgreSQL sequence, and stays locked until commit. A snapshot takes the same lock and returns state plus its cursor, avoiding a gap between snapshot loading and streaming. The tradeoff is intentionally global serialization of live/configuration writers for this bootstrap. Expensive audio calculation and network streaming occur outside that lock; ingestion/configuration transactions still contend for it. Larger deployments should measure this bottleneck before introducing partitioned ordering.

Events are persisted before delivery. Supported types are `incident.opened`, `incident.updated`, `incident.resolved`, `incident.closed`, and `location.status_changed`. Applicable payloads include stable event/incident/device/location IDs, coordinates, canonical measurement value/type/interval/time, threshold value/version, incident state, and transition reason. Public-field projection excludes credentials, internal storage paths, and lease tokens.

To subscribe:

1. Authenticate as administrator and fetch `GET /locations/status`, optionally filtered by `location_id`/`device_id`.
2. Render its current location and unresolved incident state without opening popups for historical incidents. Keep its `cursor`.
3. Request `GET /events/stream` using the same filters, `Authorization: Bearer …`, and `Last-Event-ID: <cursor>`.
4. Deduplicate by stable `event_id`, retain the latest cursor, and reconnect from it. Network delivery is **at least once**, not exactly once.

A cursor is `epoch-UUID:committed-position`; it contains no credential. A `cursor` query parameter is also accepted, but **tokens must only be in authorization headers**. Browser code uses `fetch` streaming because native `EventSource` cannot set that bearer header. The demo retains deduplication state across automatic reconnection, aborts obsolete connections, and refreshes its snapshot on explicit resynchronization.

| Cursor condition | Response |
|---|---|
| Missing cursor | 409 `snapshot_required` |
| Malformed or conflicting header/query cursors | 400 |
| Unknown epoch/position, or future position | 409, resynchronization required |
| Oldest missed event exceeds replay retention | 410, resynchronization required |
| Cursor expires while stream is already open | `stream.resync_required` control event, then closure |

Default replay retention is seven days. Events are retained in PostgreSQL; this limit restricts replay and does not automatically delete history. SSE polling uses short separate transactions with no connection or SQL lock held across a network yield. Clients receive heartbeats while idle; a slow client does not hold an evaluation lock. The worker and snapshot service persist stale transitions without resolving incidents, and the demo also ages badges while disconnected.

By default, management, snapshot, history, and streaming access require administrator authentication: API tools use the existing administrator token, while the loopback-only application uses its automatic local session. An optional `PUBLIC_DEMO_LOCATION_ID` publishes one deliberately selected location for read-only browsing, including its readings, incident history, saved reports and audio. Public queries and SSE are scoped to that location; other locations and all changes still require administrator access. Leave the setting unset on private deployments. Device tokens cannot access SSE. Administrators have all-location access; per-user organizations or tenant-specific location permissions are not implemented. UUID filters are validated, and snapshot/SSE filters reject unknown locations/devices. Each paginated snapshot is its own consistent snapshot; clients spanning many pages should use bounded location/device subscriptions and retain each subscription's cursor.

## API summary

Health routes, `/docs`, and demo assets are public. API tools and device uploads retain bearer authentication. The loopback-only `/app` workspace opens automatically using a server-issued local browser session; its pages never ask for the administrator token. In Swagger **Authorize**, paste only the token, without the word `Bearer`.

| Endpoint | Purpose |
|---|---|
| `POST /locations`, `GET /locations`, `GET /locations/{id}` | Register/inspect locations |
| `GET /locations/{id}/threshold`, `PATCH /locations/{id}/threshold` | Read/version rules with optimistic concurrency |
| `GET /locations/{id}/threshold/versions` | Immutable threshold history |
| `GET /locations/status` | Consistent location/incident snapshot and SSE cursor |
| `GET /locations/nearby`, `GET /locations/geojson` | Spatial search and GeoJSON |
| `POST /devices`, `GET /devices`, `GET /devices/{id}` | Register/inspect devices; registration returns token once |
| `PATCH /devices/{id}` | Revision-checked assignment, enablement, calibration changes |
| `POST /audio` | Device-authenticated multipart raw audio upload |
| `GET /audio/{id}`, `GET /audio/{id}/file` | Processing/results/diagnostics and preserved original |
| `GET /measurements` | Versioned results, including evaluation details |
| `GET /incidents`, `GET /incidents/{id}` | Incident history and exact rule details |
| `GET /events`, `GET /events/stream` | Durable event history and authenticated live/replay stream |

Paginated lists accept `limit` 1–200 (default 50) and nonnegative `offset`, returning `items,total,limit,offset`; GeoJSON uses `features`. Measurements/incidents accept `device_id`, `location_id`, aware `since`/`until`; incident filters also accept `status` (`active`, `recovering`, `resolved`, `closed`). Event history accepts identity and event-type filters. Invalid time ranges and malformed values return validation errors. Error envelopes use `{"error":{"code":…, "message":…, "details":…}}`.

Locations use `geography(Point,4326)` and a GiST index. Coordinates are validated; GeoJSON order is **longitude, latitude**. Nearby `radius_m` and returned distances are metres. Device credentials can download/check only their own audio. Administrator credentials cannot impersonate a device for upload.

## Upload contract and firmware packing

`POST /audio` takes multipart `file` (WAV) and `metadata` (JSON string):

```json
{
  "device_id": "registration UUID",
  "chunk_id": "boot-42-00001",
  "session_id": "boot-42",
  "sequence": 1,
  "captured_at": "2026-10-09T12:00:00+05:30"
}
```

Use the real current capture time for live testing; copying a historical example correctly produces historical data. Use a fresh session per boot, increasing nonnegative integer sequences, and a unique chunk ID per device. String identifiers permit ASCII letters, digits, `_`, `.`, `:`, `-`, up to 128 characters. Do not supply a location ID; attribution is server-derived. A capture must include `Z` or an offset and be covered by a registered device assignment.

| Property | Required value |
|---|---|
| Container | Little-endian RIFF/WAVE, correct lengths and even-byte chunk padding |
| Format tag | `1`, uncompressed PCM; no float, extensible WAV, RF64, or compressed audio |
| Channels | One, mono |
| Sample width | Exactly 24 signed valid bits, **three packed bytes per sample** |
| Default sample rates | 16000, 32000, 44100, 48000 Hz |
| Block alignment / byte rate | 3 bytes / sample rate × 3 |
| Accepted duration | Positive, within configured upload limit |
| Default limits | 20,000,000 bytes and 60 seconds |

Configure whole-second intervals 1–60. A format-valid duration that differs from the applicable rule is **preserved**, processed, and marked ineligible; it does not count toward recovery. Unsupported/malformed audio is rejected before acknowledgement. Validation checks actual RIFF bytes, not filename or MIME type.

INMP441 I²S may use a **32-bit slot**; this is not a 32-bit WAV sample. If the driver left-aligns signed 24-bit samples in bits 31..8, arithmetic-shift right by 8 and emit the low 24 bits little-endian: bits 7..0, 15..8, 23..16. Range is −8388608 through 8388607. Examples: −1 → `FF FF FF`; minimum → `00 00 80`; maximum → `FF FF 7F`. WAV must use `bitsPerSample=24`, `blockAlign=3`, `byteRate=sampleRate*3`, `dataSize=sampleCount*3`, padding odd data sizes outside the declared data chunk. Verify actual driver alignment, sign extension, channel selection, and endianness with known samples. Do not normalize, truncate to 16 bits, or store the unused slot byte.

Example upload after setting the registered device's ID/token:

```sh
curl -sS http://localhost:8000/audio \
  -H "Authorization: Bearer $DEVICE_TOKEN" \
  -F 'file=@recording.wav;type=audio/wav' \
  -F "metadata={\"device_id\":\"$DEVICE_ID\",\"chunk_id\":\"boot-42-1\",\"session_id\":\"boot-42\",\"sequence\":1,\"captured_at\":\"$CAPTURED_AT\"}"
```

Set `CAPTURED_AT` to the timezone-aware first-sample timestamp. A new accepted upload returns **202** only after the original is synced and metadata/job commit. An identical retry returns **200**, the same ID, and `duplicate:true`; changed bytes or capture/session/sequence metadata under the same device/chunk ID return **409**. Retry uncertain network/503 acknowledgements with identical bytes and IDs. SHA-256 covers the entire original, including WAV headers.

## Worker recovery, reprocessing, and legacy data

Jobs use row locking, `SKIP LOCKED`, persistent attempts, lease tokens, and expiry. Default lease is 120 seconds, maximum attempts five, exponential retry base two seconds. Abandoned leases recover; a worker with an expired/replaced lease cannot commit. Failures and exhausted jobs remain visible in `GET /audio/{id}`. Originals remain even after final failure. Fix the underlying cause before an operator deliberately retries/reprocesses; there is no public endpoint that silently erases failure history.

Reprocess an original into a **new historical result version**:

```sh
docker compose exec worker python -m scripts.reprocess AUDIO_UUID --version reviewed-v2
```

It uses the deployed processing algorithm and ingestion calibration by default. To supply a genuine replacement calibration:

```sh
docker compose cp calibration.json worker:/tmp/calibration.json
docker compose exec worker python -m scripts.reprocess AUDIO_UUID \
  --version calibrated-review-v2 --calibration-json /tmp/calibration.json
```

A version labels this additional result; it does not itself install a new algorithm. The algorithm's intrinsic `processing_version` remains recorded. Calibration is validated and snapshotted separately; original bytes and ingestion metadata are unchanged. Identical version/content retries return the existing outcome; different content under that version conflicts. Reprocessing never changes live state or generates new live alerts.

**Legacy data:** migration retains original recordings, measurements, and incidents. Existing measurements receive `legacy-v1`/`legacy_historical`; no unavailable historical threshold version is invented. Existing active incidents close explicitly with `legacy_migration`, not recovery, and without replay notifications. `0003` marks all pre-extension audio as legacy, including pending/failed jobs; later processing of those recordings remains `legacy_historical` and cannot create new live alerts. New post-upgrade recordings use the new rules. These migrations are forward-only; rollback requires restoring the pre-upgrade database/audio backup.

## Settings

Settings are shared by API/worker through Compose. Edit `.env`, then recreate services for changes to take effect.

| Setting | Default | Meaning |
|---|---:|---|
| `LIVE_FRESHNESS_SECONDS` | 120 | Maximum capture age for a new live evaluation |
| `FUTURE_SKEW_SECONDS` | 5 | Boundary between tolerated-skew and rejected-future diagnostics; neither advances live state |
| `DATA_STALE_SECONDS` | 30 | Capture age for stale data badges/events |
| `RECOVERY_COUNT` | 3 | Default recovery policy; each immutable rule records its own count |
| `EVENT_REPLAY_SECONDS` | 604800 | Maximum age of missed events for replay; no automatic event deletion |
| `SSE_POLL_SECONDS` | 0.25 | Database polling interval for each SSE client |
| `SSE_HEARTBEAT_SECONDS` | 10 | Idle stream heartbeat interval |
| `SSE_BATCH_SIZE` | 100 | Maximum events per poll |
| `WORKER_POLL_SECONDS` | 1 | Worker idle polling interval |
| `JOB_LEASE_SECONDS` | 120 | Processing claim lease |
| `JOB_MAX_ATTEMPTS` | 5 | Maximum attempts before visible failure |
| `JOB_RETRY_BASE_SECONDS` | 2 | Exponential retry base |

Database connection retries, audio limits, and allowed sample rates are also listed in `.env.example`. No database credentials have built-in usable defaults.

## Automated verification

```sh
./scripts/test-compose.sh
./scripts/test-persistence.sh
node --test tests/test_demo.mjs tests/test_application.mjs
```

The first two scripts create uniquely named disposable Compose projects with fresh private credentials and real PostGIS. Cleanup removes only their own test volumes; it does not reset development volumes. ARM hosts automatically apply the AMD64 override. The first runs Python tests with distinct restricted application/migration roles. The second recreates containers while keeping volumes and checks originals, measurements, incident/recovery state, durable event IDs, and final recovery. Node tests use its built-in runner and require no npm packages.

For an existing **dedicated disposable** PostgreSQL/PostGIS database:

```sh
python3.12 -m venv .venv
. .venv/bin/activate
pip install --require-hashes -r requirements.txt
export TEST_DATABASE_URL='postgresql+psycopg://APP_USER:URL_ENCODED_PASSWORD@127.0.0.1:5432/noise_monitor_test'
export MIGRATION_DATABASE_URL='postgresql+psycopg://MIGRATOR:URL_ENCODED_PASSWORD@127.0.0.1:5432/noise_monitor_test'
export APP_DB_USER='APP_USER'
python -m pytest -q
```

Provision separate roles using `docker/20-app-role.sh` as the reference. Fixtures require the same database with distinct application/migration roles and a database name ending `_test` or beginning `test_`. They apply migrations and **truncate application tables between tests**. Never point them at development/production data. Without dedicated database URLs, integration tests skip explicitly. Deterministic clocks and labelled synthetic audio/calibration/measurement fixtures drive the integration tests; SQLite is not used.

Latest recorded backend results are **496 Python tests passed inside Docker with real PostGIS**, plus **54 application/demo JavaScript tests passed in Node**, on **10 October 2026**. These include physical-provisioning safeguards, actual-recording verification, and consistency between live events and snapshots after unusable readings. See [PCM-INTEGRATION-VERIFICATION.md](docs/PCM-INTEGRATION-VERIFICATION.md) for the latest firmware builds, endpoint/mapping checks and preservation evidence. [STEP6-VERIFICATION.md](STEP6-VERIFICATION.md), [DAILY-VERIFICATION.md](DAILY-VERIFICATION.md) and [VERIFICATION.md](VERIFICATION.md) retain earlier dated results. Physical capture and upload were checked with the connected board; calibrated sound-pressure accuracy and controlled Wi-Fi interruption testing remain pending.

## Storage, backup, and shutdown

Originals are never normalized, overwritten, or automatically deleted. Staged files and promoted directories are synced before SQL acknowledgement. File and SQL commits cannot be one atomic transaction: an interrupted promotion/commit can leave an orphan, but uncertain commits never delete originals. Reconciliation uses a database advisory lock shared with ingestion:

```sh
docker compose exec api python -m app.reconcile
# After reviewing the report, delete only files with no committed database reference:
docker compose exec api python -m app.reconcile --delete-orphans
```

The worker also performs locked orphan cleanup at startup and hourly. Missing committed originals are reported for restoration, not removed from SQL history.

```sh
docker compose ps
docker compose logs -f api worker migrate
docker compose stop       # Stop services; retain containers and volumes.
docker compose start      # Resume existing containers.
docker compose down       # Remove containers/network; retain database/audio volumes.
```

For a consistent backup, stop writers and save **both** database and audio:

```sh
mkdir -p backups
chmod 700 backups
docker compose stop api worker
docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > backups/database.dump
docker compose run --rm --no-deps -T --entrypoint python api -c 'import sys,tarfile; archive=tarfile.open(fileobj=sys.stdout.buffer,mode="w|"); archive.add("/data/audio",arcname="audio"); archive.close()' > backups/original-audio.tar
docker compose start api worker
```

Back up `.env` and `.local` device credentials separately and privately. Verify checksums and test restoration. Restore database/audio from the same backup point into an isolated stack with empty volumes, preserving audio ownership for UID/GID 10001. Apply readiness and read-only reconciliation checks before enabling writes. Restoring only database or only audio can produce missing recordings or orphans.

`docker compose down --volumes` is a **destructive reset**: it deletes named database/audio volumes. It is not an ordinary shutdown. Test scripts use it only for their unique disposable projects.

For physical boards on other machines, use the opt-in, device-only HTTPS listener described in [docs/HARDWARE-INTEGRATION.md](docs/HARDWARE-INTEGRATION.md). Keep the web application's loopback binding and PostgreSQL's private network. Never share administrator credentials with devices. This remains a local system with global writer serialization and operator-managed storage retention; it is not a certified acoustic instrument or a complete multi-tenant production service.

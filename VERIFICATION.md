# Verification report

**Historical evidence — current-status pointer added 9 October 2026.** This report records earlier backend, deployment-package and local-access milestones. Its individual test counts and migration versions describe those runs, not the latest application. See [Step 6 verification](STEP6-VERIFICATION.md) for the subsequent 382-backend/34-frontend software and firmware results, and [Step 7 Current status](docs/step7/CURRENT-STATUS.md) for the documentation task's separate checks. UrbanEcho currently runs locally; the historical ngrok tests below do not indicate an active tunnel, and Railway was not deployed successfully.

Report date: **2026-10-09**. Test evidence applies to the current extension, using isolated test data; development recordings and volumes were not used for test cleanup.

| Check | Actual result |
|---|---|
| Complete Python suite inside Docker, real PostgreSQL/PostGIS | **216 passed in 13.83 seconds; no skips** |
| Browser state, notification deduplication, map aggregation, and SSE parser tests in Node | **18 passed** |
| Container recreation with database/audio volumes retained | **Passed**: originals, incident, recovery streak, and durable event IDs survived; the next normal reading resolved once |
| Live HTTP simulator sequence and diagnostic scenarios | **All five scenarios passed**: sequence, clipped/invalid, silence, delayed, future |
| Browser demonstration connected to the running stack | **Passed**: one opening toast, one recovery toast, separate stale state, automatic reconnect after API restart |
| Two simultaneous real HTTP SSE clients and disconnect/replay | **Passed**: identical ordered event UUIDs and complete replay after disconnect |
| Existing local Compose stack upgrade | **Passed**: protected database/audio backups validated; migration advanced from `0001_initial` to `0003_legacy_audio_guard`; readiness passed and existing named volumes/database container were retained |

The final Docker run emitted two non-failing warnings: an upstream Starlette/httpx deprecation and pytest being unable to write its cache in the container's non-writable application directory. Neither caused a skip or test failure.

## Verified automated behavior

The Python suite covers clean migrations, restricted database roles, PostGIS coordinates/distance queries, upload byte preservation, strict validation and authorization, threshold boundaries and canonical precision, supported negative/zero levels, calibration/quality diagnostics, and the seven-reading incident sequence.

Incident coverage includes configurable recovery, equality, interruption by invalid/silent/clipped/gapped/rebooted data, independent devices/locations, peak retention, recovery across new database sessions, explicit threshold/reassignment closures, preserved historical attribution, legacy-data guards, and historical reprocessing without new live notifications.

Transaction/concurrency coverage includes identical retries and conflicting content, simultaneous evaluation and first breaches, recovery/breach races, uniqueness constraints, rollback on failures, worker crashes before/after commit, lease fencing/recovery, and visible retryable failures. Timing tests exercise committed watermarks, duplicate instants, overlaps, stale/delayed captures, future captures, timezone equivalence, and rule effective-time boundaries.

SSE/snapshot tests cover authenticated delivery, independent clients/replay, commit-ordered event positions, snapshot consistency, malformed/unknown/future/expired cursors, filters, public event payloads, and slow-client isolation. Node tests cover notification deduplication, no historical popup on snapshots, genuine recovery versus policy closure, device/location aggregation, separate freshness/invalid states, disconnection ageing, canonical-unit display, and fragmented SSE parsing.

Freshness boundary cases at 30, 31, and 120 seconds also passed: eligible delayed captures publish their correct stale status in the evaluation transaction, without a transient fresh event. Valid contiguous recovery remains independent of the freshness badge.

These checks use real PostgreSQL/PostGIS for database/transaction/spatial/concurrency behavior. Synthetic calibration and measurements are explicitly test-only. No SQLite substitution or long real-time sleeps are used for deterministic policy tests.

## Verified live checks

The actual API/worker sequence persisted seven measurements, one opening, two breaches, and one resolution after three valid normal readings. The peak was `80.00000965452668` before display rounding, consistent with the synthetic 24-bit quantized fixture; the interface displayed 80.

Clipped input produced `quality_clipped`; silence produced a null level and `quality_silence`. Delayed input was `eligible_historical` with `old_capture`; future input remained historical with `future_rejected`. None falsely resolved the preceding incident.

Two actual HTTP SSE clients independently received the same ordered event UUIDs. Disconnecting after the opening and reconnecting replayed the complete missed sequence. Unauthenticated streaming returned 401 and malformed cursors returned 400. The browser showed one opening notification and one genuine recovery notification, kept staleness separate, and automatically reconnected after API restart.

The separate persistence script recreated containers without deleting volumes. An unresolved incident with a recovery streak of two, its event IDs, and original audio checksums remained intact. A third valid normal reading then produced exactly one resolution.

The local development installation was backed up and upgraded in place. It had no application rows or recordings at upgrade time; data-bearing migration preservation is covered by the separate migration and persistence tests. Backup artifacts remain privately in `backups/pre-live-20261009T101056Z/` and are excluded from the source ZIP and Docker build context. The normal API is ready at port 8000 on migration `0003_legacy_audio_guard`.

## Reproduce

From the source directory:

```sh
./scripts/test-compose.sh
./scripts/test-persistence.sh
node --test tests/test_demo.mjs
```

For the browser demonstration, start the normal stack, open `http://localhost:8000/demo`, connect with the administrator token, then load `.env` and run:

```sh
python3 scripts/simulate-live.py --scenario sequence --pause 1
python3 scripts/simulate-live.py --scenario invalid
python3 scripts/simulate-live.py --scenario silence
python3 scripts/simulate-live.py --scenario delayed
python3 scripts/simulate-live.py --scenario future
```

The test scripts generate separate private credentials and unique Compose projects, and remove only their own disposable volumes. They do not delete normal application volumes. Manual demonstration commands intentionally create labelled demo records in the selected running application.

## Limits

### Historical ngrok verification — tunnel removed 9 October 2026

**Passed:** public HTTPS readiness and demo; unauthorized business routes/SSE/audio download rejected; seven real synthetic WAV uploads processed through the tunnel; one opening, two breaches, three-reading recovery and one resolution; live SSE and exact missed-event replay after reconnect; original-audio checksum and duplicate retry without additional measurements. The public browser displayed the events and correctly marked the simulated device stale after submissions stopped.

This was a historical check. The tunnel and its local setup have since been removed; the application now runs only on localhost. Account plan was confirmed Free. Local request inspection was disabled; no paid plan or cloud Full Capture was enabled. The original backend code and development containers were not changed or restarted. A single new labelled synthetic location/device was added for this check. See `NGROK.md` for the removal status. Physical hardware was not tested.

### Railway deployment package — 9 October 2026

- **Passed:** 27 launcher unit tests covering port validation, actual mount detection, storage ownership and privilege dropping, child environment filtering, failure propagation, graceful signals and bounded shutdown.
- **Passed:** `scripts/test-railway.sh` with a separate real PostGIS database, custom database image, root-owned persistent audio mount, combined API/worker launcher and dynamic port 8087. Verified readiness/demo/authentication, migration `0003_legacy_audio_guard`, restricted database role, real uploads/processing/incidents, preserved audio checksums/events/recovery after database and app recreation, worker-exit supervision, and refusal to start without the mounted volume.
- The first smoke run exposed a test-harness process-inspection permission issue; the harness was corrected to inspect child environments as the application user, and the complete test passed. Disposable resources were cleaned up; development services/volumes were retained.
- **Passed:** Railway stored-variable checks confirmed the private database reference, matching database credentials, distinct migration/application roles, and both volume mount paths without displaying secret values. The final source ZIP passed integrity checks and contains none of the actual local or cloud credentials.
- **Railway deployment blocked:** project, service settings, credentials and two volumes were prepared. The first database deployment (`f09ee20a-217c-472c-a868-f031c3cde407`) failed before a build with `Your workspace has been restricted`. Railway's dashboard requires a paid-plan upgrade. Cloud runtime, public HTTPS, SSE, and cloud persistence have **not** been verified. See `RAILWAY.md` for prepared resource IDs and resume steps.

### Remaining product and operational limits

- Hardware acoustic calibration, INMP441 accuracy, firmware I²S alignment/packing, and certified/regulatory measurement compliance have not been verified. The demonstration's calibration is synthetic.
- There is no capture-order buffering: committed-watermark policy intentionally records late/future arrivals as historical without changing live state.
- Network delivery is at least once; stable event IDs and client deduplication provide popup suppression. Exactly-once delivery is not claimed.
- Global event-clock locking prioritizes clear commit order and consistent snapshots over high-volume writer throughput. Load testing and production scaling are not established by this suite.
- The browser is a minimal local demonstration with an initial 200-location snapshot limit. All-location administrator authorization is supported; multi-tenant user permissions are not implemented.
- Database and original-audio retention are operator-managed. Restore drills and deployment-specific TLS/operational configuration remain installation responsibilities.

### Automatic local application access — 9 October 2026

**Passed:** 307 Python tests and 29 Node tests. New local-session checks cover default-disabled server mode, loopback host and origin checks, tamper/expiry and cross-site rejection, API management, SSE access, and unchanged device credentials. Local Compose explicitly enables this mode only on its loopback-bound API service.

**Browser passed:** starting from the previously disconnected local page, a reload opened Management without entering any credential. A second reload automatically reconnected; Incident history showed three persisted incidents and Overview showed four registered locations. No administrator credential is placed in the frontend, browser storage, URLs, or public files. Ngrok remains removed.

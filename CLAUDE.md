# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

UrbanEcho is a local environmental noise-monitoring prototype: devices upload WAV audio → a worker computes sound levels → versioned per-location thresholds open/recover incidents → durable events stream to a browser app over SSE → daily reports aggregate saved readings. Stack: Python 3.12, FastAPI, SQLAlchemy 2 + GeoAlchemy2, PostgreSQL 17/PostGIS 3.5, Alembic, Docker Compose. The frontend (`app/static/`) is native ES modules with no build step. `firmware/` holds ESP-IDF C++ (PCM24) and Arduino (PCM16) device senders.

README.md is the detailed behavioral spec (threshold/recovery rules, watermark table, upload contract, daily-report math). Read the relevant section before changing behavior in that area.

## Commands

```sh
python3 scripts/setup-env.py                      # one-time: creates private .env (refuses to overwrite)
export COMPOSE_FILE=compose.yaml:compose.amd64.yaml  # Apple Silicon only (PostGIS image is amd64)
docker compose up --build                         # app at http://localhost:8000/app, API docs at /docs
```

Tests (Python integration tests need real PostGIS; there is no SQLite path):

```sh
./scripts/test-compose.sh                         # full pytest suite in a disposable Compose project + volumes
./scripts/test-compose.sh python -m pytest -q tests/test_daily.py::test_name   # single test: pass the FULL command
./scripts/test-persistence.sh                     # container-recreate/persistence checks
node --test tests/test_demo.mjs tests/test_application.mjs tests/test_application_refresh.mjs
firmware/common/run-tests.sh BUILD_DIR            # portable firmware core; set PYTHON=venv python to also check backend contract
```

`test-compose.sh` arguments replace the container command, so `./scripts/test-compose.sh tests/foo.py` fails — always prefix with `python -m pytest`. On a host venv (`pip install --require-hashes -r requirements.txt`), tests marked `integration` skip unless `TEST_DATABASE_URL` and `MIGRATION_DATABASE_URL` point at a dedicated DB named `*_test`/`test_*` with **distinct** app and migration roles; fixtures truncate tables between tests. Non-DB tests (e.g. `test_audio.py`, `test_pcm16_audio.py`) run without it.

Operational: `docker compose run --build --rm migrate` (apply migrations), `docker compose exec worker python -m scripts.reprocess AUDIO_UUID --version NAME`, `docker compose exec api python -m app.reconcile [--delete-orphans]`. `python3 scripts/verify-simulator.py` runs the three-location demo against the running stack. Physical boards: flash `firmware/arduino-pcm` (PlatformIO, `platformio.ini` there) with empty `UE_DEVICE_TOKEN` (the ID always comes from the factory MAC), then `python3 scripts/provision-board.py --location NAME` registers the board's `ESP-<MAC>` ID and stores its token on the board over USB.

## Architecture

Processes (all from one image, `compose.yaml`): `migrate` (Alembic, privileged role, runs first) → `api` (`uvicorn app.main:app`, bound to 127.0.0.1:8000) and `worker` (`python -m app.worker`). Optional `compose.hardware.yaml` adds `device-api` (`app.device_server`) — a TLS LAN listener that reuses `create_app()` but filters routes down to device uploads (`app/device_api.py`). Railway runs API + worker in one process via `scripts/serve_railway.py`.

Request/data flow across modules:
- **Ingestion** — `POST /audio` (PCM24 WAV, `app/main.py`) and `POST /upload` (raw PCM16 from the Arduino sender, `app/pcm_api.py`, wrapped into WAV) both go through `app/ingestion.py::ingest_recording`. It authenticates the device token, derives location from the device's assignment *at capture time* (never from client data), stages/fsyncs the original via `app/storage.py`, and commits `AudioChunk` + `ProcessingJob`. Same device+chunk_id with identical bytes → 200 duplicate; changed content → 409.
- **Worker** — `app/worker.py` claims jobs with `SKIP LOCKED` leases, computes levels file-only in `app/processing.py`/`app/audio.py` (outside any SQL lock), then a single transaction in `app/evaluation.py` persists measurement + evaluation + stream watermark + incident transition + durable event. A second thread runs `app/daily_jobs.py` (scheduled/manual daily reports, math in `app/daily.py`).
- **Ten-second recordings** — `recording_group_worker.py` discovers continuous one-second PCM16 originals in bounded recent/history scans and maintains leased, durable groups. `recording_groups.py` verifies ten sequential parts and serves a real ten-second WAV from their immutable bytes. Groups are a playback layer only: never ingest them as additional AudioChunks or feed them into measurements/daily totals. Missing/invalid/mixed sources cannot produce a complete file; preserve the original raw-audio APIs.
- **Incident audio/classification** — the separate `classifier` process defaults to `CLASSIFICATION_SCOPE=incidents`. `incident_analysis_jobs.py` discovers incidents, leases revisioned analyses and refreshes provisional/late evidence. `incident_audio.py` verifies originals, builds historical-assignment manifests, trims overlaps and streams assembled WAV playback; gaps are explicit and never silence. `incident_classification.py` joins contiguous source frames before bounded YAMNet windows, excludes playback context and never bridges gaps. `incident_analysis_api.py` serves authenticated saved results, recalculate and revision-bound playback. `classification_jobs.py` remains the explicit legacy `recordings` scope and retains old saved clip estimates. New ordinary clips are `not_requested`. Neither classifier path writes measurements, thresholds, incidents or live events. Only the classifier image installs `requirements-classification.txt`; `CLASSIFICATION_TESTS=1 ./scripts/test-compose.sh` exercises the actual model in disposable tests.
- **Events/SSE** — `app/events.py` allocates event positions from a singleton `event_clock` row; `app/live_api.py` serves `GET /locations/status` (snapshot + cursor) and `GET /events/stream` (replay from `Last-Event-ID`). Browser uses `fetch` streaming, not `EventSource`, so it can send auth headers.
- **Configuration** — `app/configuration.py`: immutable `ThresholdVersion`s and `DeviceAssignment` snapshots with optimistic concurrency (`expected_revision`, 409 on stale).
- **Frontend** — `/app` is served by `app/application_api.py` (`app/static/application/*.mjs`); `/demo` is the older technical demo (`app/static/demo.js`, shared `state.mjs`). Leaflet is vendored.

## Invariants to preserve

- **Lock order:** every writer of live or configuration state calls `lock_event_clock(db)` *first*, before device/job/config row locks. Functions in `evaluation.py` never commit; the caller owns the transaction.
- **Time:** use `app.clock.now()`, never `datetime.now()` directly — tests monkeypatch it via the `fake_clock` fixture.
- **Append-only history:** threshold versions, evaluations and durable events are insert-only (the app DB role is granted only `SELECT, INSERT` on them). Assignment changes close the old row and add a new one. Reprocessing creates a new result version.
- **Live vs historical:** only captures newer than the stream's committed watermark and within `LIVE_FRESHNESS_SECONDS` are evaluated live. Late/duplicate/future/reprocessed/daily-report paths must never open, recover or resolve incidents or emit live events.
- **Breach is strictly `value > threshold`** on the stored float; equality counts toward recovery. Invalid/silent/clipped readings never count as normal. Time passing alone never resolves an incident.
- **Measurement types:** `dbfs_rms` (digital) and `spl_z_leq` (requires valid calibration) are kept distinct; never relabel either as dBA or invent a dBFS→SPL offset.
- **Originals:** audio files are never normalized, overwritten or auto-deleted.
- **Two DB roles:** migrations run as the privileged role; API/worker use a restricted role that cannot do DDL. A migration that adds a table/sequence must explicitly `GRANT` the minimum privileges to `APP_DB_USER` (see `0002_live_incidents.py`, `0004_daily_summaries.py`). Migrations are forward-only.
- **Local browser access** (`LOCAL_BROWSER_ACCESS`, `app/auth.py`) issues an automatic session only for loopback Host/Origin with the `X-Soundwatch-Local` header and no forwarding headers; it must not be enabled behind a proxy/tunnel. API tools use the bearer `ADMIN_TOKEN`; device tokens cannot access admin/SSE routes.
- **Simulation labelling:** demo data is marked `SIMULATED`/`SYNTHETIC` (location names, calibration versions) and must stay separate from recorded data in reports. Don't present simulated calibration as physical accuracy.

## Repo conventions

- Never commit `.env`, `.local/`, firmware `config.h`/`device_config.h`/`privateconfig.h`, recordings, TLS keys or DB dumps (see `.gitignore`, CONTRIBUTING.md).
- Integration tests and test scripts must only target disposable databases; never point them at a DB with saved work.
- The many `*-VERIFICATION.md` files and `docs/step7/` are dated evidence records; don't rewrite their historical results when code changes — add new dated results instead.

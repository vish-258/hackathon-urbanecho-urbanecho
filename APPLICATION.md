# UrbanEcho application

The existing backend now serves a responsive monitoring application at **/app**. The root URL redirects there. The original `/demo` and API documentation remain available.

## Open it

- Local: http://localhost:8000/app
- Just open the link. The local application connects automatically; no token or login is required. Refreshing or opening a new tab restores your saved locations, readings, rules, and incidents automatically.
- Docker, the worker, and the Mac must stay running. The application is local-only at `127.0.0.1:8000`. The ngrok public tunnel and its local setup were removed on 9 October 2026.

## Start or update

From this project folder, with the existing private `.env`:

```sh
docker compose -f compose.yaml -f compose.amd64.yaml up -d --build
```

The additional `compose.amd64.yaml` is for Apple Silicon compatibility with this PostGIS image. On an Intel Mac, use only `compose.yaml`. The daily-processing update applies additive migration `0004_daily_summaries` and retains the original Docker volumes. Back up database and original audio before updating. Never use `down --volumes` for data you need to keep.

The frontend uses native browser modules and self-hosted Leaflet 1.9.4; no separate Node build is needed. Map tiles load from OpenStreetMap with visible attribution and normal browser caching. If tiles cannot load, the application keeps the location list and shows a map warning. Tile requests disclose the viewed map area to the tile provider; no application credential goes to that provider. The OSM public tile service has no availability guarantee and is intended for modest interactive use.

## What works

- **Overview:** real location markers, synchronized list/popup selection, current measurements and thresholds, device reporting counts, active incidents, and stale devices. A stale unresolved incident retains both facts. No readings and recent invalid readings have separate states.
- **Location details:** coordinates/timezone, device IDs and last contact, selectable history, per-device chart series, exact reading tooltips, visible gaps, historical threshold lines and revisions, recent incidents, and daily-report availability.
- **Incident history:** location/device text search, location/status/UTC start-date filters, pagination, historical location and threshold context, latest/peak levels, lifecycle duration, and associated readings.
- **Live updates:** existing authenticated SSE replay and incident lifecycle, one notification for each new incident, reconnection state, persisted-state reload, and no popup for every continued excessive reading.
- **Management:** create/edit locations, register devices with a one-time credential, enable/disable and reassign devices, edit versioned thresholds with explicit measurement units and stale-edit conflict checks.
- **Daily reports:** saved energy averages, min/max, incident starts, eligible recording count, usable duration, coverage and generation time; local-date selection; separate simulated/recorded results; Generate/Recalculate with queued, processing, partial, no-data and error states. The worker automatically processes yesterday after 00:05 in each location's timezone. See the README for exact calculation and overlap rules.

## Measurement and history rules

- The original mono PCM24 WAV is stored; the worker calculates numerical sound levels. This application uses those persisted results.
- `spl_z_leq` is **dB SPL (Z)** and requires the backend's valid calibration. `dbfs_rms` is **dBFS**, a digital signal level. Neither is silently renamed dBA.
- Chart gaps reflect the configured measurement interval; the demo uploads only short samples, so its missing periods are visible. The chart initially loads up to 200 results, says how many are loaded, and offers “Load older readings.” Chart series are separated by device and selected measurement method. No interpolation is used to fill missing data.
- Incident duration is elapsed time between opening and resolution/closure (or now while unresolved), not a claim that audio continuously covered that duration.
- Location edits create new current assignment snapshots, leaving earlier records unchanged. A superseded open incident remains unresolved until the evaluator receives a new eligible reading and closes the old assignment as changed. Threshold edits likewise do not rewrite old incidents.
- Incident history dates are explicitly UTC. Location readings and incident details display the saved location timezone.

## Explicit demonstration

Use [the current product walkthrough](docs/step7/DEMO-WALKTHROUGH.md) and [the simulator verification runner](SIMULATOR-VERIFICATION.md) for the preserving three-location demonstration. Nothing is automatically seeded when someone opens the application. The demo sends actual synthetic WAV files through the same upload, worker, database, threshold, and SSE path. Its fake calibration tests software behavior only; it is not physical microphone validation. `DEMO-APPLICATION.md` remains as the older script's reference.

The initial application demonstration created **SIMULATED · North garden**, **SIMULATED · Workshop**, and **SIMULATED · East gate** (with a fixture suffix). An older **SYNTHETIC live demo** location remains from the earlier ngrok verification. That earlier run showed normal readings, ongoing excessive noise, three-reading recovery, and stale data, and intentionally left a Workshop incident unresolved. Later Step 6 runs recovered that incident and retained its history. East gate's threshold was changed from 60 to 62 through the UI; its older incident retains 60. The current verification runner preserves all three thresholds. Only the legacy `demo-application.py` resets its own fixtures to 60. When any finite sender stops, its devices eventually become stale.

## Initial application verification on 9 October 2026

These checks preceded daily processing. See [DAILY-VERIFICATION.md](DAILY-VERIFICATION.md) for the current daily implementation and checks.

- **307 Python tests passed**, including original ingestion/storage/evaluation, configuration/history, new application API, demo-runner checks, concurrency, security boundaries, and deployment runtime tests, using isolated disposable databases.
- **29 Node tests passed** for the shared SSE/parser/replay logic and application states, including no readings, stale unresolved incidents, and invalid measurements.
- Browser: map marker → selected list/popup → location history; actual incident-open alerts from synthetic uploads; saved incident search/status filtering; incident detail and related readings; daily unavailable state; threshold edit; refresh and reconnect without repeated old popups.
- Responsive browser checks: desktop and 390px phone layout, including map-to-details, daily reports, and device management. The phone pages had no document-level horizontal overflow; charts/tables scroll inside their own containers.
- The browser console reported no application errors during these checks. After the local-access update, the application opened with no credential entry, survived a second refresh, and displayed Management, all four saved locations, and all three saved incidents.
- Local-access verification includes 40 isolated security cases and one database integration case: exact local host/origin, cookie tampering/expiry, duplicate or forwarded headers, cross-site requests, management access, SSE authentication, and unchanged device-upload authentication.
- The demo checks persisted readings and final normal/fresh, excessive/fresh, and recovered/stale states. These are simulated outcomes, not physical hardware certification.

## Remaining limitations

- Daily results describe recorded coverage only. Cross-midnight allocation assumes uniform whole-chunk energy; concurrent calibrated devices use the documented equal-sensor average. Late data and reprocessing require recalculation of older finalized reports.
- No new physical microphone connection or calibration was verified in this build.
- The loopback-only application is a trusted local workspace for anyone using this Mac. Individual user accounts and role-based dashboards are outside this build. Device uploads and command-line/API access retain their separate credentials.
- Map tiles require an Internet connection. Screenshots show the state at capture time; live conditions can change or become stale.
- Browser tests cover the main journeys on the available Chromium-based browser; no cross-browser or formal accessibility audit was performed.

## Technical reference

- **Backend:** Python 3.12, FastAPI, Pydantic, SQLAlchemy, Alembic, PostgreSQL 17/PostGIS 3.5, separate Python audio worker, Docker Compose and SSE; local-only access.
- **Frontend:** semantic HTML, responsive CSS, native JavaScript ES modules, Leaflet 1.9.4, SVG historical charts, same-origin authenticated requests.
- `app/static/application/`: shell, overview/map, shared UI, management, location/history/report views, vendor mapping library and its licence.
- `app/application_api.py`: root/application assets, restrictive content policy and explicit daily-summary capability.
- `app/daily.py`, `app/daily_jobs.py`, `app/daily_api.py`: daily calculation, persisted retryable jobs, location-local scheduling and authenticated results.
- `migrations/versions/0004_daily_summaries.py`: additive result-order and daily storage migration.
- `app/main.py`, `app/schemas.py`: additive location-edit endpoint with optimistic version check, enriched measurement history, incident search and related readings.
- `app/configuration.py`, `app/evaluation.py`, `app/events.py`, `app/live_api.py`: existing historical snapshots, lifecycle and durable event replay.
- `app/static/state.mjs`: reused and tested SSE state/parser; the original demonstration remains unchanged.
- `tests/test_application_api.py`, `tests/test_application.mjs`, `tests/test_application_demo.py`: new integration and state checks.
- `scripts/demo-application.py`: explicit repeatable demonstration with private saved device credentials.

Map references: [Leaflet](https://leafletjs.com/download.html), [OpenStreetMap tile policy](https://operations.osmfoundation.org/policies/tiles/).

## Automatic local access

The local Compose API service enables `LOCAL_BROWSER_ACCESS` and disables proxy-header processing. Server defaults and cloud runtime remain disabled for this mode. The host port must stay bound to `127.0.0.1:8000`; do not expose this local mode through a public tunnel or external hosting.

The browser opens a same-origin local session automatically through `POST /app/session`. The administrator token is never sent to the browser. An eight-hour, origin-bound, signed cookie uses `HttpOnly` and `SameSite=Strict`; refreshing obtains a new session automatically. Local cookie access requires an exact loopback host, the application request header, no forwarding headers, and matching browser origin/fetch metadata; changes require an explicit same-origin Origin. Cross-origin pages cannot acquire or use this access. Device uploads still require their existing device bearer credential. Original-audio API downloads and the legacy `/demo` still use bearer authentication.

# Daily processing verification — 9 October 2026

Implemented and checked locally in UrbanEcho. The app remains bound to `127.0.0.1:8000`; no cloud deployment or public tunnel was enabled.

## Source inspection and preservation

Before changes, PostgreSQL held four locations in Asia/Kolkata, four historical device assignments, 220 stored WAV recordings/measurements (all synthetic and captured on 9 October), and three incidents: two resolved and one active. There were no recordings from the previous local day. Capture durations, location snapshots, versioned thresholds, processing definitions, quality/calibration snapshots and incident start times were already persisted. No daily job or summary table existed.

A private database dump and original-audio archive were saved under `backups/pre-daily-20261009T120018Z/`. Migration `0004_daily_summaries` adds processing revision order and daily-report storage. It does not rewrite existing levels, timestamps, assignments, thresholds or incident states. Secret values were not printed or included in the source ZIP.

## Automated checks

- **346 Python tests passed** against real PostgreSQL/PostGIS in a separate disposable Docker project, using distinct migration/application database roles.
- **32 JavaScript tests passed**, including local date selection, DST calendar subtraction, accurate small coverage percentages, existing SSE replay, live notifications and recovery state.
- New daily cases cover unequal-duration energy weighting, overlap within/across devices, 23/25-hour days, midnight clipping, historical assignments, invalid/failed/missing results, latest reprocessing revisions, simulation separation, device-specific dBFS, no-data results, preserved incident provenance, concurrent/idempotent requests, retries and stale leases, authentication, timezone changes, automatic midnight grace and provisional-day finalization.
- Existing ingestion/storage, configuration, live evaluation, recovery, event replay, concurrency, access protection and forward-migration tests continue to pass.
- Non-failing test warnings: existing Starlette/httpx deprecation and an unwritable pytest cache directory in the restricted container. No tests failed or were skipped in the full Docker run.

## Running-system checks

| Check | Result |
|---|---|
| Automatic scheduling at worker startup | Created yesterday's saved reports for all four existing locations; initially no usable data, correctly classified from stored simulation evidence. |
| Historical replay | Uploaded 14 genuine synthetic PCM24 WAV files via `/audio`, processed by the existing audio worker, into the three existing application-demo locations. Three dedicated replay devices preserve the existing live devices. |
| Yesterday's garden result | 57.403622 dB SPL (Z), minimum approximately 50, maximum 60, 2 eligible recordings, 2 seconds usable, 0.0023148% coverage. |
| Yesterday's workshop result | 75.682027 dB SPL (Z), 3 eligible recordings, 3 seconds usable, 0.0034722% coverage. |
| Yesterday's gate result | 58.873915 dB SPL (Z), 3 eligible recordings, 2.5 seconds usable, 0.0028935% coverage. |
| Midnight boundary | Gate's 23:59:59.5 local one-second recording contributed 0.5 seconds to each date. |
| Invalid samples | One silent WAV per location was stored and processed but excluded from yesterday's average and coverage. |
| Historical alerts | Incident records were byte-for-byte equal through historical replay and its repetition; no false current noise incident was created. |
| Repeatability | Replayed the same 14 chunks. Audio/measurement counts, report IDs, summary IDs and statistics remained unchanged; reports were recalculated in place. |
| Stored results | API summary definitions and statistics matched PostgreSQL JSON fields exactly. |
| Restart | Restarted PostgreSQL, API and worker; all six yesterday/today demo reports retained identical IDs, values and generation times. |
| Original audio | All 240 original WAV files exist and match their database SHA-256 checksums; read-only reconciliation found no missing files or orphans. |
| Real app controls | Generate and Recalculate worked without entering a token; queued state appeared and completed results replaced the previous version. |
| No data | Generated North garden for 7 October: null levels, zero usable duration, 0% coverage and visible “No usable data.” |
| Browser values | All three yesterday averages, usable durations, counts and coverage matched saved results; today's gate showed provisional status and its saved incident count. |
| Location details | North garden's location page displayed yesterday's saved summary. |
| Responsive layout | Daily reports rendered at desktop and 390px phone width with no page-level horizontal overflow. No application console errors were observed. |
| Live regression | Six fresh synthetic recordings on the garden replay device (55 → 75 → 80 → 55 → 55 → 55) opened one incident, updated it once, then resolved it after three normal readings. The browser displayed its opening notification and updated monitoring values. Today's report counted the new incident after recalculation. The three original incidents were unchanged. |

The final verification recordings total 240: 220 original, 14 historical demo, and 6 live-check recordings. The existing four locations are retained; seven devices include the three dedicated replay devices. The saved Workshop incident is still unresolved as before; time passing does not imply recovery.

## Limits and operating notes

- These are labelled software demonstrations. Physical microphone integration and genuine acoustic calibration were not verified.
- A daily result describes its recorded periods. The demo has a few seconds of coverage, not a fully monitored day. Missing time is not silence.
- Midnight clipping assumes uniform energy within each recorded chunk. Overlapping calibrated sensors use the documented equal-sensor energy average; digital levels remain device-specific.
- Late arrivals and reprocessing require recalculation of older finalized reports. Automatic startup catch-up handles yesterday, not every day missed during a long shutdown.
- The scheduler's real startup behavior was observed; next-midnight, DST, retry and competing-worker cases were verified with controlled-clock integration tests, not by waiting overnight.
- Completed reports survive container restarts, but continuous recording and scheduling require the Mac, Docker and worker to remain running. High-volume performance and cross-browser accessibility certification were not evaluated.

See the README's **Daily processing** section for calculation rules, authentication, exact API payloads, configuration, automatic schedule, and repeatable demo instructions. Main components are `app/daily.py`, `app/daily_jobs.py`, `app/daily_api.py`, `app/models.py`, `app/worker.py`, `migrations/versions/0004_daily_summaries.py`, `app/static/application/views.mjs`, and `scripts/demo-daily.py`.

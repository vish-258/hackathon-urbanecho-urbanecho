# Step 7 verification checks

Checked **9 October 2026, Asia/Kolkata**. These are new checks for the documentation and focused software demonstration. They do not replace or re-date the broader [Step 6 verification](../../STEP6-VERIFICATION.md).

## Before and after the focused demonstration

Read-only database snapshots were taken at **18:51:29 IST** before the run and **18:53:59 IST** afterward. The collector used a repeatable-read, read-only transaction and read original WAV files; it did not register devices, change data or restart services. The separate, explicit simulator run intentionally added its labelled demonstration data.

| Persisted records | Before | After | Result |
|---|---:|---:|---|
| Locations | 4 | 4 | Same IDs, names, timezones and measurement settings retained |
| Devices / historical assignments | 7 / 7 | 7 / 7 | Registrations and assignment counts retained |
| Threshold versions | 5 | 5 | No rule reset; North garden 60, Workshop 60, East gate 62 dB SPL (Z) |
| Original audio recordings | 300 | 330 | 30 new labelled one-second recordings |
| Measurements / evaluations / audio jobs | 300 each | 330 each | One additional result/evaluation/job per new recording |
| Incidents | 10 | 11 | One new Workshop incident; all 11 resolved afterward |
| Daily reports / summaries | 8 / 8 | 8 / 8 | All report and summary IDs unchanged |
| Stream states | 5 | 5 | Existing streams reused |
| Durable events | 467 | 512 | Snapshot count includes the new live transitions |

Every original recording ID, checksum and byte size from the baseline is retained unchanged. **All 330 current originals passed SHA-256 verification**, with no missing or mismatched files; their combined file size is **15,854,520 bytes**. Earlier-day report metadata and summary contents were also unchanged. Today's three simulated summaries were recalculated in place.

Evidence: [before snapshot](verification-before.json), [after snapshot](verification-after.json), [explicit comparisons and route checks](verification-comparison.json). These files contain no credentials, internal audio paths or original sound content.

## Live simulator result

The current preserving runner completed successfully using:

```sh
python3 scripts/verify-simulator.py --focus-station workshop --hold-seconds 10
```

All three devices sent normal and exact-threshold readings. Only Workshop became excessive: about 70 dB, then two recordings at about 80 dB, against its 60 dB threshold. Exact 60/60/62 readings did not open incidents. Sustained noise updated the same incident. Identical upload retries preserved recording IDs and did not add measurement, incident or event records; downloaded bytes matched their expected checksums.

The senders paused for **32 seconds**, exceeding the configured **30-second** stale boundary. Workshop's incident remained unresolved while stale. Three contiguous normal readings then completed recovery. Its saved incident is `5605c013-d984-4abc-ac36-a0cc43f00be5`, with three breaches, a peak of `80.00000965452668`, threshold 60, and final status `resolved`.

The runner verified the saved location/time attribution, latest-state snapshot, opening/resolution events and daily results, and ended with **PASS**. Saved events are distinct from a transient popup: see [Current status](CURRENT-STATUS.md) for the separately observed browser behavior. The main application provides an opening notification and visible recovery through status/history; this report does not assert a recovery toast.

[Sanitized live-run phase log](verification-simulator.jsonl).

## Daily values checked against the database

Saved results for **9 October 2026, Asia/Kolkata**, after the focused run:

| Simulated location | Average dB SPL (Z) | Eligible recordings | Usable duration | Coverage | Incident starts |
|---|---:|---:|---:|---:|---:|
| North garden | 66.24 | 140 | 140 seconds | 0.1620% | 3 |
| Workshop | 75.49 | 134 | 134 seconds | 0.1551% | 4 |
| East gate | 72.70 | 39 | 38.5 seconds | 0.0446% | 3 |

Each total includes earlier compatible demonstration recordings. Each new run contribution was ten measurements per device. These are **simulated, partial-coverage, provisional** results, not full-day physical monitoring. East gate's half-second contribution comes from the prepared midnight-crossing historical recording. All eight existing summary identities remained, and the earlier day's saved contents were unchanged.

## Routes, runtime and targeted tests

- `/app`, `/docs`, `/health/live` and `/health/ready` all returned **HTTP 200** on `http://localhost:8000` at **18:54:23 IST**. Readiness reported migration `0004_daily_summaries`.
- All **20 documented route/method combinations** checked against the running `/openapi.json` exist, covering registration, rules, audio upload/status/download, measurements, incidents, state/SSE and daily generation/status. This is a route-contract check, not a new execution of every mutation endpoint.
- Docker's current API publisher remains **127.0.0.1:8000**. The optional `device-api` hardware listener was **not running** in either snapshot. No restart or public exposure was required for these checks.
- **Five targeted simulator integration tests passed** during this task, including the original and focused modes with repeat runs in a disposable database. The initial test-launch invocation attempted to execute the test file directly and failed with permission denied; the invocation was corrected before the successful test run. This was a test-launch issue, not a live application failure. The [test-result transcription](targeted-test-result.txt) identifies its provenance and the corrected invocation; it is not represented as a complete raw log.
- The recorded **382 backend / 34 frontend checks, two earlier three-location simulator runs and ESP32 target compilation** belong to Step 6 on 9 October. The full suites and firmware build were **not rerun** merely to prepare these documents. The original 300-file integrity claim was independently rechecked here, followed by the new 330-file check.

## Limits

The new live run is software evidence using synthetic recordings and calibration. It does not prove physical microphone capture, Wi-Fi reliability, firmware runtime headroom or calibrated acoustic accuracy. Overnight scheduling, restart/persistence and other broader behaviors retain their explicitly dated earlier evidence unless newly stated above. Physical monitoring and Step 8 acceptance remain separate in the [acceptance checklist](ACCEPTANCE-CHECKLIST.md).

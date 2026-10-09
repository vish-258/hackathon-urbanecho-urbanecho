# UrbanEcho simulator verification

This demonstration sends real PCM24 WAV files through the existing device upload API. The worker saves the recordings, calculates their sound levels, evaluates the existing thresholds, updates the application and generates saved daily summaries. The waveform and calibration are **simulated software fixtures**, not recordings from a microphone and not evidence of physical sound-level accuracy.

## Start or repeat

Keep the local Docker stack running. In a terminal, change into the extracted `noise-monitor` project folder and run:

```bash
cd /path/to/noise-monitor
python3 scripts/verify-simulator.py --hold-seconds 4
```

Replace `/path/to/noise-monitor` with the actual project folder. Use Python 3.10 or newer; the runner needs only its standard library. The current Mac project is at `~/Documents/Codex/2026-10-09/bu/outputs/noise-monitor`.

The runner privately reads the administrator credential from the project's existing `.env` file, or from `ADMIN_TOKEN` when already supplied in its environment. There is no token to paste into the application or this command. It reuses the private `.local/application-demo.json` registration file. If that fixture does not exist, it creates three clearly labelled simulated locations and devices and saves their credentials privately. Do not copy `.env` or `.local` into shared documents.

Open [the local application](http://localhost:8000/app#/overview) before starting so you can see incoming notifications. The normal run takes roughly one minute, including the configured stale-device wait. It prints one JSON line for each phase and a final `PASS` only after all checks succeed. `--hold-seconds 0` removes presentation pauses; `--hold-seconds 10` makes the visible phases easier to follow. A stale window above 60 seconds requires an explicit `--max-stale-wait` increase, up to 300 seconds.

## What the three stations demonstrate

Each station uses its **own current threshold and recovery rule**. No location, coordinate, assignment or threshold is reset. With the currently configured example thresholds, the sequence is:

| Simulated location | Threshold | Normal / recovery | Exactly equal | Above | Sustained excessive |
|---|---:|---:|---:|---:|---:|
| North garden | 60 | 55 | 60 | 70 | 80 twice |
| Workshop | 60 | 55 | 60 | 70 | 80 twice |
| East gate | 62 | 57 | 62 | 72 | 82 twice |

These are synthetic dB SPL (Z) fixture values. If a threshold changes, the runner automatically uses the new threshold minus 5, equal to it, plus 10 and plus 20. The equality check compares the stored unrounded value and confirms that equality does **not** open an incident. The runner requires the existing one-second SPL (Z), mono measurement rule and refuses incompatible rules rather than editing them.

| Phase | Expected result at each station |
|---|---|
| Normal baseline | Contiguous normal recordings recover any unfinished incident from a previous demonstration; saved history stays intact. |
| Exactly equal | A saved reading at the threshold, with no new incident. |
| Above threshold | One new saved active incident and one opening notification. |
| Sustained excessive | Two more excessive readings extend that same incident, for three total breaches. |
| Identical upload retry | HTTP 200 acknowledges the same recording; recording, job, measurement, incident and event records are unchanged. Downloaded audio matches the original checksum. |
| Disconnection | All three senders pause beyond the configured stale window. Their streams become stale; their active incidents remain unresolved. |
| Recovery | The configured number of consecutive normal one-second recordings resolves each incident, with exactly one resolution notification. |
| Daily summary | The worker generates or recalculates the saved report for each affected local calendar date, retaining its simulated label and actual coverage. |

With a recovery count of three, a successful run adds **10 one-second recordings and one new resolved incident per device**: 30 recordings and three incidents overall. Baseline recovery can also resolve a previously active incident. If recovery counts differ, the runner sends enough baseline and recovery recordings for the largest configured count, so recording totals change.

## What to check in the application

1. **Overview:** find the three `SIMULATED` station names on the map and in the station list. During `above_threshold` and `sustained_excessive`, readings rise and incident notifications appear. Click a station to see its own reading history and device details.
2. **Incident history:** filter by a simulated station. The new incident should have three excessive readings, then end after the configured recovery sequence. Earlier incidents remain visible.
3. **Disconnection:** during `stale_verified`, the selected live device has stopped reporting while its excessive-noise incident remains unresolved. Disconnection is not recovery.
4. **Daily reports:** select the station and today's local date. The report shows the saved simulated statistics, incident count, generation time and partial coverage. A few seconds of synthetic recordings do not represent a full monitored day. Earlier saved recordings at that station are also included, so the report total can be larger than this run's recording count.

The daily replay demonstration may have registered a second device at each location. Those replay devices intentionally remain silent after uploading historical recordings. Consequently, a location's combined status can show **Some devices not reporting** even while the selected live simulator device is fresh and sending correctly. Its marker retains the missing-data colour; an unresolved noise incident remains separately visible. Check the individual device stream, latest timestamp and reading. The runner verifies those individual persisted streams and does not delete or disable the replay devices to hide missing data. Once all current devices stop reporting, the label becomes **No recent data**.

At the end, all three tested live devices are normal and fresh. The sender then exits, so those devices become stale again after the normal timeout. This is expected.

## Stopping, repeating and troubleshooting

- Press **Ctrl+C** to stop. No background simulator is left running; already saved data remains. If you stop during excessive noise, its incident stays unresolved until a later valid recovery sequence.
- Repeat the same command to run again. It reuses the same registrations, uses new recording identifiers for the new demonstration, and appends a new real sequence of saved history. Within each run, deliberately retrying an identical recording proves upload deduplication. Repeating the entire demonstration intentionally adds new recordings and incidents.
- Only one demonstration can use these three fixture devices at a time. Do not run `demo-application.py` concurrently. The scripts share a fixture lock.
- A renamed, reassigned, disabled or incompatible fixture is rejected with a clear error. Review it in Management before retrying; the verification script will not silently reset those settings.
- If uploads work but processing times out, check that Docker's worker and database are running. If authentication fails, use the existing registration file and matching project `.env`; never paste either into chat.
- If no notifications appear, keep the application open before the test, check that it is connected, and inspect Incident history. Saved incident and event checks are distinct from seeing a transient notification in an open browser.
- To demonstrate yesterday's stored reports independently, use the documented [`demo-daily.py` replay](README.md). It deliberately stays outside the live freshness window so historical data does not create misleading current-noise notifications.

The `SYNTHETIC-STEP6-ONLY` calibration is applied only to these known simulated devices. It numerically anchors an exact threshold comparison and is captured with each recording. It neither changes old recordings nor calibrates an ESP microphone. Physical capture, firmware compilation, Wi-Fi testing and acoustic calibration have separate status in [HARDWARE-INTEGRATION.md](docs/HARDWARE-INTEGRATION.md).

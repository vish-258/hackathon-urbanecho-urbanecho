# UrbanEcho: repeatable local product demonstration

Prepared 9 October 2026. Audience: residential community managers reviewing the software prototype. Allow about five minutes, including an approximately 90-second live scenario. All inputs below are **generated recordings with synthetic calibration**, not physical noise measurements.

## Start and prepare

1. Start Docker Desktop on the backend Mac. Open a terminal in `~/Documents/Codex/2026-10-09/bu/outputs/noise-monitor`.
2. Keep the existing `.env` and private `.local` registrations. On this Apple Silicon Mac run:

   ```sh
   docker compose -f compose.yaml -f compose.amd64.yaml up -d
   docker compose -f compose.yaml -f compose.amd64.yaml ps
   ```

   For a first installation or an update requiring a new image, follow the [technical startup guide](TECHNICAL-GUIDE.md#start-the-local-application). Intel Mac users omit the AMD64 override. Never use `down --volumes` to restart this project.
3. Open [UrbanEcho](http://localhost:8000/app). It connects automatically on the backend Mac. Readiness should show `ready` at [the readiness address](http://localhost:8000/health/ready).
4. In **Overview**, identify the locations named **SIMULATED · North garden**, **SIMULATED · Workshop**, and **SIMULATED · East gate**. Their current thresholds are 60, 60 and 62 dB SPL (Z). The suffix distinguishes this saved fixture. Keep any other locations: the fourth, older synthetic location is unrelated to this run.
5. In a second terminal, from the same project folder, start the preserving simulator:

   ```sh
   python3 scripts/verify-simulator.py --focus-station workshop --hold-seconds 10
   ```

   It privately loads the existing credentials, uploads real generated WAV files through the normal API, and checks saved results. No token needs to be copied into the application or presentation. If the three simulator registrations are absent on a fresh installation, the helper provisions labelled fixtures. It refuses to overwrite incompatible existing rules.

## What to show, in order

Watch the phase names printed by the terminal. Keep the application open before the breach so its live opening notification is visible. A narrow window requires scrolling between the map and location cards.

| Phase | Screen action | Expected result with today's saved rules |
|---|---|---|
| `normal_verified` | Show Overview and the three location cards. | Garden 55, Workshop 55, Gate 57 dB SPL (Z). Three live fixture devices report. No new incident. |
| `equal_to_threshold` | Compare the latest values with each displayed threshold. | 60 / 60 / 62, exactly equal in saved precision. Equality does not create an incident. |
| `above_threshold` | Show Workshop's card, opening notification and map marker. | Workshop 70 against 60 opens one incident. Garden and Gate remain below their limits. Select Workshop's marker to see the saved location/value. |
| `sustained_excessive` | Show the Workshop map popup or location detail. | Two further 80 dB readings extend the same incident; its excessive-reading count becomes three. |
| `duplicate_and_audio_integrity_verified` | Point to the terminal result, then the unchanged incident. | Each device retries an identical recording. Recording identity and measurement/incident/event counts stay unchanged, and downloaded bytes match the upload. |
| `disconnect_wait`, then `stale_verified` | Open Workshop location detail and show freshness beside noise condition. | All three senders pause for 32 seconds with the default 30-second stale setting. Workshop becomes **No recent data**, while the excessive incident stays unresolved. Missing data is not recovery. |
| `recovery_reading_verified` | Return to Overview, then open the new incident from Incident history. | Three contiguous normal Workshop readings resolve the incident. Latest value returns to 55. History retains the peak of 80 and the three excessive readings. Recovery is visible through status/history; the main app only shows opening toasts. |
| `daily_summary_verified`, then `PASS` | Open Daily reports, choose Workshop and the current local date. | The completed saved report includes the new recordings. SIMULATED, Partial data, and Today · provisional labels remain visible. |

Three historical replay devices are also registered at these locations and are intentionally silent. Therefore **Some devices not reporting** and gray missing-data markers can coexist with a fresh live reading. A red incident ring/condition identifies an unresolved breach. The overview's four stale devices during the run include those replay devices and the older fixture. Explain this rather than claiming every registered device is connected.

## Show saved history and daily coverage

From Workshop's location page, show **Sound levels over time** and expand **View the saved readings**. Historical values retain their capture time, device identity and applicable threshold. Gaps remain empty. The global Incident history table displays UTC; location and incident-detail pages use the saved location timezone.

In **Daily reports**, select a location before choosing its local date. Changing location defaults the date to its previous completed day. Choose **9 October 2026** to reproduce the recorded presentation; choose the current date to inspect a new live run. The new run's reports contain existing history as well as its ten additional recordings per device.

For the recorded Workshop demonstration, the saved result was **75.49 dB SPL (Z), 134 eligible measurements, four incident starts, 134 usable seconds, and 0.1551% coverage**. Four is the day's total from saved history, not four incidents created by this run. Subsequent demonstrations intentionally change these totals. The average describes sampled time only, not a fully monitored day.

Use **Recalculate summary** to include later arrivals, then **Refresh saved results** or reload the page. The same report identity is updated. Yesterday's prepared **8 October 2026** data are also available at the three locations. They include invalid silence and a Gate recording crossing midnight, uploaded as historical replay; replay does not manufacture current alerts. The older fourth synthetic location's 8 October report demonstrates **No usable data**. On later dates, keep these fixed dates for the historical example; do not imply that old fixtures cover a new yesterday.

## Repeat and finish

Wait for `PASS` and the command to exit. With the current three-normal-reading recovery rule, one focused run adds **30 recordings and one new Workshop incident**, then resolves it. Repeating a whole scenario intentionally adds new history; that is different from retrying the same upload. The current rules and unrelated data stay intact. A prior unfinished incident may first be resolved by the baseline's genuine normal samples.

The sender stops after the run. Devices becoming stale again is expected. Do not run competing senders against these same registrations. If interrupted, repeat the command to settle its labelled incident and run a fresh scenario. If it refuses changed fixtures/rules, inspect that configuration instead of deleting records or resetting thresholds.

If Docker/API readiness fails, follow [troubleshooting](TECHNICAL-GUIDE.md#troubleshooting). If the notification was missed, show its saved incident or repeat the scenario with Overview already open. Base map tiles need internet access; readings and saved incidents remain local.

**Separate next milestone:** Real microphone → ESP32 → UrbanEcho → incident alert → daily summary. The current firmware has compiled, but flashing, physical transmission, calibration and independent acoustic validation remain pending. See the [hardware guide](../HARDWARE-INTEGRATION.md).

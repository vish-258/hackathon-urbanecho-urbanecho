# Three-location application demonstration

**Legacy demonstration reference, retained 9 October 2026.** For the current product walkthrough, use [Step 7's demonstration guide](docs/step7/DEMO-WALKTHROUGH.md) and `scripts/verify-simulator.py`. That runner preserves each fixture's current threshold. The older script described below deliberately resets its own simulated fixtures to 60 dB SPL (Z), so it is not the recommended choice when presenting the existing 60/60/62 configuration.

This optional, foreground demonstration creates **three explicitly simulated locations** around central Bangalore and sends synthetic WAV files through the real authenticated upload, database, worker, threshold, incident and event paths. It does not run automatically when the application starts or someone opens a page.

The fixture uses numerical **dB SPL(Z)** values with a test-only calibration offset. It is not a microphone calibration, a physical deployment, or dBA. All location names begin with `SIMULATED`, and each device identifies itself as a synthetic PCM24 fixture.

## Run it

Start the existing API, database and worker using the main README. Open `http://localhost:8000/app`; the local application connects automatically without a token. In a separate terminal, from this project folder:

```sh
set -a
. ./.env
set +a
python3 scripts/demo-application.py --duration 90
```

This reads the existing secret locally without putting it in command arguments or printing it. Do not paste `.env` or the generated credentials into chat, screenshots or source control. Python 3.9 or later is sufficient; no additional Python packages are required.

The private, reusable fixture file is `.local/application-demo.json` (mode `0600`). Its device tokens are only used to upload samples; no token appears in the output. Repeating the same command reuses the same three locations and devices, preserves all previous readings and incidents, starts a new upload session, and first resolves any incident left from the previous demonstration. It restores **only these simulated locations'** 60 dB SPL(Z) threshold and refreshes their test calibration if needed. Do not use a fixture that has been repurposed for real hardware.

For a separate independent demonstration, specify a new private file:

```sh
python3 scripts/demo-application.py --credentials .local/application-demo-second.json --duration 90
```

Use the default local address for this prototype. The ngrok tunnel and its local setup were removed on 9 October 2026; there is no current public endpoint. The normal application's automatic local access must remain on its loopback-bound service. If an operator deliberately tests another supported server, its URL must refer to the matching server for the chosen credentials file; that is outside this local walkthrough.

## What to expect

| Location | Real, saved demonstration behavior |
| --- | --- |
| SIMULATED · North garden | Sends approximately 52–56 dB SPL(Z) below its 60 dB threshold; remains recent and normal during the run. |
| SIMULATED · Workshop | Starts normal, then sends approximately 72–79 dB SPL(Z) after 15 seconds. A single incident opens and later excessive readings update that incident. |
| SIMULATED · East gate | Sends 75 dB SPL(Z), then three contiguous valid normal readings of approximately 59, 58 and 57 dB SPL(Z). Its incident resolves and the sender stops. After the configured stale period (normally 30 seconds), its last known normal state is also marked stale. |

Coordinates are geographically distinct and approximately a kilometre apart, so all three can be selected on the map. Each has the `Asia/Kolkata` timezone. Historical samples and incident details use the actual persisted readings and threshold version; no browser-only replacement data is involved.

At the end, the script checks each location's persisted status and prints `PASS` only if it sees normal/recent, excessive/recent, and normal/stale. It also checks the East gate resolution after three normal readings and confirms that every submitted file produced the expected live-eligible measurement.

The default presentation phase lasts **90 seconds**, plus setup and processing time. `--duration` accepts 50–600 seconds; `--tick` accepts 1–5 seconds between rounds (default 3). `--breach-after` controls when Workshop becomes excessive. The duration must exceed the server's stale period by at least 20 seconds. Press Control-C to stop early. Slow or unavailable processing causes a clear failure rather than fabricated success.

After the script finishes, **all senders stop**. North garden and Workshop will also become stale. Workshop's unresolved incident stays visible; losing contact does not resolve it. Re-run the command during a live walkthrough if recent green and red states are required. The script does not install a scheduler or background service.

## Suggested walkthrough

1. On Overview, find the three `SIMULATED` locations and use map markers and the location list to select them.
2. Watch Workshop open one notification and continue updating the same incident; inspect its latest reading and threshold.
3. Inspect East gate's resolved incident and then its stale state, with its last reading still visible.
4. Open a location's historical chart and incident history; refresh or reconnect and confirm the saved state remains consistent.
5. Open Daily reports, choose a location and today, and select Generate/Recalculate summary. The short live-demo recordings should produce a **simulated, provisional, partial-coverage** result. For repeatable recordings spanning yesterday and today, run `scripts/demo-daily.py` as documented in the README. Historical replay never fabricates current incident alerts.

This provides a repeatable software demonstration. Physical microphone connectivity, real calibration and environmental measurement accuracy require separate hardware validation. The three-second sending interval contains unsampled gaps between one-second recordings, which must not be treated as full-day coverage.

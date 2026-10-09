# UrbanEcho: current product status

**Checked 9 October 2026 · Asia/Kolkata · Working local prototype**

UrbanEcho can receive a sound recording, save it, calculate a sound level, compare it with the location's threshold, and show an incident when the level is excessive. Its web application brings the map, latest readings, notifications, history and daily summaries together. This complete software flow works with explicitly simulated recordings. The real ESP32 and microphone still need to be connected and validated.

The intended buyer is **residential community management**. No customer deployment, measured business benefit or commercial readiness is claimed. See the [business case](BUSINESS-CASE.md) for the proposed pilot and labelled assumptions.

## What works now

| Area | Current evidence |
|---|---|
| Upload and storage | Original WAV files, capture times, device identities and location attribution are saved; identical retries do not create duplicate records. |
| Processing and incidents | Strictly excessive readings create a saved incident; sustained noise updates it; the configured three normal readings complete recovery. Equality does not cause a breach. |
| Application | The real local browser showed the three simulated locations, changing map/readings, opening notification, resolved history and saved daily results during this task. |
| Daily reporting | Energy-based averages, recording counts, incident starts, usable time and coverage are saved. Manual recalculation and automatic scheduling exist; partial data and simulated results are labelled. |
| Device preparation | ESP-IDF C++ firmware for ESP-WROOM-32/INMP441 compiled previously. Physical configuration, flashing, capture, Wi-Fi reliability and acoustic calibration remain pending. |

Open **[UrbanEcho locally](http://localhost:8000/app)** with the local stack running. The application stays on the backend computer's localhost address. The optional device-only LAN HTTPS listener is prepared but **not enabled**; nothing was published to a public server.

## What we checked again for Step 7

Between **18:51 and 18:54 IST on 9 October**, a focused Workshop demonstration added **30 clearly labelled recordings**, ten per simulated device. Workshop rose to approximately **70 dB against a 60 dB threshold**, then reached approximately 80 dB. Three excessive readings stayed within one incident. After a 32-second pause, the device was stale and the incident remained unresolved; three contiguous normal recordings then resolved it. The other two locations gained no new incidents.

The live runner ended with **PASS**. The browser's map, latest readings, opening notification, history and reports were inspected against the saved results. All original 300 recordings remained intact, and **all 330 current recordings passed checksum verification**. Locations and devices stayed at **4 and 7**. Incidents increased from **10 to 11**, all resolved afterward. The **eight reports and eight summary identities** were preserved; today's results were updated in place.

Workshop's current report for 9 October displays **75.49 dB SPL (Z), 134 eligible recordings, four incident starts and 134 usable seconds**, matching the database. Its **0.1551% coverage** means it represents only those recorded moments. It is **simulated, partial and provisional for today**, and includes earlier demonstration data. It is not a full-day physical noise measurement.

**Five targeted simulator integration tests passed during this task.** The broader **382 backend tests, 34 frontend tests, two earlier three-location runs and ESP32 compilation** are recorded Step 6 results from 9 October; those full checks were not rerun for documentation. [New checks and evidence](VERIFICATION-CHECKS.md) · [Earlier Step 6 report](../../STEP6-VERIFICATION.md)

## Two separate readiness decisions

**Local software demonstration: ready.** Another person can demonstrate the working simulated flow using the documented runner. A stopped sender will become stale; that is expected and does not erase history.

**Physical monitoring with trustworthy sound levels: pending.** The next milestone is **real microphone → ESP32 → UrbanEcho → incident alert → daily summary**. Successful transmission proves connectivity and processing; it does not prove acoustic accuracy. Real calibration and independent comparison remain essential.

Before wider use, Step 8 must address acceptance evidence for physical runtime, timestamps and dropped recordings; storage growth and raw-audio retention; backup restoration; access/privacy arrangements; and larger-load reliability. Independent viewer accounts, tenant separation and automatic audio retention are not implemented. The local computer must stay awake, and the firmware's short memory queue can lose recordings during interruptions or power loss. [Final acceptance checklist](ACCEPTANCE-CHECKLIST.md)

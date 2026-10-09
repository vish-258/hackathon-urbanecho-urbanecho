# UrbanEcho demo video and narration

The accompanying [UrbanEcho-Demo.mp4](UrbanEcho-Demo.mp4) is an actual local-browser recording made on **9 October 2026**, approximately 2½ minutes long. It is **silent**, with an embedded English subtitle track and a separate [SRT file](UrbanEcho-Demo.srt). This script supplies optional spoken narration. Hardware is not shown or presented as tested.

The video uses the existing application's portrait viewport. The setup lead-in, transient frames caused by full-page screenshots, and one blank transition frame were excluded; the preceding valid capture stays visible until the next valid timestamp. No application values or alerts were fabricated. Full-page stills remain in [assets](assets/). The decode and format check is recorded in [video-verification.json](video-verification.json).

## Narration and shot list

Times are approximate cues within the exported recording. Use the [live walkthrough](DEMO-WALKTHROUGH.md) to repeat the scenario on another day; measured totals will then change.

| Time | Screen action / recorded shot | Suggested narration | Expected evidence |
|---|---|---|---|
| 0:00–0:15 | Overview shows three devices reporting, zero active incidents, and simulation notice. | “UrbanEcho gives residential community managers one place to review shared-space noise. This is our local software demonstration, using three simulated devices.” | Existing local app, SIMULATED notice, saved locations. Silent older replay devices explain the missing-data count. The runner also verifies exact threshold equality without a breach. |
| 0:16–0:33 | Workshop opening notification appears. | “Workshop has crossed its 60-decibel test threshold. The server has saved an incident, and the application displays its notification.” | 70 dB SPL (Z), threshold 60, one active incident, correct location. |
| 0:34–0:57 | Workshop map popup and location detail. | “Further excessive readings update this same incident. The map shows the saved measurement and location. Repeating the same upload does not create another record.” | 80 dB peak, same incident; duplicate checks are in the companion verification log, not exposed as a UI control. |
| 0:58–1:17 | Location history and stale state. | “Now the simulator stops sending. The reading becomes stale, but the unresolved incident stays visible. We never treat missing data as recovery or silence.” | No recent data and unresolved excessive condition coexist. Saved chart leaves gaps empty. |
| 1:18–1:40 | Normal readings return; overview has no unresolved incidents. | “The configured rule requires three consecutive normal recordings. After those arrive, the incident resolves and remains in history.” | Workshop 55 dB, zero unresolved incidents; saved incident later confirms three of three recovery samples. |
| 1:41–1:52 | Incident history. | “This is the saved incident. The list uses UTC; its detail uses the location's timezone.” | Resolved row with the same location and incident identity. |
| 1:53–2:15 | Saved incident detail. | “The record preserves the threshold, peak, and recovery evidence. Its elapsed incident window is different from the duration of sound we actually recorded.” | Resolved incident, three excessive readings, peak 80, threshold 60, and saved timestamps. The companion full-page still also shows all three normal recovery readings. |
| 2:16–end | Workshop daily report for 9 October. | “The saved report averages sound energy over the recordings we actually have. Here that is 134 seconds, about 0.155 percent of the day. It is simulated, partial, and provisional. Firmware has compiled; real microphone testing and acoustic calibration are still ahead.” | 75.49 dB SPL (Z), 134 measurements, four incident starts across the day, coverage labels, generation time. |

## Recording and inspection notes

- The actual run generated 30 recordings and one new resolved Workshop incident. Other incidents in the report predate this run.
- The video contains no microphone audio or spoken track; enable subtitles in the player, or read this narration live. Some players do not show embedded subtitles automatically; the separate SRT has the same name stem for sidecar loading.
- Frames covering the notification, map, stale state, recovery, incident history, incident detail and report were visually inspected. The final MP4 was fully decoded without errors.
- Still images show only the application, not terminal/private configuration. Device IDs are identifiers, not access tokens. No Wi-Fi password, administrator token, device credential or real audio recording is included.
- The proof is software behavior with generated inputs. Successful future transmission will prove connectivity/processing; accurate environmental sound measurements still require calibration and independent validation.

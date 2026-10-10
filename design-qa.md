# UrbanEcho concept-film theme — 10 October 2026

**final result: passed**

## Target and scope

Source visual truth: `/Users/aptyuser/Downloads/Urban-Echo-Concept.mp4`, a 90-second, 1920 × 1080 concept film. Frames at 3, 28, 55 and 87 seconds and a full-film contact sheet were inspected. Extracted reference frame: `/Users/aptyuser/Documents/Codex/2026-10-09/bu/work/urbanecho-theme/reference-3.png`.

Implementation: `http://localhost:8000/app`, the existing working application. This is a brand/theme adaptation, not a pixel-identical copy of the film's presentation slides. The application's navigation, actual location map, measurements, saved incidents, classification, reports and management forms remain functional. The film's simulated example readings and illustrated city scenes are not imported as application data or used to replace the real map.

## Evidence and normalization

Evidence folder: `/Users/aptyuser/Documents/Codex/2026-10-09/bu/work/urbanecho-theme/`.

- `comparison-v1.jpg`: initial source/overview/management comparison in one image.
- `comparison-final.jpg`: source and final browser-rendered overview together, with final Management, mobile overview and location-chart evidence.
- `brand-comparison.png`: original film wordmark and rendered app wordmark together at 2× inspection scale.
- `overview-desktop-final.png` and `management-desktop-final.png`: 1600 × 900 screenshots, 1600 × 900 CSS viewport. The source 1920 × 1080 and desktop 1600 × 900 images are normalized to 800 × 450 in the main paired comparison.
- `overview-mobile-final.png`, `management-mobile.png`: 390 × 845 CSS viewport, obtained using a 312 × 676 viewport override because this browser's existing page zoom yields a reported device pixel ratio of 0.8. Raw captures are 488 × 1056 and include unused right/bottom canvas; comparison copies crop that padding to the actual 390 × 845 rendered viewport without changing its content.
- `incidents-mobile.png`, `reports-mobile.png`: mobile filters, date/location controls and empty-result states.
- `incident-desktop-v2.png`, `location-chart-desktop-v2.png`: sound-category disclosure/audio and chart states. Final subsequent changes only refined eyebrow color/capitalization; these components are unchanged.

Browser viewport override was reset after responsive checks. Main comparison has different content/state by design: presentation film versus operational dashboard. It assesses visual identity, not matching chart values or slide proportions.

## Findings and fixes

1. **[P2, fixed] Dark form boundaries were too subtle.** Initial input borders had 1.49:1 contrast against their panel. Added a separate `--control-line: #718487` token for form controls and ordinary buttons, keeping structural dividers quiet. Final Management capture shows clearly identifiable empty fields.
2. **[P2, fixed] Taller sidebar identity could clip on short screens.** The three-line film tagline increased the sidebar's minimum content height. Added vertical overflow and non-shrinking navigation/brand sections. At 1280 × 600 CSS pixels, the sidebar had 628px of content and `overflow-y: auto`; keyboard navigation reached the footer documentation link and moved onward successfully.
3. **[P2, fixed] Detail-page eyebrow text retained the previous muted treatment.** Applied sage color and uppercase consistently across page headings; final DOM/computed-style checks confirm the shared token.

No remaining actionable P0/P1/P2 findings after the final combined visual comparison.

## Required fidelity surfaces

- **Typography:** bold warm-ivory display headings, smaller sage uppercase eyebrows and muted supporting text reproduce the film's hierarchy. Arial/Helvetica/sans-serif uses locally available fonts; the exact source font file is unavailable. Headings remain sized for a working dashboard, not a full-screen presentation slide.
- **Spacing/layout:** restrained 10–12px panel corners, thin rules and generous section gaps carry into existing responsive layouts. Four desktop statistic columns become two on mobile; management forms stack. Data tables/charts keep their intended internal horizontal scrolling. No document-level horizontal overflow at 390px or 1600px; short desktop sidebar remains reachable.
- **Colors/tokens:** canvas `#0d1b21` is sampled from the actual film; slate panels, warm ivory, sage accents and coral attention states are consistent. Secondary text has 7.27:1 contrast against the main panel. Chart series retain distinct colors/dashes, with a coral threshold and readable dark-theme axes.
- **Images/icons:** the logo and favicon waveform are actual frame crops, not redraws. Their aspect ratios and opaque background are preserved. The sidebar wordmark displays at 190px from a 224px original; it is not enlarged. Standard controls use locally served Tabler icons with the MIT license included. Map tiles remain from the existing OpenStreetMap integration, with presentation-only toning and dark controls/popups.
- **Copy/content:** the film's “Noise, made visible.” and “Measure. Understand. Respond.” identity copy is used. Actual product labels, units, calibration warnings, data-quality explanations and category-estimate limitations are preserved. No new notifications or simulated data introduced.

## Functional verification

- All **151 existing JavaScript tests passed** after the shell/chart restyle; the final application-specific tests also passed after the chart accessibility-label adjustment.
- Overview, location details, incident history, incident details, daily reports and all three Management tabs were opened.
- Verified map location selection and popup close; incident location filtering and detail navigation; daily location/date selection and refresh of saved results, including “No usable data” and “No saved summary yet”. No summaries or settings were changed for the design check.
- Saved 10-second recording loaded successfully (`readyState: 4`, duration 10s); an 18-second whole-incident recording loaded successfully (`readyState: 4`, duration 18s). Audio was not autoplayed. YAMNet details expand correctly.
- Observed live updates and persisted real-device readings. Recording collection/disabled states, recovery/status badges, form active states, audio controls, and loading/reconnecting states retain readable styling.
- No browser warnings/errors after the final reload during the final verification run. Earlier brief reconnects corresponded to the intentional API-only restarts.
- Only the `api` image/container was rebuilt/recreated. Device listener, worker, classifier and database stayed running. No migrations, credentials, firmware, database records, thresholds or classification behavior changed.

## Remaining limitations / optional polish

- The source logo is a video raster crop; an original vector brand file would improve sharpness on very high-density displays and allow future large-format usage.
- QA used the local Codex browser at desktop/mobile/short-screen sizes, not physical phone browsers. This visual change does not establish hardware/acoustic accuracy.

Implementation checklist: original logo extracted; shared theme applied; controls/charts/maps/audio covered; desktop/mobile/short-screen checks completed; functional tests passed; no outstanding blocking design findings.

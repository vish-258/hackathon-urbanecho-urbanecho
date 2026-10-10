# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Urban Echo serves staff responsible for monitoring noise, investigating incidents, and choosing an appropriate response. Community monitoring and street monitoring have equal priority, confirmed by the product owner on 10 October 2026.

The existing community use case includes facilities managers and security supervisors; residential management committees evaluate the service. Residents are beneficiaries of this workflow. A resident-facing app has not been established as a requirement.

Open decision: the specific operating roles, responsibilities, and permissions for street monitoring. Equal priority does not establish a police-only audience or authorize automated enforcement.

## Product Purpose

Make environmental noise understandable by connecting locations, devices, measured sound levels, incident history, and available recordings.

The primary success criterion, confirmed by the product owner, is to help staff investigate incidents and choose a response. Future changes should make it easier to understand what happened, where and when it happened, what evidence is available, and what remains uncertain.

## Operating Context

The current product is a working local browser application and hardware prototype. The local computer and services must remain running for ingestion and monitoring. Production deployment and commercial validation remain open.

The main workflows are:

- Review the overview for active incidents, device attention, and locations that need investigation.
- Open a location to inspect its measurements, history, and available recordings.
- Search and filter incident history, then examine the saved threshold rule, recording coverage, audio, and estimated sound category.
- Generate or recalculate a daily report for a selected local date and assess its measurement coverage.
- Manage locations, device assignments, and versioned threshold rules.

The overview and incident history carry monitoring state within the app. Notification popups are not part of the current experience.

## Capabilities and Constraints

### Measurements and incident history

- Digital amplitude in dBFS and calibrated sound pressure in dB SPL (Z) are distinct measurement types. Neither should be relabeled as dBA. SPL requires valid calibration for the particular measurement chain.
- A threshold breach requires a value strictly greater than the threshold. Equality does not breach it. Recovery requires the configured sequence of usable, compatible normal readings.
- Missing data, invalid audio, and stale contact do not establish quiet conditions or resolve an incident.
- Device contact freshness, usable measurements, and calibration are separate states. A connected device can still have unusable recordings.
- Historical incidents retain the rules and assignments applicable at the time. Configuration changes must preserve that history.

### Recordings, classification, and reports

- Two ESP32/INMP441 devices have supplied PCM16 audio through the authenticated local HTTPS listener. Verified ten-second WAVs have been assembled from original pieces. The devices remain uncalibrated, and gaps can occur.
- Complete, verified ten-second recording groups are playable. Incomplete groups remain explicit; missing pieces must never be filled with invented silence.
- Incident playback includes available audio before and after the event, with gaps and coverage made clear. Refreshing or recalculating data preserves the recording version already playing.
- Local YAMNet classification estimates Traffic, Horn, Siren, Construction, Music, Animal, Voice, or Other. These are fallible estimates; scores are not guaranteed probabilities. Voice classification does not provide transcription or speaker identification.
- Daily reports describe measured coverage. Late-arriving data can require recalculation; an uncovered period cannot support a claim about noise during that period.
- Audio processing is local and original recordings are retained. Automatic retention or deletion policies are not implemented.

### Scope and open decisions

The current application does not establish certified acoustic accuracy, role-based user accounts, tenant separation, or a production service. Resident messaging and patrol follow-up shown in the concept film are future concepts, rather than implemented response workflows.

Open decisions include the concrete response actions and escalation process for each setting, street-monitoring roles, production deployment, recording retention and access policy, commercial terms, and any required accessibility standard. Future work must establish these facts before presenting them as available capabilities or commitments.

## Brand Commitments

Preserve the Urban Echo identity and use the supplied waveform and wordmark assets according to their usage notes. Existing product language includes “Noise, made visible.” and “Measure. Understand. Respond.”

Asset provenance, proportions, and source-size limitations are documented in [the brand asset notes](app/static/application/brand/README.md). Product language should describe measurements, estimates, and uncertainty accurately.

## Evidence on Hand

- The product owner's 10 October 2026 confirmation: community and street monitoring have equal priority; incident investigation and response are the main success criterion.
- [README.md](README.md): current implementation, hardware integration, measurement semantics, recording behavior, and local operating requirements.
- [APPLICATION.md](APPLICATION.md): application workflows and architecture. Some hardware integration passages predate the current README.
- [Business-case notes](docs/step7/BUSINESS-CASE.md): the documented community audience and proposed commercial direction. Earlier implementation status and commercial proposals need current confirmation.
- [Brand assets and provenance](app/static/application/brand/README.md): supplied concept-film identity. Film scenarios illustrate a direction and do not prove deployed customer outcomes.
- The running application and locally verified device recordings demonstrate prototype behavior. They do not establish field accuracy, customer adoption, or commercial results.

When older status documents conflict with current behavior, verify against the implementation and current README. Do not turn concept scenarios, proposed pilots, or estimates into customer evidence.

## Product Principles

1. **Support a decision.** Prioritize the evidence staff need to investigate an incident and choose an appropriate response.
2. **Serve both settings.** Give community and street monitoring equal weight; avoid assumptions that make either setting an afterthought.
3. **Make uncertainty visible.** Keep missing coverage, stale contact, calibration limits, and estimated classifications understandable.
4. **Preserve the record.** Keep original evidence and historical rule context trustworthy as settings and incoming data change.
5. **Protect continuity.** Live updates should preserve the user's focus, investigation context, and active playback.

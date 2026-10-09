"""Local-calendar reports from immutable capture and processing records.

Aggregation is deliberately independent of the live-alert evaluator. Every run
uses only the latest persisted result of a recording, and never invents silence
for a gap. The caller owns the report lease and a repeatable-read transaction.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
import math
from types import SimpleNamespace
import uuid
from zoneinfo import ZoneInfo

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.dialects.postgresql import insert

from app.models import (AudioChunk, DailyReport, DailySummary, DeviceAssignment,
                        Incident, Location, Measurement, MeasurementEvaluation)
from app.processing import _calibration_offset, processing_version_for

AGGREGATION_VERSION = "duration_energy_mean_active_devices_v1"


def day_bounds(reporting_date: date, timezone_name: str) -> tuple[datetime, datetime]:
    """Return half-open UTC bounds, including 23/25-hour daylight-saving days."""
    zone = ZoneInfo(timezone_name)
    start = datetime.combine(reporting_date, time.min, zone).astimezone(timezone.utc)
    end = datetime.combine(reporting_date + timedelta(days=1), time.min, zone).astimezone(timezone.utc)
    if end <= start:
        raise ValueError("This local calendar date has no elapsed time in its timezone")
    return start, end


def source_kind(chunk=None, measurement=None, *, location_snapshot=None) -> str:
    """Keep explicit synthetic evidence even after calibration reprocessing.

    'recorded' means no persisted simulation marker; it does not independently
    certify that physical hardware or an accredited calibration was used.
    """
    snapshot = location_snapshot or (chunk.location_snapshot if chunk is not None else {}) or {}
    name = str(snapshot.get("name", "")).strip().upper()
    if name.startswith(("SIMULATED", "SYNTHETIC")):
        return "simulated"
    for calibration in (getattr(chunk, "calibration", None),
                        getattr(measurement, "calibration_snapshot", None)):
        if isinstance(calibration, dict) and "SYNTHETIC" in str(calibration.get("version", "")).upper():
            return "simulated"
    return "recorded"


def definition_for(chunk, measurement=None) -> dict:
    method = measurement.measurement_type if measurement is not None else chunk.threshold_type
    return {
        "measurement_type": method,
        "weighting": measurement.weighting if measurement is not None else ("Z" if method == "spl_z_leq" else "none"),
        "channel_policy": measurement.channel_policy if measurement is not None else "mono",
        "processing_version": measurement.processing_version if measurement is not None else processing_version_for(chunk),
        "source_kind": source_kind(chunk, measurement),
        "unit": "dB SPL (Z)" if method == "spl_z_leq" else "dBFS",
        "aggregation_method": AGGREGATION_VERSION,
        "device_id": str(chunk.device_id) if method == "dbfs_rms" else None,
    }


def definition_key(definition: dict) -> str:
    return hashlib.sha256(json.dumps(definition, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def default_definition(location: Location) -> dict:
    """Definition placeholder for a location that has no captured history."""
    placeholder = SimpleNamespace(threshold_type=location.threshold_type, device_id=None,
                                  location_snapshot={}, calibration=None)
    definition = definition_for(placeholder)
    definition["device_id"] = None
    return definition


def exclusion_reason(chunk, measurement, assignment) -> str | None:
    """Validate capture-time evidence; current device/rule settings are irrelevant."""
    if chunk.status != "completed":
        return f"recording_{chunk.status}"
    if measurement is None:
        return "measurement_missing"
    if (assignment is None or assignment.device_id != chunk.device_id
            or assignment.location_id != chunk.location_id
            or assignment.location_snapshot != chunk.location_snapshot
            or chunk.captured_at < assignment.effective_at):
        return "historical_assignment_invalid"
    end = chunk.captured_at + timedelta(seconds=chunk.duration_seconds)
    if assignment.ended_at is not None and end > assignment.ended_at:
        return "recording_crosses_assignment_change"
    if measurement.measured_at != chunk.captured_at:
        return "capture_time_mismatch"
    tolerance = max(1 / chunk.sample_rate, .001)
    if (not math.isfinite(measurement.interval_seconds)
            or abs(measurement.interval_seconds - chunk.duration_seconds) > tolerance
            or abs(chunk.duration_seconds - chunk.interval_seconds) > tolerance):
        return "interval_mismatch"
    if measurement.quality_status != "good":
        return f"quality_{measurement.quality_status}"
    if measurement.value_db is None:
        return "missing_value"
    if not math.isfinite(measurement.value_db):
        return "invalid_numeric_value"
    if measurement.measurement_type != chunk.threshold_type:
        return "measurement_type_mismatch"
    expected_weighting = "Z" if measurement.measurement_type == "spl_z_leq" else "none"
    if measurement.weighting != expected_weighting or measurement.channel_policy != "mono":
        return "incompatible_measurement_definition"
    if not measurement.processing_version:
        return "processing_definition_missing"
    lower, upper = (-100, 200) if measurement.measurement_type == "spl_z_leq" else (-200, 0)
    if not lower <= measurement.value_db <= upper:
        return "value_out_of_range"
    if measurement.measurement_type == "spl_z_leq":
        snapshot = SimpleNamespace(calibration=measurement.calibration_snapshot,
                                   captured_at=chunk.captured_at, sample_rate=chunk.sample_rate,
                                   duration_seconds=chunk.duration_seconds)
        calibration = _calibration_offset(snapshot)
        if (measurement.calibration_status != "calibrated" or calibration is None
                or calibration[1] != measurement.calibration_version):
            return "calibration_invalid_or_inapplicable"
    elif measurement.calibration_status != "not_required":
        return "calibration_status_incompatible"
    return None


@dataclass(frozen=True)
class EligibleInterval:
    start: datetime
    end: datetime
    value_db: float
    device_id: uuid.UUID


def aggregate_intervals(intervals: list[EligibleInterval], day_seconds: float) -> dict:
    """Integrate the mean active-device energy over the union of valid coverage.

    Repeated overlapping chunks from one device receive no additional device
    weight. Simultaneous devices are averaged spatially, never added as sources.
    A whole-chunk equivalent level is assumed uniform when clipping at midnight.
    """
    if not intervals:
        return dict(average_db=None, minimum_db=None, maximum_db=None, measurement_count=0,
                    device_count=0, usable_duration_seconds=0.0, recorded_duration_seconds=0.0,
                    day_duration_seconds=day_seconds, coverage_percent=0.0,
                    missing_duration_seconds=day_seconds, overlap_seconds=0.0, coverage_status="no_data")
    reference = max(item.value_db for item in intervals)
    events = defaultdict(list)
    recorded_seconds = 0.0
    for index, item in enumerate(intervals):
        energy = 10 ** ((item.value_db - reference) / 10)
        events[item.start].append((index, item.device_id, energy, True))
        events[item.end].append((index, item.device_id, energy, False))
        recorded_seconds += (item.end - item.start).total_seconds()
    active = defaultdict(dict)
    energy_seconds = 0.0
    covered_seconds = 0.0
    boundaries = sorted(events)
    for index, boundary in enumerate(boundaries[:-1]):
        for identity, device, energy, adding in events[boundary]:
            if adding:
                active[device][identity] = energy
            else:
                active[device].pop(identity, None)
                if not active[device]:
                    del active[device]
        if not active:
            continue
        seconds = (boundaries[index + 1] - boundary).total_seconds()
        device_energies = [math.fsum(values.values()) / len(values) for values in active.values()]
        energy_seconds += seconds * math.fsum(device_energies) / len(device_energies)
        covered_seconds += seconds
    average = reference + 10 * math.log10(energy_seconds / covered_seconds)
    return dict(average_db=average, minimum_db=min(item.value_db for item in intervals),
                maximum_db=reference, measurement_count=len(intervals),
                device_count=len({item.device_id for item in intervals}),
                usable_duration_seconds=covered_seconds, recorded_duration_seconds=recorded_seconds,
                day_duration_seconds=day_seconds, coverage_percent=100 * covered_seconds / day_seconds,
                missing_duration_seconds=max(0.0, day_seconds - covered_seconds),
                overlap_seconds=max(0.0, recorded_seconds - covered_seconds),
                coverage_status="complete" if abs(covered_seconds - day_seconds) < 1e-6 else "partial")


def _incident_scopes(session, report) -> tuple[Counter, dict]:
    counts = Counter()
    definitions = {}
    evidence_id = (select(Measurement.id).join(AudioChunk, AudioChunk.id == Measurement.audio_chunk_id)
                   .outerjoin(MeasurementEvaluation, MeasurementEvaluation.measurement_id == Measurement.id)
                   .where(AudioChunk.device_id == Incident.device_id,
                          AudioChunk.location_id == Incident.location_id,
                          AudioChunk.captured_at == Incident.started_at,
                          or_(Incident.stream_id.is_(None),
                              and_(MeasurementEvaluation.stream_id == Incident.stream_id,
                                   MeasurementEvaluation.breach.is_(True))))
                   .order_by(Measurement.result_order).limit(1).correlate(Incident).scalar_subquery())
    rows = session.execute(select(Incident, AudioChunk, Measurement).select_from(Incident)
                           .outerjoin(Measurement, Measurement.id == evidence_id)
                           .outerjoin(AudioChunk, AudioChunk.id == Measurement.audio_chunk_id)
                           .where(Incident.location_id == report.location_id,
                                  Incident.started_at >= report.day_start_utc,
                                  Incident.started_at < report.day_end_utc)).all()
    for incident, chunk, measurement in rows:
        kind = (source_kind(chunk, measurement) if chunk is not None
                else source_kind(location_snapshot=incident.location_snapshot))
        device = str(incident.device_id) if incident.threshold_type == "dbfs_rms" else None
        scope = (kind, incident.threshold_type, device)
        counts[scope] += 1
        if chunk is not None:
            definition = definition_for(chunk, measurement)
        else:
            placeholder = SimpleNamespace(threshold_type=incident.threshold_type,
                                          device_id=incident.device_id,
                                          location_snapshot=incident.location_snapshot, calibration=None)
            definition = definition_for(placeholder)
            definition["processing_version"] = "incident-provenance-unavailable"
        definitions.setdefault(scope, definition)
    return counts, definitions


def generate_report(session, report: DailyReport, now: datetime) -> list[DailySummary]:
    """Atomically upsert every definition, deleting obsolete result groups.

    Does not commit, change the request state, evaluate thresholds, or emit events.
    Caller must hold the DailyReport row lock and verify its lease token.
    """
    start, end = report.day_start_utc, report.day_end_utc
    day_seconds = (end - start).total_seconds()
    latest_id = (select(Measurement.id).where(Measurement.audio_chunk_id == AudioChunk.id)
                 .order_by(Measurement.result_order.desc()).limit(1).correlate(AudioChunk).scalar_subquery())
    rows = session.execute(
        select(AudioChunk, Measurement, DeviceAssignment).select_from(AudioChunk)
        .outerjoin(Measurement, Measurement.id == latest_id)
        .outerjoin(DeviceAssignment, DeviceAssignment.id == AudioChunk.assignment_id)
        .where(AudioChunk.location_id == report.location_id,
               AudioChunk.captured_at >= start - timedelta(seconds=600), AudioChunk.captured_at < end)
    ).all()
    groups = {}
    for chunk, measurement, assignment in rows:
        clip_start = max(start, chunk.captured_at)
        clip_end = min(end, chunk.captured_at + timedelta(seconds=chunk.duration_seconds))
        if clip_end <= clip_start:
            continue
        definition = definition_for(chunk, measurement)
        key = definition_key(definition)
        group = groups.setdefault(key, {"definition": definition, "intervals": [], "excluded": Counter()})
        reason = ("capture_not_completed_at_calculation"
                  if chunk.captured_at + timedelta(seconds=chunk.duration_seconds) > now
                  else exclusion_reason(chunk, measurement, assignment))
        if reason:
            group["excluded"][reason] += 1
        else:
            group["intervals"].append(EligibleInterval(clip_start, clip_end, measurement.value_db, chunk.device_id))
    if not groups:
        # One latest immutable capture per device/type supplies honest labels for
        # empty days without reading a device's mutable current calibration.
        historical = session.scalars(select(AudioChunk).where(AudioChunk.location_id == report.location_id)
                                     .distinct(AudioChunk.device_id, AudioChunk.threshold_type)
                                     .order_by(AudioChunk.device_id, AudioChunk.threshold_type,
                                               AudioChunk.captured_at.desc(), AudioChunk.id)).all()
        definitions = [definition_for(chunk) for chunk in historical]
        if not definitions:
            definitions = [default_definition(session.get(Location, report.location_id))]
        for definition in definitions:
            groups.setdefault(definition_key(definition), {"definition": definition, "intervals": [], "excluded": Counter()})
    counts, incident_definitions = _incident_scopes(session, report)
    present_scopes = {(group["definition"]["source_kind"], group["definition"]["measurement_type"],
                       group["definition"]["device_id"]) for group in groups.values()}
    # An invalid/reclassified latest result may remove every current recording
    # from an old source definition. Its already-saved incident still happened.
    for scope, definition in incident_definitions.items():
        if scope not in present_scopes:
            groups.setdefault(definition_key(definition),
                              {"definition": definition, "intervals": [], "excluded": Counter()})
    summaries = []
    for key, group in sorted(groups.items()):
        definition = group["definition"]
        statistics = aggregate_intervals(group["intervals"], day_seconds)
        statistics.update(
            incident_count=counts[(definition["source_kind"], definition["measurement_type"], definition["device_id"])],
            incident_count_scope="Saved incidents started in this location and local day with this source kind and measurement type; digital levels are also device-specific. Counts repeat across processing versions; do not add definition cards.",
            excluded_count=sum(group["excluded"].values()), exclusion_reasons=dict(sorted(group["excluded"].items())),
            is_provisional=now < end,
        )
        values = dict(id=uuid.uuid4(), report_id=report.id, location_id=report.location_id,
                      reporting_date=report.reporting_date, definition_key=key, definition=definition,
                      statistics=statistics, calculated_at=now)
        statement = insert(DailySummary).values(**values)
        session.execute(statement.on_conflict_do_update(
            constraint="uq_daily_summary_definition",
            set_={name: statement.excluded[name] for name in ("report_id", "definition", "statistics", "calculated_at")},
        ))
    session.execute(delete(DailySummary).where(DailySummary.report_id == report.id,
                                               DailySummary.definition_key.not_in(list(groups))))
    session.flush()
    summaries = session.scalars(select(DailySummary).where(DailySummary.report_id == report.id)
                               .order_by(DailySummary.definition_key).execution_options(populate_existing=True)).all()
    return summaries

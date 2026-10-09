"""Committed-watermark threshold evaluation; caller owns the transaction.

All live/configuration writers acquire the durable-event clock before device
locks. No function here commits. Audio, results, evaluation, incident state and
outbox entries therefore become visible together or roll back together.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from numbers import Real
from typing import Any

from sqlalchemy import select

from app import clock
from app.config import get_settings
from app.events import emit_event, lock_event_clock
from app.models import (AudioChunk, Device, DeviceAssignment, Evaluation, Incident,
                        Location, Measurement, StreamState, ThresholdVersion)


class MeasurementConflict(ValueError):
    """An existing result identifier was reused with different content."""


def _number(value: Any) -> bool:
    return isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(value)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("measurement timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


def _canonical(value: Any) -> Any:
    if isinstance(value, datetime):
        return _utc(value).isoformat()
    if isinstance(value, dict):
        return {str(key): _canonical(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_canonical(item) for item in value]
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    return str(value)


def _fingerprint(measurement: Measurement, chunk: AudioChunk) -> str:
    fields = ("audio_chunk_id", "measured_at", "received_at", "interval_seconds", "value_db",
              "digital_dbfs", "measurement_type", "calibration_status", "calibration_version",
              "processing_version", "weighting", "channel_policy", "quality_status", "result_version",
              "is_reprocessing", "calibration_snapshot")
    source = ("id", "device_id", "location_id", "assignment_id", "location_snapshot", "captured_at",
              "received_at", "session_id", "sequence", "duration_seconds", "sample_rate", "checksum")
    data = {"measurement": {field: getattr(measurement, field) for field in fields},
            "source": {field: getattr(chunk, field) for field in source}}
    # Float columns round-trip Python integers as floats; normalize only those
    # fields, retaining full integer precision for sequences and identifiers.
    for field in ("interval_seconds", "value_db", "digital_dbfs"):
        if _number(data["measurement"][field]):
            data["measurement"][field] = float(data["measurement"][field])
    data["source"]["duration_seconds"] = float(data["source"]["duration_seconds"])
    return hashlib.sha256(json.dumps(_canonical(data), sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def persist_measurement(session, chunk: AudioChunk, result: Any, result_version: str = "initial",
                        is_reprocessing: bool = False) -> Measurement:
    """Store a versioned result, or return an identical result without replay.

    Unsupported numeric objects are rejected explicitly before PostgreSQL can
    coerce strings or booleans to floats. The accepted original audio remains.
    Null is preserved as a diagnostic result, never interpreted as zero.
    """
    if not isinstance(result_version, str) or not result_version.strip() or len(result_version) > 100:
        raise ValueError("result_version must be a nonempty string of at most 100 characters")
    data = result.as_dict() if hasattr(result, "as_dict") else dict(result)
    for name in ("value_db", "digital_dbfs"):
        value = data.get(name)
        if value is not None and not _number(value):
            raise ValueError(f"{name} must be a strict finite number or null")
    if data.get("measurement_type") not in ("dbfs_rms", "spl_z_leq"):
        raise ValueError("unsupported measurement_type")
    if not _number(chunk.duration_seconds) or not 0 < chunk.duration_seconds <= 600:
        raise ValueError("unsupported measurement interval")
    _utc(chunk.captured_at)
    with session.no_autoflush:
        lock_event_clock(session)
        session.execute(select(Device.id).where(Device.id == chunk.device_id).with_for_update())
    data["breach"] = False
    data.setdefault("weighting", "none" if data["measurement_type"] == "dbfs_rms" else "Z")
    data.setdefault("channel_policy", "mono")
    data.setdefault("quality_status", "good" if data.get("value_db") is not None else "invalid")
    proposed = Measurement(audio_chunk_id=chunk.id, measured_at=chunk.captured_at,
                          received_at=chunk.received_at, interval_seconds=chunk.duration_seconds,
                          result_version=result_version, is_reprocessing=is_reprocessing,
                          calibration_snapshot=chunk.calibration, **data)
    digest = _fingerprint(proposed, chunk)
    existing = session.scalar(select(Measurement).where(Measurement.audio_chunk_id == chunk.id,
                                                       Measurement.result_version == result_version))
    if existing is not None:
        if (existing.content_hash or _fingerprint(existing, chunk)) != digest:
            raise MeasurementConflict("result version already exists with different measurement content")
        return existing
    proposed.content_hash = digest
    session.add(proposed)
    session.flush()
    return proposed


def _rule(session, chunk: AudioChunk) -> ThresholdVersion | None:
    return session.scalar(select(ThresholdVersion).where(ThresholdVersion.location_id == chunk.location_id,
                          ThresholdVersion.effective_at <= chunk.captured_at)
                          .order_by(ThresholdVersion.effective_at.desc()).limit(1))


def _diagnostic(session, measurement, chunk, device, assignment, rule) -> str | None:
    if device is None:
        return "unknown_device"
    if not device.enabled:
        return "disabled_device"
    if session.get(Location, chunk.location_id) is None:
        return "unknown_location"
    if (assignment is None or assignment.device_id != chunk.device_id
            or assignment.location_id != chunk.location_id
            or assignment.location_snapshot != chunk.location_snapshot
            or chunk.captured_at < assignment.effective_at
            or (assignment.ended_at is not None and chunk.captured_at >= assignment.ended_at)):
        return "invalid_assignment"
    if measurement.measured_at != chunk.captured_at:
        return "capture_time_mismatch"
    if rule is None:
        return "threshold_unavailable"
    if measurement.measurement_type != rule.threshold_type:
        return "measurement_type_mismatch"
    if measurement.weighting != rule.weighting:
        return "weighting_mismatch"
    if measurement.channel_policy != rule.channel_policy:
        return "channel_policy_mismatch"
    if (not _number(measurement.interval_seconds) or measurement.interval_seconds <= 0
            or abs(measurement.interval_seconds - rule.interval_seconds) > max(1 / chunk.sample_rate, .001)):
        return "interval_mismatch"
    if measurement.quality_status != "good":
        return f"quality_{measurement.quality_status}"
    if measurement.value_db is None:
        return "missing_value"
    if not _number(measurement.value_db):
        return "invalid_numeric_value"
    lower, upper = (-200, 0) if rule.threshold_type == "dbfs_rms" else (-100, 200)
    if not lower <= measurement.value_db <= upper:
        return "value_out_of_range"
    if rule.threshold_type == "spl_z_leq":
        from app.processing import _calibration_offset
        from types import SimpleNamespace
        calibrated_source = SimpleNamespace(calibration=measurement.calibration_snapshot,
            audio_format=chunk.audio_format, sample_rate=chunk.sample_rate,
            captured_at=chunk.captured_at, duration_seconds=chunk.duration_seconds)
        calibration = _calibration_offset(calibrated_source)
        if (measurement.calibration_status != "calibrated" or calibration is None
                or calibration[1] != measurement.calibration_version):
            return "calibration_invalid_or_inapplicable"
    elif measurement.calibration_status != "not_required":
        return "calibration_status_incompatible"
    return None


def _payload(measurement, chunk, rule, stream=None, incident=None, reason=None) -> dict:
    location = dict(chunk.location_snapshot)
    if incident is not None and incident.location_snapshot:
        location = dict(incident.location_snapshot)
    payload = dict(location=location, latitude=location.get("latitude"), longitude=location.get("longitude"),
                   measurement_id=str(measurement.id), measurement_value=measurement.value_db,
                   measurement_type=measurement.measurement_type, weighting=measurement.weighting,
                   channel_policy=measurement.channel_policy, interval_seconds=measurement.interval_seconds,
                   measured_at=measurement.measured_at.isoformat(), threshold_value=rule.threshold_value if rule else None,
                   threshold_version_id=str(rule.id) if rule else None,
                   incident_status=incident.status if incident else None, transition_reason=reason,
                   diagnostic=reason if stream is not None and stream.data_status == "invalid" else None,
                   noise_status=stream.noise_status if stream else "unknown",
                   data_status=stream.data_status if stream else "fresh")
    payload.update(stream_id=str(stream.id) if stream else None,
                   assignment_id=str(chunk.assignment_id) if chunk.assignment_id else None,
                   location_name=location.get("name"), received_at=measurement.received_at.isoformat(),
                   threshold_revision=rule.revision if rule else None,
                   recovery_count=rule.recovery_count if rule else None,
                   quality_status=measurement.quality_status, calibration_status=measurement.calibration_status)
    if incident is not None:
        payload.update(peak_db=incident.peak_db, latest_db=incident.latest_db,
                       breach_count=incident.breach_count, recovery_streak=incident.recovery_streak,
                       started_at=incident.started_at.isoformat(),
                       ended_at=incident.ended_at.isoformat() if incident.ended_at else None,
                       last_occurrence_at=incident.last_occurrence_at.isoformat() if incident.last_occurrence_at else None,
                       previous_incident_id=str(incident.previous_incident_id) if incident.previous_incident_id else None)
    payload["measurement"] = {"id": str(measurement.id), "value_db": measurement.value_db,
                              "measurement_type": measurement.measurement_type, "weighting": measurement.weighting,
                              "channel_policy": measurement.channel_policy, "interval_seconds": measurement.interval_seconds,
                              "measured_at": measurement.measured_at.isoformat()}
    payload["threshold"] = None if rule is None else dict(id=str(rule.id), revision=rule.revision,
                            threshold_value=rule.threshold_value, threshold_type=rule.threshold_type,
                            interval_seconds=rule.interval_seconds, recovery_count=rule.recovery_count)
    payload["incident"] = None if incident is None else dict(id=str(incident.id), status=incident.status,
                            peak_db=incident.peak_db, latest_db=incident.latest_db, breach_count=incident.breach_count,
                            recovery_streak=incident.recovery_streak, closed_reason=incident.closed_reason,
                            previous_incident_id=str(incident.previous_incident_id) if incident.previous_incident_id else None,
                            started_at=incident.started_at.isoformat(),
                            ended_at=incident.ended_at.isoformat() if incident.ended_at else None)
    return payload


def _emit(session, event_type, measurement, chunk, rule, stream, incident=None, reason=None):
    return emit_event(session, event_type, _payload(measurement, chunk, rule, stream, incident, reason),
                      incident_id=incident.id if incident else None, device_id=chunk.device_id,
                      location_id=incident.location_id if incident else chunk.location_id)


def _interrupt(session, measurement, chunk, settings, now, reason):
    """Fresh invalid samples break recovery without advancing eligible watermarks."""
    if chunk.captured_at > now or now - chunk.captured_at > timedelta(seconds=settings.live_freshness_seconds):
        return
    device = session.get(Device, chunk.device_id)
    if device is None or device.current_assignment_id != chunk.assignment_id:
        return
    streams = session.scalars(select(StreamState).where(StreamState.device_id == chunk.device_id,
                                                       StreamState.assignment_id == chunk.assignment_id)).all()
    if any(item.watermark is not None and chunk.captured_at <= item.watermark for item in streams):
        return
    for stream in streams:
        if stream.watermark is None or chunk.captured_at <= stream.watermark:
            continue
        if stream.observed_at is not None and chunk.captured_at <= stream.observed_at:
            continue
        stream.observed_at = chunk.captured_at
        stream.observed_measurement_id = measurement.id
        stream.data_status = "invalid"
        stream.updated_at = now
        stream.recovery_streak = 0
        incident = session.scalar(select(Incident).where(Incident.stream_id == stream.id,
                                     Incident.status.in_(("active", "recovering"))))
        if incident is not None:
            had_streak = incident.recovery_streak > 0
            incident.recovery_streak = 0
            incident.status = "active"
            stream.noise_status = "excessive"
            if had_streak:
                _emit(session, "incident.updated", measurement, chunk,
                      session.get(ThresholdVersion, incident.threshold_version_id), stream, incident, reason)
        _emit(session, "location.status_changed", measurement, chunk,
              session.get(ThresholdVersion, stream.threshold_version_id), stream, incident, reason)


def evaluate_measurement(session, measurement: Measurement, settings=None) -> Evaluation:
    """Evaluate once with strict canonical precision and a committed watermark."""
    settings = settings or get_settings()
    now = _utc(clock.now())
    with session.no_autoflush:
        lock_event_clock(session)
        chunk = session.get(AudioChunk, measurement.audio_chunk_id)
        if chunk is None:
            raise ValueError("unknown source audio chunk")
        device = session.scalar(select(Device).where(Device.id == chunk.device_id).with_for_update()
                                .execution_options(populate_existing=True))
        digest = _fingerprint(measurement, chunk)
        existing = session.scalar(select(Evaluation).where(Evaluation.measurement_id == measurement.id)) if measurement.id else None
    if existing is not None:
        if existing.content_hash != digest:
            raise MeasurementConflict("measurement identifier already evaluated with different content")
        return existing
    if measurement.content_hash is not None and measurement.content_hash != digest:
        raise MeasurementConflict("persisted measurement content changed before evaluation")
    measurement.content_hash = digest
    measurement.breach = False
    session.flush()
    if getattr(chunk, "legacy_ingestion", False):
        outcome = Evaluation(measurement_id=measurement.id, threshold_version_id=None,
                             status="legacy_historical", diagnostic="legacy_snapshot_preserved",
                             breach=None, live=False, content_hash=digest, created_at=now)
        session.add(outcome)
        session.flush()
        return outcome
    rule = _rule(session, chunk)
    assignment = session.get(DeviceAssignment, chunk.assignment_id) if chunk.assignment_id else None
    diagnostic = _diagnostic(session, measurement, chunk, device, assignment, rule)
    outcome = Evaluation(measurement_id=measurement.id, threshold_version_id=rule.id if rule else None,
                         status="ineligible", diagnostic=diagnostic, breach=None, live=False,
                         content_hash=digest, created_at=now)
    if diagnostic:
        if not measurement.is_reprocessing:
            _interrupt(session, measurement, chunk, settings, now, diagnostic)
        session.add(outcome)
        session.flush()
        return outcome
    outcome.breach = measurement.value_db > rule.threshold_value
    measurement.breach = outcome.breach
    if measurement.is_reprocessing:
        outcome.status, outcome.diagnostic = "historical_reprocessing", "reprocessing_no_live_replay"
        session.add(outcome)
        session.flush()
        return outcome
    key = f"{measurement.measurement_type}|{measurement.weighting}|{measurement.channel_policy}|{rule.interval_seconds}"
    stream = session.scalar(select(StreamState).where(StreamState.device_id == chunk.device_id,
                         StreamState.assignment_id == assignment.id, StreamState.stream_key == key))
    timing = None
    if chunk.captured_at > now:
        timing = "future_rejected" if (chunk.captured_at - now).total_seconds() > settings.future_skew_seconds else "future_clock_skew"
    elif now - chunk.captured_at > timedelta(seconds=settings.live_freshness_seconds):
        timing = "old_capture"
    elif device.current_assignment_id != assignment.id:
        timing = "historical_assignment"
    elif stream is not None:
        if stream.watermark is not None and chunk.captured_at == stream.watermark:
            timing = "duplicate_timestamp"
        elif stream.watermark is not None and chunk.captured_at < stream.watermark:
            timing = "out_of_order"
        elif stream.window_end is not None and chunk.captured_at < stream.window_end:
            timing = "overlapping_window"
        elif stream.observed_at is not None and chunk.captured_at <= stream.observed_at:
            timing = "out_of_order_after_invalid"
    if timing is None:
        # A former measurement-method stream cannot reinstate a superseded rule
        # after a newer stream already committed the assignment's live state.
        other_streams = session.scalars(select(StreamState).where(
            StreamState.device_id == chunk.device_id, StreamState.assignment_id == assignment.id)).all()
        if any(item.stream_key != key and item.watermark is not None
               and item.watermark >= chunk.captured_at for item in other_streams):
            timing = "out_of_order_policy_stream"
    if timing:
        outcome.status, outcome.diagnostic = "eligible_historical", timing
        outcome.stream_id = stream.id if stream else None
        session.add(outcome)
        session.flush()
        return outcome
    if stream is None:
        stream = StreamState(device_id=chunk.device_id, assignment_id=assignment.id, location_id=chunk.location_id,
                             stream_key=key, threshold_version_id=rule.id, recovery_streak=0,
                             noise_status="unknown", data_status="fresh", updated_at=now)
        session.add(stream)
        session.flush()
    outcome.stream_id, outcome.status, outcome.live = stream.id, "eligible_live", True
    outcome.diagnostic = None
    # Closure happens only when evidence under the new policy/assignment arrives.
    previous_incident = None
    open_incidents = session.scalars(select(Incident).where(Incident.device_id == chunk.device_id,
                                   Incident.status.in_(("active", "recovering"))).order_by(Incident.started_at)).all()
    for prior in open_incidents:
        prior_stream = session.get(StreamState, prior.stream_id) if prior.stream_id else None
        reassigned = prior_stream is not None and prior_stream.assignment_id != assignment.id
        changed = not reassigned and prior.threshold_version_id != rule.id
        if not reassigned and not changed:
            continue
        prior.status = "closed"
        prior.closed_reason = "device_reassigned" if reassigned else "threshold_changed"
        prior.ended_at = max(prior.started_at, chunk.captured_at)
        prior.recovery_streak = 0
        previous_incident = prior.id
        if prior_stream is not None:
            prior_stream.recovery_streak = 0
            prior_stream.noise_status = "unknown"
            prior_stream.updated_at = now
        _emit(session, "incident.closed", measurement, chunk,
              session.get(ThresholdVersion, prior.threshold_version_id), prior_stream, prior, prior.closed_reason)
    # Flush closures before a replacement insert to satisfy the partial unique index.
    session.flush()
    incident = session.scalar(select(Incident).where(Incident.stream_id == stream.id,
                                Incident.status.in_(("active", "recovering"))))
    contiguous = bool(stream.watermark is not None and stream.window_end is not None
                      and abs((chunk.captured_at - stream.window_end).total_seconds()) <= .001
                      and stream.last_session_id == chunk.session_id
                      and stream.last_sequence is not None and chunk.sequence == stream.last_sequence + 1
                      and stream.threshold_version_id == rule.id
                      and stream.observed_at == stream.watermark)
    if contiguous and stream.last_measurement_id is not None:
        previous = session.get(Measurement, stream.last_measurement_id)
        previous_chunk = session.get(AudioChunk, previous.audio_chunk_id)
        contiguous = (previous.calibration_version == measurement.calibration_version
                      and previous.calibration_snapshot == measurement.calibration_snapshot)
    stream.threshold_version_id = rule.id
    stream.watermark = chunk.captured_at
    stream.window_end = chunk.captured_at + timedelta(seconds=measurement.interval_seconds)
    stream.observed_at = chunk.captured_at
    stream.observed_measurement_id = measurement.id
    stream.last_session_id, stream.last_sequence = chunk.session_id, chunk.sequence
    stream.last_measurement_id, stream.last_received_at = measurement.id, measurement.received_at
    stream.last_value, stream.updated_at = measurement.value_db, now
    # Live eligibility and display freshness use separate windows. Commit the
    # correct freshness with the measurement so SSE never briefly labels an
    # eligible but delayed reading as fresh before the next worker sweep.
    stream.data_status = "fresh" if chunk.captured_at >= now - timedelta(seconds=settings.data_stale_seconds) else "stale"
    if not contiguous:
        stream.recovery_streak = 0
        if incident is not None:
            incident.recovery_streak = 0
    if outcome.breach:
        stream.noise_status, stream.recovery_streak = "excessive", 0
        if incident is None:
            incident = Incident(device_id=chunk.device_id, location_id=chunk.location_id,
                    stream_id=stream.id, threshold_version_id=rule.id, location_snapshot=dict(chunk.location_snapshot),
                    started_at=chunk.captured_at, ended_at=None, threshold_value=rule.threshold_value,
                    threshold_type=rule.threshold_type, peak_db=measurement.value_db, latest_db=measurement.value_db,
                    last_occurrence_at=chunk.captured_at, breach_count=1, recovery_streak=0, status="active",
                    closed_reason=None, previous_incident_id=previous_incident)
            session.add(incident)
            session.flush()
            _emit(session, "incident.opened", measurement, chunk, rule, stream, incident, "threshold_exceeded")
        else:
            incident.status, incident.recovery_streak = "active", 0
            incident.peak_db = max(incident.peak_db, measurement.value_db)
            incident.latest_db, incident.last_occurrence_at = measurement.value_db, chunk.captured_at
            incident.breach_count += 1
            _emit(session, "incident.updated", measurement, chunk, rule, stream, incident, "threshold_exceeded")
    elif incident is not None:
        stream.recovery_streak += 1
        incident.recovery_streak = stream.recovery_streak
        incident.latest_db = measurement.value_db
        if stream.recovery_streak >= rule.recovery_count:
            incident.status, incident.closed_reason = "resolved", "valid_recovery"
            incident.ended_at = chunk.captured_at + timedelta(seconds=measurement.interval_seconds)
            stream.noise_status, stream.recovery_streak = "normal", 0
            _emit(session, "incident.resolved", measurement, chunk, rule, stream, incident, "valid_recovery")
        else:
            incident.status, stream.noise_status = "recovering", "recovering"
            _emit(session, "incident.updated", measurement, chunk, rule, stream, incident, "recovery_progress")
    else:
        stream.noise_status, stream.recovery_streak = "normal", 0
    _emit(session, "location.status_changed", measurement, chunk, rule, stream, incident,
          "measurement_evaluated")
    session.add(outcome)
    session.flush()
    return outcome

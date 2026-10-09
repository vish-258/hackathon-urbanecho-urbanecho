"""Durable measurement, immutable policy, assignment, and live-event schema."""
import uuid
from datetime import date, datetime
from typing import Any

from geoalchemy2 import Geography
from sqlalchemy import BigInteger, Boolean, CheckConstraint, Date, DateTime, Float, ForeignKey, Identity, Index, Integer, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Location(Base):
    __tablename__ = "locations"
    __table_args__ = (
        CheckConstraint("threshold_type IN ('dbfs_rms', 'spl_z_leq')", name="ck_location_threshold_type"),
        CheckConstraint("interval_seconds BETWEEN 1 AND 60", name="ck_location_interval"),
        CheckConstraint("threshold_value > '-Infinity'::float8 AND threshold_value < 'Infinity'::float8", name="ck_location_threshold_finite"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200))
    point: Mapped[Any] = mapped_column(Geography(geometry_type="POINT", srid=4326, spatial_index=True), nullable=False)
    timezone: Mapped[str] = mapped_column(String(100))
    threshold_value: Mapped[float] = mapped_column(Float)
    threshold_type: Mapped[str] = mapped_column(String(30))
    interval_seconds: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Device(Base):
    __tablename__ = "devices"
    __table_args__ = (
        Index("ix_devices_location", "location_id"),
        UniqueConstraint("external_id", name="uq_devices_external_id"),
        CheckConstraint("external_id IS NULL OR (external_id ~ '^[A-Za-z0-9_-]{1,32}$' AND external_id !~ '^[A-Fa-f0-9]{32}$')",
                        name="ck_devices_external_id"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    external_id: Mapped[str | None] = mapped_column(String(32))
    location_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("locations.id", ondelete="RESTRICT"))
    microphone_model: Mapped[str] = mapped_column(String(100), default="INMP441")
    credential_hash: Mapped[str] = mapped_column(String(256))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    last_contact_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    calibration: Mapped[dict | None] = mapped_column(JSONB)
    assignment_revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    config_revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    current_assignment_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("device_assignments.id", use_alter=True, name="fk_device_current_assignment", ondelete="RESTRICT"))


class AudioChunk(Base):
    __tablename__ = "audio_chunks"
    __table_args__ = (
        UniqueConstraint("device_id", "device_chunk_id", name="uq_audio_device_chunk"),
        CheckConstraint("status IN ('pending', 'processing', 'completed', 'failed')", name="ck_audio_status"),
        CheckConstraint("sequence >= 0", name="ck_audio_sequence"),
        CheckConstraint("duration_seconds > 0 AND duration_seconds <= 600", name="ck_audio_duration"),
        CheckConstraint("sample_rate > 0", name="ck_audio_sample_rate"),
        CheckConstraint("threshold_type IN ('dbfs_rms', 'spl_z_leq')", name="ck_audio_threshold_type"),
        CheckConstraint("interval_seconds BETWEEN 1 AND 60", name="ck_audio_interval"),
        CheckConstraint("threshold_value > '-Infinity'::float8 AND threshold_value < 'Infinity'::float8", name="ck_audio_threshold_finite"),
        Index("ix_audio_device_captured", "device_id", "captured_at"),
        Index("ix_audio_location_captured", "location_id", "captured_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("devices.id", ondelete="RESTRICT"))
    location_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("locations.id", ondelete="RESTRICT"))
    location_snapshot: Mapped[dict] = mapped_column(JSONB)
    legacy_ingestion: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    assignment_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("device_assignments.id", ondelete="RESTRICT"))
    device_chunk_id: Mapped[str] = mapped_column(String(128))
    session_id: Mapped[str] = mapped_column(String(128))
    sequence: Mapped[int] = mapped_column(BigInteger)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    duration_seconds: Mapped[float] = mapped_column(Float)
    sample_rate: Mapped[int] = mapped_column(Integer)
    audio_format: Mapped[str] = mapped_column(String(40))
    checksum: Mapped[str] = mapped_column(String(64))
    file_path: Mapped[str] = mapped_column(Text, unique=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", server_default="pending")
    threshold_value: Mapped[float] = mapped_column(Float)
    threshold_type: Mapped[str] = mapped_column(String(30))
    interval_seconds: Mapped[int] = mapped_column(Integer)
    calibration: Mapped[dict | None] = mapped_column(JSONB)


class Measurement(Base):
    __tablename__ = "measurements"
    __table_args__ = (
        CheckConstraint("interval_seconds > 0", name="ck_measurement_interval"),
        CheckConstraint("measurement_type IN ('dbfs_rms', 'spl_z_leq')", name="ck_measurement_type"),
        CheckConstraint("calibration_status IN ('not_required', 'calibrated', 'calibration_required', 'interval_mismatch')", name="ck_measurement_calibration_status"),
        CheckConstraint("value_db IS NULL OR (value_db > '-Infinity'::float8 AND value_db < 'Infinity'::float8)", name="ck_measurement_value_finite"),
        CheckConstraint("digital_dbfs IS NULL OR (digital_dbfs > '-Infinity'::float8 AND digital_dbfs < 'Infinity'::float8)", name="ck_measurement_digital_finite"),
        CheckConstraint("NOT breach OR (value_db IS NOT NULL AND (measurement_type = 'dbfs_rms' OR (calibration_status = 'calibrated' AND calibration_version IS NOT NULL)))", name="ck_measurement_breach_calibrated"),
        Index("ix_measurements_time", "measured_at"),
        UniqueConstraint("audio_chunk_id", "result_version", name="uq_measurement_result_version"),
        Index("ix_measurements_audio_result_order", "audio_chunk_id", "result_order"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    audio_chunk_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("audio_chunks.id", ondelete="RESTRICT"))
    result_version: Mapped[str] = mapped_column(String(100), default="initial", server_default="initial")
    result_order: Mapped[int] = mapped_column(BigInteger, Identity(), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    weighting: Mapped[str] = mapped_column(String(20), default="none", server_default="none")
    channel_policy: Mapped[str] = mapped_column(String(30), default="mono", server_default="mono")
    quality_status: Mapped[str] = mapped_column(String(40), default="good", server_default="good")
    is_reprocessing: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    content_hash: Mapped[str | None] = mapped_column(String(64))
    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    interval_seconds: Mapped[float] = mapped_column(Float)
    value_db: Mapped[float | None] = mapped_column(Float)
    digital_dbfs: Mapped[float | None] = mapped_column(Float)
    measurement_type: Mapped[str] = mapped_column(String(30))
    calibration_status: Mapped[str] = mapped_column(String(40))
    calibration_version: Mapped[str | None] = mapped_column(String(100))
    calibration_snapshot: Mapped[dict | None] = mapped_column(JSONB)
    processing_version: Mapped[str] = mapped_column(String(100))
    breach: Mapped[bool] = mapped_column(Boolean, default=False)


class Incident(Base):
    __tablename__ = "incidents"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'recovering', 'resolved', 'closed')", name="ck_incident_status"),
        CheckConstraint("ended_at IS NULL OR ended_at >= started_at", name="ck_incident_time"),
        CheckConstraint("(status IN ('active', 'recovering') AND ended_at IS NULL) OR (status IN ('resolved', 'closed') AND ended_at IS NOT NULL)", name="ck_incident_end_status"),
        CheckConstraint("threshold_type IN ('dbfs_rms', 'spl_z_leq')", name="ck_incident_threshold_type"),
        CheckConstraint("peak_db > '-Infinity'::float8 AND peak_db < 'Infinity'::float8", name="ck_incident_peak_finite"),
        CheckConstraint("threshold_value > '-Infinity'::float8 AND threshold_value < 'Infinity'::float8", name="ck_incident_threshold_finite"),
        Index("ix_incidents_location_started", "location_id", "started_at"),
        Index("ix_incidents_device_started", "device_id", "started_at"),
        Index("uq_incident_unresolved_stream", "stream_id", unique=True, postgresql_where=text("status IN ('active', 'recovering')")),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("devices.id", ondelete="RESTRICT"))
    location_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("locations.id", ondelete="RESTRICT"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    threshold_value: Mapped[float] = mapped_column(Float)
    threshold_type: Mapped[str] = mapped_column(String(30))
    peak_db: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(20))
    stream_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("stream_states.id", ondelete="RESTRICT"))
    threshold_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("threshold_versions.id", ondelete="RESTRICT"))
    location_snapshot: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    latest_db: Mapped[float | None] = mapped_column(Float)
    last_occurrence_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    breach_count: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    recovery_streak: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    closed_reason: Mapped[str | None] = mapped_column(String(100))
    previous_incident_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("incidents.id", ondelete="RESTRICT"))


class ProcessingJob(Base):
    __tablename__ = "processing_jobs"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'processing', 'retry', 'completed', 'failed')", name="ck_job_status"),
        CheckConstraint("attempts >= 0", name="ck_job_attempts"),
        CheckConstraint("(status = 'processing' AND lease_until IS NOT NULL AND lease_token IS NOT NULL) OR (status <> 'processing' AND lease_until IS NULL AND lease_token IS NULL)", name="ck_job_lease"),
        Index("ix_jobs_claim", "status", "available_at", "lease_until"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    audio_chunk_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("audio_chunks.id", ondelete="RESTRICT"), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", server_default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ThresholdVersion(Base):
    __tablename__ = "threshold_versions"
    __table_args__ = (
        UniqueConstraint("location_id", "revision", name="uq_threshold_location_revision"),
        UniqueConstraint("location_id", "effective_at", name="uq_threshold_location_effective"),
        CheckConstraint("revision > 0", name="ck_threshold_revision"),
        CheckConstraint("interval_seconds BETWEEN 1 AND 60", name="ck_threshold_interval"),
        CheckConstraint("recovery_count BETWEEN 1 AND 100", name="ck_threshold_recovery"),
        CheckConstraint("channel_policy = 'mono'", name="ck_threshold_channel"),
        CheckConstraint("(threshold_type = 'dbfs_rms' AND weighting = 'none' AND threshold_value BETWEEN -200 AND 0) OR (threshold_type = 'spl_z_leq' AND weighting = 'Z' AND threshold_value BETWEEN -100 AND 200)", name="ck_threshold_method_range"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    location_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("locations.id", ondelete="RESTRICT"))
    revision: Mapped[int] = mapped_column(Integer)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    threshold_value: Mapped[float] = mapped_column(Float)
    threshold_type: Mapped[str] = mapped_column(String(30))
    weighting: Mapped[str] = mapped_column(String(20))
    channel_policy: Mapped[str] = mapped_column(String(30), default="mono", server_default="mono")
    interval_seconds: Mapped[int] = mapped_column(Integer)
    recovery_count: Mapped[int] = mapped_column(Integer, default=3, server_default="3")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DeviceAssignment(Base):
    __tablename__ = "device_assignments"
    __table_args__ = (
        CheckConstraint("ended_at IS NULL OR ended_at >= effective_at", name="ck_assignment_time"),
        Index("ix_assignment_device_effective", "device_id", "effective_at"),
        Index("uq_assignment_current", "device_id", unique=True, postgresql_where=text("ended_at IS NULL")),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("devices.id", ondelete="RESTRICT"))
    location_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("locations.id", ondelete="RESTRICT"))
    location_snapshot: Mapped[dict] = mapped_column(JSONB)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class StreamState(Base):
    __tablename__ = "stream_states"
    __table_args__ = (
        UniqueConstraint("device_id", "assignment_id", "stream_key", name="uq_stream_identity"),
        CheckConstraint("noise_status IN ('unknown', 'normal', 'excessive', 'recovering')", name="ck_stream_noise"),
        CheckConstraint("recovery_streak >= 0", name="ck_stream_recovery"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("devices.id", ondelete="RESTRICT"))
    assignment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("device_assignments.id", ondelete="RESTRICT"))
    location_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("locations.id", ondelete="RESTRICT"))
    stream_key: Mapped[str] = mapped_column(String(160))
    threshold_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("threshold_versions.id", ondelete="RESTRICT"))
    watermark: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    window_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_session_id: Mapped[str | None] = mapped_column(String(128))
    last_sequence: Mapped[int | None] = mapped_column(BigInteger)
    last_measurement_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("measurements.id", ondelete="RESTRICT"))
    last_received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_value: Mapped[float | None] = mapped_column(Float)
    noise_status: Mapped[str] = mapped_column(String(20), default="unknown", server_default="unknown")
    recovery_streak: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    observed_measurement_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("measurements.id", ondelete="RESTRICT"))
    data_status: Mapped[str] = mapped_column(String(20), default="fresh", server_default="fresh")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MeasurementEvaluation(Base):
    __tablename__ = "measurement_evaluations"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    measurement_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("measurements.id", ondelete="RESTRICT"), unique=True)
    threshold_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("threshold_versions.id", ondelete="RESTRICT"))
    stream_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("stream_states.id", ondelete="RESTRICT"))
    status: Mapped[str] = mapped_column(String(40))
    diagnostic: Mapped[str | None] = mapped_column(String(160))
    breach: Mapped[bool | None] = mapped_column(Boolean)
    live: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    content_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


Evaluation = MeasurementEvaluation


class EventClock(Base):
    __tablename__ = "event_clock"
    __table_args__ = (CheckConstraint("id = 1", name="ck_event_clock_singleton"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    last_position: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    epoch: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DurableEvent(Base):
    __tablename__ = "durable_events"
    __table_args__ = (Index("ix_events_location_pointer", "location_id", "pointer"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    pointer: Mapped[int] = mapped_column(BigInteger, unique=True)
    event_type: Mapped[str] = mapped_column(String(80))
    incident_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("incidents.id", ondelete="RESTRICT"))
    device_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("devices.id", ondelete="RESTRICT"))
    location_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("locations.id", ondelete="RESTRICT"))
    payload: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DailyReport(Base):
    """One durable, retryable report request per location and local date."""
    __tablename__ = "daily_reports"
    __table_args__ = (
        UniqueConstraint("location_id", "reporting_date", name="uq_daily_report_location_date"),
        CheckConstraint("status IN ('queued', 'processing', 'completed', 'failed')", name="ck_daily_report_status"),
        CheckConstraint("attempts >= 0", name="ck_daily_report_attempts"),
        CheckConstraint("day_end_utc > day_start_utc", name="ck_daily_report_day"),
        CheckConstraint("(status = 'processing' AND lease_until IS NOT NULL AND lease_token IS NOT NULL) OR (status <> 'processing' AND lease_until IS NULL AND lease_token IS NULL)", name="ck_daily_report_lease"),
        Index("ix_daily_report_claim", "status", "available_at", "lease_until"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    location_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("locations.id", ondelete="RESTRICT"))
    reporting_date: Mapped[date] = mapped_column(Date)
    timezone: Mapped[str] = mapped_column(String(100))
    day_start_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    day_end_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="queued", server_default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    error: Mapped[str | None] = mapped_column(Text)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class DailySummary(Base):
    """Saved result for one compatible, explicitly classified measurement definition."""
    __tablename__ = "daily_summaries"
    __table_args__ = (
        UniqueConstraint("location_id", "reporting_date", "definition_key", name="uq_daily_summary_definition"),
        Index("ix_daily_summary_report", "report_id"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    report_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("daily_reports.id", ondelete="CASCADE"))
    location_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("locations.id", ondelete="RESTRICT"))
    reporting_date: Mapped[date] = mapped_column(Date)
    definition_key: Mapped[str] = mapped_column(String(64))
    definition: Mapped[dict] = mapped_column(JSONB)
    statistics: Mapped[dict] = mapped_column(JSONB)
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

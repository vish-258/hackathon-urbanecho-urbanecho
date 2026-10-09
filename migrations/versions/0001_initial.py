"""Frozen initial schema, PostGIS in the application DB, explicit app-role grants."""
import os

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from geoalchemy2 import Geography

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.execute("SELECT PostGIS_Full_Version()")
    op.create_table(
        "locations",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("point", Geography(geometry_type="POINT", srid=4326, spatial_index=False), nullable=False),
        sa.Column("timezone", sa.String(100), nullable=False),
        sa.Column("threshold_value", sa.Float(), nullable=False),
        sa.Column("threshold_type", sa.String(30), nullable=False),
        sa.Column("interval_seconds", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("threshold_type IN ('dbfs_rms', 'spl_z_leq')", name="ck_location_threshold_type"),
        sa.CheckConstraint("interval_seconds BETWEEN 1 AND 60", name="ck_location_interval"),
        sa.CheckConstraint("threshold_value > '-Infinity'::float8 AND threshold_value < 'Infinity'::float8", name="ck_location_threshold_finite"),
    )
    op.create_index("idx_locations_point", "locations", ["point"], postgresql_using="gist")
    op.create_table(
        "devices",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("location_id", pg.UUID(as_uuid=True), sa.ForeignKey("locations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("microphone_model", sa.String(100), nullable=False),
        sa.Column("credential_hash", sa.String(256), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("last_contact_at", sa.DateTime(timezone=True)),
        sa.Column("calibration", pg.JSONB()),
    )
    op.create_index("ix_devices_location", "devices", ["location_id"])
    op.create_table(
        "audio_chunks",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("device_id", pg.UUID(as_uuid=True), sa.ForeignKey("devices.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("location_id", pg.UUID(as_uuid=True), sa.ForeignKey("locations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("location_snapshot", pg.JSONB(), nullable=False),
        sa.Column("device_chunk_id", sa.String(128), nullable=False),
        sa.Column("session_id", sa.String(128), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("duration_seconds", sa.Float(), nullable=False),
        sa.Column("sample_rate", sa.Integer(), nullable=False),
        sa.Column("audio_format", sa.String(40), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=False, unique=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("threshold_value", sa.Float(), nullable=False),
        sa.Column("threshold_type", sa.String(30), nullable=False),
        sa.Column("interval_seconds", sa.Integer(), nullable=False),
        sa.Column("calibration", pg.JSONB()),
        sa.UniqueConstraint("device_id", "device_chunk_id", name="uq_audio_device_chunk"),
        sa.CheckConstraint("status IN ('pending', 'processing', 'completed', 'failed')", name="ck_audio_status"),
        sa.CheckConstraint("sequence >= 0", name="ck_audio_sequence"),
        sa.CheckConstraint("duration_seconds > 0 AND duration_seconds <= 600", name="ck_audio_duration"),
        sa.CheckConstraint("sample_rate > 0", name="ck_audio_sample_rate"),
        sa.CheckConstraint("threshold_type IN ('dbfs_rms', 'spl_z_leq')", name="ck_audio_threshold_type"),
        sa.CheckConstraint("interval_seconds BETWEEN 1 AND 60", name="ck_audio_interval"),
        sa.CheckConstraint("threshold_value > '-Infinity'::float8 AND threshold_value < 'Infinity'::float8", name="ck_audio_threshold_finite"),
    )
    op.create_index("ix_audio_device_captured", "audio_chunks", ["device_id", "captured_at"])
    op.create_index("ix_audio_location_captured", "audio_chunks", ["location_id", "captured_at"])
    op.create_table(
        "measurements",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("audio_chunk_id", pg.UUID(as_uuid=True), sa.ForeignKey("audio_chunks.id", ondelete="RESTRICT"), nullable=False, unique=True),
        sa.Column("measured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("interval_seconds", sa.Float(), nullable=False),
        sa.Column("value_db", sa.Float()),
        sa.Column("digital_dbfs", sa.Float()),
        sa.Column("measurement_type", sa.String(30), nullable=False),
        sa.Column("calibration_status", sa.String(40), nullable=False),
        sa.Column("calibration_version", sa.String(100)),
        sa.Column("processing_version", sa.String(100), nullable=False),
        sa.Column("breach", sa.Boolean(), nullable=False),
        sa.CheckConstraint("interval_seconds > 0", name="ck_measurement_interval"),
        sa.CheckConstraint("measurement_type IN ('dbfs_rms', 'spl_z_leq')", name="ck_measurement_type"),
        sa.CheckConstraint("calibration_status IN ('not_required', 'calibrated', 'calibration_required', 'interval_mismatch')", name="ck_measurement_calibration_status"),
        sa.CheckConstraint("value_db IS NULL OR (value_db > '-Infinity'::float8 AND value_db < 'Infinity'::float8)", name="ck_measurement_value_finite"),
        sa.CheckConstraint("digital_dbfs IS NULL OR (digital_dbfs > '-Infinity'::float8 AND digital_dbfs < 'Infinity'::float8)", name="ck_measurement_digital_finite"),
        sa.CheckConstraint("NOT breach OR (value_db IS NOT NULL AND (measurement_type = 'dbfs_rms' OR (calibration_status = 'calibrated' AND calibration_version IS NOT NULL)))", name="ck_measurement_breach_calibrated"),
    )
    op.create_index("ix_measurements_time", "measurements", ["measured_at"])
    op.create_table(
        "incidents",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("device_id", pg.UUID(as_uuid=True), sa.ForeignKey("devices.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("location_id", pg.UUID(as_uuid=True), sa.ForeignKey("locations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("threshold_value", sa.Float(), nullable=False),
        sa.Column("threshold_type", sa.String(30), nullable=False),
        sa.Column("peak_db", sa.Float(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.CheckConstraint("status IN ('active', 'resolved')", name="ck_incident_status"),
        sa.CheckConstraint("ended_at IS NULL OR ended_at >= started_at", name="ck_incident_time"),
        sa.CheckConstraint("(status = 'active' AND ended_at IS NULL) OR (status = 'resolved' AND ended_at IS NOT NULL)", name="ck_incident_end_status"),
        sa.CheckConstraint("threshold_type IN ('dbfs_rms', 'spl_z_leq')", name="ck_incident_threshold_type"),
        sa.CheckConstraint("peak_db > '-Infinity'::float8 AND peak_db < 'Infinity'::float8", name="ck_incident_peak_finite"),
        sa.CheckConstraint("threshold_value > '-Infinity'::float8 AND threshold_value < 'Infinity'::float8", name="ck_incident_threshold_finite"),
    )
    op.create_index("ix_incidents_location_started", "incidents", ["location_id", "started_at"])
    op.create_index("ix_incidents_device_started", "incidents", ["device_id", "started_at"])
    op.create_table(
        "processing_jobs",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("audio_chunk_id", pg.UUID(as_uuid=True), sa.ForeignKey("audio_chunks.id", ondelete="RESTRICT"), nullable=False, unique=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("lease_token", pg.UUID(as_uuid=True)),
        sa.Column("last_error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('pending', 'processing', 'retry', 'completed', 'failed')", name="ck_job_status"),
        sa.CheckConstraint("attempts >= 0", name="ck_job_attempts"),
        sa.CheckConstraint("(status = 'processing' AND lease_until IS NOT NULL AND lease_token IS NOT NULL) OR (status <> 'processing' AND lease_until IS NULL AND lease_token IS NULL)", name="ck_job_lease"),
    )
    op.create_index("ix_jobs_claim", "processing_jobs", ["status", "available_at", "lease_until"])
    # Role identifiers are safely quoted; application credentials have no DDL rights.
    role = os.environ.get("APP_DB_USER", "noise_app")
    quoted_role = '"' + role.replace('"', '""') + '"'
    op.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
    op.execute(f"GRANT USAGE ON SCHEMA public TO {quoted_role}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE locations, devices, audio_chunks, measurements, incidents, processing_jobs TO {quoted_role}")
    op.execute(f"GRANT SELECT ON TABLE alembic_version TO {quoted_role}")


def downgrade():
    for table in ["processing_jobs", "incidents", "measurements", "audio_chunks", "devices", "locations"]:
        op.drop_table(table)
    # Shared PostGIS extension and role survive intentional schema downgrade.

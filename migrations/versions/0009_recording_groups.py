"""Durable ten-second file manifests over unchanged one-second originals."""
import os
from alembic import op

revision = "0009_recording_groups"
down_revision = "0008_incident_analysis"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE recording_groups (
      id uuid PRIMARY KEY, device_id uuid NOT NULL REFERENCES devices(id) ON DELETE RESTRICT,
      assignment_id uuid NOT NULL REFERENCES device_assignments(id) ON DELETE RESTRICT,
      location_id uuid NOT NULL REFERENCES locations(id) ON DELETE RESTRICT, location_snapshot jsonb NOT NULL,
      session_id varchar(128) NOT NULL, sequence_start bigint NOT NULL, captured_at timestamptz NOT NULL,
      received_at timestamptz NOT NULL, last_received_at timestamptz NOT NULL,
      status varchar(20) NOT NULL DEFAULT 'collecting', dirty boolean NOT NULL DEFAULT true,
      available_at timestamptz NOT NULL DEFAULT now(), next_check_at timestamptz,
      lease_until timestamptz, lease_token uuid, revision varchar(64), manifest jsonb, error text,
      generated_at timestamptz, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT uq_recording_group_identity UNIQUE(device_id,session_id,assignment_id,sequence_start),
      CONSTRAINT ck_recording_group_sequence CHECK(sequence_start >= 0 AND sequence_start % 10 = 0),
      CONSTRAINT ck_recording_group_status CHECK(status IN ('collecting','ready','partial','no_audio','failed')),
      CONSTRAINT ck_recording_group_lease CHECK((lease_until IS NULL) = (lease_token IS NULL)),
      CONSTRAINT ck_recording_group_ready CHECK(status <> 'ready' OR (manifest IS NOT NULL AND revision IS NOT NULL))
    );
    CREATE INDEX ix_recording_groups_location_capture ON recording_groups(location_id,captured_at,id);
    CREATE INDEX ix_recording_groups_device_capture ON recording_groups(device_id,captured_at,id);
    CREATE INDEX ix_recording_groups_work ON recording_groups(dirty,available_at,lease_until);
    CREATE INDEX ix_recording_groups_refresh ON recording_groups(next_check_at);
    CREATE TABLE recording_group_parts (
      audio_chunk_id uuid PRIMARY KEY REFERENCES audio_chunks(id) ON DELETE RESTRICT,
      group_id uuid NOT NULL REFERENCES recording_groups(id) ON DELETE RESTRICT,
      sequence bigint NOT NULL
    );
    CREATE INDEX ix_recording_group_parts_group ON recording_group_parts(group_id,sequence);
    CREATE TABLE recording_group_scan_state (
      id integer PRIMARY KEY, after_received_at timestamptz, after_audio_id uuid,
      history_available_at timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT ck_recording_group_scan_singleton CHECK(id=1)
    );
    """)
    role = os.environ.get("APP_DB_USER", "noise_app")
    quoted = '"' + role.replace('"', '""') + '"'
    op.execute(f"GRANT SELECT,INSERT,UPDATE ON recording_groups,recording_group_scan_state TO {quoted}")
    op.execute(f"GRANT SELECT,INSERT ON recording_group_parts TO {quoted}")


def downgrade():
    raise RuntimeError("Recording evidence is forward-only; restore a verified backup")

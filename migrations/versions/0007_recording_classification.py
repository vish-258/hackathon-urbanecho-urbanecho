"""Independent durable audio classification; existing sound evidence untouched."""
import os
from alembic import op

revision = "0007_recording_classification"
down_revision = "0006_capture_interval"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE recording_classifications (
      id uuid PRIMARY KEY, audio_chunk_id uuid NOT NULL REFERENCES audio_chunks(id) ON DELETE RESTRICT,
      model_version varchar(100) NOT NULL, mapping_version varchar(100) NOT NULL,
      status varchar(20) NOT NULL DEFAULT 'pending', source_received_at timestamptz NOT NULL,
      attempts integer NOT NULL DEFAULT 0, available_at timestamptz NOT NULL DEFAULT now(),
      lease_until timestamptz, lease_token uuid, primary_category varchar(40), result jsonb, error text,
      classified_at timestamptz, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT uq_classification_version UNIQUE(audio_chunk_id,model_version,mapping_version),
      CONSTRAINT ck_classification_status CHECK(status IN ('pending','processing','completed','failed')),
      CONSTRAINT ck_classification_attempts CHECK(attempts>=0),
      CONSTRAINT ck_classification_lease CHECK(
        (status='processing' AND lease_until IS NOT NULL AND lease_token IS NOT NULL)
        OR (status<>'processing' AND lease_until IS NULL AND lease_token IS NULL)),
      CONSTRAINT ck_classification_result CHECK(status<>'completed' OR (result IS NOT NULL AND primary_category IS NOT NULL AND classified_at IS NOT NULL))
    );
    CREATE INDEX ix_classification_claim ON recording_classifications(model_version,mapping_version,status,available_at,source_received_at);
    CREATE INDEX ix_classification_pending_order ON recording_classifications(model_version,mapping_version,source_received_at,created_at,id) WHERE status='pending';
    CREATE INDEX ix_classification_expired ON recording_classifications(model_version,mapping_version,lease_until) WHERE status='processing';
    CREATE INDEX ix_audio_received_scan ON audio_chunks(received_at,id);
    CREATE TABLE classification_scan_state (
      model_version varchar(100) NOT NULL, mapping_version varchar(100) NOT NULL,
      after_received_at timestamptz, after_audio_id uuid,
      scan_available_at timestamptz NOT NULL DEFAULT now(), worker_status varchar(20) NOT NULL DEFAULT 'starting',
      worker_error text, heartbeat_at timestamptz,
      PRIMARY KEY(model_version,mapping_version)
    );
    """)
    role = os.environ.get("APP_DB_USER", "noise_app")
    quoted_role = '"' + role.replace('"', '""') + '"'
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON recording_classifications, classification_scan_state TO {quoted_role}")


def downgrade():
    raise RuntimeError("Classification evidence is forward-only; restore a verified backup")

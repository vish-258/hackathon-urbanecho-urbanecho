"""Leased incident audio manifests and estimates; saved originals are untouched."""
import os
from alembic import op

revision = "0008_incident_analysis"
down_revision = "0007_recording_classification"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE incident_analyses (
      id uuid PRIMARY KEY, incident_id uuid NOT NULL REFERENCES incidents(id) ON DELETE RESTRICT,
      model_version varchar(100) NOT NULL, mapping_version varchar(100) NOT NULL,
      status varchar(20) NOT NULL DEFAULT 'pending', attempts integer NOT NULL DEFAULT 0,
      available_at timestamptz NOT NULL DEFAULT now(), next_scan_at timestamptz NOT NULL DEFAULT now(),
      snapshot_at timestamptz NOT NULL, input_fingerprint varchar(64), revision varchar(64),
      manifest jsonb, result jsonb, lease_until timestamptz, lease_token uuid, error text,
      generated_at timestamptz, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT uq_incident_analysis_version UNIQUE(incident_id,model_version,mapping_version),
      CONSTRAINT ck_incident_analysis_status CHECK(status IN ('pending','processing','completed','failed')),
      CONSTRAINT ck_incident_analysis_attempts CHECK(attempts>=0),
      CONSTRAINT ck_incident_analysis_lease CHECK(
        (status='processing' AND lease_until IS NOT NULL AND lease_token IS NOT NULL)
        OR (status<>'processing' AND lease_until IS NULL AND lease_token IS NULL)),
      CONSTRAINT ck_incident_analysis_result CHECK(status<>'completed' OR
        (manifest IS NOT NULL AND result IS NOT NULL AND revision IS NOT NULL AND generated_at IS NOT NULL))
    );
    CREATE INDEX ix_incident_analysis_claim ON incident_analyses(model_version,mapping_version,status,available_at,created_at);
    CREATE INDEX ix_incident_analysis_scan ON incident_analyses(model_version,mapping_version,next_scan_at);
    CREATE INDEX ix_incidents_analysis_discovery ON incidents(started_at,id);
    """)
    role = os.environ.get("APP_DB_USER", "noise_app")
    quoted_role = '"' + role.replace('"', '""') + '"'
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON incident_analyses TO {quoted_role}")


def downgrade():
    raise RuntimeError("Incident evidence is forward-only; restore a verified backup")

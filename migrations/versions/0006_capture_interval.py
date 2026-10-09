"""Preserve each recording's declared sampling cadence independently of duration.

Null retains the pre-cadence behavior: adjacent windows are required. Existing
audio, measurements and immutable fingerprints remain unchanged.
"""
from alembic import op

revision = "0006_capture_interval"
down_revision = "0005_device_external_id"
branch_labels = None
depends_on = None


def upgrade():
    # Existing table-level app grants cover this new nullable column.
    op.execute("""
    ALTER TABLE audio_chunks ADD COLUMN capture_interval_ms integer;
    ALTER TABLE audio_chunks ADD CONSTRAINT ck_audio_capture_interval CHECK (
      capture_interval_ms IS NULL OR (
        capture_interval_ms BETWEEN 1 AND 3600000
        AND capture_interval_ms >= duration_seconds * 1000
      )
    );
    """)


def downgrade():
    raise RuntimeError("Capture cadence evidence is forward-only; restore a verified backup")

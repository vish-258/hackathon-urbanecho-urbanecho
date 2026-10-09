"""Mark pre-extension audio so abandoned jobs cannot replay new live alerts."""
from alembic import op

revision = "0003_legacy_audio_guard"
down_revision = "0002_live_incidents"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE audio_chunks ADD COLUMN legacy_ingestion boolean NOT NULL DEFAULT false")
    op.execute("UPDATE audio_chunks SET legacy_ingestion=true")


def downgrade():
    raise RuntimeError("Legacy-audio evidence is forward-only; restore a pre-upgrade backup")

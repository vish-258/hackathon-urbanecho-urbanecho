"""Optional stable firmware names map to registered device identities.

Existing devices keep their UUID, credential, location history and a null alias.
"""
from alembic import op

revision = "0005_device_external_id"
down_revision = "0004_daily_summaries"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    ALTER TABLE devices ADD COLUMN external_id varchar(32);
    ALTER TABLE devices ADD CONSTRAINT uq_devices_external_id UNIQUE(external_id);
    ALTER TABLE devices ADD CONSTRAINT ck_devices_external_id CHECK (
      external_id IS NULL OR (
        external_id ~ '^[A-Za-z0-9_-]{1,32}$'
        AND external_id !~ '^[A-Fa-f0-9]{32}$'
      )
    );
    """)


def downgrade():
    raise RuntimeError("Device identity evidence is forward-only; restore a verified backup")

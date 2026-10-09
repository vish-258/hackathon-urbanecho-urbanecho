"""Immutable policies, assignment history, committed streams and durable events.

Legacy readings remain historical and retain their ingestion snapshots. Existing
active incidents close explicitly as legacy_migration; no notification is replayed.
This migration deliberately does not import the evolving application models.
"""
import os
from alembic import op

revision = "0002_live_incidents"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE threshold_versions (
      id uuid PRIMARY KEY, location_id uuid NOT NULL REFERENCES locations(id) ON DELETE RESTRICT,
      revision integer NOT NULL CHECK(revision > 0), effective_at timestamptz NOT NULL,
      threshold_value double precision NOT NULL, threshold_type varchar(30) NOT NULL,
      weighting varchar(20) NOT NULL, channel_policy varchar(30) NOT NULL DEFAULT 'mono',
      interval_seconds integer NOT NULL CHECK(interval_seconds BETWEEN 1 AND 60),
      recovery_count integer NOT NULL DEFAULT 3 CHECK(recovery_count BETWEEN 1 AND 100),
      created_at timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT uq_threshold_location_revision UNIQUE(location_id, revision),
      CONSTRAINT uq_threshold_location_effective UNIQUE(location_id, effective_at),
      CONSTRAINT ck_threshold_channel CHECK(channel_policy = 'mono'),
      CONSTRAINT ck_threshold_method_range CHECK(
        (threshold_type = 'dbfs_rms' AND weighting = 'none' AND threshold_value BETWEEN -200 AND 0)
        OR (threshold_type = 'spl_z_leq' AND weighting = 'Z' AND threshold_value BETWEEN -100 AND 200))
    );
    CREATE TABLE device_assignments (
      id uuid PRIMARY KEY, device_id uuid NOT NULL REFERENCES devices(id) ON DELETE RESTRICT,
      location_id uuid NOT NULL REFERENCES locations(id) ON DELETE RESTRICT,
      location_snapshot jsonb NOT NULL, effective_at timestamptz NOT NULL,
      ended_at timestamptz, created_at timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT ck_assignment_time CHECK(ended_at IS NULL OR ended_at >= effective_at)
    );
    CREATE INDEX ix_assignment_device_effective ON device_assignments(device_id,effective_at);
    CREATE UNIQUE INDEX uq_assignment_current ON device_assignments(device_id) WHERE ended_at IS NULL;
    ALTER TABLE devices ADD COLUMN assignment_revision integer NOT NULL DEFAULT 1;
    ALTER TABLE devices ADD COLUMN config_revision integer NOT NULL DEFAULT 1;
    ALTER TABLE devices ADD COLUMN current_assignment_id uuid REFERENCES device_assignments(id) ON DELETE RESTRICT;
    ALTER TABLE audio_chunks ADD COLUMN assignment_id uuid REFERENCES device_assignments(id) ON DELETE RESTRICT;
    ALTER TABLE measurements DROP CONSTRAINT measurements_audio_chunk_id_key;
    ALTER TABLE measurements ADD COLUMN calibration_snapshot jsonb;
    ALTER TABLE measurements ADD COLUMN result_version varchar(100) NOT NULL DEFAULT 'initial';
    ALTER TABLE measurements ADD COLUMN received_at timestamptz NOT NULL DEFAULT now();
    ALTER TABLE measurements ADD COLUMN weighting varchar(20) NOT NULL DEFAULT 'none';
    ALTER TABLE measurements ADD COLUMN channel_policy varchar(30) NOT NULL DEFAULT 'mono';
    ALTER TABLE measurements ADD COLUMN quality_status varchar(40) NOT NULL DEFAULT 'good';
    ALTER TABLE measurements ADD COLUMN is_reprocessing boolean NOT NULL DEFAULT false;
    ALTER TABLE measurements ADD COLUMN content_hash varchar(64);
    ALTER TABLE measurements ADD CONSTRAINT uq_measurement_result_version UNIQUE(audio_chunk_id,result_version);
    CREATE TABLE stream_states (
      id uuid PRIMARY KEY, device_id uuid NOT NULL REFERENCES devices(id) ON DELETE RESTRICT,
      assignment_id uuid NOT NULL REFERENCES device_assignments(id) ON DELETE RESTRICT,
      location_id uuid NOT NULL REFERENCES locations(id) ON DELETE RESTRICT,
      stream_key varchar(160) NOT NULL,
      threshold_version_id uuid REFERENCES threshold_versions(id) ON DELETE RESTRICT,
      watermark timestamptz, window_end timestamptz, last_session_id varchar(128), last_sequence bigint,
      last_measurement_id uuid REFERENCES measurements(id) ON DELETE RESTRICT,
      last_received_at timestamptz, last_value double precision,
      noise_status varchar(20) NOT NULL DEFAULT 'unknown', recovery_streak integer NOT NULL DEFAULT 0,
      observed_at timestamptz, observed_measurement_id uuid REFERENCES measurements(id) ON DELETE RESTRICT,
      data_status varchar(20) NOT NULL DEFAULT 'fresh', updated_at timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT uq_stream_identity UNIQUE(device_id,assignment_id,stream_key),
      CONSTRAINT ck_stream_noise CHECK(noise_status IN ('unknown','normal','excessive','recovering')),
      CONSTRAINT ck_stream_recovery CHECK(recovery_streak >= 0)
    );
    CREATE TABLE measurement_evaluations (
      id uuid PRIMARY KEY, measurement_id uuid NOT NULL UNIQUE REFERENCES measurements(id) ON DELETE RESTRICT,
      threshold_version_id uuid REFERENCES threshold_versions(id) ON DELETE RESTRICT,
      stream_id uuid REFERENCES stream_states(id) ON DELETE RESTRICT,
      status varchar(40) NOT NULL, diagnostic varchar(160), breach boolean,
      live boolean NOT NULL DEFAULT false, content_hash varchar(64), created_at timestamptz NOT NULL DEFAULT now()
    );
    ALTER TABLE incidents DROP CONSTRAINT ck_incident_status;
    ALTER TABLE incidents DROP CONSTRAINT ck_incident_end_status;
    ALTER TABLE incidents ADD CONSTRAINT ck_incident_status CHECK(status IN ('active','recovering','resolved','closed'));
    ALTER TABLE incidents ADD CONSTRAINT ck_incident_end_status CHECK(
      (status IN ('active','recovering') AND ended_at IS NULL)
      OR (status IN ('resolved','closed') AND ended_at IS NOT NULL));
    ALTER TABLE incidents ADD COLUMN stream_id uuid REFERENCES stream_states(id) ON DELETE RESTRICT;
    ALTER TABLE incidents ADD COLUMN threshold_version_id uuid REFERENCES threshold_versions(id) ON DELETE RESTRICT;
    ALTER TABLE incidents ADD COLUMN location_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb;
    ALTER TABLE incidents ADD COLUMN latest_db double precision;
    ALTER TABLE incidents ADD COLUMN last_occurrence_at timestamptz;
    ALTER TABLE incidents ADD COLUMN breach_count integer NOT NULL DEFAULT 1;
    ALTER TABLE incidents ADD COLUMN recovery_streak integer NOT NULL DEFAULT 0;
    ALTER TABLE incidents ADD COLUMN closed_reason varchar(100);
    ALTER TABLE incidents ADD COLUMN previous_incident_id uuid REFERENCES incidents(id) ON DELETE RESTRICT;
    CREATE UNIQUE INDEX uq_incident_unresolved_stream ON incidents(stream_id) WHERE status IN ('active','recovering');
    CREATE TABLE event_clock (
      id integer PRIMARY KEY CHECK(id=1), last_position bigint NOT NULL DEFAULT 0,
      epoch uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
    );
    INSERT INTO event_clock(id,last_position,epoch) VALUES(1,0,gen_random_uuid());
    CREATE TABLE durable_events (
      id uuid PRIMARY KEY, pointer bigint NOT NULL UNIQUE, event_type varchar(80) NOT NULL,
      incident_id uuid REFERENCES incidents(id) ON DELETE RESTRICT,
      device_id uuid REFERENCES devices(id) ON DELETE RESTRICT,
      location_id uuid NOT NULL REFERENCES locations(id) ON DELETE RESTRICT,
      payload jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX ix_events_location_pointer ON durable_events(location_id,pointer);
    """)
    # Initial registered settings have baseline applicability. Legacy evaluations
    # do NOT inherit this rule: their original audio snapshot is authoritative.
    # Values outside newly declared supported ranges remain visible unchanged,
    # with no fabricated replacement threshold; an administrator must edit them.
    op.execute("""
    INSERT INTO threshold_versions(id,location_id,revision,effective_at,threshold_value,threshold_type,weighting,channel_policy,interval_seconds,recovery_count)
      SELECT gen_random_uuid(),id,1,'1970-01-01T00:00:00Z',threshold_value,threshold_type,
             CASE WHEN threshold_type='spl_z_leq' THEN 'Z' ELSE 'none' END,'mono',interval_seconds,3
      FROM locations WHERE (threshold_type='dbfs_rms' AND threshold_value BETWEEN -200 AND 0)
        OR (threshold_type='spl_z_leq' AND threshold_value BETWEEN -100 AND 200);
    INSERT INTO device_assignments(id,device_id,location_id,location_snapshot,effective_at)
      SELECT gen_random_uuid(),d.id,l.id,
        jsonb_build_object('id',l.id,'name',l.name,'timezone',l.timezone,
          'latitude',ST_Y(l.point::geometry),'longitude',ST_X(l.point::geometry)),
        '1970-01-01T00:00:00Z' FROM devices d JOIN locations l ON l.id=d.location_id;
    UPDATE devices d SET current_assignment_id=a.id FROM device_assignments a WHERE a.device_id=d.id;
    UPDATE audio_chunks c SET assignment_id=d.current_assignment_id FROM devices d
      WHERE c.device_id=d.id AND c.location_id=d.location_id;
    INSERT INTO device_assignments(id,device_id,location_id,location_snapshot,effective_at,ended_at)
      SELECT c.id,c.device_id,c.location_id,c.location_snapshot,c.captured_at,
        c.captured_at + make_interval(secs=>c.duration_seconds)
        FROM audio_chunks c WHERE c.assignment_id IS NULL;
    UPDATE audio_chunks SET assignment_id=id WHERE assignment_id IS NULL;
    UPDATE measurements m SET result_version='legacy-v1',is_reprocessing=true,
      received_at=c.received_at,calibration_snapshot=c.calibration,weighting=CASE WHEN m.measurement_type='spl_z_leq' THEN 'Z' ELSE 'none' END,
      quality_status=CASE WHEN m.value_db IS NULL AND m.digital_dbfs IS NULL THEN 'silence' ELSE 'good' END
      FROM audio_chunks c WHERE c.id=m.audio_chunk_id;
    INSERT INTO measurement_evaluations(id,measurement_id,status,diagnostic,breach,live)
      SELECT gen_random_uuid(),id,'legacy_historical','legacy_snapshot_preserved',breach,false FROM measurements;
    UPDATE incidents i SET location_snapshot=COALESCE(
      (SELECT c.location_snapshot FROM audio_chunks c WHERE c.device_id=i.device_id
         AND c.location_id=i.location_id AND c.captured_at=i.started_at ORDER BY c.id LIMIT 1),
      (SELECT jsonb_build_object('id',l.id,'name',l.name,'timezone',l.timezone,
         'latitude',ST_Y(l.point::geometry),'longitude',ST_X(l.point::geometry)) FROM locations l WHERE l.id=i.location_id)),
      latest_db=i.peak_db,last_occurrence_at=COALESCE(i.ended_at,i.started_at);
    UPDATE incidents SET status='closed',closed_reason='legacy_migration',ended_at=GREATEST(started_at,now()) WHERE status='active';
    """)
    # Database-enforced immutability applies even to accidental ORM updates.
    op.execute("""
    CREATE FUNCTION reject_immutable_row_change() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN RAISE EXCEPTION '% is append-only', TG_TABLE_NAME USING ERRCODE='55000'; END $$;
    CREATE TRIGGER threshold_versions_immutable BEFORE UPDATE OR DELETE ON threshold_versions
      FOR EACH ROW EXECUTE FUNCTION reject_immutable_row_change();
    CREATE TRIGGER measurement_evaluations_immutable BEFORE UPDATE OR DELETE ON measurement_evaluations
      FOR EACH ROW EXECUTE FUNCTION reject_immutable_row_change();
    CREATE TRIGGER durable_events_immutable BEFORE UPDATE OR DELETE ON durable_events
      FOR EACH ROW EXECUTE FUNCTION reject_immutable_row_change();
    """)
    role = os.environ.get("APP_DB_USER", "noise_app")
    quoted_role = '"' + role.replace('"', '""') + '"'
    op.execute(f"GRANT SELECT, INSERT ON threshold_versions, measurement_evaluations, durable_events TO {quoted_role}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON device_assignments, stream_states TO {quoted_role}")
    op.execute(f"GRANT SELECT, UPDATE ON event_clock TO {quoted_role}")
    op.execute(f"REVOKE DELETE ON measurements, incidents, audio_chunks FROM {quoted_role}")


def downgrade():
    # Collapsing multiple results and rule history would destroy evidence. Restore
    # a verified pre-upgrade backup if an operational rollback is necessary.
    raise RuntimeError("0002 is forward-only: restore a pre-upgrade backup instead of deleting incident history")

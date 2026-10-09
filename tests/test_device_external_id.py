"""Stable firmware names resolve only to registered, authenticated devices."""
from datetime import timedelta
import uuid

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, text

from tests.test_integration import CAPTURED, counts, device, location
from tests.test_pcm_upload_api import headers, raw_pcm


pytestmark = pytest.mark.integration


def register_named(client, admin_headers, external_id="UE-001"):
    loc = location(client, admin_headers, name="SIMULATED · External ID bench",
                   threshold_type="dbfs_rms", threshold_value=-20.0)
    dev = device(client, admin_headers, loc["id"], external_id=external_id,
                 microphone_model="SIMULATED PCM16 fixture")
    return loc, dev


def named_headers(dev, **kwargs):
    return {**headers(dev, **kwargs), "X-Device-ID": dev["external_id"]}


def send_named(client, dev, **kwargs):
    return client.post("/upload", headers=named_headers(dev, **kwargs), content=raw_pcm())


def test_external_id_is_registered_unique_visible_and_immutable(client, admin_headers):
    loc, dev = register_named(client, admin_headers)
    assert dev["external_id"] == "UE-001"
    assert uuid.UUID(dev["id"])
    listed = client.get("/devices", headers=admin_headers).json()["items"]
    assert listed[0]["external_id"] == "UE-001"
    assert "token" not in listed[0] and "credential_hash" not in listed[0]
    duplicate = client.post("/devices", headers=admin_headers,
                            json={"location_id": loc["id"], "external_id": "UE-001"})
    assert duplicate.status_code == 409, duplicate.text
    # Existing API clients can continue to omit an external identity.
    first = device(client, admin_headers, loc["id"])
    second = device(client, admin_headers, loc["id"])
    assert first["external_id"] is None and second["external_id"] is None
    rename = client.patch("/devices/" + dev["id"], headers=admin_headers,
                          json={"expected_revision": 1, "external_id": "UE-RENAMED"})
    assert rename.status_code == 422
    unchanged = client.get("/devices/" + dev["id"], headers=admin_headers).json()
    assert unchanged["external_id"] == "UE-001" and unchanged["config_revision"] == 1


@pytest.mark.parametrize("value", ["", "with space", "../escape", "UE.001", "écho", "x" * 33,
                                   "a" * 32, "0" * 32, "ABCDEF0123456789ABCDEF0123456789"])
def test_invalid_or_compact_uuid_external_id_rejected(client, admin_headers, value):
    loc = location(client, admin_headers)
    response = client.post("/devices", headers=admin_headers,
                           json={"location_id": loc["id"], "external_id": value})
    assert response.status_code == 422, response.text
    assert client.get("/devices", headers=admin_headers).json()["total"] == 0


def test_external_id_authentication_and_uuid_share_one_duplicate_identity(client, admin_headers, db):
    loc, dev = register_named(client, admin_headers)
    other = device(client, admin_headers, loc["id"], external_id="UE-002")
    first = send_named(client, dev)
    assert first.status_code == 200, first.text
    saved = first.json()
    assert saved["recording_key"] == uuid.UUID(dev["id"]).hex + "_boot_01"
    duplicate = client.post("/upload", headers=headers(dev), content=raw_pcm())
    assert duplicate.status_code == 200 and duplicate.json()["duplicate"]
    assert duplicate.json()["id"] == saved["id"]
    echo = client.post("/text", headers=named_headers(dev), content=b"UE-001 ready")
    assert echo.status_code == 200 and echo.json()["received"] == "UE-001 ready"
    wrong = {**named_headers(dev), "Authorization": "Bearer " + other["token"]}
    assert client.post("/upload", headers=wrong, content=raw_pcm()).status_code == 401
    assert client.post("/text", headers=wrong, content=b"spoofed").status_code == 401
    unknown = {**named_headers(dev), "X-Device-ID": "UE-UNREGISTERED"}
    assert client.post("/upload", headers=unknown, content=raw_pcm()).status_code == 401
    assert client.post("/text", headers=unknown, content=b"not registered").status_code == 401
    assert counts(db) == (1, 1, 0, 0)


def test_external_identity_survives_location_reassignment_and_late_uploads(client, admin_headers, fake_clock):
    original, dev = register_named(client, admin_headers)
    first = send_named(client, dev)
    assert first.status_code == 200, first.text
    replacement = location(client, admin_headers, name="SIMULATED · Replacement bench",
                           threshold_type="dbfs_rms", threshold_value=-30.0)
    moved = client.patch("/devices/" + dev["id"], headers=admin_headers,
                         json={"expected_revision": 1, "location_id": replacement["id"]})
    assert moved.status_code == 200, moved.text
    assert moved.json()["external_id"] == "UE-001"
    late = send_named(client, dev, sequence=1, captured=CAPTURED + timedelta(seconds=1))
    assert late.status_code == 200, late.text
    fake_clock.advance(1)
    current = send_named(client, dev, sequence=2, captured=fake_clock.now())
    assert current.status_code == 200, current.text
    rows = [client.get("/audio/" + response.json()["id"], headers=admin_headers).json()
            for response in (first, late, current)]
    assert [row["location_id"] for row in rows] == [original["id"], original["id"], replacement["id"]]
    assert [row["threshold_value"] for row in rows] == [-20, -20, -30]
    assert rows[0]["assignment_id"] == rows[1]["assignment_id"] != rows[2]["assignment_id"]
    assert {row["device_id"] for row in rows} == {dev["id"]}


def test_external_identity_is_case_sensitive_and_disabled_device_is_rejected(client, admin_headers):
    _, dev = register_named(client, admin_headers)
    lowered = {**named_headers(dev), "X-Device-ID": "ue-001"}
    assert client.post("/upload", headers=lowered, content=raw_pcm()).status_code == 401
    result = client.patch("/devices/" + dev["id"], headers=admin_headers,
                          json={"expected_revision": 1, "enabled": False})
    assert result.status_code == 200
    assert send_named(client, dev).status_code == 401


def test_external_id_migration_preserves_existing_device_row(privileged, monkeypatch):
    # Upgrade a separate disposable database without downgrading or clearing the
    # live database, or the application-test fixture's current schema.
    database = "test_noise_device_alias_" + uuid.uuid4().hex
    admin = create_engine(privileged.url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    migrated = None
    try:
        with admin.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{database}"'))
        migration_url = privileged.url.set(database=database)
        monkeypatch.setenv("MIGRATION_DATABASE_URL", migration_url.render_as_string(hide_password=False))
        configuration = Config("alembic.ini")
        command.upgrade(configuration, "0004_daily_summaries")
        migrated = create_engine(migration_url)
        ids = {"location": uuid.uuid4(), "device": uuid.uuid4()}
        with migrated.begin() as connection:
            connection.execute(text("""INSERT INTO locations(id,name,point,timezone,threshold_value,threshold_type,interval_seconds)
              VALUES(:location,'Existing place',ST_SetSRID(ST_MakePoint(77,12),4326)::geography,'UTC',-20,'dbfs_rms',1)"""), ids)
            connection.execute(text("""INSERT INTO devices(id,location_id,microphone_model,credential_hash)
              VALUES(:device,:location,'Existing microphone','synthetic-not-a-credential')"""), ids)
            before = dict(connection.execute(text("SELECT * FROM devices WHERE id=:device"), ids).mappings().one())
        command.upgrade(configuration, "head")
        with migrated.connect() as connection:
            after = dict(connection.execute(text("SELECT * FROM devices WHERE id=:device"), ids).mappings().one())
            assert after.pop("external_id") is None
            assert after == before
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0005_device_external_id"
    finally:
        if migrated is not None:
            migrated.dispose()
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)'))
        admin.dispose()

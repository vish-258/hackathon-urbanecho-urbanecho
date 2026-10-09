"""History pages remain bounded in query cost and expose usable device identity."""
from contextlib import contextmanager

import pytest
from sqlalchemy import event

from app.db import get_engine
from tests.test_integration import SYNTHETIC_CALIBRATION, accept, device, location, metadata, process


pytestmark = pytest.mark.integration


@contextmanager
def count_selects():
    statements = []

    def record(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    engine = get_engine()
    event.listen(engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", record)


def test_measurement_page_batches_related_history_without_changing_saved_rules(
        client, admin_headers, settings, fake_clock):
    loc = location(client, admin_headers)
    dev = device(client, admin_headers, loc["id"], calibration=SYNTHETIC_CALIBRATION)
    saved = []
    for sequence in range(8):
        if sequence == 4:
            updated = client.patch(f'/locations/{loc["id"]}/threshold', headers=admin_headers, json={
                "threshold_value": 100, "threshold_type": "spl_z_leq", "interval_seconds": 1,
                "expected_revision": 1,
            })
            assert updated.status_code == 200, updated.text
        saved.append(accept(client, dev, meta=metadata(
            dev, sequence=sequence, captured_at=fake_clock.now().isoformat()))["id"])
        process(settings)
        fake_clock.advance(1)

    sizes = []
    for limit in (1, 200):
        with count_selects() as queries:
            response = client.get("/measurements", headers=admin_headers,
                                  params={"location_id": loc["id"], "limit": limit})
        assert response.status_code == 200, response.text
        sizes.append(len(queries))
        assert response.json()["total"] == 8
    # Number of reads is independent of the number of returned measurements.
    assert sizes[0] == sizes[1] and sizes[1] <= 7
    rows = response.json()["items"]
    assert [row["audio_chunk_id"] for row in rows] == list(reversed(saved))
    assert [row["threshold_version"]["revision"] for row in rows] == [2] * 4 + [1] * 4
    assert [row["threshold_value"] for row in rows] == [100] * 4 + [75] * 4
    assert all(row["evaluation"] and row["device_id"] == dev["id"] for row in rows)
    assert all("file_path" not in row and "content_hash" not in row for row in rows)
    assert all("content_hash" not in row["evaluation"] for row in rows)
    for row in rows:
        detail = client.get(f'/audio/{row["audio_chunk_id"]}', headers=admin_headers).json()
        assert detail["measurements"] == [row]

    incident = client.get("/incidents", headers=admin_headers).json()["items"][0]
    with count_selects() as queries:
        related = client.get(f'/incidents/{incident["id"]}/measurements', headers=admin_headers)
    assert related.status_code == 200 and len(queries) <= 8
    assert [row["audio_chunk_id"] for row in related.json()["items"]] == saved[:4]
    empty = client.get("/measurements?offset=8", headers=admin_headers).json()
    assert empty["items"] == [] and empty["total"] == 8


def test_incidents_search_and_display_external_device_code_without_credential_leaks(
        client, admin_headers, settings, fake_clock):
    loc = location(client, admin_headers)
    devices = [device(client, admin_headers, loc["id"], external_id=code,
                      calibration=SYNTHETIC_CALIBRATION) for code in ("UE_001", "UEX001", None)]
    for dev in devices:
        accept(client, dev, meta=metadata(dev, captured_at=fake_clock.now().isoformat()))
        process(settings)

    # Underscores in a printed code are literal, not SQL wildcard characters.
    matching = client.get("/incidents", headers=admin_headers, params={"q": "ue_001"})
    assert matching.status_code == 200 and matching.json()["total"] == 1
    incident = matching.json()["items"][0]
    assert incident["device_id"] == devices[0]["id"]
    assert incident["device_external_id"] == "UE_001"
    detail = client.get(f'/incidents/{incident["id"]}', headers=admin_headers).json()
    assert detail["device_external_id"] == "UE_001"
    assert detail["device_id"] == incident["device_id"]
    by_uuid = client.get("/incidents", headers=admin_headers, params={"q": devices[0]["id"]}).json()
    assert [row["id"] for row in by_uuid["items"]] == [incident["id"]]
    all_incidents = client.get("/incidents", headers=admin_headers)
    assert {row["device_external_id"] for row in all_incidents.json()["items"]} == {"UE_001", "UEX001", None}
    assert all(dev["token"] not in all_incidents.text for dev in devices)
    assert "credential_hash" not in all_incidents.text

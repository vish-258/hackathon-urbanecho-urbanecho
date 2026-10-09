"""Replay fixtures must be repeatable and use actual local calendar boundaries."""
import importlib.util
import sys
import pytest
from datetime import date, datetime, timedelta
from pathlib import Path

scripts = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(scripts))
spec = importlib.util.spec_from_file_location("daily_demo", scripts / "demo-daily.py")
demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)


def test_replay_ids_and_bytes_inputs_are_repeatable():
    day = date(2026, 10, 8)
    for station in demo.LEVELS:
        first = demo.recording_plan(station, day)
        assert first == demo.recording_plan(station, day)
        assert len({row["chunk_id"] for row in first}) == len(first)
        assert any(row["level"] is None for row in first)
        dates = {datetime.fromisoformat(row["captured_at"]).date() for row in first}
        assert dates == {day, day + timedelta(days=1)}
        assert {row["chunk_id"] for row in first}.isdisjoint(
            {row["chunk_id"] for row in demo.recording_plan(station, day + timedelta(days=1))})


def test_cross_midnight_fixture_splits_half_a_second_on_each_local_day():
    gate = demo.recording_plan("gate", date(2026, 10, 8))
    row = next(item for item in gate if item["level"] == 65)
    capture = datetime.fromisoformat(row["captured_at"])
    assert capture.isoformat() == "2026-10-08T23:59:59.500000+05:30"
    assert (capture + timedelta(seconds=1)).isoformat() == "2026-10-09T00:00:00.500000+05:30"


def test_replay_refuses_recent_data_under_actual_server_freshness_policy():
    plans = {key: demo.recording_plan(key, date(2026, 10, 8)) for key in demo.LEVELS}
    now = datetime.fromisoformat("2026-10-09T00:04:00+05:30")
    demo.validate_replay_age(plans, now, 120)
    with pytest.raises(RuntimeError, match="older than"):
        demo.validate_replay_age(plans, now, 600)
    with pytest.raises(RuntimeError, match="disclose"):
        demo.validate_replay_age(plans, now, None)

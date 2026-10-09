"""Daily calculations use source history and never drive current noise alerts."""
from datetime import date, datetime, timedelta, timezone
import math
from types import SimpleNamespace
import uuid

import pytest
from sqlalchemy import func, select

from app.daily import (EligibleInterval, aggregate_intervals, day_bounds, definition_for,
                       exclusion_reason, generate_report, source_kind)
from app.models import DailyReport, DailySummary
from tests.live_helpers import BASE, NOW, SYNTHETIC_CALIBRATION, create_stream, put_reading

UTC = timezone.utc


def test_energy_average_is_duration_weighted_not_decibel_average():
    device = uuid.uuid4()
    data = [EligibleInterval(BASE, BASE + timedelta(seconds=1), 60, device),
            EligibleInterval(BASE + timedelta(seconds=1), BASE + timedelta(seconds=4), 70, device)]
    result = aggregate_intervals(data, 86400)
    assert result['average_db'] == pytest.approx(10 * math.log10((10**6 + 3 * 10**7) / 4))
    assert result['average_db'] != 65
    assert result['usable_duration_seconds'] == 4
    assert result['recorded_duration_seconds'] == 4
    assert result['coverage_percent'] == pytest.approx(4 / 864)
    assert result['coverage_status'] == 'partial'


def test_overlap_same_device_has_no_extra_weight_and_devices_are_averaged():
    first, second = uuid.uuid4(), uuid.uuid4()
    stop = BASE + timedelta(seconds=10)
    result = aggregate_intervals([EligibleInterval(BASE, stop, 60, first),
                                 EligibleInterval(BASE, stop, 60, first),
                                 EligibleInterval(BASE, stop, 70, second)], 86400)
    assert result['average_db'] == pytest.approx(10 * math.log10((10**6 + 10**7) / 2))
    assert result['usable_duration_seconds'] == 10
    assert result['recorded_duration_seconds'] == 30
    assert result['overlap_seconds'] == 20
    assert result['measurement_count'] == 3 and result['device_count'] == 2


def test_gaps_do_not_supply_silence_or_duration():
    device = uuid.uuid4()
    result = aggregate_intervals([EligibleInterval(BASE, BASE + timedelta(seconds=1), 75, device),
                                 EligibleInterval(BASE + timedelta(hours=2), BASE + timedelta(hours=2, seconds=1), 75, device)], 86400)
    assert result['average_db'] == pytest.approx(75)
    assert result['usable_duration_seconds'] == 2
    assert result['missing_duration_seconds'] == 86398


def test_no_usable_data_is_null_not_zero():
    result = aggregate_intervals([], 82800)
    assert result['average_db'] is None and result['minimum_db'] is None and result['maximum_db'] is None
    assert result['coverage_status'] == 'no_data'
    assert result['missing_duration_seconds'] == 82800


@pytest.mark.parametrize('day,hours', [(date(2026, 3, 8), 23), (date(2026, 11, 1), 25)])
def test_local_day_bounds_account_for_daylight_saving(day, hours):
    start, end = day_bounds(day, 'America/New_York')
    assert (end - start).total_seconds() == hours * 3600


def test_kolkata_midnight_is_previous_utc_evening():
    start, end = day_bounds(date(2026, 10, 9), 'Asia/Kolkata')
    assert start == datetime(2026, 10, 8, 18, 30, tzinfo=UTC)
    assert end == datetime(2026, 10, 9, 18, 30, tzinfo=UTC)


def test_simulation_marker_survives_reprocessing_and_rename():
    chunk = SimpleNamespace(calibration={'version': 'SYNTHETIC-EXAMPLE'}, location_snapshot={'name': 'Park'})
    measurement = SimpleNamespace(calibration_snapshot={'version': 'new-unmarked-version'})
    assert source_kind(chunk, measurement) == 'simulated'
    assert source_kind(location_snapshot={'name': 'Simulated garden'}) == 'simulated'
    assert source_kind(location_snapshot={'name': 'Garden'}) == 'recorded'


def _run(stream, day=date(2026, 10, 9), zone='UTC', now=None):
    from app.db import SessionLocal
    now = now or datetime(2026, 10, 11, tzinfo=UTC)
    with SessionLocal() as session, session.begin():
        report = session.scalar(select(DailyReport).where(DailyReport.location_id == stream.location_id,
                                                         DailyReport.reporting_date == day))
        if report is None:
            start, end = day_bounds(day, zone)
            report = DailyReport(location_id=stream.location_id, reporting_date=day, timezone=zone,
                                 day_start_utc=start, day_end_utc=end, status='queued', requested_at=now)
            session.add(report)
            session.flush()
        summaries = generate_report(session, report, now)
        return [(item.id, item.definition, item.statistics) for item in summaries]


@pytest.mark.integration
class TestPersistedDailySources:
    def test_recalculation_updates_one_saved_summary_and_latest_revision(self, fake_clock):
        fake_clock.set(NOW)
        stream = create_stream()
        initial = put_reading(stream, 0, 60, evaluate=False)
        original = _run(stream)
        assert original[0][2]['average_db'] == pytest.approx(60)
        put_reading(stream, 0, 75, source_id=initial.audio_id, result_version='reprocess-2',
                    reprocessing=True, evaluate=False)
        revised = _run(stream)
        assert revised[0][0] == original[0][0]
        assert revised[0][2]['average_db'] == pytest.approx(75)
        assert revised[0][2]['measurement_count'] == 1
        from app.db import SessionLocal
        with SessionLocal() as session:
            assert session.scalar(select(func.count()).select_from(DailySummary)) == 1
            # A fresh session reads the persisted value, not a calculation cache.
            assert session.scalar(select(DailySummary)).statistics['average_db'] == pytest.approx(75)

    def test_latest_invalid_result_does_not_fall_back_to_old_good_value(self, fake_clock):
        fake_clock.set(NOW)
        stream = create_stream()
        first = put_reading(stream, 0, 65, evaluate=False)
        original = _run(stream)
        put_reading(stream, 0, None, source_id=first.audio_id, result_version='reprocess-invalid',
                    reprocessing=True, result={'quality_status': 'clipped'}, evaluate=False)
        result = _run(stream)[0]
        assert result[0] == original[0][0]
        assert result[2]['average_db'] is None
        assert result[2]['coverage_status'] == 'no_data'
        assert result[2]['exclusion_reasons'] == {'quality_clipped': 1}

    def test_midnight_crossing_clips_both_local_days(self, fake_clock):
        fake_clock.set(NOW)
        stream = create_stream()
        capture = datetime(2026, 10, 9, 18, 29, 59, tzinfo=UTC)
        put_reading(stream, 0, 72, captured_at=capture,
                    chunk_fields={'duration_seconds': 2, 'interval_seconds': 2}, evaluate=False)
        for day in (date(2026, 10, 9), date(2026, 10, 10)):
            result = _run(stream, day, 'Asia/Kolkata')[0][2]
            assert result['usable_duration_seconds'] == 1
            assert result['measurement_count'] == 1
            assert result['average_db'] == pytest.approx(72)
        assert _run(stream, date(2026, 10, 8), 'Asia/Kolkata')[0][2]['coverage_status'] == 'no_data'

    def test_empty_day_preserves_simulation_label(self, fake_clock):
        fake_clock.set(NOW)
        stream = create_stream()
        put_reading(stream, 0, 65, evaluate=False)
        _, definition, stats = _run(stream, date(2026, 10, 8))[0]
        assert definition['source_kind'] == 'simulated'
        assert stats['average_db'] is None and stats['measurement_count'] == 0

    def test_assignment_end_is_checked_for_entire_recording(self, fake_clock):
        fake_clock.set(NOW)
        stream = create_stream()
        put_reading(stream, 0, 75, captured_at=BASE,
                    chunk_fields={'duration_seconds': 2, 'interval_seconds': 2}, evaluate=False)
        from app.db import SessionLocal
        from app.models import DeviceAssignment
        with SessionLocal() as session, session.begin():
            session.get(DeviceAssignment, stream.assignment_id).ended_at = BASE + timedelta(seconds=1)
        stats = _run(stream)[0][2]
        assert stats['average_db'] is None
        assert stats['exclusion_reasons'] == {'recording_crosses_assignment_change': 1}

    def test_current_device_enable_state_does_not_reject_historical_data(self, fake_clock):
        fake_clock.set(NOW)
        stream = create_stream()
        put_reading(stream, 0, 68, evaluate=False)
        from app.db import SessionLocal
        from app.models import Device
        with SessionLocal() as session, session.begin():
            session.get(Device, stream.device_id).enabled = False
        assert _run(stream)[0][2]['average_db'] == pytest.approx(68)

    def test_multiple_digital_devices_remain_separate_definitions(self, fake_clock):
        fake_clock.set(NOW)
        first = create_stream(threshold=-20, method='dbfs_rms')
        second = create_stream(method='dbfs_rms', location_id=first.location_id)
        put_reading(first, 0, -30, evaluate=False)
        put_reading(second, 0, -10, evaluate=False)
        summaries = _run(first)
        assert len(summaries) == 2
        assert {item[1]['device_id'] for item in summaries} == {str(first.device_id), str(second.device_id)}
        assert {item[2]['average_db'] for item in summaries} == {-30, -10}

    def test_simulated_and_recorded_calibrated_sources_never_mix(self, fake_clock):
        fake_clock.set(NOW)
        first = create_stream()
        second = create_stream(location_id=first.location_id)
        put_reading(first, 0, 60, evaluate=False)
        from app.db import SessionLocal
        from app.models import DeviceAssignment
        with SessionLocal() as session, session.begin():
            assignment = session.get(DeviceAssignment, second.assignment_id)
            assignment.location_snapshot = {**assignment.location_snapshot, 'name': 'Actual garden'}
        calibration = {**SYNTHETIC_CALIBRATION, 'version': 'example-calibration-record'}
        put_reading(second, 0, 80, chunk_fields={'calibration': calibration},
                    result={'calibration_version': calibration['version']}, evaluate=False)
        rows = _run(first)
        assert len(rows) == 2
        assert {item[1]['source_kind']: item[2]['average_db'] for item in rows} == {'simulated': 60, 'recorded': 80}

    def test_future_and_unfinished_captures_are_excluded_from_today(self, fake_clock):
        fake_clock.set(NOW)
        stream = create_stream()
        put_reading(stream, 0, 65, captured_at=NOW + timedelta(hours=1), evaluate=False)
        stats = _run(stream, now=NOW)[0][2]
        assert stats['is_provisional']
        assert stats['average_db'] is None
        assert stats['exclusion_reasons'] == {'capture_not_completed_at_calculation': 1}

    def test_incidents_count_by_start_time_and_generation_emits_no_live_events(self, fake_clock):
        fake_clock.set(NOW)
        stream = create_stream()
        put_reading(stream, 0, 75)
        put_reading(stream, 1, 80)
        from app.db import SessionLocal
        from app.models import DurableEvent, Incident
        with SessionLocal() as session:
            before = session.scalar(select(func.count()).select_from(DurableEvent))
            assert session.scalar(select(func.count()).select_from(Incident)) == 1
        assert _run(stream)[0][2]['incident_count'] == 1
        assert _run(stream, date(2026, 10, 10))[0][2]['incident_count'] == 0
        with SessionLocal() as session:
            assert session.scalar(select(func.count()).select_from(DurableEvent)) == before

    def test_processing_definition_change_removes_obsolete_summary(self, fake_clock):
        fake_clock.set(NOW)
        stream = create_stream()
        first = put_reading(stream, 0, 60, evaluate=False)
        original = _run(stream)[0]
        put_reading(stream, 0, 70, source_id=first.audio_id, result_version='new-algorithm', reprocessing=True,
                    result={'processing_version': 'different-version'}, evaluate=False)
        current = _run(stream)
        assert len(current) == 1 and current[0][0] != original[0]
        assert current[0][1]['processing_version'] == 'different-version'
        assert current[0][2]['average_db'] == pytest.approx(70)

    def test_failed_and_pending_audio_are_excluded_with_honest_counts(self, fake_clock):
        fake_clock.set(NOW)
        stream = create_stream()
        put_reading(stream, 0, 70, chunk_fields={'status': 'failed'}, evaluate=False)
        put_reading(stream, 1, 80, chunk_fields={'status': 'pending'}, evaluate=False)
        stats = _run(stream)[0][2]
        assert stats['measurement_count'] == 0
        assert stats['excluded_count'] == 2
        assert stats['exclusion_reasons'] == {'recording_failed': 1, 'recording_pending': 1}
        assert stats['average_db'] is None

    def test_expired_calibration_is_excluded(self, fake_clock):
        fake_clock.set(NOW)
        stream = create_stream()
        calibration = {**SYNTHETIC_CALIBRATION, 'valid_until': BASE.isoformat()}
        put_reading(stream, 0, 70, chunk_fields={'calibration': calibration}, evaluate=False)
        stats = _run(stream)[0][2]
        assert stats['exclusion_reasons'] == {'calibration_invalid_or_inapplicable': 1}
        assert stats['coverage_status'] == 'no_data'

    def test_latest_compatible_spl_devices_are_spatially_averaged(self, fake_clock):
        fake_clock.set(NOW)
        first = create_stream()
        second = create_stream(location_id=first.location_id)
        put_reading(first, 0, 60, evaluate=False)
        put_reading(second, 0, 70, evaluate=False)
        rows = _run(first)
        assert len(rows) == 1
        assert rows[0][2]['average_db'] == pytest.approx(10 * math.log10((10**6 + 10**7) / 2))
        assert rows[0][2]['usable_duration_seconds'] == 1
        assert rows[0][2]['recorded_duration_seconds'] == 2
        assert rows[0][2]['device_count'] == 2

    def test_reclassified_latest_result_preserves_original_incident_scope(self, fake_clock):
        fake_clock.set(NOW)
        stream = create_stream()
        from app.db import SessionLocal
        from app.models import AudioChunk, DeviceAssignment
        with SessionLocal() as session, session.begin():
            assignment = session.get(DeviceAssignment, stream.assignment_id)
            assignment.location_snapshot = {**assignment.location_snapshot, 'name': 'Recorded garden'}
        recorded_calibration = {**SYNTHETIC_CALIBRATION, 'version': 'calibration-record-1'}
        first = put_reading(stream, 0, 75, chunk_fields={'calibration': recorded_calibration},
                            result={'calibration_version': recorded_calibration['version']})
        # Reprocessing normally keeps the original chunk snapshot. Construct a
        # versioned result with a separate explicit simulated calibration snapshot
        # to verify report evidence remains correct even for this historical case.
        from app.models import Measurement
        with SessionLocal() as session, session.begin():
            original = session.get(Measurement, first.measurement_id)
            replacement = Measurement(audio_chunk_id=original.audio_chunk_id, result_version='simulated-reprocess',
                received_at=original.received_at, weighting='Z', channel_policy='mono', quality_status='good',
                is_reprocessing=True, measured_at=original.measured_at, interval_seconds=1,
                value_db=80, digital_dbfs=-20, measurement_type='spl_z_leq', calibration_status='calibrated',
                calibration_version=SYNTHETIC_CALIBRATION['version'], calibration_snapshot=dict(SYNTHETIC_CALIBRATION),
                processing_version=original.processing_version, breach=False)
            session.add(replacement)
        summaries = {item[1]['source_kind']: item[2] for item in _run(stream)}
        assert summaries['recorded']['incident_count'] == 1
        assert summaries['recorded']['average_db'] is None
        assert summaries['simulated']['incident_count'] == 0
        assert summaries['simulated']['average_db'] == pytest.approx(80)

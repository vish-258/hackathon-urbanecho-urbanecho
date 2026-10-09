"""Real PostGIS tests of atomic live evaluation using labelled synthetic results."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta, timezone
import uuid

import pytest
from sqlalchemy import select, text

from tests.live_helpers import (BASE, NOW, SYNTHETIC_CALIBRATION, add_rule,
                                create_stream, put_reading, records)

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def deterministic_clock(monkeypatch, integration_environment):
    from app import clock
    monkeypatch.setattr(clock, 'now', lambda: NOW)


def events(kind=None):
    from app.models import DurableEvent
    rows = records(DurableEvent)
    return [item for item in rows if item.event_type == kind] if kind else rows


def incident():
    from app.models import Incident
    rows = records(Incident)
    assert len(rows) == 1
    return rows[0]


@pytest.mark.parametrize('value,breach', [(55, False), (60, False), (70, True),
    (59.9999, False), (60.0001, True), (0, False), (-20, False), (-100, False), (200, True)])
def test_canonical_threshold_boundaries(value, breach):
    from app.models import Incident
    result = put_reading(create_stream(), 0, value)
    assert result.status == 'eligible_live' and result.breach is breach
    assert len(records(Incident)) == int(breach)
    assert len(events('incident.opened')) == int(breach)


def test_rounding_is_display_only_and_peaks_do_not_decrease():
    stream = create_stream()
    put_reading(stream, 0, 60.0001)
    assert round(incident().latest_db, 2) == 60.00
    put_reading(stream, 1, 80)
    put_reading(stream, 2, 70)
    assert incident().peak_db == 80 and incident().latest_db == 70
    assert incident().breach_count == 3 and len(events('incident.opened')) == 1


@pytest.mark.parametrize('value,breach', [(0, True), (-20, True), (-60, False), (-200, False)])
def test_digital_zero_negative_range(value, breach):
    result = put_reading(create_stream(-60, method='dbfs_rms'), 0, value)
    assert result.status == 'eligible_live' and result.breach is breach


def test_locations_use_independent_thresholds():
    first, second = create_stream(60), create_stream(80)
    assert put_reading(first, 0, 70).breach
    assert not put_reading(second, 0, 70).breach
    assert incident().location_id == first.location_id


def test_complete_seven_reading_sequence():
    from app.models import Measurement, Evaluation, StreamState
    stream = create_stream()
    for sequence, value in enumerate([55, 60, 70, 80, 60, 59, 58]):
        put_reading(stream, sequence, value)
        if sequence in (4, 5):
            assert incident().status == 'recovering'
            assert incident().recovery_streak == sequence - 3
    assert len(records(Measurement)) == 7 and len(records(Evaluation)) == 7
    assert incident().status == 'resolved' and incident().peak_db == 80
    assert incident().breach_count == 2 and incident().closed_reason == 'valid_recovery'
    assert len(events('incident.opened')) == len(events('incident.resolved')) == 1
    assert records(StreamState)[0].noise_status == 'normal'
    put_reading(stream, 7, 70)
    assert len(events('incident.opened')) == 2


@pytest.mark.parametrize('recovery', [1, 2, 4])
def test_configurable_recovery_count(recovery):
    stream = create_stream(recovery=recovery)
    put_reading(stream, 0, 70)
    for i in range(1, recovery):
        put_reading(stream, i, 60)
        assert incident().status == 'recovering'
    put_reading(stream, recovery, 60)
    assert incident().status == 'resolved'


def test_breach_resets_recovery_and_retains_peak():
    stream = create_stream()
    for index, level in enumerate([80, 60, 59, 70]):
        put_reading(stream, index, level)
    assert incident().recovery_streak == 0 and incident().status == 'active'
    assert incident().peak_db == 80
    for index, level in enumerate([60, 60, 60], 4):
        put_reading(stream, index, level)
    assert incident().status == 'resolved'


@pytest.mark.parametrize('interruption', ['gap', 'invalid', 'silence', 'clipped', 'reboot', 'calibration'])
def test_invalid_missing_or_noncontiguous_interrupt_recovery(interruption):
    stream = create_stream()
    put_reading(stream, 0, 70)
    put_reading(stream, 1, 60)
    put_reading(stream, 2, 59)
    kwargs, next_sequence = {}, 3
    if interruption == 'gap':
        next_sequence = 4
    elif interruption in ('invalid', 'silence', 'clipped'):
        quality = interruption if interruption != 'invalid' else 'unusable'
        outcome = put_reading(stream, 3, None if interruption == 'silence' else 50,
                              result={'quality_status': quality})
        assert outcome.status == 'ineligible'
        assert incident().status == 'active' and incident().recovery_streak == 0
        next_sequence = 4
    elif interruption == 'reboot':
        kwargs = {'session_id': 'new-boot', 'captured_at': BASE + timedelta(seconds=3)}
        next_sequence = 0
    elif interruption == 'calibration':
        kwargs = {'result': {'calibration_version': 'synthetic-test-only-v2'},
                  'chunk_fields': {'calibration': {**SYNTHETIC_CALIBRATION, 'version': 'synthetic-test-only-v2'}}}
    put_reading(stream, next_sequence, 58, **kwargs)
    assert incident().status == 'recovering' and incident().recovery_streak == 1
    assert len(events('incident.resolved')) == 0


def test_devices_never_share_recovery_and_disconnection_never_resolves(monkeypatch):
    from app import clock
    first = create_stream()
    second = create_stream(location_id=first.location_id)
    put_reading(first, 0, 70)
    for i in range(3):
        put_reading(second, i, 50)
    assert incident().status == 'active' and incident().recovery_streak == 0
    monkeypatch.setattr(clock, 'now', lambda: NOW + timedelta(days=3))
    assert incident().status == 'active'
    assert put_reading(first, 1, 50).diagnostic == 'old_capture'
    assert incident().status == 'active'


def test_recovery_survives_connection_pool_restart():
    from app.db import get_engine
    stream = create_stream()
    put_reading(stream, 0, 70)
    put_reading(stream, 1, 60)
    get_engine().dispose()
    put_reading(stream, 2, 59)
    assert incident().recovery_streak == 2
    get_engine().dispose()
    put_reading(stream, 3, 58)
    assert incident().status == 'resolved'


@pytest.mark.parametrize('result,chunk_fields,diagnostic', [
    ({'value_db': None}, {}, 'missing_value'),
    ({'weighting': 'A'}, {}, 'weighting_mismatch'),
    ({'channel_policy': 'stereo'}, {}, 'channel_policy_mismatch'),
    ({'measurement_type': 'dbfs_rms', 'value_db': -20}, {}, 'measurement_type_mismatch'),
    ({}, {'duration_seconds': 2}, 'interval_mismatch'),
    ({'quality_status': 'clipped'}, {}, 'quality_clipped'),
    ({'quality_status': 'silence', 'value_db': None}, {}, 'quality_silence'),
    ({'quality_status': 'invalid'}, {}, 'quality_invalid'),
    ({}, {'calibration': None}, 'calibration_invalid_or_inapplicable'),
    ({'calibration_status': 'calibration_required'}, {}, 'calibration_invalid_or_inapplicable'),
    ({'calibration_version': 'failed-calibration'}, {}, 'calibration_invalid_or_inapplicable'),
    ({}, {'calibration': {**SYNTHETIC_CALIBRATION, 'status': 'failed'}}, 'calibration_invalid_or_inapplicable'),
    ({}, {'calibration': {**SYNTHETIC_CALIBRATION, 'weighting': 'A'}}, 'calibration_invalid_or_inapplicable'),
    ({}, {'calibration': {**SYNTHETIC_CALIBRATION, 'channel_policy': 'stereo'}}, 'calibration_invalid_or_inapplicable'),
    ({}, {'calibration': {**SYNTHETIC_CALIBRATION, 'sample_rate': 48000}}, 'calibration_invalid_or_inapplicable'),
    ({}, {'calibration': {**SYNTHETIC_CALIBRATION, 'valid_until': '2026-01-02T00:00:00Z'}}, 'calibration_invalid_or_inapplicable'),
    ({'value_db': 201}, {}, 'value_out_of_range'),
    ({'value_db': -101}, {}, 'value_out_of_range'),
])
def test_ineligible_values_preserve_diagnostic_and_never_recover(result, chunk_fields, diagnostic):
    stream = create_stream()
    put_reading(stream, 0, 70)
    put_reading(stream, 1, 60)
    outcome = put_reading(stream, 2, 50, result=result, chunk_fields=chunk_fields)
    assert outcome.status == 'ineligible' and outcome.diagnostic == diagnostic
    assert outcome.breach is None and incident().status == 'active'
    assert incident().recovery_streak == 0 and len(events('incident.resolved')) == 0


@pytest.mark.parametrize('value', [True, False, '70', float('nan'), float('inf'), -float('inf')])
def test_non_strict_numeric_measurements_rejected(value):
    from app.models import Measurement
    stream = create_stream()
    with pytest.raises(ValueError, match='strict finite'):
        put_reading(stream, 0, value)
    assert not records(Measurement) and not events()


@pytest.mark.parametrize('method', ['dba', 'unsupported', None])
def test_unsupported_methods_rejected(method):
    with pytest.raises(ValueError, match='unsupported measurement_type'):
        put_reading(create_stream(), 0, 70, result={'measurement_type': method})


def test_disabled_device_and_forged_assignment_diagnostic():
    from app.db import SessionLocal
    from app.models import Device
    stream = create_stream()
    with SessionLocal() as session, session.begin():
        session.get(Device, stream.device_id).enabled = False
    assert put_reading(stream, 0, 70).diagnostic == 'disabled_device'
    with SessionLocal() as session, session.begin():
        session.get(Device, stream.device_id).enabled = True
    other = create_stream()
    outcome = put_reading(stream, 1, 70, chunk_fields={'assignment_id': other.assignment_id})
    assert outcome.diagnostic == 'invalid_assignment'
    assert not events('incident.opened')


@pytest.mark.parametrize('delta,diagnostic', [(0, 'duplicate_timestamp'), (-1, 'out_of_order'),
    (.5, 'overlapping_window'), (-300, 'old_capture'), (92, 'future_clock_skew'), (96, 'future_rejected')])
def test_historical_and_future_timing_never_changes_live_state(delta, diagnostic):
    from app.models import StreamState
    stream = create_stream()
    put_reading(stream, 0, 70)
    prior_event_count = len(events())
    outcome = put_reading(stream, 9, 50, captured_at=BASE + timedelta(seconds=delta))
    assert outcome.status == 'eligible_historical' and outcome.diagnostic == diagnostic
    assert records(StreamState)[0].watermark == BASE
    assert incident().status == 'active' and incident().recovery_streak == 0
    assert len(events()) == prior_event_count
    put_reading(stream, 1, 60)
    assert incident().recovery_streak == 1


def test_equivalent_timezone_offsets_are_duplicate_instants():
    stream = create_stream()
    put_reading(stream, 0, 70)
    same = BASE.astimezone(timezone(timedelta(hours=5, minutes=30)))
    assert put_reading(stream, 1, 60, captured_at=same).diagnostic == 'duplicate_timestamp'
    assert incident().status == 'active'


def test_invalid_observation_does_not_advance_watermark_or_allow_earlier_recovery():
    from app.models import StreamState
    stream = create_stream()
    put_reading(stream, 0, 70)
    put_reading(stream, 1, 60)
    put_reading(stream, 3, None, result={'quality_status': 'silence'})
    assert records(StreamState)[0].watermark == BASE + timedelta(seconds=1)
    assert put_reading(stream, 2, 59).diagnostic == 'out_of_order_after_invalid'
    put_reading(stream, 4, 58)
    assert incident().recovery_streak == 1


@pytest.mark.parametrize('new_threshold,new_level,new_breach', [(90, 80, False), (50, 55, True)])
def test_rule_change_closes_explicitly_on_first_eligible_measurement(new_threshold, new_level, new_breach):
    from app.models import Incident, Evaluation
    stream = create_stream()
    old = put_reading(stream, 0, 70)
    old_incident = incident()
    new_rule = add_rule(stream, at=BASE + timedelta(seconds=2), threshold=new_threshold)
    assert incident().status == 'active' and not events('incident.closed')
    put_reading(stream, 1, 80)
    assert incident().breach_count == 2
    put_reading(stream, 2, new_level)
    rows = records(Incident)
    closed = next(item for item in rows if item.id == old_incident.id)
    assert closed.status == 'closed' and closed.closed_reason == 'threshold_changed'
    assert len(events('incident.closed')) == 1 and not events('incident.resolved')
    assert next(item for item in records(Evaluation) if item.id == old.evaluation_id).threshold_version_id == stream.rule_id
    if new_breach:
        active = next(item for item in rows if item.status == 'active')
        assert active.threshold_version_id == new_rule and active.previous_incident_id == closed.id
    else:
        assert len(rows) == 1


def test_rule_change_method_and_delayed_old_stream_cannot_reinstate_previous_policy():
    from app.models import Incident
    stream = create_stream()
    put_reading(stream, 0, 70)
    add_rule(stream, at=BASE + timedelta(seconds=2), threshold=-60, method='dbfs_rms')
    put_reading(stream, 2, -20, result={'measurement_type': 'dbfs_rms', 'weighting': 'none',
                'calibration_status': 'not_required', 'calibration_version': None})
    before = len(events())
    late = put_reading(stream, 1, 80)
    assert late.diagnostic == 'out_of_order_policy_stream' and len(events()) == before
    assert len([item for item in records(Incident) if item.status == 'active']) == 1


def test_reassignment_retains_location_snapshot_and_explicit_link():
    from app.db import SessionLocal
    from app.models import Device, DeviceAssignment, Incident
    first, target = create_stream(), create_stream()
    put_reading(first, 0, 70)
    with SessionLocal() as session, session.begin():
        old = session.get(DeviceAssignment, first.assignment_id)
        old.ended_at = BASE + timedelta(seconds=2)
        session.flush()
        target_assignment = session.get(DeviceAssignment, target.assignment_id)
        new = DeviceAssignment(device_id=first.device_id, location_id=target.location_id,
            location_snapshot=dict(target_assignment.location_snapshot), effective_at=old.ended_at, created_at=NOW)
        session.add(new)
        session.flush()
        session.get(Device, first.device_id).current_assignment_id = new.id
        session.get(Device, first.device_id).location_id = target.location_id
        assignment_id = new.id
    put_reading(first, 1, 80)
    assert incident().status == 'active'  # The old assignment upload is historical only.
    replacement = type(first)(first.device_id, target.location_id, assignment_id, target.rule_id)
    put_reading(replacement, 2, 70)
    rows = records(Incident)
    closed = next(item for item in rows if item.status == 'closed')
    active = next(item for item in rows if item.status == 'active')
    assert closed.location_id == first.location_id and closed.location_snapshot['id'] == str(first.location_id)
    assert closed.closed_reason == 'device_reassigned' and active.previous_incident_id == closed.id


def test_reprocessing_is_versioned_historical_without_popups():
    from app.models import Measurement
    stream = create_stream()
    original = put_reading(stream, 0, 55)
    count = len(events())
    replacement = put_reading(stream, 0, 80, source_id=original.audio_id,
                              result_version='new-algorithm-v2', reprocessing=True)
    assert replacement.status == 'historical_reprocessing' and replacement.breach
    assert len(records(Measurement)) == 2 and len(events()) == count
    assert not events('incident.opened')


def test_identical_retries_are_noops_and_changed_content_conflicts():
    from app.db import SessionLocal
    from app.evaluation import MeasurementConflict, evaluate_measurement
    from app.models import Measurement, Evaluation
    stream = create_stream()
    first = put_reading(stream, 0, 70)
    retry = put_reading(stream, 0, 70, source_id=first.audio_id)
    assert retry.measurement_id == first.measurement_id and retry.evaluation_id == first.evaluation_id
    assert len(records(Evaluation)) == 1 and len(events('incident.opened')) == 1 and incident().breach_count == 1
    with pytest.raises(MeasurementConflict):
        put_reading(stream, 0, 80, source_id=first.audio_id)
    with SessionLocal() as session:
        measurement = session.get(Measurement, first.measurement_id)
        measurement.value_db = 80
        with pytest.raises(MeasurementConflict):
            evaluate_measurement(session, measurement)
        session.rollback()
    assert incident().breach_count == 1


def test_concurrent_workers_same_measurement_are_idempotent():
    from app.db import SessionLocal
    from app.evaluation import evaluate_measurement
    from app.models import Measurement, Evaluation
    source = put_reading(create_stream(), 0, 70, evaluate=False)
    def evaluate(_):
        with SessionLocal() as session, session.begin():
            return evaluate_measurement(session, session.get(Measurement, source.measurement_id)).id
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(evaluate, range(4)))
    assert len(set(ids)) == 1 and len(records(Evaluation)) == 1
    assert incident().breach_count == 1 and len(events('incident.opened')) == 1


def test_concurrent_first_breaches_leave_one_active_incident():
    stream = create_stream()
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda sequence: put_reading(stream, sequence, 70 + sequence), (0, 1)))
    assert incident().status == 'active' and incident().peak_db == 71
    assert len(events('incident.opened')) == 1


def test_concurrent_recovery_and_breach_keep_latest_breach_unresolved():
    from app.models import Incident, StreamState
    stream = create_stream()
    for sequence, value in enumerate((70, 60, 59)):
        put_reading(stream, sequence, value)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(put_reading, stream, 3, 58), pool.submit(put_reading, stream, 4, 80)]
        [future.result() for future in futures]
    active = [item for item in records(Incident) if item.status in ('active', 'recovering')]
    assert len(active) == 1 and active[0].status == 'active' and active[0].recovery_streak == 0
    assert records(StreamState)[0].watermark == BASE + timedelta(seconds=4)


@pytest.mark.parametrize('crash_kind', ['event_failure', 'before_commit'])
def test_transition_failure_rolls_back_all_state(monkeypatch, crash_kind):
    from app.db import SessionLocal
    from app import evaluation as evaluator
    from app.models import Evaluation, Incident, Measurement, StreamState
    stream = create_stream()
    if crash_kind == 'event_failure':
        def fail(*args, **kwargs):
            raise RuntimeError('synthetic durable event failure')
        monkeypatch.setattr(evaluator, 'emit_event', fail)
        with pytest.raises(RuntimeError, match='synthetic'):
            put_reading(stream, 0, 70)
    else:
        source = put_reading(stream, 0, 70, evaluate=False)
        with pytest.raises(RuntimeError):
            with SessionLocal() as session, session.begin():
                evaluator.evaluate_measurement(session, session.get(Measurement, source.measurement_id))
                raise RuntimeError('synthetic crash before commit')
    assert not records(Evaluation) and not records(Incident) and not records(StreamState) and not events()
    if crash_kind == 'event_failure':
        assert not records(Measurement)
    else:
        put_reading(stream, 0, 70, source_id=source.audio_id)
        assert len(events('incident.opened')) == 1


def test_database_partial_uniqueness_and_immutable_history(privileged):
    from app.models import Incident
    from sqlalchemy.exc import DBAPIError
    stream = create_stream()
    put_reading(stream, 0, 70)
    row = incident()
    with pytest.raises(DBAPIError):
        with privileged.begin() as connection:
            connection.execute(text('''INSERT INTO incidents
                (id,device_id,location_id,stream_id,threshold_version_id,location_snapshot,started_at,
                 threshold_value,threshold_type,peak_db,latest_db,last_occurrence_at,breach_count,recovery_streak,status)
                SELECT :new_id,device_id,location_id,stream_id,threshold_version_id,location_snapshot,started_at,
                       threshold_value,threshold_type,peak_db,latest_db,last_occurrence_at,breach_count,recovery_streak,status
                FROM incidents WHERE id=:existing_id'''), {'new_id': uuid.uuid4(), 'existing_id': row.id})
    assert len(records(Incident)) == 1


def test_event_payload_contains_public_identity_and_no_credentials():
    import json
    stream = create_stream()
    put_reading(stream, 0, 70)
    event = events('incident.opened')[0]
    required = ('event_id', 'incident_id', 'device_id', 'location_id', 'latitude', 'longitude',
                'measurement_value', 'measurement_type', 'interval_seconds', 'threshold_value',
                'threshold_version_id', 'measured_at', 'event_created_at', 'incident_status', 'transition_reason')
    assert all(key in event.payload for key in required)
    payload = json.dumps(event.payload).lower()
    assert 'credential' not in payload and 'token' not in payload and 'password' not in payload


@pytest.mark.parametrize('replacement', [True, '1'])
def test_identifier_conflict_detects_numeric_coercion_changes(replacement):
    from app.db import SessionLocal
    from app.evaluation import MeasurementConflict, evaluate_measurement
    from app.models import Measurement
    source = put_reading(create_stream(), 0, 1)
    with SessionLocal() as session:
        measurement = session.get(Measurement, source.measurement_id)
        measurement.value_db = replacement
        with pytest.raises(MeasurementConflict):
            evaluate_measurement(session, measurement)
        session.rollback()


def test_late_invalid_former_stream_cannot_alter_current_live_state():
    from app.models import StreamState
    stream = create_stream()
    put_reading(stream, 0, 70)
    add_rule(stream, at=BASE + timedelta(seconds=2), threshold=-60, method='dbfs_rms')
    put_reading(stream, 2, -20, result={'measurement_type': 'dbfs_rms', 'weighting': 'none',
                'calibration_status': 'not_required', 'calibration_version': None})
    count = len(events())
    result = put_reading(stream, 1, None, result={'quality_status': 'silence'})
    assert result.status == 'ineligible' and len(events()) == count
    states = records(StreamState)
    assert next(item for item in states if item.stream_key.startswith('dbfs_rms')).noise_status == 'excessive'


@pytest.mark.parametrize('age,live', [(120, True), (120.000001, False)])
def test_freshness_limit_boundary(age, live):
    result = put_reading(create_stream(), 0, 70, captured_at=NOW - timedelta(seconds=age))
    assert result.live is live
    assert len(events('incident.opened')) == int(live)

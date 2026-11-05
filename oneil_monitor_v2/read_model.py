"""Versioned monitor.v1 projection of execution-v2 store.read_model.

This module imports no execution engine, database, broker, network, or credential
code. A heartbeat proves only a worker's last reported presence, not connectivity.
"""
from datetime import datetime, timezone
import json
from uuid import UUID

STATES = {
    'QUEUED': ('pending', '대기'),
    'VALIDATING': ('pending', '검증 중'),
    'SUBMISSION_UNKNOWN': ('unknown', '결과 미확인'),
    'ACKNOWLEDGED': ('pending', '브로커 접수'),
    'PART_FILLED': ('pending', '부분 체결'),
    'FILLED': ('filled', '체결 완료'),
    'CANCELLED': ('closed', '취소'),
    'REJECTED': ('closed', '거절'),
    'EXPIRED': ('closed', '만료'),
    'BLOCKED': ('closed', '차단'),
}
UNRESOLVED = {'SUBMISSION_UNKNOWN', 'ACKNOWLEDGED', 'PART_FILLED'}


class SnapshotUnavailable(ValueError):
    """The incoming evidence cannot be presented as a trustworthy snapshot."""


def timestamp(value):
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError as error:
            raise SnapshotUnavailable('INVALID_TIMESTAMP') from error
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise SnapshotUnavailable('AWARE_TIMESTAMP_REQUIRED')
    return value.astimezone(timezone.utc)


def iso(value):
    return timestamp(value).isoformat().replace('+00:00', 'Z')


def evidence(value, now, max_age):
    if value is None:
        return {'status': 'missing', 'observed_at': None, 'age_seconds': None, 'max_age_seconds': max_age}
    stamp = timestamp(value)
    age = (now - stamp).total_seconds()
    status = 'clock_error' if age < 0 else ('fresh' if age <= max_age else 'stale')
    return {'status': status, 'observed_at': iso(stamp), 'age_seconds': age, 'max_age_seconds': max_age}


def integer(value, minimum=0):
    return type(value) is int and value >= minimum


def short_text(value, limit=200):
    if not isinstance(value, str) or len(value) > limit:
        raise SnapshotUnavailable('INVALID_TEXT_FIELD')
    return value


def project_status(raw, *, now, broker_max_age_seconds):
    """Read only. Caller supplies freshness policy; no policy guessed from a clock.

    Accept only the existing simulation contract. Unknown future request states
    remain visibly unknown. Invalid/misleading evidence rejects the whole snapshot.
    Private fields and any unrecognized fields are not serialized. The caller must
    set an honest source after binding this projection to an authenticated source.
    """
    now = timestamp(now)
    if not isinstance(raw, dict) or raw.get('mode') != 'SIMULATION' or raw.get('live_capable') is not False:
        raise SnapshotUnavailable('SIMULATION_ONLY')
    if not integer(broker_max_age_seconds, 1):
        raise SnapshotUnavailable('EXPLICIT_FRESHNESS_POLICY_REQUIRED')
    if type(raw.get('halted')) is not bool or not integer(raw.get('control_revision'), 1):
        raise SnapshotUnavailable('INVALID_CONTROL_EVIDENCE')
    as_of = timestamp(raw.get('as_of'))
    health = raw.get('health')
    if health is not None and not isinstance(health, dict):
        raise SnapshotUnavailable('INVALID_HEALTH')
    health = health or {}
    heartbeat = evidence(health.get('heartbeat_at'), now, 15)
    reconciliation = evidence(raw.get('broker_reconciled_at'), now, broker_max_age_seconds)
    snapshot = evidence(as_of, now, 15)
    worker_report = short_text(health.get('status', 'NOT_REPORTED'), 64)
    rows = raw.get('requests')
    if not isinstance(rows, (list, tuple)) or len(rows) > 100:
        raise SnapshotUnavailable('INVALID_REQUEST_WINDOW')
    requests, counts, seen = [], {key: 0 for key in ('pending', 'unknown', 'filled', 'closed')}, set()
    unresolved = 0
    for row in rows:
        if not isinstance(row, dict):
            raise SnapshotUnavailable('INVALID_REQUEST')
        try:
            request_id = str(UUID(str(row['request_id'])))
        except (KeyError, ValueError, TypeError, AttributeError) as error:
            raise SnapshotUnavailable('INVALID_REQUEST_ID') from error
        if request_id in seen:
            raise SnapshotUnavailable('DUPLICATE_REQUEST_ID')
        seen.add(request_id)
        state = short_text(row.get('state'), 64)
        group, label = STATES.get(state, ('unknown', '알 수 없는 상태'))
        attempts, filled = row.get('attempts'), row.get('filled_quantity')
        if not integer(attempts) or attempts > 1 or not integer(filled):
            raise SnapshotUnavailable('INVALID_EXECUTION_EVIDENCE')
        if filled > 0 and attempts != 1:
            raise SnapshotUnavailable('FILL_WITHOUT_ATTEMPT')
        if state in {'SUBMISSION_UNKNOWN', 'ACKNOWLEDGED', 'PART_FILLED', 'FILLED'} and attempts != 1:
            raise SnapshotUnavailable('MISSING_ATTEMPT_EVIDENCE')
        if state in {'QUEUED', 'VALIDATING', 'ACKNOWLEDGED', 'REJECTED', 'EXPIRED', 'BLOCKED'} and filled != 0:
            raise SnapshotUnavailable('CONTRADICTORY_FILL_EVIDENCE')
        if state in {'FILLED', 'PART_FILLED'} and filled <= 0:
            raise SnapshotUnavailable('MISSING_FILL_EVIDENCE')
        if state in {'QUEUED', 'VALIDATING', 'EXPIRED', 'BLOCKED'} and attempts != 0:
            raise SnapshotUnavailable('CONTRADICTORY_ATTEMPT_EVIDENCE')
        created, updated = timestamp(row.get('created_at')), timestamp(row.get('updated_at'))
        if created > updated or updated > as_of:
            raise SnapshotUnavailable('INVALID_REQUEST_CHRONOLOGY')
        unresolved += state in UNRESOLVED or state not in STATES
        counts[group] += 1
        requests.append({
            'request_id': request_id, 'state': state, 'label': label, 'group': group,
            'reason': short_text(row.get('reason', '')),
            'attempts': attempts, 'filled_quantity': filled,
            'created_at': iso(created), 'updated_at': iso(updated),
            'can_cancel_before_admission': state in {'QUEUED', 'VALIDATING'} and attempts == 0,
            # Core list projection has no order content. Do not invent a ticker,
            # requested quantity, fill percentage, venue, or execution price.
            'order_details_available': False,
        })
    warnings = []
    if raw['halted']:
        warnings.append('ACCOUNT_HALTED')
    if counts['unknown']:
        warnings.append('UNKNOWN_OUTCOME_DO_NOT_RESUBMIT')
    for name, item in [('SNAPSHOT', snapshot), ('WORKER', heartbeat), ('BROKER_SYNC', reconciliation)]:
        if item['status'] != 'fresh':
            warnings.append(f'{name}_{item["status"].upper()}')
    return {
        'schema_version': 'monitor.v1', 'mode': 'SIMULATION', 'live_capable': False,
        'source': 'UNBOUND_SIMULATION_PROJECTION', 'read_only': True,
        'server_time': iso(now), 'snapshot': snapshot,
        'control': {'halted': raw['halted'], 'revision': raw['control_revision']},
        'heartbeat': {**heartbeat, 'reported_state': worker_report},
        'broker_sync': reconciliation,
        'strategy': {'state': 'NOT_REPORTED'},
        'session': {'state': 'NOT_VERIFIED'},
        'owner_authorization': {'state': 'NOT_IMPLEMENTED'},
        'requests': requests, 'counts': counts, 'unresolved_in_window': unresolved,
        'request_window': {'limit': 100, 'returned': len(requests), 'scope': 'LATEST_REQUESTS_ONLY',
                           'account_total_known': False, 'may_be_truncated': len(requests) == 100},
        'warnings': warnings,
        'controls': {
            name: {'enabled': False, 'reason': 'READ_ONLY_PROTOTYPE'}
            for name in ('halt', 'resume_simulation', 'cancel_pending', 'confirm_order')
        },
    }


def json_bytes(model):
    return json.dumps(model, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')

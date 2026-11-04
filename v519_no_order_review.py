"""Offline review boundary for unknown BUY intents, never an order/retry path.

Only a trusted operator may supply verified complete, final broker history.
Screenshots, empty holdings and empty pending lists are NOT such evidence.
No CLI, scheduled workflow, broker transport, or automatic activation is exposed.
"""
import datetime as dt
import hashlib
import json
import re
from zoneinfo import ZoneInfo

from v519_pg_ledger import LedgerBlocked

KST = ZoneInfo('Asia/Seoul')
AUDIT_SCHEMA = '''
CREATE TABLE IF NOT EXISTS v519_no_order_reviews (
 intent_id text PRIMARY KEY REFERENCES v519_order_intents(intent_id),
 account_fingerprint text NOT NULL,
 evidence_sha256 text NOT NULL CHECK (evidence_sha256 ~ '^[0-9a-f]{64}$'),
 reviewer text NOT NULL,
 reviewed_at timestamptz NOT NULL,
 recorded_at timestamptz NOT NULL DEFAULT now(),
 resolution text NOT NULL CHECK (resolution = 'OPERATOR_VERIFIED_NO_ORDER')
);
ALTER TABLE v519_no_order_reviews ENABLE ROW LEVEL SECURITY;
'''


def prepare_review(intent, history, *, now):
    """Validate operator attestation, returning a digest, not permission to trade.

    `history` must come from a trusted operator review, not arbitrary web/UI
    input. The caller must verify broker account mapping, all filters/pages,
    cancellations/rejections, and finality independently before attesting.
    Reject the current KST day because an absent row may be delayed.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise LedgerBlocked('AWARE_CLOCK_REQUIRED')
    if not isinstance(intent, dict) or not isinstance(history, dict):
        raise LedgerBlocked('REVIEW_INVALID')
    if (intent.get('status') != 'SUBMISSION_UNKNOWN' or intent.get('side') != 'BUY'
            or intent.get('broker_order_id') is not None
            or type(intent.get('filled_quantity')) is not int or intent['filled_quantity'] != 0
            or intent.get('verified_not_sent') is not False):
        raise LedgerBlocked('NO_ORDER_REVIEW_STATE_CONFLICT')
    fingerprint = intent.get('account_fingerprint')
    day = intent.get('trading_day')
    if (not isinstance(fingerprint, str) or not re.fullmatch('[0-9a-f]{64}', fingerprint)
            or type(day) is not dt.date or day >= now.astimezone(KST).date()
            or not isinstance(intent.get('intent_id'), str)
            or not 16 <= len(intent['intent_id']) <= 128
            or not isinstance(intent.get('ticker'), str)
            or not re.fullmatch('[0-9]{6}', intent['ticker'])):
        raise LedgerBlocked('COMPLETED_DAY_AND_IDENTITY_REQUIRED')
    required = ('account_mapping_verified', 'all_statuses', 'all_sides',
                'all_tickers', 'all_venues', 'all_pages', 'broker_finality_verified',
                'operator_approved')
    if (any(history.get(k) is not True for k in required)
            or history.get('account_fingerprint') != fingerprint
            or history.get('trading_day') != day.isoformat()
            or history.get('intent_id') != intent['intent_id']):
        raise LedgerBlocked('FULL_VERIFIED_HISTORY_REQUIRED')
    reviewer = history.get('reviewer')
    source = history.get('source_evidence_sha256')
    if (not isinstance(reviewer, str) or not re.fullmatch('[A-Za-z0-9-]{1,39}', reviewer)
            or not isinstance(source, str) or not re.fullmatch('[0-9a-f]{64}', source)):
        raise LedgerBlocked('AUDIT_IDENTITY_REQUIRED')
    reviewed = history.get('reviewed_at')
    try:
        stamp = dt.datetime.fromisoformat(reviewed)
        valid = (stamp.tzinfo is not None and stamp.utcoffset() is not None
                 and 0 <= (now - stamp).total_seconds() <= 900
                 and stamp.astimezone(KST).date() > day)
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise LedgerBlocked('REVIEW_STALE_OR_INVALID')
    rows = history.get('rows')
    if not isinstance(rows, list):
        raise LedgerBlocked('HISTORY_ROWS_INVALID')
    for row in rows:
        if (not isinstance(row, dict) or not isinstance(row.get('pdno'), str)
                or not re.fullmatch('[0-9]{6}', row['pdno'])
                or row.get('ord_dt') != day.strftime('%Y%m%d')):
            raise LedgerBlocked('HISTORY_ROWS_INVALID')
        # Any order for the ticker, regardless of side/status/quantity, blocks
        # negative reconciliation. Do not guess which row belongs to the intent.
        if row['pdno'] == intent['ticker']:
            raise LedgerBlocked('BROKER_ORDER_PRESENT')
    try:
        digest = hashlib.sha256(json.dumps(history, sort_keys=True, separators=(',', ':'),
                                          allow_nan=False).encode()).hexdigest()
    except (TypeError, ValueError):
        raise LedgerBlocked('HISTORY_ROWS_INVALID') from None
    return {'evidence_sha256': digest, 'reviewer': reviewer, 'reviewed_at': stamp}


def record_no_order_review(ledger, intent_id, history, *, now, env):
    """Privileged, explicit operator operation. Caller owns DB authorization.

    Uses the same runtime account lock and transaction/control row lock. An
    audit insert failure rolls back resolution. Never deletes/renames an intent,
    changes its day/ticker, grants SELL retries, clears halt, or activates release.
    AUDIT_SCHEMA must be installed separately by an authorized migration.
    """
    if any(env.get(k, 'false').strip().lower() != 'false'
           for k in ('LIVE_ENABLED', 'KIS_ALLOW_ORDER')):
        raise LedgerBlocked('LIVE_SWITCH_MUST_BE_OFF')
    with ledger.lock(), ledger.conn.transaction():
        controls = ledger.rows('SELECT halted FROM v519_control WHERE singleton=true FOR UPDATE')
        if len(controls) != 1 or controls[0]['halted'] is not True:
            raise LedgerBlocked('EMERGENCY_HALT_REQUIRED')
        rows = ledger.rows('''SELECT * FROM v519_order_intents
            WHERE intent_id=%s AND account_fingerprint=%s FOR UPDATE''',
                           (intent_id, ledger.fingerprint))
        if len(rows) != 1:
            raise LedgerBlocked('INTENT_NOT_FOUND')
        review = prepare_review(rows[0], history, now=now)
        ledger.conn.execute('''INSERT INTO v519_no_order_reviews
            (intent_id,account_fingerprint,evidence_sha256,reviewer,reviewed_at,resolution)
            VALUES (%s,%s,%s,%s,%s,'OPERATOR_VERIFIED_NO_ORDER')''',
            (intent_id, ledger.fingerprint, review['evidence_sha256'],
             review['reviewer'], review['reviewed_at']))
        updated = ledger.conn.execute('''UPDATE v519_order_intents SET status='REJECTED',updated_at=now()
            WHERE intent_id=%s AND account_fingerprint=%s RETURNING intent_id''',
            (intent_id, ledger.fingerprint)).fetchall()
        if len(updated) != 1:
            raise LedgerBlocked('NO_ORDER_RESOLUTION_NOT_APPLIED')
    return {'resolution': 'OPERATOR_VERIFIED_NO_ORDER', 'orders_sent': 0,
            'gate': 'DENY', 'halt_preserved': True, 'daily_buy_guard_preserved': True}

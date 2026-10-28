"""Durable KIS order submission core for V5.19.

This is an integration component, not an independently approved live trader.
Caller must provide independently verified order/risk evidence and approval.
Unknown KIS responses permanently lock the intent pending reconciliation.
"""
import hashlib
import re
from v519_pg_ledger import connect, LedgerBlocked

CODE = re.compile(r'^[0-9]{6}$')
BROKER_ID = re.compile(r'^[0-9A-Za-z-]{1,64}$')


def make_intent_id(account, trading_day, ticker, side, signal):
    if not re.fullmatch(r'[0-9]{8}', account) or not CODE.fullmatch(ticker):
        raise LedgerBlocked('BAD_ACCOUNT_OR_TICKER')
    if side not in ('BUY', 'SELL') or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', signal):
        raise LedgerBlocked('BAD_SIDE_OR_SIGNAL')
    return hashlib.sha256(f'v519|{account}|{trading_day}|{ticker}|{side}|{signal}'.encode()).hexdigest()


def record_intent(*, intent_id, fingerprint, ticker, side, quantity):
    """Commit durable reservation before any broker network call."""
    if not re.fullmatch('[0-9a-f]{64}', fingerprint) or not CODE.fullmatch(ticker):
        raise LedgerBlocked('INVALID_IDENTITY')
    if side not in ('BUY', 'SELL') or type(quantity) is not int or quantity < 1:
        raise LedgerBlocked('INVALID_ORDER')
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT halted FROM v519_control WHERE singleton=true FOR UPDATE')
            row = cur.fetchone()
            if row is None or row[0] is not False:
                raise LedgerBlocked('EMERGENCY_HALT_ACTIVE')
            cur.execute('''SELECT 1 FROM v519_order_intents WHERE account_fingerprint=%s
                AND status NOT IN ('FILLED','CANCELLED','REJECTED') LIMIT 1''', (fingerprint,))
            if cur.fetchone():
                raise LedgerBlocked('UNRESOLVED_INTENT_EXISTS')
            cur.execute('''INSERT INTO v519_order_intents
                (intent_id,account_fingerprint,ticker,side,venue,quantity,status)
                VALUES (%s,%s,%s,%s,'KRX',%s,'SUBMISSION_UNKNOWN')
                ON CONFLICT DO NOTHING RETURNING intent_id''',
                (intent_id, fingerprint, ticker, side, quantity))
            if cur.fetchone() is None:
                raise LedgerBlocked('DUPLICATE_INTENT')
    # Context manager commits before returning. Never retry a rejected/unknown insert.


def persist_acknowledgement(intent_id, broker_id):
    if not isinstance(broker_id, str) or not BROKER_ID.fullmatch(broker_id):
        raise LedgerBlocked('BROKER_ID_INVALID')
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute('''UPDATE v519_order_intents SET broker_order_id=%s,
                status='ACKNOWLEDGED',updated_at=now() WHERE intent_id=%s
                AND status='SUBMISSION_UNKNOWN' RETURNING intent_id''',
                (broker_id, intent_id))
            if cur.fetchone() is None:
                raise LedgerBlocked('ACK_CONFLICT')


def submit_once(*, intent_id, fingerprint, ticker, side, quantity, price,
                verified, approved, broker_submit):
    """Single submission attempt; broker_submit injected for integration.

    `verified` is a structured *independently obtained* attestation, not a CLI
    assertion. The current scanner does not supply it, so live execution is blocked.
    """
    if approved is not True or verified is not True:
        raise LedgerBlocked('INDEPENDENT_LIVE_RISK_APPROVAL_MISSING')
    if type(price) is not int or price <= 0 or type(quantity) is not int or quantity <= 0:
        raise LedgerBlocked('INVALID_SIZE')
    # Legacy bool attestations cannot authorize orders. Use integrated runtime.
    raise LedgerBlocked('RELEASE_LOCKED_USE_INTEGRATED_ENGINE')

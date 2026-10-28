"""V5.19 unified order gateway: durable reservation and KIS acknowledgement.
NO AUTOMATIC LIVE ORDERS: full-account risk attestation is not yet available.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import re
from zoneinfo import ZoneInfo
from v519_pg_ledger import connect, LedgerBlocked
from oneil_v519_live_runner import token, live_submit

KST = ZoneInfo("Asia/Seoul")
ACCOUNT = re.compile(r"^[0-9]{8}$")
TICKER = re.compile(r"^[0-9]{6}$")
FINAL = {"FILLED", "CANCELLED", "REJECTED"}

def proposal_id(account, ticker, side, signal_id, trading_day):
    if not ACCOUNT.fullmatch(account) or not TICKER.fullmatch(ticker):
        raise LedgerBlocked("INVALID_ACCOUNT_OR_TICKER")
    if side not in ("BUY", "SELL") or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", signal_id):
        raise LedgerBlocked("INVALID_SIDE_OR_SIGNAL")
    return hashlib.sha256(f"v519|{account}|{trading_day}|{ticker}|{side}|{signal_id}".encode()).hexdigest()

def validate(ticker, side, qty, price, signal):
    if not TICKER.fullmatch(ticker) or side not in ("BUY", "SELL"):
        raise LedgerBlocked("INVALID_ORDER")
    if type(qty) is not int or type(price) is not int or qty <= 0 or price <= 0:
        raise LedgerBlocked("INVALID_SIZE")
    if qty * price > 180000:
        raise LedgerBlocked("PER_STOCK_CAP_EXCEEDED")
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", signal):
        raise LedgerBlocked("INVALID_SIGNAL")
    now = dt.datetime.now(KST)
    if now.weekday() >= 5 or not dt.time(9) <= now.time() < dt.time(15, 30):
        raise LedgerBlocked("OUTSIDE_REGULAR_SESSION")
    return now

def reserve_intent(conn, ident, fingerprint, ticker, side, qty):
    """One transaction, account-wide lock, unique ID. No broker call in transaction."""
    with conn.cursor() as cur:
        cur.execute("SELECT halted FROM v519_control WHERE singleton=true FOR UPDATE")
        row = cur.fetchone()
        if row is None or row[0] is not False:
            raise LedgerBlocked("EMERGENCY_HALT")
        cur.execute("""SELECT 1 FROM v519_order_intents
            WHERE account_fingerprint=%s AND status NOT IN ('FILLED','CANCELLED','REJECTED')
            LIMIT 1""", (fingerprint,))
        if cur.fetchone():
            raise LedgerBlocked("UNRESOLVED_ORDER")
        cur.execute("""INSERT INTO v519_order_intents
            (intent_id,account_fingerprint,ticker,side,venue,quantity,status)
            VALUES (%s,%s,%s,%s,'KRX',%s,'SUBMISSION_UNKNOWN')
            ON CONFLICT DO NOTHING RETURNING intent_id""",
            (ident,fingerprint,ticker,side,qty))
        if cur.fetchone() is None:
            raise LedgerBlocked("DUPLICATE_INTENT")
    # The caller must commit BEFORE submitting to KIS.

def acknowledge(ident, broker_id):
    """Persist acknowledged KIS order; uncertain outcomes stay SUBMISSION_UNKNOWN."""
    if not isinstance(broker_id,str) or not re.fullmatch(r"[A-Za-z0-9-]{1,64}",broker_id):
        raise LedgerBlocked("BROKER_ID_INVALID")
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""UPDATE v519_order_intents SET
                broker_order_id=%s,status='ACKNOWLEDGED',updated_at=now()
                WHERE intent_id=%s AND status='SUBMISSION_UNKNOWN'
                RETURNING intent_id""",(broker_id,ident))
            if cur.fetchone() is None:
                raise LedgerBlocked("ACK_STATE_CONFLICT")

def execute(args):
    # Deliberate fail-closed: account-wide exposure, loss limits and verified
    # broker history have not been independently integrated. Never override
    # this gate with a workflow variable or self-attested CLI flag.
    raise LedgerBlocked("FULL_ACCOUNT_RISK_AND_RECOVERY_GATE_NOT_INTEGRATED")

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--ticker",required=True)
    p.add_argument("--side",choices=["BUY","SELL"],required=True)
    p.add_argument("--quantity",type=int,required=True)
    p.add_argument("--price",type=int,required=True)
    p.add_argument("--signal-id",required=True)
    p.add_argument("--execute",action="store_true")
    a=p.parse_args()
    try:
        now=validate(a.ticker,a.side,a.quantity,a.price,a.signal_id)
        if a.execute:
            execute(a)
        print(json.dumps({"gate":"DENY","phase":"VALIDATED_PROPOSAL","orders_sent":0,
                          "trading_day":now.date().isoformat()}))
    except Exception as e:
        print(json.dumps({"gate":"DENY","orders_sent":0,
                          "reason":str(e) if isinstance(e,LedgerBlocked) else "UNVERIFIED_FAILURE"}))
        raise SystemExit(2)
if __name__=="__main__": main()

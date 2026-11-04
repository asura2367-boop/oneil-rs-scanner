"""Standalone incident history evidence. No ledger writes or order transport.
Absence is evidence only, never permission to release an unknown order.
"""
import argparse
import datetime as dt
import hashlib
import hmac
import json
import os
import re
from zoneinfo import ZoneInfo
from oneil_v519_history_observe import fetch_page, flags_off

KST = ZoneInfo("Asia/Seoul")

def read_reference(conn, day):
    # The connection must already enforce default_transaction_read_only=on.
    with conn.cursor() as cur:
        cur.execute("SHOW transaction_read_only")
        if cur.fetchone() != ("on",):
            raise ValueError("READ_ONLY_REQUIRED")
        cur.execute("""SELECT account_fingerprint FROM public.v519_order_intents
            WHERE trading_day=%s AND status='SUBMISSION_UNKNOWN'""", (day,))
        rows = cur.fetchall()
    if len(rows) != 1 or not re.fullmatch("[0-9a-f]{64}", str(rows[0][0])):
        raise ValueError("INCIDENT_REFERENCE_AMBIGUOUS")
    return rows[0][0]

def collect(provider, day, max_pages=100):
    fk = nk = ""
    seen = set()
    count = 0
    for page in range(max_pages):
        cont, body = provider(day, fk, nk, page > 0)
        if not isinstance(body, dict) or str(body.get("rt_cd")) != "0":
            raise ValueError("INVALID_RESPONSE")
        if not isinstance(cont, str) or cont.strip().upper() not in ("", "D", "E", "F", "M"):
            raise ValueError("INVALID_CONTINUATION")
        rows = body.get("output1")
        if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
            raise ValueError("INVALID_ROWS")
        count += len(rows)
        if count > 10000:
            raise ValueError("ROW_LIMIT")
        if cont.strip().upper() not in ("F", "M"):
            return page + 1, count
        nf, nn = body.get("ctx_area_fk100"), body.get("ctx_area_nk100")
        if not isinstance(nf, str) or not isinstance(nn, str):
            raise ValueError("INVALID_CURSOR")
        nf, nn = nf.strip(), nn.strip()
        if not nf or not nn or (nf, nn) in seen:
            raise ValueError("INVALID_CURSOR")
        seen.add((nf, nn))
        fk, nk = nf, nn
    raise ValueError("PAGE_LIMIT")

def observe(day, now, account, product, reference, provider, env):
    if not flags_off(env):
        raise ValueError("LIVE_SWITCH_NOT_DISABLED")
    if now.tzinfo is None:
        raise ValueError("NAIVE_CLOCK")
    if not re.fullmatch(r"[0-9]{8}", day):
        raise ValueError("INVALID_DAY")
    date = dt.datetime.strptime(day, "%Y%m%d").date()
    age = (now.astimezone(KST).date() - date).days
    if not 1 <= age <= 89:
        raise ValueError("DAY_NOT_CLOSED_OR_OUT_OF_RANGE")
    if not re.fullmatch(r"[0-9]{8}", account) or not re.fullmatch(r"[0-9]{2}", product):
        raise ValueError("ACCOUNT_INVALID")
    fingerprint = hashlib.sha256((account + "|" + product).encode()).hexdigest()
    if not isinstance(reference, str) or not re.fullmatch("[0-9a-f]{64}", reference) or not hmac.compare_digest(fingerprint, reference):
        raise ValueError("ACCOUNT_REFERENCE_MISMATCH")
    pages, rows = collect(provider, day)
    return {"status": "CLOSED_DAY_HISTORY_OBSERVED_NOT_RECONCILED",
            "trading_day": date.isoformat(), "observed_at_kst": now.astimezone(KST).isoformat(),
            "account_matches_incident": True, "day_closed": True,
            "pagination_complete": True, "venues_requested": "ALL",
            "statuses_requested": "ALL", "pages_observed": pages, "rows_observed": rows,
            "gate": "DENY", "orders_sent": 0, "broker_order_api_calls": 0,
            "ledger_writes": 0, "unknown_order_released": False,
            "independent_reconciliation_verified": False}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--day", required=True)
    args = parser.parse_args()
    try:
        if not flags_off(os.environ):
            raise ValueError("LIVE_SWITCH_NOT_DISABLED")
        day = dt.datetime.strptime(args.day, "%Y-%m-%d").date()
        now = dt.datetime.now(KST)
        if not 1 <= (now.date() - day).days <= 89:
            raise ValueError("DAY_NOT_CLOSED_OR_OUT_OF_RANGE")
        # No engine, journal, migration, activation or recovery imports.
        import psycopg
        from kis_buying_power_readonly import credentials
        from kis_live_v4_5_2 import _read, _key, UTC
        key, secret, account, product = credentials()
        url = os.environ["SUPABASE_DB_URL"]
        with psycopg.connect(url, connect_timeout=8, sslmode="require",
                options="-c default_transaction_read_only=on -c statement_timeout=10000") as conn:
            reference = read_reference(conn, day)
        cached = _read(_key(), dt.datetime.now(UTC))
        if not cached:
            raise ValueError("TOKEN_CACHE_MISSING")
        provider = lambda d, fk, nk, again: fetch_page(key, secret, account, product, cached[0], d, fk, nk, again)
        result = observe(day.strftime("%Y%m%d"), now, account, product, reference, provider, os.environ)
    except Exception:
        # Never reveal DB errors, credential values, reference hash or response content.
        result = {"status": "BLOCKED", "gate": "DENY", "orders_sent": 0,
                  "ledger_writes": 0, "unknown_order_released": False}
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] != "BLOCKED" else 2

if __name__ == "__main__":
    raise SystemExit(main())

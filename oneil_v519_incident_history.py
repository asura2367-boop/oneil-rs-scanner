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

DAY_HELP = "Use the incident date in KST (YYYY-MM-DD), not today's date; it must be 1-89 days ago."
DAY_REASONS = frozenset({"INVALID_INCIDENT_DAY", "DAY_NOT_CLOSED_OR_OUT_OF_RANGE"})
EVIDENCE_REASONS = DAY_REASONS | frozenset({
    "READ_ONLY_REQUIRED", "INCIDENT_REFERENCE_AMBIGUOUS", "INVALID_RESPONSE",
    "INVALID_CONTINUATION", "INVALID_ROWS", "ROW_LIMIT", "INVALID_CURSOR",
    "PAGE_LIMIT", "LIVE_SWITCH_NOT_DISABLED", "NAIVE_CLOCK", "INVALID_DAY",
    "ACCOUNT_INVALID", "ACCOUNT_REFERENCE_MISMATCH", "TOKEN_CACHE_MISSING",
})

class IncidentEvidenceError(ValueError):
    """Locally generated validation code; never wrap external exceptions."""

class IncidentDayError(IncidentEvidenceError):
    """Locally generated date validation errors, safe to show without raw details."""

def parse_incident_day(value, now):
    try:
        day = dt.datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        raise IncidentDayError("INVALID_INCIDENT_DAY") from None
    if not 1 <= (now.astimezone(KST).date() - day).days <= 89:
        raise IncidentDayError("DAY_NOT_CLOSED_OR_OUT_OF_RANGE")
    return day

def read_reference(conn, day):
    # Verify the effective transaction, not merely a connection startup option.
    with conn.cursor() as cur:
        cur.execute("SHOW transaction_read_only")
        if cur.fetchone() != ("on",):
            raise IncidentEvidenceError("READ_ONLY_REQUIRED")
        cur.execute("""SELECT account_fingerprint FROM public.v519_order_intents
            WHERE trading_day=%s AND status='SUBMISSION_UNKNOWN'""", (day,))
        rows = cur.fetchall()
    if len(rows) != 1 or not re.fullmatch("[0-9a-f]{64}", str(rows[0][0])):
        raise IncidentEvidenceError("INCIDENT_REFERENCE_AMBIGUOUS")
    return rows[0][0]

def collect(provider, day, max_pages=100):
    fk = nk = ""
    seen = set()
    count = 0
    for page in range(max_pages):
        cont, body = provider(day, fk, nk, page > 0)
        if not isinstance(body, dict) or str(body.get("rt_cd")) != "0":
            raise IncidentEvidenceError("INVALID_RESPONSE")
        if not isinstance(cont, str) or cont.strip().upper() not in ("", "D", "E", "F", "M"):
            raise IncidentEvidenceError("INVALID_CONTINUATION")
        rows = body.get("output1")
        if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
            raise IncidentEvidenceError("INVALID_ROWS")
        count += len(rows)
        if count > 10000:
            raise IncidentEvidenceError("ROW_LIMIT")
        if cont.strip().upper() not in ("F", "M"):
            return page + 1, count
        nf, nn = body.get("ctx_area_fk100"), body.get("ctx_area_nk100")
        if not isinstance(nf, str) or not isinstance(nn, str):
            raise IncidentEvidenceError("INVALID_CURSOR")
        nf, nn = nf.strip(), nn.strip()
        if not nf or not nn or (nf, nn) in seen:
            raise IncidentEvidenceError("INVALID_CURSOR")
        seen.add((nf, nn))
        fk, nk = nf, nn
    raise IncidentEvidenceError("PAGE_LIMIT")

def observe(day, now, account, product, reference, provider, env):
    if not flags_off(env):
        raise IncidentEvidenceError("LIVE_SWITCH_NOT_DISABLED")
    if now.tzinfo is None:
        raise IncidentEvidenceError("NAIVE_CLOCK")
    if not re.fullmatch(r"[0-9]{8}", day):
        raise IncidentEvidenceError("INVALID_DAY")
    date = dt.datetime.strptime(day, "%Y%m%d").date()
    age = (now.astimezone(KST).date() - date).days
    if not 1 <= age <= 89:
        raise IncidentEvidenceError("DAY_NOT_CLOSED_OR_OUT_OF_RANGE")
    if not re.fullmatch(r"[0-9]{8}", account) or not re.fullmatch(r"[0-9]{2}", product):
        raise IncidentEvidenceError("ACCOUNT_INVALID")
    fingerprint = hashlib.sha256((account + "|" + product).encode()).hexdigest()
    if not isinstance(reference, str) or not re.fullmatch("[0-9a-f]{64}", reference) or not hmac.compare_digest(fingerprint, reference):
        raise IncidentEvidenceError("ACCOUNT_REFERENCE_MISMATCH")
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
    parser.add_argument("--day", required=True, help=DAY_HELP)
    args = parser.parse_args()
    stage = "VALIDATE_INPUT"
    try:
        if not flags_off(os.environ):
            raise IncidentEvidenceError("LIVE_SWITCH_NOT_DISABLED")
        now = dt.datetime.now(KST)
        day = parse_incident_day(args.day, now)
        # No engine, journal, migration, activation or recovery imports.
        stage = "LOAD_DEPENDENCIES"
        import psycopg
        from kis_buying_power_readonly import credentials
        from kis_live_v4_5_2 import _read, _key, UTC
        stage = "READ_CREDENTIALS"
        key, secret, account, product = credentials()
        stage = "CONNECT_DATABASE"
        url = os.environ["SUPABASE_DB_URL"]
        with psycopg.connect(url, connect_timeout=8, sslmode="require",
                options="-c default_transaction_read_only=on -c statement_timeout=10000") as conn:
            # Psycopg emits BEGIN READ ONLY before the first cursor operation.
            # Retain the independent server-side SHOW guard before table reads.
            stage = "CONFIGURE_READ_ONLY_TRANSACTION"
            conn.read_only = True
            stage = "READ_INCIDENT_REFERENCE"
            reference = read_reference(conn, day)
        stage = "READ_TOKEN_CACHE"
        cached = _read(_key(), dt.datetime.now(UTC))
        if not cached:
            raise IncidentEvidenceError("TOKEN_CACHE_MISSING")
        provider = lambda d, fk, nk, again: fetch_page(key, secret, account, product, cached[0], d, fk, nk, again)
        stage = "READ_BROKER_HISTORY"
        result = observe(day.strftime("%Y%m%d"), now, account, product, reference, provider, os.environ)
    except Exception as exc:
        # Never reveal DB errors, credential values, reference hash or response content.
        result = {"status": "BLOCKED", "gate": "DENY", "orders_sent": 0,
                  "ledger_writes": 0, "unknown_order_released": False, "failure_stage": stage}
        # Only our own validation codes are disclosed. Never echo input,
        # broker responses, database exceptions, credentials or arbitrary errors.
        if isinstance(exc, IncidentEvidenceError) and str(exc) in EVIDENCE_REASONS:
            result["reason"] = str(exc)
            if str(exc) in DAY_REASONS:
                result["help"] = DAY_HELP
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] != "BLOCKED" else 2

if __name__ == "__main__":
    raise SystemExit(main())

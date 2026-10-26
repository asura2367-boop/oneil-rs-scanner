#!/usr/bin/env python3
"""KIS LIVE V4 stage 1: offline order-intent planner, NO network or order API.

Never reads KIS credentials. Never imports broker clients.
Fail-closed until a separate audited preflight / approval workflow exists.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo

KST = ZoneInfo('Asia/Seoul')
CAP = 1_000_000
PER_STOCK_CAP = 200_000
MAX_STOCKS = 5

class SafetyBlock(RuntimeError):
    pass


def read_json(path):
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise SafetyBlock('INVALID_INPUT')
    return data


def protect():
    # No combination of environment settings can activate orders in V4 stage 1.
    if os.getenv('LIVE_ENABLED', 'false').lower() != 'false':
        raise SafetyBlock('LIVE_MUST_BE_FALSE')
    if os.getenv('KIS_ALLOW_ORDER', 'false').lower() != 'false':
        raise SafetyBlock('ORDER_GATE_MUST_BE_FALSE')


def prepare(state, summary, now=None):
    now = now or dt.datetime.now(KST)
    pending = state.get('pending')
    report = summary.get('pending')
    if not isinstance(pending, list) or not isinstance(report, list):
        raise SafetyBlock('MISSING_PENDING_DATA')
    if state.get('last_processed') != summary.get('last_processed'):
        raise SafetyBlock('INCONSISTENT_PAPER_DATE')
    by_code = {}
    for rec in report:
        if not isinstance(rec, dict):
            raise SafetyBlock('INVALID_REPORT_ROW')
        code = str(rec.get('ticker', ''))
        if code in by_code:
            raise SafetyBlock('DUPLICATE_REPORT_TICKER')
        by_code[code] = rec
    if len(pending) > MAX_STOCKS:
        raise SafetyBlock('TOO_MANY_CANDIDATES')
    intents = []
    seen = set()
    reserved = 0
    for row in pending:
        if not isinstance(row, dict):
            raise SafetyBlock('INVALID_PENDING_ROW')
        code = str(row.get('ticker',''))
        day = str(row.get('signal_date',''))
        if len(code) != 6 or not code.isascii() or not code.isdigit() or code in seen:
            raise SafetyBlock('INVALID_OR_DUPLICATE_TICKER')
        if row.get('side') != 'BUY':
            raise SafetyBlock('UNSUPPORTED_ORDER_SIDE')
        try:
            dt.date.fromisoformat(day)
        except ValueError:
            raise SafetyBlock('INVALID_SIGNAL_DATE') from None
        if day != state.get('last_processed'):
            raise SafetyBlock('SIGNAL_DATE_MISMATCH')
        seen.add(code)
        rec = by_code.get(code)
        if rec is None:
            raise SafetyBlock('MISSING_PRICE_REPORT')
        try:
            ref = float(rec['reference_close'])
            indicated = float(rec['indicative_price'])
            qty = int(rec['estimated_qty'])
        except (KeyError, ValueError, TypeError):
            raise SafetyBlock('INVALID_ESTIMATE') from None
        if not (0 < ref and 0 < indicated and 0 < qty):
            raise SafetyBlock('INVALID_ESTIMATE')
        # Estimate values are not executable and intentionally NOT carried as orders.
        intent_id = hashlib.sha256(f'{day}|BUY|{code}|oneil-v4'.encode()).hexdigest()[:20]
        intents.append({'intent_id': intent_id, 'signal_date': day,
                        'ticker': code, 'side': 'BUY',
                        'estimated_price_krw': round(indicated, 2),
                        'estimated_qty_paper': qty,
                        'strategy_cap_krw': CAP,
                        'per_stock_cap_krw': PER_STOCK_CAP,
                        'status': 'BLOCKED_AWAITING_LIVE_PREFLIGHT',
                        'order_qty': 0, 'orders_sent': 0})
    return {'generated_at_kst': now.isoformat(), 'source_date': state.get('last_processed'),
            'mode': 'LIVE_V4_OFFLINE_INTENTS', 'live_enabled': False,
            'broker_api_calls': 0, 'orders_sent': 0,
            'note': 'PAPER estimates only; require fresh independent broker checks. No execution code.',
            'intents': intents}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--state', default='data/paper/state.json')
    p.add_argument('--summary', default='data/paper/paper_summary.json')
    p.add_argument('--output', default='data/live/v4_intents.json')
    a = p.parse_args()
    protect()
    doc = prepare(read_json(a.state), read_json(a.summary))
    output = Path(a.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(doc, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(f'V4 SAFETY PASS: {len(doc["intents"])} blocked intents; broker_api_calls=0; orders_sent=0; LIVE_ENABLED=false')


if __name__ == '__main__':
    try:
        main()
    except (SafetyBlock, OSError, json.JSONDecodeError) as exc:
        # Never include raw payloads or secret values in logs.
        code = str(exc) if isinstance(exc, SafetyBlock) else type(exc).__name__
        print(f'V4 BLOCKED: {code}; orders_sent=0')
        raise SystemExit(1)

#!/usr/bin/env python3
"""V4.2 read-only durable intent ledger. NO broker clients, NO order API.

Records verified preflight intent IDs in a versioned repo JSON file. Not a
production execution idempotency mechanism; no actual orders are possible.
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path
from kis_live_v4 import protect, SafetyBlock

HEX_ID = re.compile(r'^[0-9a-f]{20}$')
CODE = re.compile(r'^\d{6}$')


def build(preflight, ledger):
    if not isinstance(preflight, dict) or preflight.get('mode') != 'READ_ONLY_PREFLIGHT_V4_1':
        raise SafetyBlock('INVALID_PREFLIGHT_MODE')
    if preflight.get('live_enabled') is not False or preflight.get('orders_sent') != 0:
        raise SafetyBlock('PREFLIGHT_NOT_READ_ONLY')
    rows = preflight.get('decisions')
    if not isinstance(rows, list) or len(rows) > 5:
        raise SafetyBlock('INVALID_PREFLIGHT_DECISIONS')
    if ledger is None:
        ledger = {'schema_version': 1, 'recorded': {}}
    if (not isinstance(ledger, dict) or ledger.get('schema_version') != 1
            or not isinstance(ledger.get('recorded'), dict)):
        raise SafetyBlock('INVALID_LEDGER')
    recorded = dict(ledger['recorded'])
    result = []
    local = set()
    for row in rows:
        if not isinstance(row, dict):
            raise SafetyBlock('BAD_DECISION')
        ident, ticker = row.get('intent_id'), row.get('ticker')
        if not isinstance(ident, str) or not HEX_ID.fullmatch(ident):
            raise SafetyBlock('INVALID_INTENT_ID')
        if not isinstance(ticker, str) or not CODE.fullmatch(ticker) or ident in local:
            raise SafetyBlock('INVALID_OR_DUPLICATE_INTENT')
        if row.get('orders_sent') != 0:
            raise SafetyBlock('NONZERO_ORDERS')
        local.add(ident)
        eligible = row.get('status') == 'PREVIEW_ELIGIBLE_NO_ORDER'
        if eligible:
            qty, amount = row.get('preview_qty'), row.get('preview_amount_krw')
            if (not isinstance(qty, int) or isinstance(qty, bool) or qty < 1
                    or not isinstance(amount, int) or isinstance(amount, bool)
                    or not 0 < amount <= 200000):
                raise SafetyBlock('INVALID_PREVIEW_AMOUNT')
        if ident in recorded:
            old = recorded[ident]
            if not isinstance(old, dict) or old.get('ticker') != ticker:
                raise SafetyBlock('LEDGER_ID_CONFLICT')
            status = 'BLOCKED_ALREADY_RECORDED'
        elif eligible:
            status = 'RECORDED_ONCE_NO_ORDER'
            recorded[ident] = {'ticker': ticker, 'state': 'PREVIEW_RECORDED_NO_ORDER'}
        else:
            status = 'NOT_ELIGIBLE_NO_ORDER'
        result.append({'intent_id': ident, 'ticker': ticker, 'status': status,
                       'orders_sent': 0})
    return {'schema_version': 1, 'recorded': recorded}, {
        'mode': 'V4_2_NO_ORDER_LEDGER', 'live_enabled': False, 'orders_sent': 0,
        'results': result}


def read_dict(path):
    with open(path, encoding='utf-8') as handle:
        obj = json.load(handle)
    if not isinstance(obj, dict):
        raise SafetyBlock('INVALID_JSON_DOCUMENT')
    return obj


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--preflight', default='data/live/v4_1_preflight.json')
    p.add_argument('--ledger', default='data/live/v4_2_ledger.json')
    p.add_argument('--report', default='data/live/v4_2_report.json')
    args = p.parse_args()
    protect()
    if os.getenv('KIS_ALLOW_ORDER', 'false').lower() != 'false':
        raise SafetyBlock('ORDER_GATE_MUST_BE_FALSE')
    ledger_path = Path(args.ledger)
    old = read_dict(ledger_path) if ledger_path.exists() else None
    updated, report = build(read_dict(args.preflight), old)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    # Local atomic replacement; cross-run serialization is implemented in workflow.
    for target, doc in ((ledger_path, updated), (report_path, report)):
        tmp = target.with_suffix(target.suffix + '.tmp')
        tmp.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
        tmp.replace(target)
    for r in report['results']:
        print(r['ticker'], r['status'])
    print('V4.2 LEDGER COMPLETE; LIVE_ENABLED=false; orders_sent=0')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        code = str(exc) if isinstance(exc, SafetyBlock) else type(exc).__name__
        print('V4.2 BLOCKED: ' + code + '; orders_sent=0', file=sys.stderr)
        raise SystemExit(1)

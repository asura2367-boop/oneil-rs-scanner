#!/usr/bin/env python3
"""Oneil RS: 한국투자증권 LIVE 연결 준비용 안전한 주문계획 생성기.

LIVE 주문 API 호출은 이 버전에서 의도적으로 구현하지 않았음.
paper 엔진 및 state.json 은 절대 변경하지 않음.
"""
from __future__ import annotations
import argparse
import csv
import datetime as dt
import hashlib
import json
import os
import re
from pathlib import Path
from zoneinfo import ZoneInfo

KST = ZoneInfo('Asia/Seoul')
BUDGET_CAP = 1_000_000
PER_STOCK_CAP = 200_000
MAX_ORDERS = 5
MAX_PRICE_STALENESS_DAYS = 4


def load_json(path: Path):
    with path.open(encoding='utf-8') as f:
        return json.load(f)


def is_six_digits(s):
    return isinstance(s, str) and re.fullmatch(r'\d{6}', s) is not None


def make_plan(state_path: Path, summary_path: Path | None = None, today: dt.date | None = None):
    today = today or dt.datetime.now(KST).date()
    state = load_json(state_path)
    pending = state.get('pending')
    if not isinstance(pending, list):
        raise ValueError('pending must be a list')
    # State's pending signal is advisory only; require complete reference prices from summary.
    ref_by_ticker = {}
    if summary_path and summary_path.exists():
        summary = load_json(summary_path)
        for row in summary.get('pending', []):
            if is_six_digits(row.get('ticker')):
                ref_by_ticker[row['ticker']] = row
    rows = []
    used = set()
    for entry in pending:
        code = entry.get('ticker', '')
        if entry.get('side') != 'BUY' or not is_six_digits(code) or code in used:
            continue
        used.add(code)
        detail = ref_by_ticker.get(code, {})
        reason = str(entry.get('reason', ''))[:250]
        status, qty, price, amount = 'BLOCKED_MISSING_REFERENCE', 0, 0, 0
        reference_date = str(detail.get('reference_date', ''))
        try:
            d = dt.date.fromisoformat(reference_date)
            close = float(detail.get('reference_close', 0))
            if (today-d).days < 0 or (today-d).days > MAX_PRICE_STALENESS_DAYS:
                status = 'BLOCKED_STALE_PRICE'
            elif close <= 0 or not (close < float('inf')):
                status = 'BLOCKED_BAD_PRICE'
            else:
                # This is reference-only, not an exchange-validated order price/tick size.
                price = int(close)
                qty = min(PER_STOCK_CAP // price, int(detail.get('estimated_qty') or 0))
                amount = qty * price
                status = 'PREVIEW_ONLY' if qty > 0 else 'BLOCKED_ZERO_QTY'
        except (ValueError, TypeError, OverflowError):
            pass
        rows.append(dict(ticker=code, name=entry.get('name', ''), signal_date=entry.get('signal_date'),
                         reason=reason, reference_date=reference_date, reference_close=price,
                         preview_qty=qty, preview_notional=amount, status=status))
    rows = rows[:MAX_ORDERS]
    # Explicit never-execute semantics, regardless of injected environment variables.
    return {
        'generated_at_kst': dt.datetime.now(KST).isoformat(timespec='seconds'),
        'live_enabled': False, 'execution_capable': False, 'broker': 'KIS',
        'paper_unchanged': True, 'budget_cap_krw': BUDGET_CAP,
        'per_stock_cap_krw': PER_STOCK_CAP, 'max_orders': MAX_ORDERS,
        'note': 'PREVIEW ONLY; prices are historical reference closes, NOT executable live quotes. No orders sent.',
        'plans': rows,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--state', default='data/paper/state.json')
    parser.add_argument('--summary', default='data/paper/paper_summary.json')
    parser.add_argument('--output', default='data/live/preview.json')
    args = parser.parse_args()
    # Refuse any attempt to enable live via environment until a separately reviewed engine exists.
    if os.getenv('LIVE_ENABLED', 'false').lower() not in ('false', '0', 'no', ''):
        raise SystemExit('FAIL CLOSED: this build does not support LIVE_ENABLED=true')
    plan = make_plan(Path(args.state), Path(args.summary))
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('SAFE PREVIEW: no broker request, no order placement; candidates:', len(plan['plans']))
    print('output:', out)

if __name__ == '__main__':
    main()

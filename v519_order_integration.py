"""V5.19 order integration: safe review-to-broker order handoff.

This integration intentionally requires a separately verified broker risk snapshot.
It does not accept a boolean/CLI self-attestation as live authorization.
"""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo

from v519_pg_ledger import connect, LedgerBlocked
from v519_durable_submit import make_intent_id, submit_once

KST = ZoneInfo('Asia/Seoul')
from v519_cash_policy import limits


def broker_risk_snapshot():
    """Fetch real holdings, outstanding orders, and buying power.

    Deliberately refuses to certify daily P&L, marketable quote freshness or
    multi-day unknown submissions from incomplete broker data.
    """
    from kis_buying_power_readonly import credentials
    from oneil_v519_live_runner import token
    from kis_safety_v3 import verified_balance_snapshot, unfilled_codes
    key, secret, account, product = credentials()
    access_token = token(key, secret)
    held, invested = verified_balance_snapshot(key, secret, account, product, access_token)
    pending = unfilled_codes(key, secret, account, product, access_token)
    if type(invested) is not int or invested < 0:
        raise LedgerBlocked('INVALID_PORTFOLIO_VALUE')
    return {'account': account, 'product': product, 'held': held,
            'invested': invested, 'pending': pending, 'cash_verified': False,
            'complete_daily_pnl': False, 'complete_history': False,
            'executable_quote_verified': False}


def make_execution_review(plan, snapshot, ledger):
    if plan.get('status') != 'ORDER_PLAN_READ_ONLY' or plan.get('orders_sent') != 0:
        raise LedgerBlocked('PLAN_INVALID')
    if ledger.get('halted') is not True or ledger.get('unresolved') != 0:
        raise LedgerBlocked('LEDGER_HALTED_OR_UNRESOLVED')
    if snapshot['pending']:
        raise LedgerBlocked('BROKER_PENDING_ORDERS')
    if snapshot.get('cash_verified') is not True:
        raise LedgerBlocked('KIS_DEPOSIT_NOT_VERIFIED')
    budget=limits(snapshot['cash_krw'],snapshot['invested'],snapshot['orderable_krw'])
    remaining = budget['remaining']
    proposals = []
    for order in plan.get('orders', []):
        ticker = order['ticker']
        price = order['reference_price']
        quantity = order['preview_quantity']
        if ticker in snapshot['held']:
            quantity = 0
        if type(price) is not int or price <= 0 or type(quantity) is not int or quantity < 0:
            raise LedgerBlocked('ORDER_SIZE_INVALID')
        quantity = min(quantity, budget['per_stock'] // price, remaining // price)
        remaining -= quantity * price
        proposals.append({'ticker': ticker, 'side': 'BUY', 'quantity': quantity,
                          'reference_price': price, 'approved': False,
                          'reason': 'BROKER_RISK_EVIDENCE_INCOMPLETE'})
    return {'gate': 'DENY', 'orders_sent': 0, 'proposals': proposals,
            'daily_pnl_verified': snapshot['complete_daily_pnl'],
            'broker_history_verified': snapshot['complete_history'],
            'executable_quote_verified': snapshot['executable_quote_verified']}


def execute_live(*args, **kwargs):
    """No order attempt until independently sourced risk evidence is complete."""
    raise LedgerBlocked('INCOMPLETE_BROKER_RISK_AND_ORDER_RECOVERY')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--plan', required=True)
    parser.add_argument('--output', default='v519_execution_review.json')
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    result = {'gate': 'DENY', 'orders_sent': 0, 'status': 'BLOCKED'}
    try:
        if args.execute:
            execute_live()
        plan = json.loads(Path(args.plan).read_text(encoding='utf-8'))
        with connect() as conn:
            from v519_pg_ledger import status
            ledger = status(conn)
        snapshot = broker_risk_snapshot()
        result = make_execution_review(plan, snapshot, ledger)
        result['status'] = 'RISK_REVIEW_COMPLETE_NOT_ORDERABLE'
        code = 0
    except Exception as exc:
        result['reason'] = str(exc) if isinstance(exc, LedgerBlocked) else 'BROKER_OR_DB_FAILURE'
        code = 2
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('V519_EXECUTION=' + result['status'] + '; orders_sent=0')
    raise SystemExit(code)

if __name__ == '__main__':
    main()

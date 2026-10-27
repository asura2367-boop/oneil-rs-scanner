#!/usr/bin/env python3
"""Oneil V5.8: segregated read-only broker check and synthetic order plan; ALWAYS DENY.
No broker orders, and synthetic calculations are never presented as broker verified.
"""
import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo
from oneil_live_v5_7 import validate

KST = ZoneInfo('Asia/Seoul')

def safe_mode(env):
    return env.get('LIVE_ENABLED', 'false').lower() == 'false' and env.get('KIS_ALLOW_ORDER', 'false').lower() == 'false'

def preview(path, reserve, stock, evening):
    # Inputs are explicitly synthetic and never converted to a broker order.
    data=json.loads(Path(path).read_text(encoding='utf-8'))
    p=validate(data, reserve, stock, evening)
    return {'source':'SYNTHETIC_EXAMPLE','gate':'DENY',
            'plan_count':len(p['plans']), 'session_from_example':p['session'],
            'block_codes':p['block_codes'], 'allocation_basis':'SYNTHETIC_NOT_ACCOUNT_VERIFIED'}


REPORT_PATHS = {
    'preflight': 'data/live/v4_5_preflight.json',
    'reconciliation': 'data/live/v4_5_reconcile.json',
    'gate': 'data/live/v4_5_gate_report.json',
}

def verify_broker_reports(root, start_time):
    """Fail closed: freshness and report shape only, NEVER authorizes trading."""
    result = {}
    for kind, filename in REPORT_PATHS.items():
        path = root / filename
        if not path.is_file():
            result[kind] = 'MISSING'
            continue
        try:
            if path.stat().st_mtime < start_time - 2:
                result[kind] = 'STALE'
                continue
            data = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(data, dict) or data.get('orders_sent') != 0:
                result[kind] = 'INVALID'
            elif kind == 'preflight':
                # Explicit no-candidate / closed-market report is a valid DENIAL,
                # never proof that broker balance or buying power was verified.
                if (data.get('mode') == 'READ_ONLY_PREFLIGHT_BLOCKED' and
                    data.get('status') == 'DENY' and
                    data.get('reason') in ('NO_INTENTS', 'MARKET_CLOSED') and
                    data.get('broker_verified') is False and
                    data.get('live_enabled') is False and
                    data.get('decisions') == [] and
                    isinstance(data.get('generated_at_kst'), str)):
                    try:
                        stamp = dt.datetime.fromisoformat(data['generated_at_kst'])
                        if stamp.tzinfo is None:
                            raise ValueError('naive timestamp')
                        result[kind] = ('NO_INTENTS_DENY' if data['reason'] == 'NO_INTENTS'
                                        else 'MARKET_CLOSED_DENY')
                    except ValueError:
                        result[kind] = 'INVALID'
                elif (data.get('mode') == 'READ_ONLY_PREFLIGHT_V4_1' and
                      data.get('live_enabled') is False and
                      isinstance(data.get('decisions'), list)):
                    result[kind] = 'SNAPSHOT_PRESENT'
                else:
                    result[kind] = 'INVALID'
            elif kind == 'reconciliation':
                result[kind] = 'READ_ONLY_VALID' if (
                    data.get('mode') == 'V4_4_RECONCILE_READ_ONLY' and
                    data.get('live_enabled') is False and
                    data.get('broker_order_api_calls') == 0 and
                    data.get('broker_readonly_queries_performed') is True and
                    data.get('requires_manual_review') is False and
                    data.get('unmatched_broker_order_ids') == 0 and
                    data.get('unresolved_linked_ids') == 0) else 'INVALID'
            else:
                result[kind] = 'DENY_VALID' if (
                    data.get('mode') == 'V4_5_FINAL_GATE_READ_ONLY' and
                    data.get('decision') == 'DENY' and
                    data.get('approved_for_live_order') is False) else 'INVALID'
        except (OSError, ValueError, TypeError):
            result[kind] = 'INVALID'
    return result

def orchestrate(root, synthetic, now=None, *, broker_read=False, reserve='40', stock='30', evening='30', env=None, runner=subprocess.run):
    env=dict(os.environ if env is None else env)
    if not safe_mode(env):
        raise RuntimeError('LIVE_FLAGS_MUST_REMAIN_FALSE')
    root=Path(root)
    now=now or dt.datetime.now(KST)
    findings=['DAILY_PNL_NOT_VERIFIED','LIVE_EXCHANGE_QUOTES_NOT_VERIFIED','BROKER_ORDER_SUBMISSION_NOT_IMPLEMENTED']
    p=preview(root/synthetic,reserve,stock,evening)
    status='NOT_REQUESTED'
    evidence_status = {k:'NOT_REQUESTED' for k in REPORT_PATHS}
    if broker_read:
        # V4.5.2 owns the encrypted token cache; no tokens or stdout/stderr are exported.
        # Drop any historical snapshot before checking whether this run regenerates it.
        for filename in REPORT_PATHS.values():
            (root/filename).unlink(missing_ok=True)
        start_time=time.time()
        try:
            res=runner([sys.executable,'kis_live_v4_5_2.py'],cwd=root,env=env,
                       capture_output=True,text=True,timeout=180)
            # A success means existing read-only pipeline exited zero; still NOT an authorization.
            status='CHILD_COMPLETED' if res.returncode == 0 else 'CHILD_BLOCKED'
        except (OSError, subprocess.TimeoutExpired):
            status='CHILD_UNAVAILABLE'
        evidence_status=verify_broker_reports(root,start_time)
        if status!='CHILD_COMPLETED': findings.append('BROKER_READ_FAILED_OR_BLOCKED')
        else: findings.append('BROKER_READ_COMPLETED_NOT_LIVE_AUTHORIZATION')
        if any(v not in ('SNAPSHOT_PRESENT','READ_ONLY_VALID','DENY_VALID') for v in evidence_status.values()):
            findings.append('BROKER_EVIDENCE_INCOMPLETE')
    else:
        findings.append('BROKER_READ_NOT_REQUESTED')
    return {
        'version':'5.8', 'generated_at_kst':now.isoformat(timespec='seconds'),
        'mode':'READ_ONLY_PLUS_SYNTHETIC_SEPARATE','gate':'DENY',
        'broker_read_status':status,'broker_report_checks':evidence_status,'broker_accounts_verified_for_order':False,
        'actual_orderable_cash_verified':False,
        'verified_daily_pnl':False,'exchange_live_quote_verified':False,
        'synthetic_preview':p,'blocking_reasons':sorted(findings),
        'reserve_pct':float(reserve),'stock_pct_of_trading_budget':float(stock),
        'evening_pct_of_trading_budget':float(evening),
        'orders_sent':0,'broker_order_api_calls':0,'LIVE_ENABLED':False,'KIS_ALLOW_ORDER':False}

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--broker-read',action='store_true')
    parser.add_argument('--synthetic',default='v5_7_synthetic.json')
    parser.add_argument('--output',default='v5_8_safe_report.json')
    parser.add_argument('--reserve-pct',default='40')
    parser.add_argument('--stock-pct',default='30')
    parser.add_argument('--evening-pct',default='30')
    args=parser.parse_args()
    result=orchestrate('.',args.synthetic,broker_read=args.broker_read,
                       reserve=args.reserve_pct,stock=args.stock_pct,evening=args.evening_pct)
    Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('V5.8 GATE=DENY; broker_read='+result['broker_read_status']+
          '; report_checks='+json.dumps(result['broker_report_checks'],sort_keys=True,separators=(',',':'))+
          '; preview_source=SYNTHETIC_EXAMPLE; orders_sent=0; LIVE_ENABLED=false; KIS_ALLOW_ORDER=false')
if __name__=='__main__':main()

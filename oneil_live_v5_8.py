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

def orchestrate(root, synthetic, now=None, *, broker_read=False, reserve='40', stock='30', evening='30', env=None, runner=subprocess.run):
    env=dict(os.environ if env is None else env)
    if not safe_mode(env):
        raise RuntimeError('LIVE_FLAGS_MUST_REMAIN_FALSE')
    root=Path(root)
    now=now or dt.datetime.now(KST)
    findings=['DAILY_PNL_NOT_VERIFIED','LIVE_EXCHANGE_QUOTES_NOT_VERIFIED','BROKER_ORDER_SUBMISSION_NOT_IMPLEMENTED']
    p=preview(root/synthetic,reserve,stock,evening)
    status='NOT_REQUESTED'
    if broker_read:
        # V4.5.2 owns the encrypted token cache; no tokens or stdout/stderr are exported.
        try:
            res=runner([sys.executable,'kis_live_v4_5_2.py'],cwd=root,env=env,
                       capture_output=True,text=True,timeout=180)
            # A success means existing read-only pipeline exited zero; still NOT an authorization.
            status='CHILD_COMPLETED' if res.returncode == 0 else 'CHILD_BLOCKED'
        except (OSError, subprocess.TimeoutExpired):
            status='CHILD_UNAVAILABLE'
        if status!='CHILD_COMPLETED': findings.append('BROKER_READ_FAILED_OR_BLOCKED')
        else: findings.append('BROKER_READ_COMPLETED_NOT_LIVE_AUTHORIZATION')
    else:
        findings.append('BROKER_READ_NOT_REQUESTED')
    return {
        'version':'5.8', 'generated_at_kst':now.isoformat(timespec='seconds'),
        'mode':'READ_ONLY_PLUS_SYNTHETIC_SEPARATE','gate':'DENY',
        'broker_read_status':status,'broker_accounts_verified_for_order':False,
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
          '; preview_source=SYNTHETIC_EXAMPLE; orders_sent=0; LIVE_ENABLED=false; KIS_ALLOW_ORDER=false')
if __name__=='__main__':main()

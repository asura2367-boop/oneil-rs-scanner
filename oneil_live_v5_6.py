#!/usr/bin/env python3
"""V5.6 orchestrates existing read-only KIS checks, then ALWAYS DENIES orders."""
import argparse
import datetime as dt
import json
import os
import re
from pathlib import Path
import subprocess
import sys
from zoneinfo import ZoneInfo

KST=ZoneInfo('Asia/Seoul')
SOURCES=('data/live/v4_5_preflight.json','data/live/v4_5_reconcile.json',
         'data/live/v4_5_gate_report.json','v5_5_report.json')

def safe_flags(env):
    return env.get('LIVE_ENABLED','false').lower()=='false' and env.get('KIS_ALLOW_ORDER','false').lower()=='false'

# Only known non-secret diagnostic codes are copied from child output.
# Never print the raw child stdout/stderr; they may contain credentials or URLs.
CACHE_CODES = {
    'CACHE_KEY_INVALID', 'CACHE_DECRYPT_OR_FORMAT_FAILURE',
    'CACHE_PERSIST_FAILURE', 'AUTH_EXPIRY_INVALID', 'AUTH_EXPIRY_MISSING',
    'AUTH_TOO_CLOSE_TO_EXPIRY', 'AUTH_TOKEN_MISSING',
    'AUTH_TRANSPORT', 'AUTH_FAILURE', 'UNSAFE_EVENT',
    'EXTERNAL_SESSION_TOKEN_FORBIDDEN', 'LIVE_NOT_DISABLED'
}
SAFE_HTTP = re.compile(r'^AUTH_HTTP_(?:[1-5][0-9]{2}|unknown)$')
SAFE_STAGE = {'authentication', 'credentials_format', 'balance_valuation',
              'unfilled_orders', 'quote_and_buying_power', 'candidate_plan',
              'safety_guard', 'initialization'}
SAFE_ORDER_STAGE = {'authentication', 'order_history'}

def classify_broker_failure(stdout, stderr):
    # Constrain parsing to the small, bounded known-format diagnostic lines.
    # Ignore anything else rather than leaking broker error responses.
    for line in (stderr or '').splitlines()[:100]:
        if len(line) > 700: continue
        match = re.fullmatch(r'V4\.5\.2 BLOCKED: ([A-Z0-9_]+); orders_sent=0; LIVE_ENABLED=false',line.strip())
        if match:
            code=match.group(1)
            if code in CACHE_CODES or SAFE_HTTP.fullmatch(code):
                return 'CACHE_OR_AUTH_' + code
            return 'BROKER_INTERNAL_BLOCKED'
    combined=(stderr or '') + '\n' + (stdout or '')
    for line in combined.splitlines()[:100]:
        if len(line)>700: continue
        match=re.search(r'V4\.1 BLOCKED: stage=([a-z_]+); error_type=(HTTPError|URLError|SafetyBlock|ValueError|RuntimeError|TypeError|KeyError|OSError|Other); http_status=(none|unknown|[1-5][0-9]{2})',line)
        if match and match.group(1) in SAFE_STAGE:
            return 'PREFLIGHT_' + match.group(1).upper() + '_' + match.group(2).upper() + '_' + match.group(3).upper()
        match=re.search(r'V4\.4 BLOCKED: stage=(authentication|order_history); error_code=BROKER_HTTP_FAILURE_([1-5][0-9]{2});',line)
        if match and match.group(1) in SAFE_ORDER_STAGE:
            return 'RECONCILE_' + match.group(1).upper() + '_HTTP_' + match.group(2)
    return 'BROKER_UNKNOWN_FAILURE'

def orchestrate(root, report, *, use_broker=False, env=None, runner=subprocess.run):
    env=dict(os.environ if env is None else env)
    if not safe_flags(env): raise RuntimeError('LIVE_FLAGS_NOT_DISABLED')
    root=Path(root); report=Path(report)
    # Remove preexisting output: never validate stale reports accidentally.
    for name in SOURCES:
        (root/name).unlink(missing_ok=True)
    observations=[]
    if use_broker:
        # Reads account/orders only; an encrypted cache may be updated and committed
        # by the existing tool. No raw output or tokens are copied to the report.
        try:
            result=runner([sys.executable,'kis_live_v4_5_2.py'],cwd=root,env=env,
                          capture_output=True,text=True,timeout=150)
            observations.append('BROKER_READ_SUCCESS' if result.returncode==0 else 'BROKER_READ_FAILED')
            if result.returncode != 0:
                observations.append(classify_broker_failure(getattr(result, 'stdout', ''), getattr(result, 'stderr', '')))
        except subprocess.TimeoutExpired:
            observations.extend(('BROKER_READ_FAILED', 'BROKER_TIMEOUT'))
        except OSError:
            observations.extend(('BROKER_READ_FAILED', 'BROKER_PROCESS_START_FAILED'))
    else:
        observations.append('OFFLINE_ONLY')
    # V5.5 performs fail-closed evidence validation and can never authorize orders.
    try:
        result=runner([sys.executable,'oneil_live_v5_5.py','--report','v5_5_report.json'],
                      cwd=root,env=env,capture_output=True,text=True,timeout=30)
        if result.returncode!=0: observations.append('EVIDENCE_GATE_FAILED')
    except (OSError,subprocess.TimeoutExpired):
        observations.append('EVIDENCE_GATE_FAILED')
    try:
        evidence=json.loads((root/'v5_5_report.json').read_text())
        issues=evidence.get('inspection_issues',[]) if isinstance(evidence,dict) else []
        if not isinstance(issues,list) or any(not isinstance(x,str) for x in issues):
            issues=['EVIDENCE_INVALID']
    except (OSError,ValueError):
        issues=['EVIDENCE_MISSING']
    summary={'version':'5.6','timestamp_kst':dt.datetime.now(KST).isoformat(timespec='seconds'),
             'status':'DENY','read_mode':'BROKER_READ_ONLY' if use_broker else 'OFFLINE',
             'observations':observations,'evidence_issues':sorted(set(issues)),
             'daily_loss_verified':False,'exchange_quote_fresh_verified':False,
             'actual_broker_trade_authorization':False,'orders_sent':0,
             'LIVE_ENABLED':False,'KIS_ALLOW_ORDER':False}
    report.parent.mkdir(parents=True,exist_ok=True)
    report.write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    print('V5.6 GATE=DENY; '+','.join(observations)+'; orders_sent=0; LIVE_ENABLED=false; KIS_ALLOW_ORDER=false')
    return summary

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--broker-read',action='store_true',help='Execute EXISTING read-only V4.5.2 checks')
    parser.add_argument('--report',default='v5_6_report.json')
    args=parser.parse_args()
    orchestrate(Path('.'),Path(args.report),use_broker=args.broker_read)
if __name__=='__main__':main()

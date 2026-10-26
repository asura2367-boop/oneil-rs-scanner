#!/usr/bin/env python3
"""V4.6 no-order risk governor: persistent halt and conservative daily-loss gate.

There is deliberately NO brokerage client, order URL, or option to enable orders.
An independently verified intraday P&L feed is not yet implemented. As a result
LIVE approval is ALWAYS denied; values used in unit tests are synthetic only.
"""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo

UTC = dt.timezone.utc
KST = ZoneInfo('Asia/Seoul')
CAP_KRW = 1_000_000
STOCK_CAP_KRW = 200_000
DAILY_LOSS_LIMIT_KRW = 20_000  # 2% * 1,000,000, chosen conservatively
HALT_FILE = Path('data/live/v4_6_halt.json')
REPORT_FILE = Path('data/live/v4_6_risk_report.json')

class RiskError(Exception):
    pass

def require_off():
    if os.environ.get('LIVE_ENABLED', 'false').strip().lower() != 'false' or os.environ.get('KIS_ALLOW_ORDER','false').strip().lower() != 'false':
        raise RiskError('ORDER_SWITCH_NOT_OFF')

def read_halt(path):
    if not path.exists():
        return {'version': 1, 'halted': False, 'halted_at_kst': None}
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        if set(value) != {'version','halted','halted_at_kst'} or value['version'] != 1 or type(value['halted']) is not bool:
            raise ValueError('schema')
        if value['halted'] and (not isinstance(value['halted_at_kst'],str) or not value['halted_at_kst']):
            raise ValueError('timestamp')
        return value
    except Exception:
        raise RiskError('HALT_STATE_INVALID') from None

def atomic_write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=True)+'\n', encoding='utf-8')
    tmp.replace(path)

def latch_halt(old, now):
    if old['halted']:
        return old
    return {'version':1, 'halted':True, 'halted_at_kst':now.astimezone(KST).isoformat(timespec='seconds')}

def daily_loss_check(*, verified, realized, unrealized, limit=DAILY_LOSS_LIMIT_KRW):
    """Pure risk-rule test hook. Production deliberately passes verified=False.
    A future verified source must include positions, fills, fees, and cash flows.
    """
    if verified is not True:
        return 'DAILY_LOSS_NOT_VERIFIED'
    if type(realized) not in (int,float) or type(unrealized) not in (int,float):
        return 'DAILY_LOSS_INVALID'
    import math
    if not math.isfinite(realized) or not math.isfinite(unrealized):
        return 'DAILY_LOSS_INVALID'
    if realized + unrealized <= -limit:
        return 'DAILY_LOSS_LIMIT_REACHED'
    return 'DAILY_LOSS_LIMIT_NOT_REACHED'

def build_report(state):
    codes = []
    if state['halted']:
        codes.append('EMERGENCY_HALT_ACTIVE')
    # No audited/verified daily P&L data exists in the V4.5.2 pipeline.
    codes.append(daily_loss_check(verified=False, realized=None, unrealized=None))
    codes.append('ORDER_TRANSMISSION_NOT_IMPLEMENTED')
    return {'version':1,'mode':'NO_ORDER_RISK_GOVERNOR','approved':False,
            'gate':'DENY','block_codes':codes,'emergency_halt':state['halted'],
            'strategy_cap_krw':CAP_KRW,'stock_cap_krw':STOCK_CAP_KRW,
            'daily_loss_limit_krw':DAILY_LOSS_LIMIT_KRW,
            'daily_loss_verified':False,'orders_sent':0,'broker_api_calls':0,
            'live_enabled':False,'allow_order':False}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--emergency-halt', action='store_true', help='Permanently latch stop until separate audited reset process exists')
    args=parser.parse_args()
    require_off()
    old=read_halt(HALT_FILE)
    state=latch_halt(old, dt.datetime.now(KST)) if args.emergency_halt else old
    if args.emergency_halt or not HALT_FILE.exists():
        atomic_write(HALT_FILE,state)
    report=build_report(state)
    atomic_write(REPORT_FILE,report)
    print('V4.6 RISK GATE: DENY; orders_sent=0; LIVE_ENABLED=false; KIS_ALLOW_ORDER=false')
    print('halted='+str(state['halted']).lower()+'; daily_loss_verified=false; block_codes='+','.join(report['block_codes']))

if __name__=='__main__':
    try:
        main()
    except Exception as e:
        code = str(e) if isinstance(e,RiskError) and str(e) in ('ORDER_SWITCH_NOT_OFF','HALT_STATE_INVALID') else 'RISK_GATE_FAILED'
        print('V4.6 BLOCKED: '+code+'; orders_sent=0; LIVE_ENABLED=false')
        raise SystemExit(1)

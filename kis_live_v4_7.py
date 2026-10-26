#!/usr/bin/env python3
"""KIS V4.7: read-only daily equity loss VERIFIER, never an order authorization.

Requires independently verified start/current equity and net external flows.
Does not fetch raw KIS data, infer deposits, or mistake trade proceeds for flows.
Any missing/ambiguous evidence -> DENY. No order endpoints or broker clients.
"""
import argparse
import datetime as dt
import json
import os
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

def require_off():
    if (os.environ.get('LIVE_ENABLED', 'false').strip().lower() != 'false' or
        os.environ.get('KIS_ALLOW_ORDER', 'false').strip().lower() != 'false'):
        raise ValidationError('ORDER_SWITCH_NOT_OFF')

def read_halt(path):
    if not path.exists():
        return {'version': 1, 'halted': False, 'halted_at_kst': None}
    try:
        obj = json.loads(path.read_text(encoding='utf-8'))
        if (set(obj) != {'version','halted','halted_at_kst'} or obj['version'] != 1 or
            type(obj['halted']) is not bool or
            (obj['halted'] and not isinstance(obj['halted_at_kst'],str))):
            raise ValueError('schema')
        return obj
    except Exception:
        raise ValidationError('HALT_STATE_INVALID') from None

KST = ZoneInfo('Asia/Seoul')
LOSS_LIMIT = Decimal('20000')
OUT = Path('data/live/v4_7_risk_report.json')

class ValidationError(Exception):
    pass

def integer_krw(value):
    # Avoid float roundoff, booleans, negative balances and exponent notation.
    if type(value) is not int or value < 0:
        raise ValidationError('AMOUNT_INVALID')
    return Decimal(value)

def signed_krw(value):
    if type(value) is not int:
        raise ValidationError('FLOW_INVALID')
    return Decimal(value)

def verify_snapshot(obj, now):
    """Verify strict evidence schema; never automatically trust user/API input."""
    needed = {'schema_version','market_date','scope','evidence_verified',
              'baseline_time_kst','current_time_kst','baseline_equity_krw',
              'current_equity_krw','net_external_cashflow_krw',
              'cashflows_reconciled','holdings_reconciled','fees_included',
              'source_complete'}
    if not isinstance(obj,dict) or set(obj) != needed or obj['schema_version'] != 1:
        raise ValidationError('EVIDENCE_SCHEMA_INVALID')
    if obj['market_date'] != now.strftime('%Y-%m-%d'):
        raise ValidationError('DATE_MISMATCH')
    if obj['scope'] != 'WHOLE_ACCOUNT':
        # Isolated strategy P&L not supported without an audited allocation ledger.
        raise ValidationError('SCOPE_NOT_VERIFIED')
    if any(obj[k] is not True for k in ('evidence_verified','cashflows_reconciled',
                                       'holdings_reconciled','fees_included','source_complete')):
        raise ValidationError('DAILY_LOSS_NOT_VERIFIED')
    try:
        start = dt.datetime.fromisoformat(obj['baseline_time_kst'])
        end = dt.datetime.fromisoformat(obj['current_time_kst'])
    except (ValueError, TypeError, KeyError):
        raise ValidationError('TIMESTAMP_INVALID') from None
    if (start.tzinfo is None or end.tzinfo is None or
        start.utcoffset() != dt.timedelta(hours=9) or
        end.utcoffset() != dt.timedelta(hours=9) or
        start.date() != now.date() or end.date() != now.date()):
        raise ValidationError('TIMESTAMP_INVALID')
    if start.time() > dt.time(9,0) or start >= end:
        raise ValidationError('BASELINE_NOT_PREOPEN')
    if end > now + dt.timedelta(seconds=30) or now - end > dt.timedelta(minutes=5):
        raise ValidationError('STALE_EVIDENCE')
    baseline = integer_krw(obj['baseline_equity_krw'])
    current = integer_krw(obj['current_equity_krw'])
    cashflow = signed_krw(obj['net_external_cashflow_krw'])
    if baseline <= 0:
        raise ValidationError('BASELINE_INVALID')
    pnl = current - baseline - cashflow
    return pnl

def assess(snapshot, now, halted=False):
    codes = ['ORDER_TRANSMISSION_NOT_IMPLEMENTED']
    if halted:
        codes.append('EMERGENCY_HALT_ACTIVE')
    verified = False
    loss_reached = None
    if snapshot is None:
        codes.append('DAILY_LOSS_NOT_VERIFIED')
    else:
        try:
            pnl = verify_snapshot(snapshot, now)
            verified = True
            loss_reached = pnl <= -LOSS_LIMIT
            if loss_reached:
                codes.append('DAILY_LOSS_LIMIT_REACHED')
        except ValidationError as exc:
            codes.append(str(exc))
    return {'schema_version':1,'mode':'READ_ONLY_DAILY_LOSS_GATE',
            'gate':'DENY','approved':False,'daily_loss_verified':verified,
            'daily_loss_limit_krw':int(LOSS_LIMIT),'daily_loss_limit_reached':loss_reached,
            'emergency_halt':bool(halted),'block_codes':codes,
            'orders_sent':0,'broker_api_calls':0,'live_enabled':False,
            'allow_order':False,'scope':'WHOLE_ACCOUNT',
            'caution':'A verified input is not automatically generated or independently authenticated by this program.'}

def atomic_write(path,report):
    path.parent.mkdir(parents=True,exist_ok=True)
    p=path.with_suffix(path.suffix+'.tmp')
    p.write_text(json.dumps(report,ensure_ascii=False,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    p.replace(path)

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--evidence',type=Path,default=None,
                        help='Optional private, independently verified equity snapshots; NEVER commit')
    parser.add_argument('--report',type=Path,default=OUT)
    args=parser.parse_args()
    require_off()
    halted=read_halt(Path('data/live/v4_6_halt.json'))['halted']
    snap=None
    if args.evidence:
        try:
            snap=json.loads(args.evidence.read_text(encoding='utf-8'))
        except Exception:
            # No silent fallback to presumed zero loss.
            snap={}
    report=assess(snap,dt.datetime.now(KST),halted)
    atomic_write(args.report,report)
    print('V4.7 DAILY LOSS GATE: DENY; orders_sent=0; LIVE_ENABLED=false; KIS_ALLOW_ORDER=false')
    print('daily_loss_verified='+str(report['daily_loss_verified']).lower()+
          '; block_codes='+','.join(report['block_codes']))

if __name__=='__main__':
    try: main()
    except Exception:
        print('V4.7 BLOCKED: INPUT_OR_STATE_ERROR; orders_sent=0; LIVE_ENABLED=false')
        raise SystemExit(1)

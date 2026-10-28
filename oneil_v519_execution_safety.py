#!/usr/bin/env python3
"""Read-only V5.19 risk and simulated order-state diagnostics. NEVER places orders."""
import argparse
import datetime as dt
import json
import os
import re
from pathlib import Path
from zoneinfo import ZoneInfo

KST = ZoneInfo('Asia/Seoul')
TICKER = re.compile(r'^[0-9]{6}$')
STATES = {'PREVIEW_ONLY', 'SUBMISSION_UNKNOWN', 'ACKNOWLEDGED', 'PART_FILLED', 'FILLED', 'CANCELLED', 'REJECTED'}
ALLOWED = {
    'PREVIEW_ONLY': {'SUBMISSION_UNKNOWN'},
    'SUBMISSION_UNKNOWN': {'ACKNOWLEDGED', 'PART_FILLED', 'FILLED', 'CANCELLED', 'REJECTED'},
    'ACKNOWLEDGED': {'PART_FILLED', 'FILLED', 'CANCELLED', 'REJECTED'},
    'PART_FILLED': {'PART_FILLED', 'FILLED', 'CANCELLED'},
    'FILLED': set(), 'CANCELLED': set(), 'REJECTED': set(),
}

class SafetyError(ValueError): pass

def ensure_disabled(env=None):
    env = os.environ if env is None else env
    if env.get('LIVE_ENABLED', 'false').lower() != 'false' or env.get('KIS_ALLOW_ORDER', 'false').lower() != 'false':
        raise SafetyError('LIVE_FLAGS_NOT_DISABLED')

def parse_time(value):
    if not isinstance(value, str): return None
    try:
        parsed = dt.datetime.fromisoformat(value)
        return parsed.astimezone(KST) if parsed.tzinfo and parsed.utcoffset() is not None else None
    except (ValueError, TypeError, OverflowError): return None

def recent(doc, now, minutes=5):
    if not isinstance(doc, dict): return False
    stamp = parse_time(doc.get('generated_at_kst') or doc.get('as_of_kst') or doc.get('timestamp_kst'))
    return stamp is not None and 0 <= (now-stamp).total_seconds() <= minutes*60

def transition(order, next_state, *, broker_order_id=None, cumulative_filled_qty=None):
    """Pure simulated reconciliation; unknown submissions must never trigger automatic resubmission."""
    if not isinstance(order, dict) or next_state not in STATES: raise SafetyError('STATE_INVALID')
    current = order.get('state')
    if current not in STATES or next_state not in ALLOWED[current]: raise SafetyError('TRANSITION_DENIED')
    qty = order.get('requested_qty'); done = order.get('filled_qty')
    if type(qty) is not int or qty <= 0 or type(done) is not int or not 0 <= done <= qty:
        raise SafetyError('QUANTITY_INVALID')
    if cumulative_filled_qty is None: cumulative_filled_qty = done
    if type(cumulative_filled_qty) is not int or not done <= cumulative_filled_qty <= qty:
        raise SafetyError('FILL_REGRESSION_OR_OVERFILL')
    if next_state == 'FILLED' and cumulative_filled_qty != qty: raise SafetyError('FILL_INCOMPLETE')
    if next_state == 'PART_FILLED' and not 0 < cumulative_filled_qty < qty: raise SafetyError('PART_FILL_INVALID')
    existing = order.get('broker_order_id')
    if existing is not None and broker_order_id is not None and broker_order_id != existing:
        raise SafetyError('BROKER_ID_CONFLICT')
    if next_state in {'ACKNOWLEDGED', 'PART_FILLED', 'FILLED'} and existing is None and broker_order_id is None:
        raise SafetyError('BROKER_ID_REQUIRED')
    result = dict(order)
    result.update(state=next_state, filled_qty=cumulative_filled_qty,
                  broker_order_id=existing or broker_order_id, automatic_resubmission_allowed=False)
    return result

def inspect(candidate, account, quote, risk, now=None):
    ensure_disabled()
    now = now or dt.datetime.now(KST)
    if now.tzinfo is None: raise SafetyError('CLOCK_MUST_BE_AWARE')
    blocks=[]
    def block(code, ok):
        if not ok: blocks.append(code)
    intents = candidate.get('intents') if isinstance(candidate, dict) else None
    codes = [x.get('ticker') for x in intents] if isinstance(intents,list) and all(isinstance(x,dict) for x in intents) else []
    candidate_ok = (isinstance(candidate,dict) and candidate.get('mode')=='LIVE_INTENTS_PREVIEW_ONLY'
                    and candidate.get('gate')=='DENY' and candidate.get('orders_sent')==0
                    and isinstance(intents,list) and 0 < len(intents) <= 25 and len(codes)==len(intents)
                    and all(isinstance(x,str) and TICKER.fullmatch(x) for x in codes)
                    and len(set(codes))==len(codes) and recent(candidate,now))
    block('CANDIDATE_MISSING_STALE_OR_DUPLICATE',candidate_ok)
    account_observed = (recent(account,now) and account.get('gate')=='DENY' and account.get('orders_sent')==0
                        and account.get('broker_status')=='READ_ONLY_OBSERVED')
    block('ACCOUNT_OBSERVATION_MISSING_OR_STALE',account_observed)
    buying_verified = bool(account_observed and account.get('broker_buying_power_verified') is True
                           and account.get('holding_unfilled_verified') is True)
    block('BROKER_BUYING_POWER_NOT_VERIFIED',buying_verified)
    quote_received = recent(quote,now) and quote.get('version')=='5.18' and quote.get('orders_sent')==0
    block('QUOTE_OBSERVATION_MISSING_OR_STALE',quote_received)
    quote_verified = bool(quote_received and quote.get('quote_schema_fully_attested') is True
                          and quote.get('exchange_business_day_attested') is True
                          and quote.get('exchange_session_phase_attested') is True
                          and quote.get('quote_price_approved_count',0)>0)
    block('EXECUTABLE_QUOTE_NOT_VERIFIED',quote_verified)
    pnl_verified = bool(recent(risk,now) and risk.get('orders_sent')==0
                        and risk.get('daily_loss_verified') is True
                        and risk.get('external_cashflows_reconciled') is True
                        and risk.get('baseline_verified') is True)
    block('DAILY_LOSS_BASELINE_OR_CASHFLOWS_NOT_VERIFIED',pnl_verified)
    blocks.extend(['ORDER_BROKER_RECONCILIATION_NOT_IMPLEMENTED', 'LIVE_ORDER_TRANSMISSION_DISABLED'])
    return {'version':'v5.19-consolidated','generated_at_kst':now.astimezone(KST).isoformat(timespec='seconds'),
            'gate':'DENY','candidate_count':len(codes) if candidate_ok else 0,
            'account_observed':account_observed,'buying_power_verified':buying_verified,
            'quote_observed':quote_received,'executable_quote_verified':quote_verified,
            'daily_loss_verified':pnl_verified,'blocks':blocks,'orders_sent':0,
            'broker_order_api_calls':0,'LIVE_ENABLED':False,'KIS_ALLOW_ORDER':False}

def load_json(path):
    try:
        obj=json.loads(Path(path).read_text(encoding='utf-8'))
        return obj if isinstance(obj,dict) else None
    except (OSError, ValueError, UnicodeError): return None

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--candidate',default='v5_11_review.json')
    ap.add_argument('--account',default='v5_13_review.json')
    ap.add_argument('--quote',default='v5_18_report.json')
    ap.add_argument('--risk',default='data/live/v4_8_audit.json')
    ap.add_argument('--output',default='v5_19_execution_safety.json')
    x=ap.parse_args()
    result=inspect(load_json(x.candidate),load_json(x.account),load_json(x.quote),load_json(x.risk))
    Path(x.output).write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print('V5.19 EXECUTION_SAFETY GATE=DENY; candidates='+str(result['candidate_count'])+'; blocks='+','.join(result['blocks'])+'; orders_sent=0; LIVE_ENABLED=false; KIS_ALLOW_ORDER=false')

if __name__=='__main__': main()

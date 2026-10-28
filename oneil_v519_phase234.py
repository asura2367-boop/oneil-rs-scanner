#!/usr/bin/env python3
"""V5.19 phases 2–4: no-order portfolio risk, durable simulation and replay.
No broker client, network functions or actual order submission are imported.
The diagnostic ALWAYS returns DENY. No account details are serialized.
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
import os
import re
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

KST=ZoneInfo('Asia/Seoul')
CODE=re.compile(r'^\d{6}$')
STATES=('PREVIEW','SUBMISSION_UNKNOWN','ACKNOWLEDGED','PART_FILLED','FILLED','CANCELLED','REJECTED')
TERMINAL={'FILLED','CANCELLED','REJECTED'}
TRANSITIONS={
 'PREVIEW':{'SUBMISSION_UNKNOWN'},
 'SUBMISSION_UNKNOWN':{'ACKNOWLEDGED','PART_FILLED','FILLED','CANCELLED','REJECTED'},
 'ACKNOWLEDGED':{'PART_FILLED','FILLED','CANCELLED','REJECTED'},
 'PART_FILLED':{'PART_FILLED','FILLED','CANCELLED'},
 'FILLED':set(),'CANCELLED':set(),'REJECTED':set(),
}
class Blocked(ValueError):pass

def off(env=None):
 env=os.environ if env is None else env
 if env.get('LIVE_ENABLED','false').strip().lower()!='false' or env.get('KIS_ALLOW_ORDER','false').strip().lower()!='false':
  raise Blocked('LIVE_SWITCH_NOT_DISABLED')

def integer(x,allow_negative=False):
 if type(x) is not int or (x<0 and not allow_negative):raise Blocked('INTEGER_INVALID')
 return x

def aware_stamp(x):
 if not isinstance(x,str):raise Blocked('TIMESTAMP_INVALID')
 try:t=dt.datetime.fromisoformat(x)
 except (ValueError,OverflowError):raise Blocked('TIMESTAMP_INVALID') from None
 if t.tzinfo is None or t.utcoffset() is None:raise Blocked('TIMESTAMP_NAIVE')
 return t.astimezone(KST)

def verify_equity(evidence,now,loss_cap=20000):
 """Authenticated provenance cannot be established here; numeric loss assessment only."""
 off();integer(loss_cap)
 if not isinstance(evidence,dict):raise Blocked('EQUITY_MISSING')
 required={'market_date','scope','baseline_time_kst','current_time_kst','baseline_equity_krw',
  'current_equity_krw','net_external_cashflow_krw','cashflows_reconciled','fees_included',
  'holdings_reconciled','source_complete','independently_verified'}
 if set(evidence)!=required or evidence['market_date']!=now.date().isoformat() or evidence['scope']!='WHOLE_ACCOUNT':
  raise Blocked('EQUITY_SCHEMA_OR_DATE')
 if any(evidence[k] is not True for k in ('cashflows_reconciled','fees_included','holdings_reconciled','source_complete','independently_verified')):
  raise Blocked('EQUITY_NOT_INDEPENDENTLY_VERIFIED')
 start=aware_stamp(evidence['baseline_time_kst']);end=aware_stamp(evidence['current_time_kst'])
 if start.date()!=now.date() or end.date()!=now.date() or start.time()>dt.time(9,0) or not start<end:
  raise Blocked('BASELINE_MISSING_OR_LATE')
 if not 0<=(now-end).total_seconds()<=300:raise Blocked('EQUITY_STALE_OR_FUTURE')
 base=integer(evidence['baseline_equity_krw']);current=integer(evidence['current_equity_krw']);flow=integer(evidence['net_external_cashflow_krw'],True)
 if base<=0:raise Blocked('BASELINE_ZERO')
 pnl=current-base-flow
 return {'loss_limit_reached':pnl<=-loss_cap,'loss_limit_krw':loss_cap,'pnl_amount_private':pnl}

def allocation(initial_capital,broker_buying_cash,price,*,current_trading_exposure=0,stock_exposure=0,
               risk_verified=False,price_certified=False,account_verified=False,held=False,unfilled=False):
 """One-ticker simulated size, never authorized; integer KRW only."""
 off()
 for x in (initial_capital,broker_buying_cash,price,current_trading_exposure,stock_exposure):integer(x)
 if not risk_verified or not price_certified or not account_verified:raise Blocked('EVIDENCE_UNVERIFIED')
 if held or unfilled:raise Blocked('HELD_OR_UNFILLED')
 if price==0:raise Blocked('ZERO_PRICE')
 trade_cap=initial_capital*60//100
 stock_cap=trade_cap*30//100
 remaining=max(0,trade_cap-current_trading_exposure)
 allowance=min(broker_buying_cash,remaining,max(0,stock_cap-stock_exposure))
 return {'simulated_qty':allowance//price,'simulated_cap_krw':allowance,'authorized':False}

def event_fold(intent,events,*,now=None):
 """Deterministic simulated event replay; unknown submission must NOT auto retry."""
 off()
 if not isinstance(intent,dict) or not CODE.fullmatch(str(intent.get('ticker',''))):raise Blocked('INTENT_INVALID')
 req=integer(intent.get('requested_qty'))
 if req==0:raise Blocked('QUANTITY_ZERO')
 if not isinstance(events,list) or len(events)>2000:raise Blocked('EVENTS_INVALID')
 state='PREVIEW';done=0;broker_id=None;seen=set();last=None
 for event in events:
  if not isinstance(event,dict) or set(event)!={'event_id','at_kst','next_state','filled_qty','broker_order_id'}:raise Blocked('EVENT_SCHEMA')
  eid=event['event_id'];t=aware_stamp(event['at_kst']);nxt=event['next_state'];filled=integer(event['filled_qty'])
  if not isinstance(eid,str) or not eid or eid in seen:raise Blocked('EVENT_DUPLICATE')
  if last is not None and t<last:raise Blocked('EVENT_OUT_OF_ORDER')
  if nxt not in TRANSITIONS[state]:raise Blocked('STATE_TRANSITION_DENIED')
  ident=event['broker_order_id']
  if ident is not None and (not isinstance(ident,str) or not ident):raise Blocked('BROKER_ID_INVALID')
  if broker_id and ident and broker_id!=ident:raise Blocked('BROKER_ID_CONFLICT')
  if nxt in {'ACKNOWLEDGED','PART_FILLED','FILLED'} and not (ident or broker_id):raise Blocked('BROKER_ID_MISSING')
  if not done<=filled<=req:raise Blocked('FILL_REGRESSION_OR_OVERFILL')
  if nxt=='FILLED' and filled!=req:raise Blocked('FILL_INCOMPLETE')
  if nxt=='PART_FILLED' and not 0<filled<req:raise Blocked('PART_FILL_INVALID')
  seen.add(eid);last=t;done=filled;state=nxt;broker_id=broker_id or ident
 return {'state':state,'filled_qty':done,'pending_qty':req-done,'automatic_resubmission_allowed':False,
  'broker_reconciliation_required':state not in TERMINAL,'orders_sent':0}

def simulate(candidate,account,quote,history,*,now=None,initial_capital=1000000,events=None):
 off();now=now or dt.datetime.now(KST)
 if now.tzinfo is None:raise Blocked('CLOCK_NAIVE')
 issues=[]
 rows=candidate.get('intents') if isinstance(candidate,dict) else None
 tickers=[x.get('ticker') for x in rows] if isinstance(rows,list) and all(isinstance(x,dict) for x in rows) else []
 if (not tickers or len(tickers)>25 or len(tickers)!=len(set(tickers)) or
    not all(isinstance(t,str) and CODE.fullmatch(t) for t in tickers) or
    candidate.get('gate')!='DENY' or candidate.get('orders_sent')!=0):
  issues.append('CANDIDATE_NOT_VERIFIED');tickers=[]
 if not isinstance(account,dict) or account.get('broker_status')!='READ_ONLY_OBSERVED':issues.append('ACCOUNT_NOT_VERIFIED')
 if not isinstance(quote,dict) or quote.get('version')!='5.18':issues.append('QUOTE_NOT_OBSERVED')
 issues.append('EXECUTABLE_PRICE_NOT_CERTIFIED')
 if not isinstance(history,dict) or history.get('status')!='OBSERVED_PAGES_COMPLETE_NOT_RECONCILED':
  issues.append('ORDER_HISTORY_NOT_OBSERVED')
 issues.append('ORDER_HISTORY_NOT_INDEPENDENTLY_RECONCILED')
 issues.append('WHOLE_ACCOUNT_DAILY_LOSS_NOT_VERIFIED')
 scenario={}
 for name,evs in (events or {}).items():
  if name not in tickers:raise Blocked('UNLISTED_TICKER_EVENT')
  scenario[name]=event_fold({'ticker':name,'requested_qty':10},evs)
 return {'version':'5.19-phases-2-4','generated_at_kst':now.isoformat(timespec='seconds'),
  'mode':'SIMULATION_AND_EVIDENCE_DIAGNOSTIC','gate':'DENY','candidate_count':len(tickers),
  'reserve_pct':40,'trading_pct':60,'stock_pct_of_trading_budget':30,
  'initial_capital_krw':initial_capital,'simulated_scenarios':{k:{'state':v['state'],'automatic_resubmission_allowed':False} for k,v in scenario.items()},
  'issues':issues,'orders_sent':0,'broker_order_api_calls':0,'LIVE_ENABLED':False,'KIS_ALLOW_ORDER':False}

def load(path):
 try:
  x=json.loads(Path(path).read_text(encoding='utf8'));return x if isinstance(x,dict) else None
 except (OSError,ValueError,UnicodeError):return None

def main():
 p=argparse.ArgumentParser()
 for key,default in [('candidate','v5_11_review.json'),('account','v5_13_review.json'),('quote','v5_18_report.json'),('history','v5_19_history_observe.json'),('output','v5_19_phase234.json')]:p.add_argument('--'+key,default=default)
 a=p.parse_args()
 r=simulate(load(a.candidate),load(a.account),load(a.quote),load(a.history))
 Path(a.output).write_text(json.dumps(r,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
 print('V519_PHASE234=SAFE_DENY; candidates='+str(r['candidate_count'])+'; orders_sent=0')
if __name__=='__main__':main()

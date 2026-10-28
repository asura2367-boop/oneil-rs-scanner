"""V5.19 operational readiness boundary. NO broker orders.

Never infer authenticated cash or certified quotes from diagnostic status flags.
Durable storage must be external to an ephemeral Actions runner for live use.
"""
import datetime as dt
import json
import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

KST=ZoneInfo('Asia/Seoul')

class Blocked(ValueError): pass

@dataclass(frozen=True)
class AttestedCash:
    available_cash_krw:int
    orderable_krw:int
    existing_commitments_krw:int
    ticker_commitments_krw:int
    complete:bool
    as_of:dt.datetime

@dataclass(frozen=True)
class OperationalProof:
    independent_equity_and_flows:bool=False
    exchange_session_confirmed:bool=False
    ticker_eligible:bool=False
    certified_executable_quote:bool=False
    broker_orders_reconciled:bool=False
    ledger_persistent:bool=False
    ledger_recovery_tested:bool=False
    emergency_halt_verified:bool=False

def assess(cash,proof,*,now=None,price_krw=None,env=None):
    now=now or dt.datetime.now(KST)
    env=os.environ if env is None else env
    if any(env.get(k,'false').strip().lower()!='false' for k in ('LIVE_ENABLED','KIS_ALLOW_ORDER')):
        raise Blocked('LIVE_FLAGS_MUST_BE_OFF')
    if now.tzinfo is None: raise Blocked('AWARE_CLOCK_REQUIRED')
    failures=[]
    def block(name,condition):
        if not condition:failures.append(name)
    if not isinstance(cash,AttestedCash):
        failures.append('ACCOUNT_CASH_MISSING')
        cash_ok=False
    else:
        fields=(cash.available_cash_krw,cash.orderable_krw,cash.existing_commitments_krw,cash.ticker_commitments_krw)
        amounts_ok=all(type(x) is int and x>=0 for x in fields)
        stamp_ok=(cash.as_of.tzinfo is not None and 0<=(now-cash.as_of.astimezone(KST)).total_seconds()<=60)
        cash_ok=cash.complete is True and amounts_ok and stamp_ok
        block('ACCOUNT_CASH_NOT_ATTESTED',cash_ok)
    if not isinstance(proof,OperationalProof):proof=OperationalProof()
    for key in proof.__dataclass_fields__:
        block(key.upper()+'_NOT_VERIFIED',getattr(proof,key) is True)
    if type(price_krw) is not int or price_krw<=0:
        failures.append('EXECUTABLE_PRICE_MISSING')
    result={'version':'5.19-operations','gate':'DENY','orders_sent':0,
            'LIVE_ENABLED':False,'KIS_ALLOW_ORDER':False,
            'reserve_pct':40,'trading_pct':60,'ticker_pct_of_trading':30,
            'issues':failures+['HUMAN_APPROVAL_REQUIRED','ORDER_TRANSMISSION_DISABLED'],
            'proposed_qty':None}
    # Never export a trade quantity unless all data provenance and operating evidence pass.
    # Even then, this module is a diagnostic and never authorizes an order.
    if cash_ok and not failures:
        cap=cash.available_cash_krw*60//100
        ticker_cap=cap*30//100
        allowed=max(0,min(cap-cash.existing_commitments_krw,
                          ticker_cap-cash.ticker_commitments_krw,cash.orderable_krw))
        result['proposed_qty']=allowed//price_krw
    return result

def main():
    # No live sensitive numbers are accepted from a repo file or artifact.
    report=assess(None,OperationalProof())
    output=Path('v5_19_operations_readiness.json')
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print('V519_OPERATIONS=DENY; orders_sent=0; no live broker data loaded')
if __name__=='__main__':main()

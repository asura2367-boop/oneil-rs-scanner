"""V5.19: verify order plan against independently collected execution evidence.
Does not place orders. Fail closed until real broker reconciliation exists.
"""
import json, argparse, datetime as dt, re
from pathlib import Path
from zoneinfo import ZoneInfo
KST=ZoneInfo('Asia/Seoul')
class Blocked(Exception): pass

def check(plan, evidence, now=None):
    now=now or dt.datetime.now(KST)
    if now.tzinfo is None: raise Blocked('CLOCK_INVALID')
    if plan.get('status')!='ORDER_PLAN_READ_ONLY' or plan.get('gate')!='DENY' or plan.get('orders_sent')!=0:
        raise Blocked('PLAN_NOT_VERIFIED')
    if evidence.get('broker_history_complete') is not True or evidence.get('pending_orders_reconciled') is not True:
        raise Blocked('BROKER_RECONCILIATION_MISSING')
    if evidence.get('quotes_executable_and_fresh') is not True:
        raise Blocked('EXECUTABLE_QUOTES_MISSING')
    if evidence.get('full_account_exposure_verified') is not True or evidence.get('daily_loss_verified') is not True:
        raise Blocked('ACCOUNT_RISK_EVIDENCE_MISSING')
    # Even with attested evidence, this module is not an order sender.
    orders=plan.get('orders',[])
    if not isinstance(orders,list): raise Blocked('ORDERS_INVALID')
    for o in orders:
        if not re.fullmatch(r'\d{6}',str(o.get('ticker',''))):raise Blocked('TICKER_INVALID')
        if type(o.get('preview_quantity')) is not int or type(o.get('reference_price')) is not int:raise Blocked('SIZE_INVALID')
        if o['preview_quantity']*o['reference_price']>180000:raise Blocked('STOCK_LIMIT')
    return {'gate':'DENY','status':'RISK_EVIDENCE_REVIEWED_NOT_EXECUTABLE','orders_sent':0}

def main():
    p=argparse.ArgumentParser();p.add_argument('--plan',required=True);p.add_argument('--evidence',required=True);p.add_argument('--output',default='v519_execution_gate.json');a=p.parse_args()
    try:
        result=check(json.loads(Path(a.plan).read_text()),json.loads(Path(a.evidence).read_text()))
        code=0
    except (Blocked,ValueError,KeyError,TypeError):
        result={'gate':'DENY','status':'BLOCKED','orders_sent':0}
        code=2
    Path(a.output).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(result['status']+'; orders_sent=0');raise SystemExit(code)
if __name__=='__main__':main()

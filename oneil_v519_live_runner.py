#!/usr/bin/env python3
"""V5.19 KIS live runner for persistent machines.
Live orders require explicit operator unlock. Default: READ_ONLY.
NEVER run live on ephemeral GitHub Actions because local SQLite state would vanish.
"""
import argparse, datetime as dt, fcntl, hashlib, json, os, re, sqlite3, sys, time, urllib.parse, urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo
from oneil_v519_order_recovery import OrderJournal, ReconcileBlocked

KST=ZoneInfo("Asia/Seoul")
HOST="https://openapi.koreainvestment.com:9443"
CODE=re.compile(r"^[0-9]{6}$")
class Blocked(Exception): pass
def integer(x):
    if type(x) is not int or x<0:raise Blocked("AMOUNT_INVALID")
    return x
def flag(name):return os.environ.get(name,"false").lower()=="true"
def api(path, token, key, secret, tr, *, method="GET", params=None, payload=None):
    url=HOST+path+("?" + urllib.parse.urlencode(params) if params is not None else "")
    headers={"authorization":"Bearer "+token,"appkey":key,"appsecret":secret,
             "tr_id":tr,"custtype":"P","Content-Type":"application/json"}
    req=urllib.request.Request(url,data=json.dumps(payload).encode() if payload else None,
                               headers=headers,method=method)
    with urllib.request.urlopen(req,timeout=12) as res: body=json.load(res)
    if not isinstance(body,dict) or str(body.get("rt_cd"))!="0":raise Blocked("KIS_RESPONSE_REJECTED")
    return body
def token(key,secret):
    req=urllib.request.Request(HOST+"/oauth2/tokenP",data=json.dumps(
        {"grant_type":"client_credentials","appkey":key,"appsecret":secret}).encode(),
        headers={"Content-Type":"application/json"},method="POST")
    with urllib.request.urlopen(req,timeout=12) as res:body=json.load(res)
    t=body.get("access_token")
    if not isinstance(t,str) or not t:raise Blocked("TOKEN_MISSING")
    return t
def strict_amount(v):
    if not isinstance(v,str) or not re.fullmatch(r"\d+",v):raise Blocked("BAD_BROKER_AMOUNT")
    return integer(int(v))
def balance(tok,key,secret,cano,prod):
    params={"CANO":cano,"ACNT_PRDT_CD":prod,"AFHR_FLPR_YN":"N","OFL_YN":"",
     "INQR_DVSN":"00","UNPR_DVSN":"01","FUND_STTL_ICLD_YN":"N",
     "FNCG_AMT_AUTO_RDPT_YN":"N","PRCS_DVSN":"00","CTX_AREA_FK100":"","CTX_AREA_NK100":""}
    data=api("/uapi/domestic-stock/v1/trading/inquire-balance",tok,key,secret,"TTTC8434R",params=params)
    if not isinstance(data.get("output1"),list) or not isinstance(data.get("output2"),list) or len(data["output2"])!=1:
        raise Blocked("BALANCE_INCOMPLETE")
    cash=strict_amount(data["output2"][0].get("dnca_tot_amt"))
    held=set()
    for r in data["output1"]:
        qty=strict_amount(r.get("hldg_qty"))
        if qty:
            code=r.get("pdno")
            if not isinstance(code,str) or not CODE.fullmatch(code):raise Blocked("POSITION_INVALID")
            held.add(code)
    return cash,held
def buying_power(tok,key,secret,cano,prod,ticker,price):
    body=api("/uapi/domestic-stock/v1/trading/inquire-psbl-order",tok,key,secret,"TTTC8908R",
       params={"CANO":cano,"ACNT_PRDT_CD":prod,"PDNO":ticker,"ORD_UNPR":str(price),
       "ORD_DVSN":"00","CMA_EVLU_AMT_ICLD_YN":"N","OVRS_ICLD_YN":"N"})
    x=body.get("output")
    if not isinstance(x,dict):raise Blocked("POWER_MISSING")
    return min(strict_amount(x.get("nrcvb_buy_amt")),strict_amount(x.get("ord_psbl_cash"))),strict_amount(x.get("nrcvb_buy_qty"))
def price_reference(tok,key,secret,ticker):
    body=api("/uapi/domestic-stock/v1/quotations/inquire-price",tok,key,secret,"FHKST01010100",
      params={"FID_COND_MRKT_DIV_CODE":"J","FID_INPUT_ISCD":ticker})
    out=body.get("output")
    if not isinstance(out,dict):raise Blocked("QUOTE_MISSING")
    return strict_amount(out.get("stck_prpr"))
def open_persistent_journal(state):
    """Open the durable journal on a persistent host; fail closed on unresolved orders."""
    journal = OrderJournal(state / "orders.sqlite")
    if journal.pending():
        journal.close()
        raise Blocked("UNRESOLVED_BROKER_ORDERS_RECONCILIATION_REQUIRED")
    return journal

def live_submit(tok,key,secret,cano,prod,ticker,qty,price,side):
    if not (flag("LIVE_ENABLED") and flag("KIS_ALLOW_ORDER")
            and os.environ.get("I_APPROVE_LIVE_KIS_ORDERS")=="YES"):
        raise Blocked("USER_APPROVAL_MISSING")
    payload={"CANO":cano,"ACNT_PRDT_CD":prod,"PDNO":ticker,"ORD_DVSN":"00",
             "ORD_QTY":str(qty),"ORD_UNPR":str(price)}
    return api("/uapi/domestic-stock/v1/trading/order-cash",tok,key,secret,
        "TTTC0012U" if side=="BUY" else "TTTC0011U",method="POST",payload=payload)
def recover_orders_readonly(state):
    """Reconcile persisted order IDs against complete same-day KIS history.
    Unknown submissions remain locked and are NEVER automatically retried.
    """
    if flag("LIVE_ENABLED") or flag("KIS_ALLOW_ORDER"):
        raise Blocked("RECOVERY_REQUIRES_LIVE_FLAGS_OFF")
    from oneil_v519_broker_recovery import observe_and_reconcile
    with open(state / "oneil.lock", "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        journal = OrderJournal(state / "orders.sqlite")
        try:
            result = observe_and_reconcile(journal)
            return {"gate": "DENY", "recovered": result["matched"],
                    "unresolved": len(journal.pending()), "orders_sent": 0}
        finally:
            journal.close()

def run(args):
    if os.environ.get("GITHUB_ACTIONS")=="true":raise Blocked("EPHEMERAL_RUNNER_UNSAFE_FOR_LIVE")
    state=Path(args.state_dir).resolve()
    if not state.exists() or not state.is_dir():raise Blocked("PERSISTENT_STATE_DIR_MISSING")
    if state.stat().st_mode & 0o022:raise Blocked("INSECURE_STATE_DIR")
    if args.recover_only:
        print(json.dumps(recover_orders_readonly(state), ensure_ascii=False))
        return
    if not args.ticker or not CODE.fullmatch(args.ticker):raise Blocked("TICKER_INVALID")
    # This release is BUY-only. Sell workflows require verified held quantity.
    if args.side!="BUY":raise Blocked("SELL_NOT_SUPPORTED_IN_THIS_RELEASE")
    if not args.certified_quote or not args.daily_loss_evidence or not args.order_history_reconciled:
        raise Blocked("QUOTE_PNL_OR_ORDER_RECONCILIATION_MISSING")
    if not args.external_ledger_verified:raise Blocked("LEDGER_RECOVERY_NOT_PROVEN")
    now=dt.datetime.now(KST)
    if now.weekday()>4 or not(dt.time(9)<=now.time()<dt.time(15,30)):
        raise Blocked("REGULAR_SESSION_ONLY_UNTIL_VENUE_ATTESTATION")
    key=os.environ["KIS_APP_KEY"];secret=os.environ["KIS_APP_SECRET"]
    cano=os.environ["KIS_ACCOUNT_NO"];prod=os.environ["KIS_ACCOUNT_PRODUCT"]
    if not re.fullmatch(r"\d{8}",cano) or not re.fullmatch(r"\d{2}",prod):
        raise Blocked("ACCOUNT_CONFIG_INVALID")
    t=token(key,secret)
    cash,held=balance(t,key,secret,cano,prod)
    if args.ticker in held:raise Blocked("ALREADY_HELD")
    # IMPORTANT: require external independently verified unfilled/order history as well.
    quote=price_reference(t,key,secret,args.ticker)
    if quote==0:raise Blocked("QUOTE_INVALID")
    amount,broker_qty=buying_power(t,key,secret,cano,prod,args.ticker,quote)
    qty=min((cash*60//100)*30//100//quote,amount//quote,broker_qty)
    if qty<=0:raise Blocked("ZERO_ORDER_QUANTITY")
    if args.max_qty:qty=min(qty,args.max_qty)
    ident=hashlib.sha256(f"{now.date()}|{args.ticker}|{args.side}|{args.signal_id}".encode()).hexdigest()
    with open(state/"oneil.lock","a+") as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        journal=open_persistent_journal(state)
        if not args.execute:
            print(json.dumps({"gate":"DENY","phase":"REVIEW","ticker":args.ticker,
              "broker_cash_observed":True,"price_is_reference_only":True,"qty_preview":qty,
              "orders_sent":0},ensure_ascii=False))
            journal.close()
            return
        # No live order can proceed until price is certified by venue-specific book,
        # full holdings/unfilled exposure and daily loss are independently verified.
        journal.close()
        raise Blocked("LIVE_ORDER_RELEASE_NOT_YET_ATTESTED")
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--state-dir",required=True)
    p.add_argument("--ticker")
    p.add_argument("--side",default="BUY",choices=["BUY","SELL"])
    p.add_argument("--signal-id")
    p.add_argument("--certified-quote",action="store_true")
    p.add_argument("--daily-loss-evidence",action="store_true")
    p.add_argument("--order-history-reconciled",action="store_true")
    p.add_argument("--external-ledger-verified",action="store_true")
    p.add_argument("--max-qty",type=int,default=None)
    p.add_argument("--recover-only",action="store_true")
    p.add_argument("--execute",action="store_true")
    a=p.parse_args()
    try:run(a)
    except Exception as e:
        print("GATE=DENY; reason="+(str(e) if isinstance(e,Blocked) else "UNVERIFIED_FAILURE")+"; orders_sent=0")
        sys.exit(2)
if __name__=="__main__":main()

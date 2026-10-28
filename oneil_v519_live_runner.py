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
def calculate_cash_budget(cash_krw, orderable_krw, broker_qty, reference_price_krw, *,
                          existing_exposure_krw, pending_orders_verified):
    """Fail-closed whole-account cash sizing. Never authorizes an order.

    The reserve is 40% of broker-observed cash; the 60% trading budget must
    also cover verified pre-existing exposure. Unknown pending commitments block.
    """
    for value in (cash_krw,orderable_krw,broker_qty,reference_price_krw,existing_exposure_krw):
        integer(value)
    if reference_price_krw == 0: raise Blocked("REFERENCE_PRICE_INVALID")
    if pending_orders_verified is not True:
        raise Blocked("PENDING_ORDERS_NOT_VERIFIED")
    trading_cap=cash_krw*60//100
    stock_cap=trading_cap*30//100
    remaining=max(0,trading_cap-existing_exposure_krw)
    notional=min(remaining,stock_cap,orderable_krw)
    return min(notional//reference_price_krw,broker_qty)

def determine_exit(*, held_qty, average_cost_krw, reference_price_krw,
                   stop_loss_pct=5, take_profit_pct=10):
    """Conservative sell *signal* only, never a broker order price/approval.

    Percent limits are configuration examples, not an established user preference.
    Fees, taxes and slip are not included. Never sell more than verified holdings.
    """
    for amount in (held_qty, average_cost_krw, reference_price_krw):
        integer(amount)
    if held_qty <= 0 or average_cost_krw <= 0 or reference_price_krw <= 0:
        raise Blocked("INVALID_HOLDING_OR_QUOTE")
    for pct in (stop_loss_pct, take_profit_pct):
        if type(pct) is not int or not 1 <= pct <= 50:
            raise Blocked("EXIT_PARAMETER_INVALID")
    loss_hit = reference_price_krw * 100 <= average_cost_krw * (100 - stop_loss_pct)
    profit_hit = reference_price_krw * 100 >= average_cost_krw * (100 + take_profit_pct)
    reason = "STOP_LOSS" if loss_hit else "TAKE_PROFIT" if profit_hit else "HOLD"
    return {"decision": reason, "max_sell_qty": held_qty if reason != "HOLD" else 0,
            "gate": "DENY", "executable_price_approved": False, "orders_sent": 0}

def local_status(state):
    """Inspect durable journal without KIS side effects or exposing private IDs."""
    with open(state / "oneil.lock", "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        journal = OrderJournal(state / "orders.sqlite")
        try:
            pending = journal.pending()
            return {"gate":"DENY", "pending_orders":len(pending),
                    "can_start_new_orders":False, "orders_sent":0}
        finally:
            journal.close()

def open_persistent_journal(state):
    """Open the durable journal on a persistent host; fail closed on unresolved orders."""
    journal = OrderJournal(state / "orders.sqlite")
    if journal.pending():
        journal.close()
        raise Blocked("UNRESOLVED_BROKER_ORDERS_RECONCILIATION_REQUIRED")
    return journal

def live_submit(tok,key,secret,cano,prod,ticker,qty,price,side):
    """Compatibility entry point: orders must use the integrated durable engine."""
    if side not in ('BUY','SELL'):
        raise Blocked('ORDER_SIDE_INVALID')
    if type(qty) is not int or qty<=0 or type(price) is not int or price<=0:
        raise Blocked('ORDER_QUANTITY_OR_PRICE_INVALID')
    if not re.fullmatch('[0-9]{8}',cano) or not re.fullmatch('[0-9]{2}',prod):
        raise Blocked('ACCOUNT_CONFIG_INVALID')
    raise Blocked("RELEASE_LOCKED_PENDING_USER_APPROVAL")

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

def emergency_halt(state):
    """Persist an irreversible-until-manual-review emergency halt."""
    import tempfile
    halt_file=state / "EMERGENCY_HALT.json"
    with open(state / "oneil.lock", "a+") as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        doc={"halted":True,"at_kst":dt.datetime.now(KST).isoformat(),
             "reason":"OPERATOR_EMERGENCY_HALT"}
        fd,temp=tempfile.mkstemp(dir=str(state),prefix=".halt-")
        try:
            with os.fdopen(fd,"w",encoding="utf8") as out:
                json.dump(doc,out,ensure_ascii=False)
                out.flush()
                os.fsync(out.fileno())
            os.replace(temp,halt_file)
        finally:
            if os.path.exists(temp):os.unlink(temp)
        return {"halted":True,"gate":"DENY","orders_sent":0}

def require_not_halted(state):
    halt_file=state / "EMERGENCY_HALT.json"
    if halt_file.exists():
        raise Blocked("EMERGENCY_HALT_ACTIVE_MANUAL_REVIEW_REQUIRED")

def run(args):
    if os.environ.get("GITHUB_ACTIONS")=="true":raise Blocked("EPHEMERAL_RUNNER_UNSAFE_FOR_LIVE")
    state=Path(args.state_dir).resolve()
    if not state.exists() or not state.is_dir():raise Blocked("PERSISTENT_STATE_DIR_MISSING")
    if state.stat().st_mode & 0o022:raise Blocked("INSECURE_STATE_DIR")
    if args.emergency_halt:
        print(json.dumps(emergency_halt(state),ensure_ascii=False))
        return
    require_not_halted(state)
    if args.status_only:
        print(json.dumps(local_status(state), ensure_ascii=False))
        return
    if args.exit_preview:
        if not (args.held_qty is not None and args.average_cost is not None and args.reference_price is not None):
            raise Blocked("EXIT_INPUT_MISSING")
        print(json.dumps(determine_exit(held_qty=args.held_qty,
            average_cost_krw=args.average_cost,reference_price_krw=args.reference_price,
            stop_loss_pct=args.stop_loss_pct,take_profit_pct=args.take_profit_pct),ensure_ascii=False))
        return
    if args.recover_only:
        print(json.dumps(recover_orders_readonly(state), ensure_ascii=False))
        return
    if not args.ticker or not CODE.fullmatch(args.ticker):raise Blocked("TICKER_INVALID")
    # This release is BUY-only. Sell workflows require verified held quantity.
    if args.execute:
        raise Blocked("LIVE_EXECUTION_UNAVAILABLE_UNTIL_EVIDENCE_AND_RECOVERY_AUDITED")
    if args.side!="BUY":raise Blocked("SELL_NOT_SUPPORTED_IN_THIS_RELEASE")
    # CLI flags are user assertions, never independent certification.
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
    # A first-page balance response is not a complete portfolio snapshot.
    from kis_safety_v3 import verified_balance_snapshot, unfilled_codes
    held, _portfolio_value = verified_balance_snapshot(key, secret, cano, prod, t)
    unfilled = unfilled_codes(key, secret, cano, prod, t)
    if args.ticker in held:raise Blocked("ALREADY_HELD")
    if unfilled:
        # No verified commitment amounts for other pending orders; fail closed.
        raise Blocked("UNFILLED_ORDERS_REQUIRE_COMMITMENT_RECONCILIATION")
    # Read current cash separately. This is account observation, not certified
    # whole-account daily PnL and not evidence of a stable 60% trading ceiling.
    cash,_=balance(t,key,secret,cano,prod)
    # IMPORTANT: require external independently verified unfilled/order history as well.
    quote=price_reference(t,key,secret,args.ticker)
    if quote==0:raise Blocked("QUOTE_INVALID")
    amount,broker_qty=buying_power(t,key,secret,cano,prod,args.ticker,quote)
    qty=calculate_cash_budget(cash,amount,broker_qty,quote,
        existing_exposure_krw=_portfolio_value,pending_orders_verified=True)
    if qty<=0:raise Blocked("ZERO_ORDER_QUANTITY")
    if args.max_qty is not None:
        if type(args.max_qty) is not int or args.max_qty <= 0:
            raise Blocked("INVALID_MAX_QTY")
        qty=min(qty,args.max_qty)
    if qty <= 0 or qty * quote > (cash * 60 // 100) * 30 // 100:
        raise Blocked("PER_STOCK_BUDGET_EXCEEDED")
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
        journal.close()
        raise Blocked("LIVE_EXECUTION_UNAVAILABLE_UNTIL_EVIDENCE_AND_RECOVERY_AUDITED")
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
    p.add_argument("--emergency-halt",action="store_true")
    p.add_argument("--status-only",action="store_true")
    p.add_argument("--exit-preview",action="store_true")
    p.add_argument("--held-qty",type=int)
    p.add_argument("--average-cost",type=int)
    p.add_argument("--reference-price",type=int)
    p.add_argument("--stop-loss-pct",type=int,default=5)
    p.add_argument("--take-profit-pct",type=int,default=10)
    p.add_argument("--recover-only",action="store_true")
    p.add_argument("--execute",action="store_true")
    a=p.parse_args()
    try:run(a)
    except Exception as e:
        print("GATE=DENY; reason="+(str(e) if isinstance(e,Blocked) else "UNVERIFIED_FAILURE")+"; orders_sent=0")
        sys.exit(2)
if __name__=="__main__":main()

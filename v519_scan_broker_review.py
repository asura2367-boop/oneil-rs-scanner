#!/usr/bin/env python3
"""V5.19 candidate/KIS/Supabase read-only review with safe stage diagnostics."""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo
from v519_same_run_candidates import build
from v519_pg_ledger import connect, status

KST = ZoneInfo("Asia/Seoul")
class ReviewBlocked(RuntimeError):
    def __init__(self, stage, reason):
        super().__init__(reason)
        self.stage, self.reason = stage, reason

def checked(stage, func):
    try:
        return func()
    except ReviewBlocked:
        raise
    except Exception:
        raise ReviewBlocked(stage, "OPERATION_FAILED") from None

def review(csv_path, day, *, now=None, broker=None, ledger=None):
    now = now or dt.datetime.now(KST)
    if os.getenv("LIVE_ENABLED", "false").lower() != "false" or os.getenv("KIS_ALLOW_ORDER", "false").lower() != "false":
        raise ReviewBlocked("LIVE_GATE", "SWITCH_MUST_BE_OFF")
    if now.astimezone(KST).date().isoformat() != day:
        raise ReviewBlocked("DATE", "DAY_MISMATCH")
    candidates = checked("CANDIDATE_INPUT", lambda: build(csv_path, day, now)["candidates"])
    if not candidates:
        return {"gate":"DENY","orders_sent":0,"status":"NO_ELIGIBLE_CANDIDATES","candidates":[]}
    if ledger is None or broker is None:
        raise ReviewBlocked("CONFIG", "ADAPTER_MISSING")
    control = checked("SUPABASE_STATUS", ledger)
    if control.get("halted") is not True or control.get("unresolved") != 0:
        raise ReviewBlocked("SUPABASE_CONTROL", "HALTED_OR_UNRESOLVED")
    rows = []
    for c in candidates:
        evidence = checked("KIS_CANDIDATE", lambda: broker(c["ticker"]))
        if not isinstance(evidence,dict) or evidence.get("ticker") != c["ticker"]:
            raise ReviewBlocked("KIS_CANDIDATE", "TICKER_MISMATCH")
        price = evidence.get("price")
        if type(price) is not int or price <= 0:
            raise ReviewBlocked("KIS_PRICE", "INVALID_PRICE")
        broker_qty = evidence.get("broker_qty")
        broker_cash = evidence.get("broker_cash")
        if type(broker_qty) is not int or broker_qty < 0 or type(broker_cash) is not int or broker_cash < 0:
            raise ReviewBlocked("KIS_BUYING_POWER", "INVALID_AMOUNT")
        from v519_cash_policy import quantity
        qty = 0 if evidence.get("held") or evidence.get("pending") else quantity(
            evidence['cash'], evidence['invested'], broker_cash, price, broker_qty)
        rows.append({"ticker":c["ticker"],"rs":c["rs"],"reference_price":price,
                     "theoretical_max_qty":qty,"quote_executable_verified":False,
                     "order_authorized":False})
    return {"gate":"DENY","orders_sent":0,"status":"READ_ONLY_BROKER_REVIEW",
            "risk_policy_pct":{"reserve":40,"trading":60,"per_stock_of_trading":30},
            "candidates":rows}

def broker_adapter():
    from oneil_v519_live_engine import KisBroker
    broker=checked('KIS_AUTH',KisBroker)
    snapshot=checked('KIS_BALANCE',broker.snapshot)
    def get(ticker):
        price=checked('KIS_PRICE',lambda:broker.quote(ticker,'BUY'))
        amount,qty=checked('KIS_BUYING_POWER',lambda:broker.power(ticker,price))
        return {'ticker':ticker,'price':price,'broker_qty':qty,'broker_cash':amount,
                'cash':snapshot['cash'],'invested':snapshot['invested'],
                'held':ticker in snapshot['positions'],'pending':bool(snapshot['pending'])}
    return get

def ledger_adapter():
    def read():
        with connect() as db:
            return status(db)
    return checked("SUPABASE_STATUS", read)

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--csv",required=True)
    parser.add_argument("--date",required=True)
    parser.add_argument("--output",default="v519_broker_review.json")
    args=parser.parse_args()
    try:
        # Check candidate input and Supabase before initializing broker adapter.
        # This avoids hiding candidate errors behind unrelated KIS authentication errors.
        today=dt.datetime.now(KST)
        if today.date().isoformat()!=args.date:
            raise ReviewBlocked("DATE","DAY_MISMATCH")
        candidates=checked("CANDIDATE_INPUT",lambda:build(args.csv,args.date,today)["candidates"])
        if not candidates:
            result={"gate":"DENY","orders_sent":0,"status":"NO_ELIGIBLE_CANDIDATES","candidates":[]}
        else:
            ledger=ledger_adapter()
            if ledger.get("halted") is not True or ledger.get("unresolved")!=0:
                raise ReviewBlocked("SUPABASE_CONTROL","HALTED_OR_UNRESOLVED")
            broker=broker_adapter()
            result=review(args.csv,args.date,now=today,broker=broker,ledger=lambda:ledger)
        code=0
    except ReviewBlocked as e:
        result={"gate":"DENY","orders_sent":0,"status":"BLOCKED","stage":e.stage,
                "reason":e.reason,"candidates":[]}
        code=2
    except Exception:
        result={"gate":"DENY","orders_sent":0,"status":"BLOCKED","stage":"UNEXPECTED",
                "reason":"OPERATION_FAILED","candidates":[]}
        code=2
    Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("V519_REVIEW="+result["status"]+"; stage="+result.get("stage","OK")+"; reason="+result.get("reason","NONE")+"; orders_sent=0")
    raise SystemExit(code)

if __name__=="__main__":
    main()

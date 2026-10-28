#!/usr/bin/env python3
"""V5.19 same-run evidence summary. Diagnostics only; NO order or broker API calls."""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo

KST=ZoneInfo("Asia/Seoul")
def _load(path):
    try:
        obj=json.loads(Path(path).read_text(encoding="utf-8"))
        return obj if isinstance(obj,dict) else None
    except (OSError,ValueError,UnicodeError):
        return None

def _fresh(obj,now,seconds=300):
    if not isinstance(obj,dict):return False
    val=obj.get("generated_at_kst") or obj.get("as_of_kst")
    try:
        t=dt.datetime.fromisoformat(val)
        return t.tzinfo is not None and 0 <= (now-t.astimezone(KST)).total_seconds() <= seconds
    except (TypeError,ValueError,OverflowError):return False

def summarize(candidate,account,buying,quote,loss,history,now=None,env=None):
    env=os.environ if env is None else env
    if env.get("LIVE_ENABLED","false").lower()!="false" or env.get("KIS_ALLOW_ORDER","false").lower()!="false":
        raise ValueError("LIVE_SWITCH_NOT_DISABLED")
    now=now or dt.datetime.now(KST)
    if now.tzinfo is None:raise ValueError("NAIVE_CLOCK")
    blocks=[]
    def check(ok,code):
        if not ok:blocks.append(code)
        return bool(ok)
    rows=candidate.get("intents") if isinstance(candidate,dict) else None
    codes=[x.get("ticker") for x in rows] if isinstance(rows,list) and all(isinstance(x,dict) for x in rows) else None
    candidate_ok=check(
        _fresh(candidate,now) and candidate.get("gate")=="DENY"
        and candidate.get("mode")=="LIVE_INTENTS_PREVIEW_ONLY" and type(candidate.get("orders_sent")) is int
        and candidate["orders_sent"]==0 and codes is not None and 0<len(codes)<=25
        and len(set(codes))==len(codes)
        and all(isinstance(t,str) and len(t)==6 and t.isascii() and t.isdigit() for t in codes)
        and all(x.get("status")=="REVIEW_ONLY_UNPRICED" and x.get("quantity") is None
                and x.get("price_krw") is None for x in rows),
        "CANDIDATE_EVIDENCE_UNVERIFIED")
    account_ok=check(_fresh(account,now) and account.get("gate")=="DENY"
       and account.get("orders_sent")==0 and account.get("broker_status")=="READ_ONLY_OBSERVED",
       "ACCOUNT_OBSERVATION_UNVERIFIED")
    buying_ok=check(_fresh(buying,now) and buying.get("gate")=="DENY"
       and buying.get("orders_sent")==0 and buying.get("status")=="OBSERVATION_ATTEMPTED",
       "INDICATIVE_POWER_UNVERIFIED")
    quote_ok=check(_fresh(quote,now) and quote.get("version")=="5.18"
       and quote.get("gate")=="DENY" and quote.get("orders_sent")==0,
       "QUOTE_OBSERVATION_UNVERIFIED")
    # Observation is NOT a certified executable price.
    blocks.append("EXECUTABLE_QUOTE_UNATTESTED")
    # Only independently verified pre-open whole-account baseline and reconciled
    # cash flows can verify daily loss. A read-only account snapshot is insufficient.
    loss_ok=check(_fresh(loss,now) and loss.get("gate")=="DENY"
       and loss.get("daily_loss_evidence_valid") is True
       and loss.get("daily_loss_limit_reached") is False
       and loss.get("orders_sent")==0, "INDEPENDENT_DAILY_LOSS_NOT_VERIFIED")
    # Format-valid history is not evidence of complete broker order reconciliation.
    history_ok=check(_fresh(history,now) and history.get("gate")=="DENY"
       and history.get("order_history_structure_valid") is True
       and history.get("orders_sent")==0, "BROKER_HISTORY_FORMAT_NOT_VERIFIED")
    blocks.extend(["BROKER_ORDER_HISTORY_INDEPENDENCE_UNVERIFIED",
                   "AUTO_RESUBMISSION_DISABLED","LIVE_ORDER_TRANSMISSION_DISABLED"])
    return {"version":"5.19-final-batch-diagnostic",
        "generated_at_kst":now.isoformat(timespec="seconds"),"gate":"DENY",
        "candidate_count":len(codes) if candidate_ok else 0,
        "account_observed":account_ok,"indicative_buying_power_observed":buying_ok,
        "quote_observed":quote_ok,"daily_loss_evidence_valid":loss_ok,
        "history_structure_valid":history_ok,"block_codes":blocks,
        "reserve_pct":40,"trading_pct":60,"stock_pct_of_trading_budget":30,
        "approved_order_qty_count":0,"orders_sent":0,"broker_order_api_calls":0,
        "LIVE_ENABLED":False,"KIS_ALLOW_ORDER":False}

def main():
    p=argparse.ArgumentParser()
    for k,default in [
      ("candidate","v5_11_review.json"),("account","v5_13_review.json"),
      ("buying","v5_19_indicative_power.json"),("quote","v5_18_report.json"),
      ("loss","v5_19_loss_history_diagnostic.json"),
      ("history","v5_19_loss_history_diagnostic.json"),
      ("output","v5_19_final_diagnostic.json")]:
        p.add_argument("--"+k,default=default)
    a=p.parse_args()
    r=summarize(*(_load(getattr(a,k)) for k in ("candidate","account","buying","quote","loss","history")))
    Path(a.output).write_text(json.dumps(r,ensure_ascii=False,indent=2)+"\n",encoding="utf8")
    print("V519_FINAL_DIAGNOSTIC=DENY; candidates="+str(r["candidate_count"])+"; orders_sent=0")
if __name__=="__main__":main()

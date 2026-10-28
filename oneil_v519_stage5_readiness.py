#!/usr/bin/env python3
"""Stage 5 readiness report for O'Neil RS V5.19. NEVER calls an order endpoint.
A report is informational, not permission to transmit orders.
"""
from __future__ import annotations
import argparse, datetime as dt, json, os
from pathlib import Path
from zoneinfo import ZoneInfo
KST=ZoneInfo("Asia/Seoul")
REQUIRED={
 "candidate":"candidate_data_current",
 "account":"broker_account_verified",
 "buying_power":"venue_buying_power_verified",
 "quotes":"executable_quote_certified",
 "loss":"independent_daily_pnl_verified",
 "history":"broker_execution_reconciled",
 "recovery":"crash_recovery_rehearsed",
 "paper":"paper_trial_accepted",
 "stop":"emergency_stop_tested",
 "market":"exchange_calendar_and_eligibility_verified",
}
def _load(path):
    if not path:return None
    try:
        d=json.loads(Path(path).read_text(encoding="utf8"))
        return d if isinstance(d,dict) else None
    except (OSError,UnicodeError,ValueError):return None

def assess(evidence,now=None,env=None):
    now=now or dt.datetime.now(KST)
    env=os.environ if env is None else env
    if now.tzinfo is None:raise ValueError("CLOCK_NAIVE")
    if env.get("LIVE_ENABLED","false").strip().lower()!="false" or env.get("KIS_ALLOW_ORDER","false").strip().lower()!="false":
        raise ValueError("ORDER_SWITCH_MUST_REMAIN_OFF")
    blocks=[]
    evidence=evidence if isinstance(evidence,dict) else {}
    for category,field in REQUIRED.items():
        report=evidence.get(category)
        valid=(isinstance(report,dict) and report.get("verified") is True
               and report.get(field) is True and report.get("orders_sent")==0)
        if valid:
            try:
                stamp=dt.datetime.fromisoformat(report["generated_at_kst"])
                valid=bool(stamp.tzinfo and 0 <= (now-stamp.astimezone(KST)).total_seconds() <= 300)
            except (KeyError,TypeError,ValueError,OverflowError):valid=False
        if not valid:blocks.append("MISSING_OR_UNVERIFIED_"+category.upper())
    # Explicit human sign-off is NEVER inferred from a file/flag.
    blocks+=["FINAL_USER_APPROVAL_NOT_REQUESTED","ORDER_TRANSMISSION_DISABLED"]
    return {"version":"5.19-stage5-readiness","generated_at_kst":now.isoformat(timespec="seconds"),
            "gate":"DENY","readiness_checks_total":len(REQUIRED),
            "readiness_checks_missing":len(blocks)-2,
            "blocks":blocks,"capital_krw":1000000,"reserve_pct":40,
            "trading_pct":60,"stock_pct_of_trading":30,"daily_loss_limit_krw":20000,
            "orders_sent":0,"broker_order_api_calls":0,
            "LIVE_ENABLED":False,"KIS_ALLOW_ORDER":False,"user_approved":False}
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--evidence",default=None)
    p.add_argument("--output",default="v519_stage5_readiness.json")
    a=p.parse_args()
    report=assess(_load(a.evidence))
    Path(a.output).write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf8")
    print("STAGE5_READINESS=DENY; missing_checks="+str(report["readiness_checks_missing"])+"; orders_sent=0")
if __name__=="__main__":main()

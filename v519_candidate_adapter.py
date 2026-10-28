"""V5.19: connect V5.10 candidate reports to the order proposal stage.
Strictly read-only; rejects stale/unverified candidates; never submits orders.
"""
import argparse
import datetime as dt
import json
import re
from pathlib import Path
from zoneinfo import ZoneInfo

KST=ZoneInfo("Asia/Seoul")
CODE=re.compile(r"^[0-9]{6}$")

def select_candidates(report, *, now=None, max_age_minutes=15, min_rs=90):
    now=now or dt.datetime.now(KST)
    if now.tzinfo is None: raise ValueError("NAIVE_CLOCK")
    if report.get("candidate_input_valid") is not True: raise ValueError("CANDIDATE_INPUT_INVALID")
    if report.get("candidate_source") != "V4B_CSV_UNVERIFIED":
        raise ValueError("UNRECOGNIZED_CANDIDATE_SOURCE")
    if report.get("mode") != "READ_ONLY_CANDIDATE_AND_ACCOUNT_EVIDENCE":
        raise ValueError("UNEXPECTED_REPORT_MODE")
    generated=dt.datetime.fromisoformat(report["generated_at_kst"])
    age=(now-generated).total_seconds()/60
    if generated.tzinfo is None or not 0<=age<=max_age_minutes:
        raise ValueError("STALE_CANDIDATES")
    if report.get("candidate_count") != len(report.get("candidates",[])):
        raise ValueError("CANDIDATE_COUNT_MISMATCH")
    seen=set(); result=[]
    for c in report["candidates"]:
        code=c.get("ticker"); rs=c.get("rs")
        if not isinstance(code,str) or not CODE.fullmatch(code) or code in seen:
            raise ValueError("INVALID_OR_DUPLICATE_TICKER")
        seen.add(code)
        if not isinstance(rs,(int,float)) or isinstance(rs,bool) or not 0<=rs<=100:
            raise ValueError("INVALID_RS")
        if rs>=min_rs:
            result.append({"ticker":code,"rs":rs,"flow_category":c.get("flow_category"),
                           "price_verified":False,"orderable_qty":None,
                           "order_permission":False})
    return sorted(result,key=lambda x:-x["rs"])

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--report",required=True)
    p.add_argument("--output",default="v519_candidate_proposals.json")
    args=p.parse_args()
    report=json.loads(Path(args.report).read_text(encoding="utf-8"))
    try:
        candidates=select_candidates(report)
        result={"gate":"DENY","orders_sent":0,"status":"FRESH_UNVERIFIED_CANDIDATES",
                "candidates":candidates}
    except (ValueError,KeyError,TypeError) as exc:
        result={"gate":"DENY","orders_sent":0,"status":"BLOCKED",
                "reason":str(exc),"candidates":[]}
    Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print("V519_CANDIDATES="+result["status"]+"; orders_sent=0")
    if result["status"]=="BLOCKED": raise SystemExit(2)
if __name__=="__main__": main()

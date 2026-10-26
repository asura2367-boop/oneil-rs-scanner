#!/usr/bin/env python3
"""Schedule-aware, NO-ORDER, existing-data analysis controlled by V5.1 switch."""
import argparse
import csv
import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
def locked():
    return (os.getenv("LIVE_ENABLED", "false").lower() == "false"
            and os.getenv("KIS_ALLOW_ORDER", "false").lower() == "false")
def control(path):
    if not path.is_file():
        return False, "SWITCH_MISSING_OFF"
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        if (obj.get("schema_version") != 1 or
                type(obj.get("analysis_enabled")) is not bool or
                obj.get("real_order_enabled") is not False or
                obj.get("orders_authorized") is not False):
            return False, "SWITCH_INVALID_OFF"
        return obj["analysis_enabled"], "ON" if obj["analysis_enabled"] else "OFF"
    except (OSError, ValueError, TypeError):
        return False, "SWITCH_INVALID_OFF"
def session(now):
    t=now.hour*60+now.minute
    if now.weekday()>4: return "CLOSED"
    if 9*60 <= t < 15*60+30: return "REGULAR"
    if 15*60+40 <= t < 20*60: return "EVENING_REVIEW"
    return "CLOSED"
def read_candidates(path, today):
    if not path.is_file():
        return "MISSING_CANDIDATE_FILE", []
    try:
        with path.open(encoding="utf-8-sig",newline="") as f:
            reader=csv.DictReader(f)
            if not {"date","ticker","rs"}.issubset(reader.fieldnames or []):
                return "INVALID_CANDIDATE_FILE", []
            rows=list(reader)
        if not rows: return "NO_CANDIDATES",[]
        # Signal date alone is not proof of live prices or tradeable evening market.
        valid=[r for r in rows if r["date"]==today
               and len(r["ticker"])==6 and r["ticker"].isascii()
               and r["ticker"].isdigit()]
        if not valid: return "STALE_CANDIDATES", []
        return "TODAY_SIGNAL_FILE_ONLY", sorted(set(r["ticker"] for r in valid))
    except (ValueError, OSError, UnicodeError):
        return "INVALID_CANDIDATE_FILE", []
def run(switch, candidates, now):
    if not locked():
        raise RuntimeError("HARD_LIVE_ORDER_GUARD_FAILED")
    enabled, switch_reason = control(switch)
    result={
        "kst_time":now.isoformat(timespec="seconds"),
        "switch":switch_reason, "auto_analysis_enabled":enabled,
        "session":session(now), "orders_sent":0,
        "live_enabled":False, "kis_allow_order":False,
        "broker_calls":0, "market_data_live_verified":False,
        "execution_authorized":False, "mode":"DATA_FILE_ANALYSIS_ONLY"
    }
    if not enabled:
        result.update(status="SKIPPED_OFF", candidates=[], reason=switch_reason)
    else:
        status,codes=read_candidates(candidates,now.strftime("%Y-%m-%d"))
        result.update(status="REVIEW_ONLY", candidates=codes, reason=status)
    # Never interprets dated CSV as actual realtime/evening tradability.
    return result
def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--switch",type=Path,default=Path("data/live/auto_control.json"))
    ap.add_argument("--candidates",type=Path,default=Path("data/v4b/candidates.csv"))
    ap.add_argument("--report",type=Path,default=Path("v5_2_analysis_report.json"))
    args=ap.parse_args()
    result=run(args.switch,args.candidates,datetime.now(KST))
    args.report.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("V5.2 ANALYSIS status="+result["status"]+"; switch="+result["switch"]+
          "; session="+result["session"]+"; reason="+result["reason"]+
          "; candidates="+str(len(result["candidates"]))+
          "; market_data_live_verified=false; orders_sent=0; LIVE_ENABLED=false; KIS_ALLOW_ORDER=false")
if __name__=="__main__":main()

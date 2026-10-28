"""V4B same-run candidate CSV -> V5.19 review; NEVER submits orders."""
import argparse,csv,datetime as dt,json,re
from pathlib import Path
from zoneinfo import ZoneInfo
KST=ZoneInfo('Asia/Seoul')
def build(csv_path,day,now=None):
    now=now or dt.datetime.now(KST)
    if now.tzinfo is None or now.astimezone(KST).date().isoformat()!=day:
        raise ValueError("WRONG_TRADING_DAY")
    with open(csv_path,encoding="utf-8-sig",newline="") as f:
        reader=csv.DictReader(f)
        required={"date","ticker","rs","foreign_5d","institution_5d","volume_ratio","channel_position","signal_date","channel_up"}
        if not required.issubset(reader.fieldnames or []):raise ValueError("COLUMNS_MISSING")
        rows=list(reader)
    if not rows:raise ValueError("NO_CANDIDATES")
    seen=set(); selected=[]
    for r in rows:
        t=r["ticker"]
        if not re.fullmatch(r"[0-9]{6}",t) or t in seen:raise ValueError("INVALID_TICKER")
        seen.add(t)
        if r["date"]!=day or r["signal_date"]>=day:raise ValueError("INVALID_SIGNAL_DAY")
        rs=int(r["rs"]); volume=float(r["volume_ratio"]); channel=float(r["channel_position"])
        foreign=float(r["foreign_5d"]); institution=float(r["institution_5d"])
        if not 1<=rs<=99 or not all(map(__import__('math').isfinite,[volume,channel,foreign,institution])):
            raise ValueError("INVALID_METRICS")
        if rs>=90 and volume>=1 and 0<=channel<=.75 and r["channel_up"].lower()=="true" and (foreign>0 or institution>0):
            selected.append({"ticker":t,"rs":rs,"signal_date":r["signal_date"],
                             "foreign_5d":foreign,"institution_5d":institution,
                             "reference_price":None,"order_quantity":None,"order_authorized":False})
    return {"status":"SAME_RUN_CANDIDATES_NOT_ORDERABLE","gate":"DENY","orders_sent":0,
            "date":day,"candidates":sorted(selected,key=lambda x:-x["rs"])}
def main():
    a=argparse.ArgumentParser()
    a.add_argument("--csv",required=True);a.add_argument("--date",required=True)
    a.add_argument("--output",default="v519_same_run_result.json")
    x=a.parse_args()
    try:
        result=build(x.csv,x.date)
        exitcode=0
    except Exception as e:
        result={"status":"BLOCKED","gate":"DENY","orders_sent":0,
                "reason":str(e) if isinstance(e,ValueError) else "INVALID_INPUT","candidates":[]}
        exitcode=2
    Path(x.output).write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf8")
    print(result["status"]+"; orders_sent=0")
    raise SystemExit(exitcode)
if __name__=="__main__":main()

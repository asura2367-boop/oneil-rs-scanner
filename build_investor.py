"""KIS read-only investor flow collector. Never writes secrets/tokens to Git."""
import os, time
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd
import requests

BASE = "https://openapi.koreainvestment.com:9443"
DATA = Path(__file__).resolve().parent / "data"
DEST = DATA / "kis_investor_flow.csv"
FIELDS = ["ticker","date","foreign_net_qty","institution_net_qty","foreign_net_amount_raw","institution_net_amount_raw"]

def main():
    key, secret = os.getenv("KIS_APP_KEY", "").strip(), os.getenv("KIS_APP_SECRET", "").strip()
    if not key or not secret:
        raise RuntimeError("KIS_APP_KEY / KIS_APP_SECRET GitHub Actions Secrets 확인")
    source = DATA / "kis_daily_universe.csv"
    if not source.exists():
        raise RuntimeError("data/kis_daily_universe.csv 없음")
    prices = pd.read_csv(source, dtype={"ticker":str}, usecols=["ticker","date","close"])
    prices["ticker"] = prices["ticker"].str.zfill(6)
    prices["date"] = pd.to_datetime(prices["date"],errors="coerce")
    prices["close"] = pd.to_numeric(prices["close"],errors="coerce")
    # Prefer stocks with sufficient 1-year history and high weighted returns.
    candidates=[]
    for ticker,h in prices.dropna().sort_values("date").groupby("ticker"):
        p=h["close"].to_numpy()
        if len(p) < 253 or min(p[-253:]) <= 0: continue
        r=[(p[-1]/p[-1-n]-1)*100 for n in (63,126,189,252)]
        candidates.append((ticker,sum(x*w for x,w in zip(r,[.4,.2,.2,.2]))))
    candidates.sort(key=lambda x:x[1],reverse=True)
    if not candidates: raise RuntimeError("253개 이상 일봉을 가진 종목 없음")
    DATA.mkdir(exist_ok=True)
    old=pd.read_csv(DEST,dtype={"ticker":str,"date":str}) if DEST.exists() else pd.DataFrame(columns=FIELDS)
    old=old.reindex(columns=FIELDS)
    if not old.empty: old["ticker"]=old["ticker"].str.zfill(6)
    today=datetime.now(ZoneInfo("Asia/Seoul")).date()
    # Refresh existing tracked symbols first only if older than last available trading date.
    latest_price=prices.groupby("ticker")["date"].max().to_dict()
    last_flow=old.groupby("ticker")["date"].max().to_dict() if not old.empty else {}
    pending=[c for c,_ in candidates if c not in last_flow or pd.Timestamp(last_flow[c]) < latest_price[c]]
    limit=max(1,min(int(os.getenv("KIS_FLOW_BATCH_SIZE","10")),30))
    batch=pending[:limit]
    if not batch:
        print("수급 최신 상태. 요청 없음."); return
    with requests.Session() as session:
        auth=session.post(BASE+"/oauth2/tokenP",json={"grant_type":"client_credentials","appkey":key,"appsecret":secret},timeout=25)
        if not auth.ok: raise RuntimeError(f"토큰 발급 HTTP {auth.status_code}; 발급 제한 확인")
        token=auth.json().get("access_token")
        if not token: raise RuntimeError("토큰 응답에 access_token 없음")
        frames=[old]; success=0; failed=0
        for code in batch:
            try:
                resp=session.get(BASE+"/uapi/domestic-stock/v1/quotations/inquire-investor",
                    headers={"authorization":f"Bearer {token}","appkey":key,"appsecret":secret,
                             "tr_id":"FHKST01010900","content-type":"application/json"},
                    params={"FID_COND_MRKT_DIV_CODE":"J","FID_INPUT_ISCD":code},timeout=25)
                if not resp.ok: raise RuntimeError(f"HTTP {resp.status_code}")
                payload=resp.json()
                if str(payload.get("rt_cd"))!="0":
                    raise RuntimeError(f"API {payload.get('msg_cd','unknown')}")
                rows=payload.get("output") or []
                if isinstance(rows,dict): rows=[rows]
                df=pd.DataFrame(rows)
                required=["stck_bsop_date","frgn_ntby_qty","orgn_ntby_qty"]
                if not set(required).issubset(df.columns): raise RuntimeError("순매수 필드 누락")
                result=pd.DataFrame({"ticker":code,
                    "date":pd.to_datetime(df["stck_bsop_date"],format="%Y%m%d",errors="coerce").dt.strftime("%Y-%m-%d"),
                    "foreign_net_qty":pd.to_numeric(df["frgn_ntby_qty"],errors="coerce"),
                    "institution_net_qty":pd.to_numeric(df["orgn_ntby_qty"],errors="coerce"),
                    "foreign_net_amount_raw":pd.to_numeric(df.get("frgn_ntby_tr_pbmn",pd.Series(index=df.index,dtype=float)),errors="coerce"),
                    "institution_net_amount_raw":pd.to_numeric(df.get("orgn_ntby_tr_pbmn",pd.Series(index=df.index,dtype=float)),errors="coerce")})
                result=result.dropna(subset=["date","foreign_net_qty","institution_net_qty"])
                if result.empty: raise RuntimeError("유효한 날짜별 수급 없음")
                frames.append(result); success+=1
                print(f"OK {code}: {len(result)} rows")
            except Exception as e:
                failed+=1; print(f"FAIL {code}: {type(e).__name__}: {str(e)[:100]}")
            time.sleep(float(os.getenv("KIS_FLOW_PAUSE","0.8")))
    if success:
        merged=pd.concat(frames,ignore_index=True).dropna(subset=["ticker","date"])
        merged=merged.drop_duplicates(["ticker","date"],keep="last").sort_values(["ticker","date"])
        temp=DEST.with_suffix(".tmp"); merged.to_csv(temp,index=False,encoding="utf-8-sig");temp.replace(DEST)
        print(f"저장 완료: {merged.ticker.nunique()}종목, {len(merged)}행, 이번 성공 {success}, 실패 {failed}")
    else:
        raise RuntimeError("이번 배치 수급 조회 전부 실패. 기존 파일 유지")

if __name__=="__main__": main()

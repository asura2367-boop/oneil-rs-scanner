from datetime import date, timedelta
from pathlib import Path
import numpy as np
import pandas as pd
from pykrx import stock
import FinanceDataReader as fdr

OUT=Path(__file__).parent/'data'/'rs_latest.csv'

def universe(day):
    rows=[]
    for market in ('KOSPI','KOSDAQ'):
        try:
            codes=stock.get_market_ticker_list(day,market=market)
            rows.extend((str(t).zfill(6),stock.get_market_ticker_name(t),market) for t in codes)
        except Exception as exc: print('pykrx universe:',market,exc)
    if len(rows)<100:
        rows=[]
        for market in ('KOSPI','KOSDAQ'):
            try:
                d=fdr.StockListing(market)
                code=next(c for c in ('Code','Symbol','종목코드') if c in d)
                name=next(c for c in ('Name','종목명') if c in d)
                rows.extend((str(t).zfill(6),str(n),market) for t,n in zip(d[code],d[name]))
            except Exception as exc: print('FDR universe:',market,exc)
    u=pd.DataFrame(rows,columns=['ticker','종목명','시장']).drop_duplicates('ticker').set_index('ticker')
    if len(u)<100: raise RuntimeError('종목 목록 수집 실패; 기존 저장 파일은 유지합니다')
    return u

def collect(day):
    end=day.strftime('%Y%m%d')
    u=universe(end)
    series=[]
    for days in (91,182,273,365):
        start=(day-timedelta(days=days)).strftime('%Y%m%d')
        d=stock.get_market_price_change_by_ticker(start,end,market='ALL')
        if d is None or d.empty or '등락률' not in d: raise RuntimeError(f'전체 시장 수익률 조회 실패: {days}일')
        s=pd.to_numeric(d['등락률'],errors='coerce')/100
        s.index=s.index.astype(str).str.zfill(6)
        series.append(s)
    r=pd.concat(series,axis=1)
    r.columns=['r3','r6','r9','r12']
    r=r.join(u,how='inner').dropna()
    if len(r)<max(100,int(len(u)*0.6)): raise RuntimeError('시장 수익률 데이터 불완전')
    r['가중수익률']=r[['r3','r6','r9','r12']].dot([.4,.2,.2,.2])
    r['RS']=np.ceil(r['가중수익률'].rank(pct=True)*99).clip(1,99).astype(int)
    start=(day-timedelta(days=12)).strftime('%Y%m%d')
    for who,col in [('외국인','외국인_억원'),('기관합계','기관_억원')]:
        r[col]=np.nan
        try:
            d=stock.get_market_net_purchases_of_equities_by_ticker(start,end,'ALL',who)
            if d is not None and not d.empty and '순매수거래대금' in d:
                s=pd.to_numeric(d['순매수거래대금'],errors='coerce')/1e8
                s.index=s.index.astype(str).str.zfill(6)
                r[col]=s.reindex(r.index)
        except Exception as exc: print('flows unavailable:',who,exc)
    r['기준일']=day.isoformat()
    return r.sort_values(['RS','가중수익률'],ascending=False).reset_index().rename(columns={'index':'ticker'})

if __name__=='__main__':
    errors=[]
    for back in range(0,8):
        day=date.today()-timedelta(days=back)
        if day.weekday()>=5: continue
        try:
            df=collect(day)
            OUT.parent.mkdir(parents=True,exist_ok=True)
            tmp=OUT.with_suffix('.tmp')
            df.to_csv(tmp,index=False,encoding='utf-8-sig')
            tmp.replace(OUT)
            print('saved',OUT,len(df),day)
            break
        except Exception as exc:
            print(day,'failed:',exc)
            errors.append(str(exc))
    else:
        raise RuntimeError('최근 거래일 자료 수집 실패: '+'; '.join(errors))

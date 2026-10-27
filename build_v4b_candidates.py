"""Build pre-session V4-B candidates from saved historical daily data. No orders."""
import argparse
from pathlib import Path
import json
import pandas as pd
import numpy as np

COLUMNS=['date','ticker','rs','foreign_5d','institution_5d','volume_ratio','channel_position','channel_up','signal_date','name']

def generate(prices_path, flows_path, target, output, diagnostics=None):
    target=pd.Timestamp(target).normalize()
    if not prices_path.exists() or not flows_path.exists():
        raise FileNotFoundError('일봉 또는 수급 CSV 없음: 후보를 생성하지 않습니다')
    p=pd.read_csv(prices_path,dtype={'ticker':str},encoding='utf-8-sig')
    f=pd.read_csv(flows_path,dtype={'ticker':str},encoding='utf-8-sig')
    required_p={'ticker','name','date','close','volume'}
    required_f={'ticker','date','foreign_net_qty','institution_net_qty'}
    if not required_p.issubset(p.columns) or not required_f.issubset(f.columns):
        raise ValueError('CSV 필수 열 누락')
    for df in (p,f):
        df['ticker']=df['ticker'].astype(str).str.zfill(6)
        df['date']=pd.to_datetime(df['date'],errors='coerce').dt.normalize()
        df.dropna(subset=['date','ticker'],inplace=True)
        df.drop(df[df['date']>=target].index,inplace=True) # strict no lookahead
        df.sort_values(['ticker','date'],inplace=True)
        df.drop_duplicates(['ticker','date'],keep='last',inplace=True)
    for c in ['close','volume']:p[c]=pd.to_numeric(p[c],errors='coerce')
    for c in ['foreign_net_qty','institution_net_qty']:f[c]=pd.to_numeric(f[c],errors='coerce')
    sessions=pd.DatetimeIndex(sorted(p.loc[(p.volume>0)&(p.close>0),'date'].unique()))
    if sessions.empty: raise ValueError('유효한 전일 거래 데이터 없음')
    signal_day=sessions[-1]
    if (target-signal_day).days>5: raise ValueError('일봉 데이터가 오래됨: 후보 생성 중단')
    flows={c:g.set_index('date') for c,g in f.groupby('ticker')}
    counts={'universe_symbols':int(p.ticker.nunique()),'latest_signal_day':str(signal_day.date()),
            'target_date':str(target.date()),'valid_history':0,'missing_history':0,
            'stale_price':0,'invalid_price_or_volume':0,
            'rs_85':0,'volume_1x':0,'up_channel':0,'channel_0_75':0,
            'flow_five_valid':0,'foreign_positive':0,'institution_positive':0,'selected':0,
            'flow_missing_or_short':0,'flow_stale':0,'flow_invalid':0,
             'foreign_only':0,'institution_only':0,'both_positive':0}
    scores=[]
    for ticker,g in p.groupby('ticker'):
        h=g.set_index('date')
        if len(h)<253:
            counts['missing_history']+=1;continue
        if h.index[-1]!=signal_day:
            counts['stale_price']+=1;continue
        h=h.tail(253)
        if h[['close','volume']].isna().any().any() or (h.close<=0).any() or (h.volume.tail(21)<=0).any():
            counts['invalid_price_or_volume']+=1;continue
        counts['valid_history']+=1
        close=h.close.to_numpy(float);vol=h.volume.to_numpy(float)
        returns=np.array([close[-1]/close[-1-n]-1 for n in (63,126,189,252)])
        score=float(np.dot(returns,[.4,.2,.2,.2]))
        ratio=float(vol[-1]/np.mean(vol[-21:-1]))
        y=np.log(close[-60:]);x=np.arange(60)
        slope,intercept=np.polyfit(x,y,1)
        mid=intercept+slope*x
        width=max(2*np.std(y-mid),1e-8)
        channel=float((y[-1]-mid[-1]+width)/(2*width))
        scores.append(dict(ticker=ticker,name=str(h.name.iloc[-1]),score=score,volume_ratio=ratio,channel_position=channel,channel_up=slope>0))
    if not scores: raise ValueError('RS 계산 가능한 전일 종목 없음')
    df=pd.DataFrame(scores)
    df['rs']=np.ceil(df.score.rank(pct=True)*99).clip(1,99).astype(int)
    for key,condition in [('rs_85',df.rs>=85),('volume_1x',df.volume_ratio>=1),
                          ('up_channel',df.channel_up),('channel_0_75',df.channel_position.between(0,.75))]:
        df=df[condition.loc[df.index]]
        counts[key]=int(len(df))
    result=[]
    for r in df.itertuples():
        fh=flows.get(r.ticker)
        if fh is None:
            counts['flow_missing_or_short']+=1;continue
        fh=fh.loc[:signal_day]
        if len(fh)<5:
            counts['flow_missing_or_short']+=1;continue
        lag=sessions.searchsorted(signal_day,side='right')-sessions.searchsorted(fh.index[-1],side='right')
        if lag not in (0,1):
            counts['flow_stale']+=1;continue
        five=fh.tail(5)
        if five[['foreign_net_qty','institution_net_qty']].isna().any().any():
            counts['flow_invalid']+=1;continue
        counts['flow_five_valid']+=1
        foreign=float(five.foreign_net_qty.sum());institution=float(five.institution_net_qty.sum())
        foreign_ok = foreign > 0
        institution_ok = institution > 0
        if foreign_ok: counts['foreign_positive']+=1
        if institution_ok: counts['institution_positive']+=1
        if foreign_ok and institution_ok: counts['both_positive']+=1
        elif foreign_ok: counts['foreign_only']+=1
        elif institution_ok: counts['institution_only']+=1
        if not (foreign_ok or institution_ok): continue
        result.append(dict(date=target.strftime('%Y-%m-%d'),ticker=r.ticker,rs=r.rs,foreign_5d=foreign,institution_5d=institution,volume_ratio=round(r.volume_ratio,6),channel_position=round(r.channel_position,6),channel_up=bool(r.channel_up),signal_date=signal_day.strftime('%Y-%m-%d'),name=r.name))
    output.parent.mkdir(parents=True,exist_ok=True)
    pd.DataFrame(result,columns=COLUMNS).sort_values(['rs','ticker'],ascending=[False,True]).to_csv(output,index=False,encoding='utf-8-sig')
    counts['selected']=len(result)
    if diagnostics is not None:
        diagnostics.parent.mkdir(parents=True,exist_ok=True)
        temp=diagnostics.with_suffix('.tmp')
        temp.write_text(json.dumps(counts,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        temp.replace(diagnostics)
    print('V4B_DIAGNOSTICS '+json.dumps(counts,ensure_ascii=False,sort_keys=True),flush=True)
    print(f'전일 신호일 {signal_day.date()} / 재현 대상일 {target.date()} / 후보 {len(result)}개 / {output}')
    return result

if __name__=='__main__':
    ap=argparse.ArgumentParser()
    ap.add_argument('--date',required=True,help='재현 대상 거래일 YYYY-MM-DD')
    ap.add_argument('--prices',type=Path,default=Path('data/kis_daily_universe.csv'))
    ap.add_argument('--flows',type=Path,default=Path('data/kis_investor_flow.csv'))
    ap.add_argument('--output',type=Path,default=Path('data/v4b/candidates.csv'))
    ap.add_argument('--diagnostics',type=Path,default=Path('data/v4b/diagnostics.json'))
    args=ap.parse_args()
    generate(args.prices,args.flows,args.date,args.output,args.diagnostics)

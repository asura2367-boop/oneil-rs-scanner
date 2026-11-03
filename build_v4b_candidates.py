"""Build opening or intraday RS candidates. Quote collection never sends orders."""
import argparse
from pathlib import Path
import json
import datetime as dt
from zoneinfo import ZoneInfo
import pandas as pd
import numpy as np

COLUMNS=['date','ticker','rs','foreign_5d','institution_5d','volume_ratio','channel_position','channel_up','signal_date','name',
         'signal_basis','observed_at_kst','flow_signal_date','reference_close','technical_policy','technical_entry','technical_details']

def load_inputs(prices_path, flows_path, target):
    target=pd.Timestamp(target).normalize()
    if not prices_path.exists():
        raise FileNotFoundError('일봉 CSV 없음: 후보를 생성하지 않습니다')
    p=pd.read_csv(prices_path,dtype={'ticker':str},encoding='utf-8-sig')
    # Keep the legacy argument/return shape; flow data is not an input anymore.
    f=pd.DataFrame(columns=['ticker','date','foreign_net_qty','institution_net_qty'])
    required_p={'ticker','name','date','close','volume'}
    if not required_p.issubset(p.columns):
        raise ValueError('CSV 필수 열 누락')
    for df in (p,):
        df['ticker']=df['ticker'].astype(str).str.zfill(6)
        df['date']=pd.to_datetime(df['date'],errors='coerce').dt.normalize()
        df.dropna(subset=['date','ticker'],inplace=True)
        df.drop(df[df['date']>=target].index,inplace=True) # strict no lookahead
        df.sort_values(['ticker','date'],inplace=True)
        df.drop_duplicates(['ticker','date'],keep='last',inplace=True)
    for c in ['close','volume']:p[c]=pd.to_numeric(p[c],errors='coerce')
    sessions=pd.DatetimeIndex(sorted(p.loc[(p.volume>0)&(p.close>0),'date'].unique()))
    if sessions.empty: raise ValueError('유효한 전일 거래 데이터 없음')
    signal_day=sessions[-1]
    if (target-signal_day).days>5: raise ValueError('일봉 데이터가 오래됨: 후보 생성 중단')
    return p,f,signal_day,sessions


def history_quality(h,signal_day,sessions):
    if len(h)<253:return 'missing_history'
    if h.index[-1]!=signal_day:return 'stale_price'
    h=h.tail(253)
    if not h.index.equals(sessions[-253:]):return 'misaligned_history'
    if (not np.isfinite(h[['close']].to_numpy(float)).all() or
            (h.close<=0).any()):
        return 'invalid_price_or_volume'
    return None


def intraday_universe(prices_path, flows_path, target, previous):
    p,_,signal_day,sessions=load_inputs(prices_path,flows_path,target)
    if signal_day!=pd.Timestamp(previous):raise ValueError('HISTORY_SESSION_MISMATCH')
    codes=[]
    for ticker,g in p.groupby('ticker'):
        h=g.set_index('date')
        if history_quality(h,signal_day,sessions) is None:codes.append(ticker)
    if not codes:raise ValueError('INTRADAY_UNIVERSE_EMPTY')
    return codes


def generate(prices_path, flows_path, target, output, diagnostics=None, *, intraday=None, now=None):
    target=pd.Timestamp(target).normalize()
    p,f,signal_day,sessions=load_inputs(prices_path,flows_path,target)
    now=now or dt.datetime.now(ZoneInfo('Asia/Seoul'))
    if intraday is not None:
        from quote_collector_v4a import validate_candidate_quote, candidate_session
        if not candidate_session(now) or now.astimezone(ZoneInfo('Asia/Seoul')).date()!=target.date():
            raise ValueError('INTRADAY_SESSION_CLOSED')
        expected=intraday_universe(prices_path,flows_path,target,signal_day)
        if set(intraday)!=set(expected):raise ValueError('INTRADAY_COVERAGE_INCOMPLETE')
        for code,row in intraday.items():
            if row is not None:validate_candidate_quote(row,code,now)
        # All returned snapshots must fit one bounded collection window.
        observed=[dt.datetime.fromisoformat(r['received_at_kst']) for r in intraday.values() if r is not None]
        if not observed:raise ValueError('INTRADAY_QUOTES_EMPTY')
        if (max(observed)-min(observed)).total_seconds()>600:raise ValueError('INTRADAY_COLLECTION_EXPIRED')
    counts={'universe_symbols':int(p.ticker.nunique()),'latest_signal_day':str(signal_day.date()),
            'latest_history_day':str(signal_day.date()),
            'source_basis':'INTRADAY_KIS_SNAPSHOT' if intraday is not None else 'PREVIOUS_CONFIRMED_SESSION',
            'target_date':str(target.date()),'valid_history':0,'missing_history':0,
            'stale_price':0,'invalid_price_or_volume':0,'misaligned_history':0,
            'rs_85':0,'volume_1x':0,'up_channel':0,'channel_0_75':0,
            'selection_policy':'RS_ONLY_V1','flow_filter_enabled':False,'volume_filter_enabled':False,'channel_filter_enabled':False,'technical_filter_enabled':False,'min_rs':90,'selected':0,
            'technical_data_missing':0,'technical_rejected':0,'technical_passed':0,'legacy_selected':0,
            'technical_bb_not_breaking_out':0,'technical_ichimoku_not_bullish':0}
    scores=[]
    if intraday is not None:
        counts.update(latest_signal_day=str(target.date()),quote_universe=len(intraday),
            quotes_observed=sum(r is not None for r in intraday.values()),
            no_current_trade=sum(r is None for r in intraday.values()),
            collection_started_at_kst=min(observed).isoformat(),collection_finished_at_kst=max(observed).isoformat())
    for ticker,g in p.groupby('ticker'):
        h=g.set_index('date')
        reason=history_quality(h,signal_day,sessions)
        if reason:
            counts[reason]+=1;continue
        h=h.tail(253)
        if intraday is not None:
            live=intraday.get(ticker)
            if live is None:continue
            h=pd.concat([h,pd.DataFrame([{'name':str(h.name.iloc[-1]),'close':live['price'],
                'volume':live['cumulative_volume'],'high':live.get('high'),'low':live.get('low')}],index=[target])]).tail(253)
        counts['valid_history']+=1
        close=h.close.to_numpy(float);vol=h.volume.to_numpy(float)
        returns=np.array([close[-1]/close[-1-n]-1 for n in (63,126,189,252)])
        score=float(np.dot(returns,[.4,.2,.2,.2]))
        scores.append(dict(ticker=ticker,name=str(h.name.iloc[-1]),score=score,reference_close=int(close[-1])))
    if not scores: raise ValueError('RS 계산 가능한 전일 종목 없음')
    registry=prices_path.parent/'universe_test.csv'
    registered=set(p.ticker.unique())
    if registry.exists():
        listed=pd.read_csv(registry,dtype=str,encoding='utf-8-sig')
        if 'ticker' not in listed:raise ValueError('RS_REGISTRY_INVALID')
        registered.update(str(t).zfill(6) for t in listed.ticker if str(t).isdigit() and len(str(t))<=6)
    counts.update(registered_universe_symbols=len(registered),
        uncollected_symbols=len(registered-set(p.ticker.unique())),
        rs_cohort=len(scores),rs_scope='AVAILABLE_SAME_SESSION_253_BARS',
        rs_history_session=str(signal_day.date()),
        rs_coverage_pct=round(100*len(scores)/len(registered),2) if registered else 0,
        rs_formula='0.4*R63+0.2*R126+0.2*R189+0.2*R252',
        volume_basis='ACTUAL_CUMULATIVE_VS_CONFIRMED_20D_DAILY')
    df=pd.DataFrame(scores)
    df['rs']=np.ceil(df.score.rank(pct=True)*99).clip(1,99).astype(int)
    counts['rs_90']=int((df.rs>=90).sum())
    counts['rs_85']=int((df.rs>=85).sum())
    df=df[df.rs>=90]
    result=[]
    for r in df.itertuples():
        result.append(dict(technical_policy='RS_ONLY_V1',technical_entry=None,technical_details='',
            date=target.strftime('%Y-%m-%d'),ticker=r.ticker,rs=r.rs,foreign_5d=None,institution_5d=None,
            volume_ratio=None,channel_position=None,channel_up=None,
            signal_date=(target if intraday is not None else signal_day).strftime('%Y-%m-%d'),name=r.name,
            signal_basis=counts['source_basis'],observed_at_kst=intraday[r.ticker]['received_at_kst'] if intraday is not None else '',
            flow_signal_date='',reference_close=r.reference_close))
    output.parent.mkdir(parents=True,exist_ok=True)
    temp=output.with_suffix('.tmp')
    pd.DataFrame(result,columns=COLUMNS).sort_values(['rs','ticker'],ascending=[False,True]).to_csv(temp,index=False,encoding='utf-8-sig')
    temp.replace(output)
    counts['selected']=len(result)
    if diagnostics is not None:
        diagnostics.parent.mkdir(parents=True,exist_ok=True)
        temp=diagnostics.with_suffix('.tmp')
        temp.write_text(json.dumps(counts,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        temp.replace(diagnostics)
    print('V4B_DIAGNOSTICS '+json.dumps(counts,ensure_ascii=False,sort_keys=True),flush=True)
    print(f'신호 기준 {counts["source_basis"]} / 대상일 {target.date()} / 후보 {len(result)}개 / {output}')
    return result

if __name__=='__main__':
    ap=argparse.ArgumentParser()
    ap.add_argument('--date',required=True,help='재현 대상 거래일 YYYY-MM-DD')
    ap.add_argument('--prices',type=Path,default=Path('data/kis_daily_universe.csv'))
    ap.add_argument('--flows',type=Path,default=Path('data/kis_investor_flow.csv'))
    ap.add_argument('--output',type=Path,default=Path('data/v4b/candidates.csv'))
    ap.add_argument('--diagnostics',type=Path,default=Path('data/v4b/diagnostics.json'))
    ap.add_argument('--intraday',action='store_true',help='KIS 당일 스냅샷으로 RS 재계산; 주문 없음')
    ap.add_argument('--previous-session',help='거래소 달력으로 확인한 직전 거래일; 장중 수집에 필수')
    args=ap.parse_args()
    stage='HISTORY_INPUT'
    try:
        quotes=None
        if args.intraday:
            if not args.previous_session:raise ValueError('PREVIOUS_SESSION_REQUIRED')
            from quote_collector_v4a import collect_candidate_quotes
            codes=intraday_universe(args.prices,args.flows,args.date,args.previous_session)
            stage='KIS_QUOTE_COLLECTION'
            quotes=collect_candidate_quotes(codes,args.date,workers=8)
        stage='CANDIDATE_CALCULATION'
        generate(args.prices,args.flows,args.date,args.output,args.diagnostics,intraday=quotes)
    except Exception as exc:
        # No raw response, URL, credential or token-bearing traceback in Actions.
        known={'INTRADAY_SESSION_CLOSED','INTRADAY_COLLECTION_EXPIRED','INTRADAY_COVERAGE_INCOMPLETE',
            'INTRADAY_UNIVERSE_EMPTY','INTRADAY_QUOTES_EMPTY','HISTORY_SESSION_MISMATCH','PREVIOUS_SESSION_REQUIRED',
            'CANDIDATE_QUOTE_HTTP_FAILURE','CANDIDATE_QUOTE_TRANSPORT_FAILURE','CANDIDATE_QUOTE_REJECTED',
            'CANDIDATE_QUOTE_RESPONSE_EXPIRED','CANDIDATE_QUOTE_IDENTITY_INVALID','CANDIDATE_QUOTE_DAY_CONFLICT',
            'CANDIDATE_QUOTE_STALE','CANDIDATE_QUOTE_VALUE_INVALID','CACHE_KEY_INVALID',
            'CACHE_DECRYPT_OR_FORMAT_FAILURE','CACHE_PERSIST_FAILURE','AUTH_FAILURE','AUTH_TRANSPORT',
            'AUTH_TOKEN_MISSING','AUTH_TOO_CLOSE_TO_EXPIRY','AUTH_EXPIRY_INVALID','AUTH_EXPIRY_MISSING'}
        reason=str(exc) if str(exc) in known else 'INPUT_OR_API_VALIDATION_FAILED'
        print(json.dumps({'status':'CANDIDATE_REFRESH_FAILED','stage':stage,'reason':reason,
            'prior_published_candidates_preserved':True,'orders_sent':0}))
        raise SystemExit(2)

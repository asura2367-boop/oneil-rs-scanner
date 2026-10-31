"""KIS read-only: incremental KOSPI/KOSDAQ daily OHLCV collection."""
import os
import json
import time
from datetime import datetime, timedelta, time as clock_time
from zoneinfo import ZoneInfo
from pathlib import Path

import pandas as pd
import requests

BASE = 'https://openapi.koreainvestment.com:9443'
SYMBOLS = {
    '005930':'삼성전자','000660':'SK하이닉스','005380':'현대차',
    '035420':'NAVER','035720':'카카오','005490':'POSCO홀딩스',
    '051910':'LG화학','006400':'삼성SDI','068270':'셀트리온',
    '105560':'KB금융','055550':'신한지주','086790':'하나금융지주',
    '012330':'현대모비스','000270':'기아','028260':'삼성물산',
    '066570':'LG전자','003550':'LG','034730':'SK',
    '009150':'삼성전기','017670':'SK텔레콤','030200':'KT',
    '015760':'한국전력','010130':'고려아연','032830':'삼성생명',
    '000810':'삼성화재','010950':'S-Oil','003670':'포스코퓨처엠',
    '247540':'에코프로비엠','086520':'에코프로','196170':'알테오젠',
}
OUT = Path(__file__).resolve().parent / 'data'


def get_token(session, key, secret):
    response = session.post(f'{BASE}/oauth2/tokenP', json={
        'grant_type': 'client_credentials', 'appkey': key, 'appsecret': secret
    }, timeout=20)
    if not response.ok:
        raise RuntimeError(f'토큰 발급 HTTP {response.status_code}; KIS 발급 제한 또는 인증 상태 확인')
    payload = response.json()
    if not payload.get('access_token'):
        raise RuntimeError('토큰 발급 실패: ' + str(payload.get('error_description', '응답 확인 필요')))
    return payload['access_token']


def fetch_year(session, key, secret, token, symbol, start, end, *, min_rows=200):
    rows = []
    cursor = end
    for _ in range(7):
        params = {
            'FID_COND_MRKT_DIV_CODE': 'J',
            'FID_INPUT_ISCD': symbol,
            'FID_INPUT_DATE_1': start.strftime('%Y%m%d'),
            'FID_INPUT_DATE_2': cursor.strftime('%Y%m%d'),
            'FID_PERIOD_DIV_CODE': 'D',
            'FID_ORG_ADJ_PRC': '0',
        }
        headers = {
            'authorization': f'Bearer {token}', 'appkey': key, 'appsecret': secret,
            'tr_id': 'FHKST03010100', 'content-type': 'application/json',
        }
        response = session.get(f'{BASE}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice',
                               params=params, headers=headers, timeout=25, allow_redirects=False)
        if response.status_code in (429, 500, 502, 503, 504):
            raise RuntimeError(f'{symbol} 일시적 API 오류 HTTP {response.status_code}')
        if response.status_code != 200:
            raise RuntimeError(f'PRICE_HTTP_{response.status_code}')
        payload = response.json()
        if str(payload.get('rt_cd')) != '0':
            raise RuntimeError(f'{symbol} API 오류: {payload.get("msg_cd")} {payload.get("msg1")}')
        batch = payload.get('output2') or []
        if not batch:
            break
        rows.extend(batch)
        oldest = min(datetime.strptime(x['stck_bsop_date'], '%Y%m%d').date() for x in batch)
        if oldest <= start:
            break
        cursor = oldest - timedelta(days=1)
        time.sleep(0.35)
    if not rows:
        raise RuntimeError(f'{symbol} 일봉 응답 없음')
    df = pd.DataFrame(rows)
    df = df.rename(columns={'stck_bsop_date':'date','stck_clpr':'close','acml_vol':'volume','stck_hgpr':'high','stck_lwpr':'low'})
    df = df[['date','close','volume','high','low']].copy()
    df['date'] = pd.to_datetime(df['date'], format='%Y%m%d', errors='coerce')
    for col in ('close','volume','high','low'):
        df[col] = pd.to_numeric(df[col], errors='coerce')
    if ((df['low']<=0)|(df['high']<df['close'])|(df['low']>df['close'])).any():raise ValueError('OHLC_RANGE_INVALID')
    df = df.dropna().drop_duplicates('date').sort_values('date')
    df = df[(df['date'].dt.date >= start) & (df['date'].dt.date <= end)]
    if len(df) < min_rows:
        raise RuntimeError('PRICE_HISTORY_TOO_SHORT')
    return df



def load_universe(path):
    if not path.exists():
        raise RuntimeError('data/universe_test.csv 파일이 없습니다.')
    u = pd.read_csv(path, dtype=str, encoding='utf-8-sig').fillna('')
    u.columns = [str(c).strip() for c in u.columns]
    code_col = next((c for c in ['ticker','Ticker','Code','code','종목코드','Symbol','symbol'] if c in u), None)
    name_col = next((c for c in ['name','Name','종목명','회사명'] if c in u), None)
    market_col = next((c for c in ['market','Market','시장','시장구분'] if c in u), None)
    if code_col is None or name_col is None:
        raise RuntimeError(f'종목 목록 열 이름 확인 필요: {list(u.columns)}')
    u = u.rename(columns={code_col:'ticker',name_col:'name'})
    u['ticker'] = u['ticker'].astype(str).str.strip().str.replace(r'\.0$', '', regex=True).str.zfill(6)
    u = u[u['ticker'].str.fullmatch(r'\d{6}') & u['name'].astype(str).str.strip().ne('')]
    if market_col:
        allowed = u[market_col].astype(str).str.upper().str.strip().isin(['KOSPI','KOSDAQ','코스피','코스닥'])
        if allowed.any():
            u = u[allowed]
    u = u.drop_duplicates('ticker')
    if len(u) < 100:
        raise RuntimeError(f'종목 목록이 너무 적습니다: {len(u)}개')
    return list(u[['ticker','name']].itertuples(index=False, name=None))


def read_existing(path):
    cols=['ticker','name','date','close','volume']
    if not path.exists():
        return pd.DataFrame(columns=cols)
    d=pd.read_csv(path,dtype={'ticker':str},encoding='utf-8-sig')
    if not set(cols).issubset(d.columns):
        raise RuntimeError(f'{path.name}: CSV 열 이름 불일치')
    for c in ('high','low'):
        if c not in d:d[c]=float('nan')
    cols+=['high','low']
    d=d[cols].copy()
    for c in ('high','low'):d[c]=pd.to_numeric(d[c],errors='coerce')
    d['ticker']=d['ticker'].astype(str).str.zfill(6)
    d['date']=pd.to_datetime(d['date'],errors='coerce')
    d['close']=pd.to_numeric(d['close'],errors='coerce')
    d['volume']=pd.to_numeric(d['volume'],errors='coerce')
    return d.dropna(subset=['date','close']).drop_duplicates(['ticker','date'],keep='last')


def save_atomic(path, data):
    data=data.drop_duplicates(['ticker','date'],keep='last').sort_values(['ticker','date']).copy()
    data['date']=pd.to_datetime(data['date']).dt.strftime('%Y-%m-%d')
    tmp=path.with_suffix('.tmp')
    data.to_csv(tmp,index=False,encoding='utf-8-sig')
    tmp.replace(path)
    return data


def confirmed_session(now):
    """Latest closed KRX session; never call an intraday candle confirmed."""
    import exchange_calendars as xcals
    cal=xcals.get_calendar('XKRX')
    day=now.astimezone(ZoneInfo('Asia/Seoul')).date()
    session=cal.date_to_session(day.isoformat(),direction='previous')
    if session.date()==day and now.time()<clock_time(16,0):
        session=cal.previous_session(session)
    return session.date()


def collection_plan(universe, existing, expected, *, bootstrap_limit=100, refresh_limit=3000):
    latest=existing.groupby('ticker')['date'].max().to_dict() if not existing.empty else {}
    missing=[item for item in universe if item[0] not in latest]
    ohlc_ready={c for c,g in existing.groupby('ticker') if {'high','low'}.issubset(g.columns) and g.tail(253)[['high','low']].notna().all().all()} if not existing.empty else set()
    stale=[item for item in universe if item[0] in latest and (latest[item[0]].date()<expected or item[0] not in ohlc_ready)]
    stale.sort(key=lambda item:(latest[item[0]],item[0]))
    # Backfill progresses every run; daily refresh no longer waits three days or
    # loses all but five slots to new symbols. An execution budget bounds work.
    return missing[:bootstrap_limit]+stale[:refresh_limit]


def fetch_incremental(session,key,secret,token,code,history,start,end):
    if history.empty or not {'high','low'}.issubset(history.columns) or history.tail(253)[['high','low']].isna().any().any():
        return fetch_year(session,key,secret,token,code,start,end,min_rows=1),True
    recent=history.sort_values('date')
    since=max(start,recent.date.max().date()-timedelta(days=14))
    delta=fetch_year(session,key,secret,token,code,since,end,min_rows=1)
    overlap=recent.merge(delta,on='date',suffixes=('_old','_new'))
    # Adjustment revisions must refresh the whole window, not combine prices
    # from different split/rights-adjustment scales into one RS history.
    if overlap.empty or (overlap.close_old!=overlap.close_new).any():
        return fetch_year(session,key,secret,token,code,start,end,min_rows=1),True
    return delta,False


def write_status(path, universe, data, today, succeeded, failed, *, expected=None,
                 attempted=0,remaining_batch=0,full_history_requests=0):
    codes={code for code,_ in universe}
    latest=data.groupby('ticker')['date'].max().to_dict() if not data.empty else {}
    completed=len(set(latest)&codes)
    expected=expected or today
    current={c for c in codes if c in latest and latest[c].date()==expected}
    history_count=data.groupby('ticker').size().to_dict() if not data.empty else {}
    eligible={c for c in current if history_count.get(c,0)>=253}
    status={
        'as_of':today.isoformat(),'confirmed_session':expected.isoformat(),
        'universe_total':len(codes),'collected_symbols':completed,
        'remaining_symbols':max(0,len(codes)-completed),
        'progress_pct':round(100*completed/len(codes),2) if codes else 0,
        'current_session_symbols':len(current),
        'current_history_253_symbols':len(eligible),
        'current_session_coverage_pct':round(100*len(current)/len(codes),2) if codes else 0,
        'daily_rows':int(len(data)),'last_run_success':succeeded,
        'last_run_failures':failed,'last_run_attempted':attempted,
        'remaining_planned_symbols':remaining_batch,
        'full_history_requests':full_history_requests,
        'note':'최신 확정 거래일·253개 이력 확보 수를 별도 표시합니다. RS 최종 적격 수는 가격·거래량·정렬 검증 후 결정됩니다.'
    }
    tmp=path.with_suffix('.tmp')
    tmp.write_text(json.dumps(status,ensure_ascii=False,indent=2),encoding='utf-8')
    tmp.replace(path)


def main(shared_token=None):
    key=os.getenv('KIS_APP_KEY','').strip()
    secret=os.getenv('KIS_APP_SECRET','').strip()
    if not key or not secret:
        raise RuntimeError('KIS_APP_KEY / KIS_APP_SECRET GitHub Secrets 확인 필요')
    OUT.mkdir(parents=True,exist_ok=True)
    universe=load_universe(OUT/'universe_test.csv')
    destination=OUT/'kis_daily_universe.csv'
    existing=read_existing(destination)
    # 이전에 수집한 30종목 데이터 재사용 (신규 파일이 비어 있을 때만)
    if existing.empty:
        seed=read_existing(OUT/'kis_daily_30stocks.csv')
        existing=seed
    now_kst=datetime.now(ZoneInfo('Asia/Seoul'))
    today=now_kst.date()
    # During the session, today's daily candle is provisional. Even after
    # market close, a zero-volume row must not be treated as a confirmed bar.
    allow_today = now_kst.time() >= clock_time(16, 0)
    def confirmed_rows(frame):
        if frame.empty:
            return frame
        dates=pd.to_datetime(frame['date'],errors='coerce').dt.date
        volume=pd.to_numeric(frame['volume'],errors='coerce')
        provisional=(dates == today) & ((not allow_today) | volume.isna() | (volume <= 0))
        if provisional.any():
            print(f'당일 미확정/거래량 0 일봉 {int(provisional.sum())}행 제외 (원본 API 응답 변경 없음)',flush=True)
        return frame.loc[~provisional].copy()
    # Remove provisional rows already committed by previous runs as well.
    # Historical zero-volume rows remain untouched: they may be real suspensions.
    existing=confirmed_rows(existing)
    start=today-timedelta(days=410)
    expected=confirmed_session(now_kst)
    batch_size=max(1,min(int(os.getenv('KIS_BATCH_SIZE','100')),200))
    refresh_limit=max(1,min(int(os.getenv('KIS_DAILY_REFRESH_LIMIT','3000')),5000))
    max_seconds=max(60,min(int(os.getenv('KIS_COLLECTION_SECONDS','1800')),2100))
    batch=collection_plan(universe,existing,expected,bootstrap_limit=batch_size,refresh_limit=refresh_limit)
    latest=existing.groupby('ticker')['date'].max().to_dict() if not existing.empty else {}
    missing=[item for item in universe if item[0] not in latest]
    print(f'확정 거래일 {expected}; 신규 이력 최대 {batch_size}; 기존 일봉 갱신 최대 {refresh_limit}',flush=True)
    print(f'전체 {len(universe)}개 / 저장 {len(latest)}개 / 미수집 {len(missing)}개 / 이번 {len(batch)}개',flush=True)
    if not batch:
        if destination.exists():
            save_atomic(destination,existing)
        write_status(OUT/'collection_status.json', universe, existing, today, 0, 0, expected=expected)
        print('수집 대상 없음.',flush=True)
        return
    session=requests.Session()
    token = shared_token if shared_token is not None else get_token(session,key,secret)
    if not isinstance(token,str) or not token or any(c.isspace() for c in token):
        raise RuntimeError('KIS 공유 토큰 형식 오류')
    success=0;attempted=0;full_history_requests=0;started=time.monotonic()
    failures=[]
    histories={c:g.copy() for c,g in existing.groupby('ticker')}
    for code,name in batch:
        if time.monotonic()-started>=max_seconds:
            print('조회 예산 종료; 다음 수집에서 미완료 종목을 이어갑니다.',flush=True)
            break
        attempted+=1
        try:
            history=histories.get(code,pd.DataFrame())
            df,replaced=fetch_incremental(session,key,secret,token,code,history,start,expected)
            full_history_requests+=int(replaced)
            df=confirmed_rows(df)
            if df.empty:
                raise RuntimeError('확정된 일봉 없음')
            df.insert(0,'ticker',code)
            df.insert(1,'name',name)
            if replaced:
                dates=pd.to_datetime(existing['date']).dt.date
                existing=existing.loc[~((existing.ticker==code)&(dates>=start))].copy()
            existing=pd.concat([existing,df],ignore_index=True)
            existing=existing.drop_duplicates(['ticker','date'],keep='last')
            success+=1
            print(f'OK {code} {name}: {len(df)} 일봉',flush=True)
        except Exception as e:
            failures.append(code)
            print(f'FAIL {code}: {type(e).__name__}; 이전 이력 유지',flush=True)
        # Large daily refreshes checkpoint without rewriting the full CSV per five API calls.
        if success and success%50==0:
            save_atomic(destination,existing)
        time.sleep(float(os.getenv('KIS_REQUEST_PAUSE','0.8')))
    # Persist cleanup even when API calls fail; don't discard other historical bars.
    if success or destination.exists():
        save_atomic(destination,existing)
    write_status(OUT/'collection_status.json', universe, existing, today, success, len(failures),
        expected=expected,attempted=attempted,remaining_batch=len(batch)-attempted,full_history_requests=full_history_requests)
    print(f'이번 성공 {success}/{len(batch)}, 실패 {len(failures)}, 전체 저장 종목 {existing.ticker.nunique()}',flush=True)
    if success==0:
        raise RuntimeError('이번 실행 성공 종목 0개. 기존 CSV 보존. API 응답/토큰 발급 제한 확인')

if __name__=='__main__':
    main()

"""KIS read-only: incremental KOSPI/KOSDAQ daily OHLCV collection."""
import os
import time
from datetime import datetime, timedelta
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
    response.raise_for_status()
    payload = response.json()
    if not payload.get('access_token'):
        raise RuntimeError('토큰 발급 실패: ' + str(payload.get('error_description', '응답 확인 필요')))
    return payload['access_token']


def fetch_year(session, key, secret, token, symbol, start, end):
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
                               params=params, headers=headers, timeout=25)
        response.raise_for_status()
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
    df = df.rename(columns={'stck_bsop_date':'date','stck_clpr':'close','acml_vol':'volume'})
    df = df[['date','close','volume']].copy()
    df['date'] = pd.to_datetime(df['date'], format='%Y%m%d', errors='coerce')
    for col in ('close','volume'):
        df[col] = pd.to_numeric(df[col], errors='coerce')
    df = df.dropna().drop_duplicates('date').sort_values('date')
    df = df[(df['date'].dt.date >= start) & (df['date'].dt.date <= end)]
    if len(df) < 200:
        raise RuntimeError(f'{symbol} 수집 부족: {len(df)}개 일봉 (200개 이상 필요)')
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
    d=d[cols].copy()
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


def main():
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
    today=datetime.now(ZoneInfo('Asia/Seoul')).date()
    start=today-timedelta(days=410)
    batch_size=max(1,min(int(os.getenv('KIS_BATCH_SIZE','20')),50))
    fresh_days=max(1,int(os.getenv('KIS_FRESH_DAYS','3')))
    latest=existing.groupby('ticker')['date'].max().to_dict() if not existing.empty else {}
    pending=[(code,name) for code,name in universe if code not in latest or (today-latest[code].date()).days>fresh_days]
    batch=pending[:batch_size]
    print(f'종목 목록 {len(universe)}개, 기존 {existing.ticker.nunique()}개, 이번 시도 {len(batch)}개',flush=True)
    if not batch:
        print('최신 데이터 수집 완료. 재수집 생략.',flush=True)
        return
    session=requests.Session()
    token=get_token(session,key,secret)  # 작업당 한 번만 발급. 토큰은 파일/로그에 저장하지 않음
    success=0
    failures=[]
    for code,name in batch:
        try:
            df=fetch_year(session,key,secret,token,code,start,today)
            df.insert(0,'ticker',code)
            df.insert(1,'name',name)
            existing=pd.concat([existing,df],ignore_index=True)
            existing=existing.drop_duplicates(['ticker','date'],keep='last')
            success+=1
            print(f'OK {code} {name}: {len(df)} 일봉',flush=True)
        except Exception as e:
            failures.append(code)
            print(f'FAIL {code}: {type(e).__name__}: {str(e)[:160]}',flush=True)
        # 5개마다 저장해 중간 실패에도 수집 결과 보존
        if success and (success%5==0):
            save_atomic(destination,existing)
        time.sleep(0.7)
    if success:
        save_atomic(destination,existing)
    print(f'이번 성공 {success}/{len(batch)}, 실패 {len(failures)}, 전체 저장 종목 {existing.ticker.nunique()}',flush=True)
    if success==0:
        raise RuntimeError('이번 실행 성공 종목 0개. 기존 CSV 보존. API 응답/토큰 발급 제한 확인')

if __name__=='__main__':
    main()

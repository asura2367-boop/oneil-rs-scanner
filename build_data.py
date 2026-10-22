"""KIS 실전 API: 3종목 1년 일봉 수집 테스트. 주문 기능 없음."""
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

BASE = 'https://openapi.koreainvestment.com:9443'
SYMBOLS = {'005930': '삼성전자', '000660': 'SK하이닉스', '005380': '현대차'}
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


def main():
    key = os.environ.get('KIS_APP_KEY', '').strip()
    secret = os.environ.get('KIS_APP_SECRET', '').strip()
    if not key or not secret:
        raise RuntimeError('GitHub Actions Secrets: KIS_APP_KEY / KIS_APP_SECRET 미설정')
    today = datetime.now().date()
    start = today - timedelta(days=410)
    session = requests.Session()
    token = get_token(session, key, secret)
    frames = []
    for code, name in SYMBOLS.items():
        df = fetch_year(session, key, secret, token, code, start, today)
        df.insert(0, 'ticker', code)
        df.insert(1, 'name', name)
        frames.append(df)
        print(f'{name}: {len(df)}개 일봉 수집 성공', flush=True)
        time.sleep(0.35)
    OUT.mkdir(parents=True, exist_ok=True)
    destination = OUT / 'kis_daily_3stocks.csv'
    pd.concat(frames, ignore_index=True).to_csv(destination, index=False, encoding='utf-8-sig')
    print(f'저장 완료: {destination} (3종목 테스트, 전체시장 RS 아님)', flush=True)


if __name__ == '__main__':
    main()

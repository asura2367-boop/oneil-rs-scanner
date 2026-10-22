"""KIS 실전 API: 3종목 1년 일봉 수집 테스트. 주문 기능 없음."""
import os
import time
from datetime import datetime, timedelta
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


def main():
    key = os.environ.get('KIS_APP_KEY', '').strip()
    secret = os.environ.get('KIS_APP_SECRET', '').strip()
    if not key or not secret:
        raise RuntimeError('GitHub Actions Secrets: KIS_APP_KEY / KIS_APP_SECRET 미설정')
    today = datetime.now().date()
    start = today - timedelta(days=410)
    OUT.mkdir(parents=True, exist_ok=True)
    destination = OUT / 'kis_daily_30stocks.csv'
    existing = pd.DataFrame(columns=['ticker', 'name', 'date', 'close', 'volume'])
    if destination.exists():
        existing = pd.read_csv(destination, dtype={'ticker': str})
        existing['ticker'] = existing['ticker'].str.zfill(6)
    # 매 실행 5종목씩 처리. 이미 수집한 종목은 날짜가 3일 이내라면 건너뜀.
    pending = []
    for code, name in SYMBOLS.items():
        subset = existing[existing['ticker'] == code]
        if not subset.empty:
            latest = pd.to_datetime(subset['date'], errors='coerce').max()
            if pd.notna(latest) and (today - latest.date()).days <= 3:
                continue
        pending.append((code, name))
    batch_size = int(os.getenv('KIS_BATCH_SIZE', '5'))
    batch_size = max(1, min(batch_size, 10))
    batch = pending[:batch_size]
    if not batch:
        print('모든 30종목 최신 데이터 확보. 저장된 CSV 유지.', flush=True)
        return
    session = requests.Session()
    token = get_token(session, key, secret)
    frames = [existing]
    succeeded = 0
    for code, name in batch:
        try:
            df = fetch_year(session, key, secret, token, code, start, today)
            df.insert(0, 'ticker', code)
            df.insert(1, 'name', name)
            frames.append(df)
            succeeded += 1
            print(f'{name}({code}): {len(df)}개 일봉 수집 성공', flush=True)
        except Exception as exc:
            print(f'{name}({code}) 조회 실패: {type(exc).__name__}: {exc}', flush=True)
        time.sleep(0.6)
    if succeeded == 0:
        raise RuntimeError('이번 배치에서 성공한 종목이 없습니다. 이전 CSV는 유지합니다.')
    merged = pd.concat(frames, ignore_index=True)
    merged['date'] = pd.to_datetime(merged['date'], errors='coerce')
    merged = merged.dropna(subset=['date', 'close']).drop_duplicates(['ticker', 'date'], keep='last')
    merged = merged.sort_values(['ticker', 'date'])
    merged['date'] = merged['date'].dt.strftime('%Y-%m-%d')
    tmp = destination.with_suffix('.tmp')
    merged.to_csv(tmp, index=False, encoding='utf-8-sig')
    tmp.replace(destination)
    print(f'저장 완료: {merged.ticker.nunique()}/30종목, {len(merged)}행. 전체시장 RS 아님.', flush=True)


if __name__ == '__main__':
    main()

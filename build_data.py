"""한국투자증권 KIS 일봉 수집기: 1회 인증, 최대 30종목 순차 수집, 배치별 안전 저장.

실전 시세 조회 전용. 주문 API를 사용하지 않습니다.
GitHub Actions 환경변수 KIS_APP_KEY, KIS_APP_SECRET 필요.
"""
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

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
OUT = Path(__file__).resolve().parent / 'data' / 'kis_daily_30stocks.csv'
COLUMNS = ['ticker','name','date','close','volume']


def get_token(session, key, secret):
    """한 번만 발급. 403이면 무작정 재시도하지 않음."""
    try:
        r = session.post(f'{BASE}/oauth2/tokenP', json={
            'grant_type': 'client_credentials', 'appkey': key, 'appsecret': secret
        }, timeout=25)
    except requests.RequestException as exc:
        raise RuntimeError(f'토큰 서버 연결 실패: {type(exc).__name__}') from None
    if not r.ok:
        try:
            obj = r.json()
            code = str(obj.get('error_code') or obj.get('msg_cd') or '없음')[:50]
            description = str(obj.get('error_description') or obj.get('msg1') or '')[:150]
        except ValueError:
            code, description = '응답 파싱 불가', ''
        raise RuntimeError(f'KIS 토큰 발급 거절 HTTP {r.status_code} / 코드: {code} / 설명: {description}. '
                           '발급 제한·인증정보·접속정책 확인 필요. 즉시 재실행하지 마세요.')
    try:
        payload = r.json()
    except ValueError:
        raise RuntimeError('KIS 토큰 응답이 JSON 형식이 아닙니다.') from None
    token = payload.get('access_token')
    if not token:
        raise RuntimeError(f'KIS 토큰 누락: {str(payload.get("error_code", "원인 미확인"))[:50]}')
    return token


def fetch_year(session, key, secret, token, symbol, start, end):
    rows = []
    cursor = end
    for _ in range(8):
        params = {
            'FID_COND_MRKT_DIV_CODE': 'J', 'FID_INPUT_ISCD': symbol,
            'FID_INPUT_DATE_1': start.strftime('%Y%m%d'),
            'FID_INPUT_DATE_2': cursor.strftime('%Y%m%d'),
            'FID_PERIOD_DIV_CODE': 'D', 'FID_ORG_ADJ_PRC': '0',
        }
        headers = {
            'authorization': f'Bearer {token}', 'appkey': key, 'appsecret': secret,
            'tr_id': 'FHKST03010100', 'content-type': 'application/json',
        }
        r = session.get(f'{BASE}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice',
                        params=params, headers=headers, timeout=30)
        if not r.ok:
            raise RuntimeError(f'{symbol} 일봉 HTTP {r.status_code}')
        payload = r.json()
        if str(payload.get('rt_cd')) != '0':
            raise RuntimeError(f'{symbol} 일봉 API 오류: {payload.get("msg_cd")} {str(payload.get("msg1"))[:100]}')
        batch = payload.get('output2') or []
        if not batch:
            break
        rows.extend(batch)
        oldest = min(datetime.strptime(x['stck_bsop_date'], '%Y%m%d').date() for x in batch)
        if oldest <= start:
            break
        cursor = oldest - timedelta(days=1)
        time.sleep(0.5)
    if not rows:
        raise RuntimeError(f'{symbol} 일봉 응답 없음')
    df = pd.DataFrame(rows).rename(columns={
        'stck_bsop_date': 'date', 'stck_clpr': 'close', 'acml_vol': 'volume'})
    df = df[['date', 'close', 'volume']].copy()
    df['date'] = pd.to_datetime(df['date'], format='%Y%m%d', errors='coerce')
    for c in ('close', 'volume'):
        df[c] = pd.to_numeric(df[c], errors='coerce')
    df = df.dropna().drop_duplicates('date').sort_values('date')
    df = df[(df.date.dt.date >= start) & (df.date.dt.date <= end)]
    if len(df) < 200:
        raise RuntimeError(f'{symbol} 일봉 부족: {len(df)}개')
    return df


def save(existing, additions):
    merged = pd.concat([existing, *additions], ignore_index=True)
    merged['ticker'] = merged['ticker'].astype(str).str.zfill(6)
    merged['date'] = pd.to_datetime(merged['date'], errors='coerce')
    merged['close'] = pd.to_numeric(merged['close'], errors='coerce')
    merged['volume'] = pd.to_numeric(merged['volume'], errors='coerce')
    merged = merged.dropna(subset=['date', 'close']).drop_duplicates(['ticker', 'date'], keep='last')
    merged = merged.sort_values(['ticker', 'date'])
    merged['date'] = merged['date'].dt.strftime('%Y-%m-%d')
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix('.tmp')
    merged[COLUMNS].to_csv(tmp, index=False, encoding='utf-8-sig')
    tmp.replace(OUT)
    return merged


def main():
    key = os.getenv('KIS_APP_KEY', '').strip()
    secret = os.getenv('KIS_APP_SECRET', '').strip()
    if not key or not secret:
        raise RuntimeError('GitHub Actions Secrets에 KIS_APP_KEY와 KIS_APP_SECRET을 설정하세요.')
    today = datetime.now(ZoneInfo('Asia/Seoul')).date()
    start = today - timedelta(days=410)
    existing = pd.read_csv(OUT, dtype={'ticker': str}) if OUT.exists() else pd.DataFrame(columns=COLUMNS)
    if not existing.empty:
        existing['ticker'] = existing['ticker'].astype(str).str.zfill(6)
    pending = []
    for code, name in SYMBOLS.items():
        subset = existing.loc[existing.ticker == code]
        if not subset.empty:
            latest = pd.to_datetime(subset.date, errors='coerce').max()
            if pd.notna(latest) and (today - latest.date()).days <= 3:
                continue
        pending.append((code, name))
    if not pending:
        print('모든 30종목 데이터가 최근 3일 이내입니다. 토큰 요청 없이 종료.', flush=True)
        return
    # 기본 30종목: 같은 날 여러 차례 수동 실행할 필요 없음
    limit = max(1, min(int(os.getenv('KIS_BATCH_SIZE', '30')), 30))
    batch = pending[:limit]
    print(f'기존 {existing.ticker.nunique()}종목 / 이번 실행 최대 {len(batch)}종목', flush=True)
    session = requests.Session()
    token = get_token(session, key, secret)
    additions = []
    success = 0
    for i, (code, name) in enumerate(batch, 1):
        try:
            df = fetch_year(session, key, secret, token, code, start, today)
            df.insert(0, 'ticker', code)
            df.insert(1, 'name', name)
            additions.append(df)
            success += 1
            print(f'[{i}/{len(batch)}] {name}({code}) {len(df)}일봉 성공', flush=True)
        except (requests.RequestException, RuntimeError, ValueError, KeyError) as exc:
            print(f'[{i}/{len(batch)}] {name}({code}) 실패: {type(exc).__name__}: {str(exc)[:160]}', flush=True)
        # 5종목마다 파일 저장. 실패한 종목은 다음 실행에 재시도 가능
        if len(additions) >= 5:
            existing = save(existing, additions)
            additions = []
            print(f'중간 저장: {existing.ticker.nunique()}/30종목', flush=True)
        time.sleep(0.8)
    if additions:
        existing = save(existing, additions)
    print(f'완료: 이번 실행 {success}종목 성공, 총 {existing.ticker.nunique()}/30종목, {len(existing)}행', flush=True)
    if success == 0:
        raise RuntimeError('신규 수집 성공 종목이 없습니다. 기존 CSV는 유지했습니다.')


if __name__ == '__main__':
    main()

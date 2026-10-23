"""KIS read-only quote collector V4-A. No order endpoints, no paper account writes.
Run once: python quote_collector_v4a.py --once
Run during KRX regular hours: python quote_collector_v4a.py --loop
Requires KIS_APP_KEY and KIS_APP_SECRET environment variables.
"""
import argparse
import csv
import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

KST = ZoneInfo('Asia/Seoul')
BASE = 'https://openapi.koreainvestment.com:9443'
OUT = Path(__file__).resolve().parent / 'data' / 'intraday_v4a'
FIELDS = ['received_at_kst', 'ticker', 'price', 'cumulative_volume', 'api_code', 'quote_status']


def now_kst():
    return datetime.now(KST)


def regular_session(dt):
    return dt.weekday() < 5 and (9, 10) <= (dt.hour, dt.minute) <= (15, 20)


class QuoteClient:
    def __init__(self):
        self.key = os.getenv('KIS_APP_KEY', '').strip()
        self.secret = os.getenv('KIS_APP_SECRET', '').strip()
        if not self.key or not self.secret:
            raise RuntimeError('KIS_APP_KEY / KIS_APP_SECRET 환경변수가 필요합니다.')
        self.session = requests.Session()
        self.token = None
        self.token_expires = datetime.min.replace(tzinfo=KST)

    def authorize(self):
        if self.token and now_kst() < self.token_expires:
            return
        res = self.session.post(BASE + '/oauth2/tokenP', json={
            'grant_type': 'client_credentials', 'appkey': self.key, 'appsecret': self.secret
        }, timeout=20)
        res.raise_for_status()
        data = res.json()
        self.token = data.get('access_token')
        if not self.token:
            raise RuntimeError('토큰 발급 응답에 access_token 없음')
        expires = int(data.get('expires_in', 3600))
        self.token_expires = now_kst() + timedelta(seconds=max(60, expires - 300))

    def quote(self, ticker):
        self.authorize()
        res = self.session.get(
            BASE + '/uapi/domestic-stock/v1/quotations/inquire-price',
            headers={'authorization': f'Bearer {self.token}', 'appkey': self.key,
                     'appsecret': self.secret, 'tr_id': 'FHKST01010100'},
            params={'FID_COND_MRKT_DIV_CODE': 'J', 'FID_INPUT_ISCD': ticker}, timeout=20)
        res.raise_for_status()
        payload = res.json()
        if str(payload.get('rt_cd')) != '0':
            raise RuntimeError(f"KIS 오류 {payload.get('msg_cd', '?')}: {payload.get('msg1', '')}")
        output = payload.get('output') or {}
        if not isinstance(output, dict):
            raise RuntimeError('시세 응답 형식 오류')
        price = int(output.get('stck_prpr') or 0)
        volume = int(output.get('acml_vol') or 0)
        if price <= 0 or volume < 0:
            raise RuntimeError('시세 값 검증 실패')
        return {'received_at_kst': now_kst().isoformat(timespec='seconds'),
                'ticker': ticker, 'price': price, 'cumulative_volume': volume,
                'api_code': str(payload.get('msg_cd', '')), 'quote_status': 'snapshot_only'}

    def close(self):
        self.session.close()


def append_snapshot(row):
    OUT.mkdir(parents=True, exist_ok=True)
    day = row['received_at_kst'][:10]
    path = OUT / f'quotes_{day}.csv'
    with path.open('a', newline='', encoding='utf-8-sig') as file:
        writer = csv.DictWriter(file, fieldnames=FIELDS)
        if path.stat().st_size == 0:
            writer.writeheader()
        writer.writerow(row)
    return path


def collect(client, tickers):
    for index, ticker in enumerate(tickers):
        try:
            row = client.quote(ticker)
            path = append_snapshot(row)
            print(f"OK {ticker}: {row['price']:,}원, 누적 {row['cumulative_volume']:,}주 -> {path}", flush=True)
        except (requests.RequestException, RuntimeError, ValueError) as exc:
            print(f'FAIL {ticker}: {type(exc).__name__}: {exc}', flush=True)
        if index < len(tickers) - 1:
            time.sleep(1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true', help='즉시 1회 조회 (장외 시세 주의)')
    parser.add_argument('--loop', action='store_true', help='평일 정규장 09:10~15:20, 5분 간격')
    parser.add_argument('--tickers', nargs='+', default=['005930', '022220', '024950'])
    args = parser.parse_args()
    if args.once == args.loop:
        parser.error('--once 또는 --loop 중 하나만 지정')
    if len(args.tickers) > 5 or len(set(args.tickers)) != len(args.tickers) or any(
        len(t) != 6 or not t.isdigit() for t in args.tickers
    ):
        parser.error('중복 없는 6자리 종목코드 1~5개만 지정')
    client = QuoteClient()
    try:
        if args.once:
            collect(client, args.tickers)
            return
        print('V4-A 장중 조회 대기: 공휴일 자동 판정 없음. 서버가 계속 실행되어야 합니다.', flush=True)
        last_slot = None
        while True:
            dt = now_kst()
            slot = (dt.date(), dt.hour, dt.minute // 5)
            if regular_session(dt) and slot != last_slot and dt.minute % 5 == 0:
                last_slot = slot
                collect(client, args.tickers)
            time.sleep(10)
    finally:
        client.close()


if __name__ == '__main__':
    main()

"""KIS read-only quote collector V4-A. No order endpoints, no paper account writes.
Run once: python quote_collector_v4a.py --once
Run during KRX regular hours: python quote_collector_v4a.py --loop
Requires KIS_APP_KEY and KIS_APP_SECRET environment variables.
"""
import argparse
import csv
import json
import os
import re
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
        from kis_live_v4_5_2 import required_cached_token
        self.token=required_cached_token()
        # Re-read encrypted expiry before every quote; no separate memory TTL.
        self.token_expires=now_kst()

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


def candidate_session(at):
    if at.tzinfo is None:return False
    at=at.astimezone(KST)
    return at.weekday()<5 and (9,0)<=(at.hour,at.minute)<(15,15)


def validate_candidate_quote(row,ticker,at):
    if not isinstance(row,dict) or row.get('ticker')!=ticker or not re.fullmatch('[0-9]{6}',ticker):
        raise ValueError('CANDIDATE_QUOTE_IDENTITY_INVALID')
    observed=datetime.fromisoformat(row['received_at_kst'])
    if (observed.tzinfo is None or observed.astimezone(KST).date()!=at.astimezone(KST).date()
            or not candidate_session(observed) or not -2<=(at-observed).total_seconds()<=600
            or row.get('trading_day')!=at.astimezone(KST).date().isoformat()):
        raise ValueError('CANDIDATE_QUOTE_STALE')
    for key in ('price','cumulative_volume'):
        if type(row.get(key)) is not int or row[key]<=0:raise ValueError('CANDIDATE_QUOTE_VALUE_INVALID')


class CandidateQuoteClient(QuoteClient):
    """Fixed KRX quotation GET only; encrypted existing token cache in memory."""
    def __init__(self):
        if any(os.getenv(n,'false').lower()!='false' for n in ('LIVE_ENABLED','KIS_ALLOW_ORDER')):
            raise ValueError('CANDIDATE_COLLECTOR_REQUIRES_READ_ONLY')
        super().__init__()
        from kis_live_v4_5_2 import required_cached_token
        self.token=required_cached_token()

    def daily_candidate_snapshot(self,ticker,*,clock=now_kst,pause=time.sleep):
        if not re.fullmatch('[0-9]{6}',ticker):raise ValueError('CANDIDATE_TICKER_INVALID')
        started=clock()
        if not candidate_session(started):raise ValueError('INTRADAY_SESSION_CLOSED')
        day=started.astimezone(KST).strftime('%Y%m%d')
        # Current bar confirms the trading date. output1 provides the current
        # price and actual cumulative volume, never a projected closing volume.
        for attempt in range(2):
            try:
                response=self.session.get(BASE+'/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice',
                    headers={'authorization':'Bearer '+self.token,'appkey':self.key,'appsecret':self.secret,
                        'tr_id':'FHKST03010100','custtype':'P'},
                    params={'FID_COND_MRKT_DIV_CODE':'J','FID_INPUT_ISCD':ticker,
                        'FID_INPUT_DATE_1':day,'FID_INPUT_DATE_2':day,'FID_PERIOD_DIV_CODE':'D','FID_ORG_ADJ_PRC':'0'},
                    timeout=12,allow_redirects=False)
                if response.status_code in (429,500,502,503,504) and not attempt:
                    pause(.8);continue
                if response.status_code!=200:raise ValueError('CANDIDATE_QUOTE_HTTP_FAILURE')
                body=response.json()
                break
            except (requests.Timeout,requests.ConnectionError):
                if attempt:raise ValueError('CANDIDATE_QUOTE_TRANSPORT_FAILURE') from None
                pause(.8)
        at=clock()
        if not candidate_session(at) or at.date()!=started.date() or (at-started).total_seconds()>30:
            raise ValueError('CANDIDATE_QUOTE_RESPONSE_EXPIRED')
        if not isinstance(body,dict) or str(body.get('rt_cd'))!='0':raise ValueError('CANDIDATE_QUOTE_REJECTED')
        current=body.get('output1');bars=body.get('output2')
        if not isinstance(current,dict) or current.get('stck_shrn_iscd')!=ticker or not isinstance(bars,list):
            raise ValueError('CANDIDATE_QUOTE_IDENTITY_INVALID')
        today=[r for r in bars if isinstance(r,dict) and r.get('stck_bsop_date')==day]
        if not today:return None  # No verified trade today: exclude, never reuse yesterday's price.
        if len(today)!=1:raise ValueError('CANDIDATE_QUOTE_DAY_CONFLICT')
        def integer(value):
            if not isinstance(value,str) or not re.fullmatch('[0-9]+',value):
                raise ValueError('CANDIDATE_QUOTE_VALUE_INVALID')
            return int(value)
        price=integer(current.get('stck_prpr'));volume=integer(current.get('acml_vol'))
        if volume==0:return None
        row={'ticker':ticker,'price':price,'cumulative_volume':volume,
             'received_at_kst':at.isoformat(),'trading_day':at.date().isoformat(),
             'high':integer(today[0].get('stck_hgpr')),'low':integer(today[0].get('stck_lwpr'))}
        if not 0<row['low']<=price<=row['high']:raise ValueError('CANDIDATE_OHLC_INVALID')
        validate_candidate_quote(row,ticker,at)
        return row


def collect_candidate_quotes(tickers,target,*,client=None,clock=now_kst,pause=time.sleep,workers=1):
    if any(os.getenv(n,'false').lower()!='false' for n in ('LIVE_ENABLED','KIS_ALLOW_ORDER')):
        raise ValueError('CANDIDATE_COLLECTOR_REQUIRES_READ_ONLY')
    if type(workers) is not int or not 1<=workers<=8:raise ValueError('CANDIDATE_WORKERS_INVALID')
    tickers=list(tickers);started=clock()
    if (not tickers or len(tickers)!=len(set(tickers)) or
            any(not re.fullmatch('[0-9]{6}',c) for c in tickers)):
        raise ValueError('CANDIDATE_UNIVERSE_INVALID')
    if not candidate_session(started) or started.date().isoformat()!=target:
        raise ValueError('INTRADAY_SESSION_CLOSED')
    owned=client is None;client=client or CandidateQuoteClient();result={}
    sessions=[]
    def check_clock():
        at=clock()
        if not candidate_session(at) or at.date()!=started.date() or (at-started).total_seconds()>600:
            raise ValueError('INTRADAY_COLLECTION_EXPIRED')
    try:
        if workers==1:
            for ticker in tickers:
                check_clock()
                row=client.daily_candidate_snapshot(ticker,clock=clock,pause=pause)
                if row is not None:validate_candidate_quote(row,ticker,clock())
                result[ticker]=row
                pause(.2)
        else:
            import copy,threading
            from concurrent.futures import ThreadPoolExecutor,as_completed
            local=threading.local();gate=threading.Lock();last=[None]
            def query(ticker):
                check_clock()
                # One globally paced start per 200ms; concurrency hides network
                # latency without a burst of one request per worker.
                with gate:
                    current=time.monotonic()
                    if last[0] is not None:
                        delay=max(0,.2-(current-last[0]))
                        if delay:pause(delay)
                    last[0]=time.monotonic()
                check_clock()
                if not hasattr(local,'client'):
                    local.client=copy.copy(client)
                    if isinstance(client,CandidateQuoteClient):
                        local.client.session=requests.Session()
                        sessions.append(local.client.session)
                row=local.client.daily_candidate_snapshot(ticker,clock=clock,pause=pause)
                if row is not None:validate_candidate_quote(row,ticker,clock())
                return row
            with ThreadPoolExecutor(max_workers=workers) as pool:
                pending={pool.submit(query,ticker):ticker for ticker in tickers}
                try:
                    for future in as_completed(pending):
                        result[pending[future]]=future.result()
                except BaseException:
                    for future in pending:future.cancel()
                    raise
        check_clock()
        return {ticker:result[ticker] for ticker in tickers}
    finally:
        for session in sessions:session.close()
        if owned:client.close()


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

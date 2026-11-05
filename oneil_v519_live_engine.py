#!/usr/bin/env python3
"""V5.19 integrated runtime: scanner, cash sizing, PostgreSQL ledger and recovery.
KIS automatic buy/sell runtime with operator-reviewed source and persistent activation.
"""
from __future__ import annotations
import datetime as dt
import hashlib
import json
import os
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo
from contextlib import contextmanager

@contextmanager
def read_stage(stage):
    """Fixed stage names and duration only; never broker/account payloads."""
    import time
    started=time.monotonic()
    print(json.dumps({'event':'READ_STAGE_STARTED','stage':stage}),flush=True)
    try:
        yield
    finally:
        print(json.dumps({'event':'READ_STAGE_FINISHED','stage':stage,
                          'elapsed_seconds':round(time.monotonic()-started,3)}),flush=True)

class TradingBlocked(ValueError): pass
class OrderNotSent(TradingBlocked):
    """Trusted adapter rejection before entering order transport; never infer from error text."""
    pass
class SellDisabledBeforeSend(OrderNotSent): pass
CODE=re.compile(r"^[0-9]{6}$")

def money(n):
    if type(n) is not int or n < 0:raise TradingBlocked("INVALID_KRW")
    return n

@dataclass(frozen=True)
class VerifiedAccount:
    cash_krw: int
    orderable_cash_krw: int
    trading_exposure_krw: int
    stock_exposure_krw: int
    held: bool
    unfilled: bool
    balance_fresh: bool
    orders_fresh: bool
    cash_independently_verified: bool

@dataclass(frozen=True)
class CertifiedPrice:
    ticker: str
    venue: str
    limit_price_krw: int
    exchange_day_confirmed: bool
    ticker_venue_eligible: bool
    fresh_executable_book: bool
    venue_buying_power_supported: bool

def budget(account: VerifiedAccount, price: CertifiedPrice):
    """Real account cash is input; there is NO 1,000,000 KRW constant."""
    for n in (account.cash_krw,account.orderable_cash_krw,account.trading_exposure_krw,
              account.stock_exposure_krw,price.limit_price_krw):money(n)
    if not CODE.fullmatch(price.ticker) or price.venue not in {"KRX","NXT"}:
        raise TradingBlocked("VENUE_OR_TICKER_INVALID")
    if price.limit_price_krw==0:raise TradingBlocked("PRICE_ZERO")
    if not (account.balance_fresh and account.orders_fresh and account.cash_independently_verified):
        raise TradingBlocked("ACCOUNT_NOT_VERIFIED")
    if account.held or account.unfilled:raise TradingBlocked("ALREADY_HELD_OR_UNFILLED")
    if not (price.exchange_day_confirmed and price.ticker_venue_eligible
            and price.fresh_executable_book and price.venue_buying_power_supported):
        raise TradingBlocked("EXECUTABLE_VENUE_EVIDENCE_MISSING")
    trading_cap=account.cash_krw*60//100
    max_stock=trading_cap*30//100
    available=min(max(0,trading_cap-account.trading_exposure_krw),
                  max(0,max_stock-account.stock_exposure_krw),
                  account.orderable_cash_krw)
    # Reserve cash conservatively; never let buying use >60% of current verified cash.
    qty=available//price.limit_price_krw
    return {"ticker":price.ticker,"venue":price.venue,
            "qty":qty,"limit_price_krw":price.limit_price_krw,
            "cash_reserve_krw":account.cash_krw-trading_cap,
            "trading_cap_krw":trading_cap,"per_stock_cap_krw":max_stock,
            "notional_krw":qty*price.limit_price_krw}

def daily_loss(baseline_equity_krw,current_equity_krw,external_cashflows_krw,
               *, baseline_authenticated=False,current_authenticated=False,
               cashflows_reconciled=False,loss_limit_krw=20000):
    money(baseline_equity_krw);money(current_equity_krw);money(loss_limit_krw)
    if type(external_cashflows_krw) is not int:raise TradingBlocked("CASHFLOW_INVALID")
    if not (baseline_authenticated and current_authenticated and cashflows_reconciled):
        raise TradingBlocked("DAILY_PNL_NOT_VERIFIED")
    pnl=current_equity_krw-baseline_equity_krw-external_cashflows_krw
    return {"halt":pnl<=-loss_limit_krw,"pnl_krw":pnl}

def intent_id(trading_day,ticker,side,signal_id):
    if not CODE.fullmatch(ticker) or side not in {"BUY","SELL"}:raise TradingBlocked("BAD_INTENT")
    return hashlib.sha256(f"v519|{trading_day}|{ticker}|{side}|{signal_id}".encode()).hexdigest()

class DurableOrderLedger:
    """Persist unknown submissions BEFORE network send; never auto-resubmit."""
    def __init__(self,db_path):
        self.path=Path(db_path)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.cx=sqlite3.connect(self.path)
        self.cx.execute("PRAGMA journal_mode=WAL")
        self.cx.execute("""CREATE TABLE IF NOT EXISTS orders (
            intent TEXT PRIMARY KEY,ticker TEXT NOT NULL,side TEXT NOT NULL,
            venue TEXT NOT NULL,quantity INTEGER NOT NULL,
            cumulative_fill INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL,broker_order_id TEXT UNIQUE)""")
        self.cx.commit()
    def reserve(self,ident,ticker,side,venue,quantity):
        if not CODE.fullmatch(ticker) or side not in ("BUY","SELL") or venue not in ("KRX","NXT"):
            raise TradingBlocked("INTENT_INVALID")
        if type(quantity) is not int or quantity<=0:raise TradingBlocked("QTY_INVALID")
        try:
            self.cx.execute("""INSERT INTO orders(intent,ticker,side,venue,quantity,status)
                               VALUES(?,?,?,?,?,'SUBMISSION_UNKNOWN')""",(ident,ticker,side,venue,quantity))
            self.cx.commit()
        except sqlite3.IntegrityError:raise TradingBlocked("DUPLICATE_INTENT_BLOCKED") from None
    def reconcile(self,ident,*,broker_id,status,filled):
        if status not in ("ACKNOWLEDGED","PART_FILLED","FILLED","CANCELLED","REJECTED"):
            raise TradingBlocked("STATUS_INVALID")
        if not isinstance(broker_id,str) or not broker_id:raise TradingBlocked("BROKER_ID_REQUIRED")
        row=self.cx.execute("SELECT quantity,cumulative_fill,status,broker_order_id FROM orders WHERE intent=?",(ident,)).fetchone()
        if row is None:raise TradingBlocked("UNKNOWN_INTENT")
        quantity,old_fill,old_status,old_id=row
        if old_status in ("FILLED","CANCELLED","REJECTED"):raise TradingBlocked("TERMINAL_ORDER")
        if old_id and old_id != broker_id:raise TradingBlocked("BROKER_ORDER_ID_CONFLICT")
        if type(filled) is not int or not old_fill<=filled<=quantity:raise TradingBlocked("FILL_INVALID")
        if status=="FILLED" and filled!=quantity:raise TradingBlocked("FILL_INCOMPLETE")
        if status=="PART_FILLED" and not 0<filled<quantity:raise TradingBlocked("PART_FILL_INVALID")
        self.cx.execute("UPDATE orders SET status=?,cumulative_fill=?,broker_order_id=? WHERE intent=?",
                        (status,filled,broker_id,ident))
        self.cx.commit()
        return {"status":status,"pending_qty":quantity-filled,"auto_resubmit":False}
    def close(self):self.cx.close()

class KisCashOrderAdapter:
    """KIS real REST order endpoint mapping, not directly executable by this build.
    Separate, audited activation code and explicit user approval are REQUIRED.
    """
    def __init__(self,transport):
        self.transport=transport
    def prepare(self,*,side,ticker,qty,limit_price_krw,cano,product):
        if side not in ("BUY","SELL") or not CODE.fullmatch(ticker):raise TradingBlocked("ORDER_INVALID")
        if type(qty) is not int or qty<=0:raise TradingBlocked("QTY_INVALID")
        money(limit_price_krw)
        if limit_price_krw==0:raise TradingBlocked("PRICE_INVALID")
        if not (isinstance(cano,str) and len(cano)==8 and cano.isdigit()
                and isinstance(product,str) and len(product)==2 and product.isdigit()):
            raise TradingBlocked("ACCOUNT_FORMAT_INVALID")
        return {
            "path":"/uapi/domestic-stock/v1/trading/order-cash",
            "tr_id":"TTTC0012U" if side=="BUY" else "TTTC0011U",
            "body":{"CANO":cano,"ACNT_PRDT_CD":product,"PDNO":ticker,
                    "ORD_DVSN":"00","ORD_QTY":str(qty),"ORD_UNPR":str(limit_price_krw)}}
    def submit(self,*args,**kwargs):
        # Real orders are NOT available until a later separately approved activation.
        raise TradingBlocked("ACTUAL_ORDER_TRANSMISSION_LOCKED_AWAITING_FINAL_APPROVAL")

def readiness():
    return {"version":"5.19-integrated","gate":"DENY","orders_sent":0,
            "LIVE_ENABLED":False,"KIS_ALLOW_ORDER":False,
            "broker_order_transport_enabled":False,"current_cash_not_fixed":True}

# Operator-controlled runtime: exact source approval and durable account activation
# are separate prerequisites. Ordinary LIVE flags alone never authorize an order.
RELEASE_LIVE_APPROVED = False


RELEASE_FILES = (
    'oneil_v519_live_engine.py','v519_pg_ledger.py','v519_pg_reconcile.py',
    'v519_no_order_review.py',
    'v519_cash_policy.py','oneil_v519_live_runner.py','oneil_v519_history_observe.py',
    'oneil_v519_broker_recovery.py','oneil_v519_order_recovery.py',
    'kis_buying_power_readonly.py','kis_safety_v3.py','oneil_live_v5_9.py',
    '.github/workflows/oneil_v519_live_engine_review.yml',
    'build_v4b_candidates.py','quote_collector_v4a.py','kis_live_v4_5_2.py',
    '.github/workflows/v4b_candidate_save_auto.yml',
    'build_data.py','build_investor.py','collect_shared_token.py',
    '.github/workflows/kis_live_v4_5_2.yml',
    '.github/workflows/update.yml','.github/workflows/v10_collect_auto.yml','requirements.txt',
)


def release_fingerprint(root=None):
    root=Path(root) if root is not None else Path(__file__).resolve().parent
    digest=hashlib.sha256(b'oneil-v519-operator-release-v1\0')
    for name in RELEASE_FILES:
        digest.update(name.encode()+b'\0'+(root/name).read_bytes()+b'\0')
    return digest.hexdigest()


def release_is_approved():
    # The compatibility constant is False in source; tests may override it.
    # An operator must approve the exact code AND activate this account in DB.
    import hmac
    approved=os.getenv('V519_APPROVED_RELEASE','')
    return RELEASE_LIVE_APPROVED or (bool(re.fullmatch('[0-9a-f]{64}',approved)) and
        hmac.compare_digest(approved,release_fingerprint()))


def require_live_release():
    if not release_is_approved():
        raise TradingBlocked('RELEASE_LOCKED_PENDING_OPERATOR_ACTIVATION')
    if not (os.getenv('LIVE_ENABLED') == 'true' and os.getenv('KIS_ALLOW_ORDER') == 'true'
            and os.getenv('I_APPROVE_LIVE_KIS_ORDERS') == 'YES'):
        raise TradingBlocked('USER_APPROVAL_MISSING')


@dataclass(frozen=True)
class OrderPlan:
    ticker: str
    side: str
    quantity: int
    price: int
    signal: str
    reason: str


def plan_orders(candidates, snapshot, quote, power, *, stop_loss_pct=5, take_profit_pct=10, max_buys=None,
                reserve_pct=40, per_stock_pct=30, include_exits=True):
    """One broker snapshot, exits first, conservative whole-account exposure.

    Thresholds are configurable defaults requiring review before activation.
    Quote is a bounded LIMIT price, never a market-order price.
    Reserve a 0.5% fee buffer for buys. All account holdings count toward exposure;
    only positions originating in our ledger may be sold automatically.
    """
    from decimal import Decimal
    from v519_cash_policy import limits
    cash = money(snapshot['cash'])
    invested = money(snapshot['invested'])
    if snapshot['pending']:
        raise TradingBlocked('BROKER_PENDING_ORDERS')
    for pct in (stop_loss_pct, take_profit_pct):
        if type(pct) is not int or not 1 <= pct <= 50:
            raise TradingBlocked('EXIT_PARAMETER_INVALID')
    plans = []
    for ticker, pos in sorted(snapshot['positions'].items()) if include_exits else []:
        owned = money(snapshot['managed'].get(ticker, 0))
        qty = min(money(pos['sellable']), owned, money(pos['quantity']))
        if not qty:
            continue
        avg = Decimal(str(pos['average']))
        if not avg.is_finite() or avg <= 0:
            raise TradingBlocked('COST_INVALID')
        price = quote(ticker, 'SELL')
        money(price)
        if not price:
            raise TradingBlocked('PRICE_ZERO')
        reason = ('STOP_LOSS' if price * 100 <= avg * (100-stop_loss_pct) else
                  'TAKE_PROFIT' if price * 100 >= avg * (100+take_profit_pct) else ('TAKE_PROFIT_SIGNAL' if price>avg else 'STOP_LOSS_SIGNAL')
                  if isinstance(pos.get('technical'),dict) and pos['technical'].get('exit') is True else None)
        if reason:
            plans.append(OrderPlan(ticker, 'SELL', qty, price, 'exit', reason))
    b = limits(cash, invested, cash,reserve_pct=reserve_pct,per_stock_pct=per_stock_pct)
    if max_buys is not None and (type(max_buys) is not int or max_buys<1):
        raise TradingBlocked('BUY_BATCH_INVALID')
    if max_buys is not None and plans:
        return plans,b  # exits do not wait for candidate quote/power reads
    remaining = b['remaining']
    seen = set()
    def rank(c):
        rs=Decimal(str(c.get('rs',0)))
        if not rs.is_finite():raise TradingBlocked('RS_INVALID')
        return (-rs,str(c.get('ticker','')))
    for c in sorted(candidates,key=rank):
        # No later candidate can fit once either hard cash cap is zero.
        if remaining<=0 or b['per_stock']<=0:
            break
        ticker = c['ticker']
        if not isinstance(ticker, str) or not CODE.fullmatch(ticker) or ticker in seen:
            raise TradingBlocked('CANDIDATE_INVALID')
        seen.add(ticker)
        if ticker in snapshot['positions'] or ticker in snapshot.get('bought_today', set()):
            continue
        price = quote(ticker, 'BUY')
        if type(price) is not int or price <= 0:
            raise TradingBlocked('PRICE_INVALID')
        # Broker buying power cannot make a share fit a smaller hard cash cap.
        if (price*1005+999)//1000 > min(remaining,b['per_stock']):
            continue
        amount, broker_qty = power(ticker, price)
        money(amount); money(broker_qty)
        # ceiling(notional * 1.005) must fit ALL three cash limits.
        cap = min(remaining, b['per_stock'], amount)
        qty = min(broker_qty, cap * 1000 // (price * 1005))
        if qty:
            charge = (qty * price * 1005 + 999) // 1000
            remaining -= charge
            plans.append(OrderPlan(ticker, 'BUY', qty, price, 'rs', 'RS_SELECTION'))
            if max_buys is not None and sum(p.side=='BUY' for p in plans)>=max_buys:
                break
    return plans, b


KIS_READ_APIS = {
    ('/uapi/domestic-stock/v1/trading/inquire-balance','TTTC8434R'):'BALANCE',
    ('/uapi/domestic-stock/v1/quotations/inquire-daily-price','FHKST01010400'):'DAILY_PRICE',
    ('/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn','FHKST01010200'):'ORDER_BOOK',
}


def safe_kis_error_code(body):
    # Fixed broker code formats only. Never expose msg1, URL, headers or output.
    code=body.get('msg_cd') if isinstance(body,dict) else None
    return code if isinstance(code,str) and re.fullmatch(r'(?:EGW[0-9]{5}|APBK[0-9]{4}|OPSQ[0-9]{4})',code) else None


class KisBroker:
    """Existing KIS auth/power APIs plus complete balance/history adapters.

    This release implements real order payloads but keeps the send gate locked.
    REST quotes are indicative only; certified execution prices remain a live gate.
    """
    def __init__(self, *, execution=False, operator_activation=False):
        if execution or operator_activation:
            require_live_release()
        self.execution=execution
        self.quote_evidence={}
        self.history_cache={}
        from kis_buying_power_readonly import credentials
        from kis_live_v4_5_2 import required_cached_token
        self.key, self.secret, self.account, self.product = credentials(read_only=not (execution or operator_activation))
        self.access = required_cached_token()
        self.fingerprint = hashlib.sha256((self.account+'|'+self.product).encode()).hexdigest()

    def request(self, path, tr, params, *, continuing=False, post=False):
        import urllib.request
        from urllib.error import HTTPError,URLError
        from urllib.parse import urlencode
        from oneil_v519_live_runner import HOST
        import time
        headers = {'authorization':'Bearer '+self.access, 'appkey':self.key,
                   'appsecret':self.secret, 'tr_id':tr, 'custtype':'P',
                   'Content-Type':'application/json'}
        if continuing:
            headers['tr_cont'] = 'N'
        url = HOST+path
        if not post:
            url += '?' + urlencode(params)
        request = urllib.request.Request(url, headers=headers,
            data=json.dumps(params).encode() if post else None, method='POST' if post else 'GET')
        # Only these exact read-only endpoint/TR pairs may retry. An order POST
        # (including an HTTP 500 or lost response) is always sent at most once.
        api=KIS_READ_APIS.get((path,tr))
        can_retry=not post and api is not None
        api=api or ('CASH_ORDER' if post and path=='/uapi/domestic-stock/v1/trading/order-cash' else 'OTHER')
        attempts=3 if can_retry else 1
        def annotate(exc,attempt,broker_code=None):
            exc.broker_api=api;exc.broker_attempts=attempt
            if broker_code:exc.broker_code=broker_code
        def wait(attempt,status=None,code=None):
            delay=.5*2**(attempt-1)
            event={'event':'KIS_READ_RETRY','broker_api':api,'attempt':attempt,'delay_seconds':delay}
            if status is not None:event['http_status']=status
            if code:event['broker_code']=code
            print(json.dumps(event),flush=True)
            time.sleep(delay)
        for attempt in range(1,attempts+1):
            started=time.monotonic()
            print(json.dumps({'event':'BROKER_REQUEST_STARTED','broker_api':api,'attempt':attempt}),flush=True)
            try:
                with urllib.request.urlopen(request, timeout=15) as response:
                    body=json.load(response)
                    continuation=response.headers.get('tr_cont')
                print(json.dumps({'event':'BROKER_REQUEST_FINISHED','broker_api':api,'attempt':attempt,'elapsed_seconds':round(time.monotonic()-started,3)}),flush=True)
            except HTTPError as exc:
                broker_code=None
                try:
                    raw=exc.read(8193)
                    if len(raw)<=8192:broker_code=safe_kis_error_code(json.loads(raw))
                except Exception:
                    pass
                finally:
                    exc.close()
                annotate(exc,attempt,broker_code)
                if can_retry and exc.code in (429,500,502,503,504) and attempt<attempts:
                    wait(attempt,exc.code,broker_code);continue
                raise
            except (URLError,TimeoutError,ConnectionError) as exc:
                annotate(exc,attempt)
                if can_retry and attempt<attempts:
                    wait(attempt);continue
                raise
            except Exception as exc:
                annotate(exc,attempt)
                raise
            if not isinstance(body,dict) or str(body.get('rt_cd'))!='0':
                broker_code=safe_kis_error_code(body)
                if can_retry and broker_code=='EGW00201' and attempt<attempts:
                    wait(attempt,code=broker_code);continue
                exc=TradingBlocked('BROKER_RESPONSE_REJECTED')
                annotate(exc,attempt,broker_code)
                raise exc
            if attempt>1:
                print(json.dumps({'event':'KIS_READ_RECOVERED','broker_api':api,'attempts':attempt}),flush=True)
            return continuation,body

    def snapshot(self):
        from decimal import Decimal, ROUND_CEILING
        from oneil_v519_broker_recovery import exact_int
        from kis_safety_v3 import unfilled_codes
        base = {'CANO':self.account, 'ACNT_PRDT_CD':self.product, 'AFHR_FLPR_YN':'N',
                'OFL_YN':'', 'INQR_DVSN':'00', 'UNPR_DVSN':'01', 'FUND_STTL_ICLD_YN':'N',
                'FNCG_AMT_AUTO_RDPT_YN':'N', 'PRCS_DVSN':'00'}
        positions = {}; totals = []; fk = nk = ''; seen = set()
        for page in range(10):
            cont, body = self.request('/uapi/domestic-stock/v1/trading/inquire-balance',
                'TTTC8434R', {**base, 'CTX_AREA_FK100':fk, 'CTX_AREA_NK100':nk}, continuing=page>0)
            if cont is None or cont.strip().upper() not in ('','D','E','F','M'):
                raise TradingBlocked('BALANCE_PAGINATION_INVALID')
            summaries = body.get('output2'); rows = body.get('output1')
            if not isinstance(summaries, list) or len(summaries)!=1 or not isinstance(rows, list):
                raise TradingBlocked('BALANCE_INCOMPLETE')
            s = summaries[0]
            totals.append((exact_int(s.get('dnca_tot_amt')), exact_int(s.get('scts_evlu_amt')),exact_int(s.get('tot_evlu_amt'))))
            for r in rows:
                qty = exact_int(r.get('hldg_qty'))
                if not qty:
                    continue
                ticker = r.get('pdno')
                if not isinstance(ticker,str) or not CODE.fullmatch(ticker) or ticker in positions:
                    raise TradingBlocked('HOLDING_CONFLICT')
                avg = Decimal(r['pchs_avg_pric'])
                if not avg.is_finite() or avg <= 0:
                    raise TradingBlocked('COST_INVALID')
                positions[ticker] = {'quantity':qty, 'sellable':exact_int(r.get('ord_psbl_qty')),
                    'name':str(r.get('prdt_name') or ticker), 'average':str(avg), 'cost':int((avg*qty).to_integral_value(rounding=ROUND_CEILING)),
                    'valuation':exact_int(r.get('evlu_amt')),'technical':self.technical_signal(ticker)}
            if cont.strip().upper() not in ('F','M'):
                break
            fk, nk = str(body.get('ctx_area_fk100') or '').strip(), str(body.get('ctx_area_nk100') or '').strip()
            if not fk or not nk or (fk,nk) in seen:
                raise TradingBlocked('BALANCE_CURSOR_INVALID')
            seen.add((fk,nk))
        else:
            raise TradingBlocked('BALANCE_PAGE_LIMIT')
        if len(set(totals)) != 1:
            raise TradingBlocked('BALANCE_CHANGED_DURING_PAGING')
        cash, valuation, equity = totals[0]
        # Cost and market exposure are both counted conservatively.
        invested = max(valuation, sum(max(p['cost'],p['valuation']) for p in positions.values()))
        with read_stage('UNFILLED_ORDERS'):
            pending = unfilled_codes(self.key,self.secret,self.account,self.product,self.access)
        return {'cash':cash,'invested':invested,'positions':positions,'pending':pending,
                'equity':equity,'valuation':valuation}

    def technical_signal(self,ticker):
        with read_stage('CONFIRMED_TECHNICAL_HISTORY'):
            return self._technical_signal(ticker)

    def _technical_signal(self,ticker):
        # Only confirmed OHLC bars; missing data never disables the existing
        # percentage exits and never creates a guessed technical exit.
        try:
            import csv
            from build_data import confirmed_session
            from oneil_live_v5_9 import technical_timing
            import exchange_calendars as xcals
            now=dt.datetime.now(ZoneInfo('Asia/Seoul'));day=confirmed_session(now)
            with open(Path(__file__).resolve().parent/'data/kis_daily_universe.csv',encoding='utf-8-sig',newline='') as f:
                reader=csv.DictReader(f)
                if not {'high','low','close'}.issubset(reader.fieldnames or []):return None
                rows=[r for r in reader if r['ticker'].zfill(6)==ticker and dt.date.fromisoformat(r['date'])<=day]
            rows.sort(key=lambda r:r['date']);rows=rows[-78:]
            if len(rows)!=78:return None
            dates=[r['date'] for r in rows]
            cal=xcals.get_calendar('XKRX');sessions=cal.sessions_in_range(dates[0],day.isoformat())
            if dates!=[s.date().isoformat() for s in sessions]:return None
            t=technical_timing(rows);t['signal_date']=day.isoformat();t['basis']='CONFIRMED_DAILY_OHLC'
            return t
        except (OSError,ValueError,TypeError,KeyError,ImportError):return None

    def history(self, day):
        from oneil_v519_history_observe import fetch_page
        from oneil_v519_broker_recovery import read_full_day
        if (dt.date.today()-dt.datetime.strptime(day,'%Y%m%d').date()).days > 85:
            raise TradingBlocked('HISTORY_TOO_OLD_MANUAL_RECONCILIATION')
        if day not in self.history_cache:
            with read_stage('ORDER_HISTORY'):
                self.history_cache[day]=read_full_day(lambda date,fk,nk,again: fetch_page(self.key,self.secret,
                    self.account,self.product,self.access,date,fk,nk,again), day)
        return self.history_cache[day]

    def quote(self, ticker, side):
        from oneil_v519_live_runner import price_reference
        if not self.execution:
            return self.reference_quote(ticker)
        return self.execution_quote(ticker,side)

    def execution_quote(self,ticker,side):
        from oneil_v519_broker_recovery import exact_int
        now=dt.datetime.now(ZoneInfo('Asia/Seoul'))
        if now.weekday()>=5 or not dt.time(9)<=now.time().replace(tzinfo=None)<dt.time(15,15):
            raise TradingBlocked('OUTSIDE_KRX_CONTINUOUS_SESSION')
        # A current daily bar and a fresh KRX book are required together. Any
        # missing field or delayed/closed-market response blocks an order.
        _,daily=self.request('/uapi/domestic-stock/v1/quotations/inquire-daily-price',
            'FHKST01010400',{'FID_COND_MRKT_DIV_CODE':'J','FID_INPUT_ISCD':ticker,
                           'FID_PERIOD_DIV_CODE':'D','FID_ORG_ADJ_PRC':'0'})
        bars=daily.get('output')
        if not isinstance(bars,list) or not bars or bars[0].get('stck_bsop_date')!=now.strftime('%Y%m%d'):
            raise TradingBlocked('EXCHANGE_DAY_NOT_CONFIRMED')
        _,book=self.request('/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn',
            'FHKST01010200',{'FID_COND_MRKT_DIV_CODE':'J','FID_INPUT_ISCD':ticker})
        b=book.get('output1')
        if not isinstance(b,dict) or side not in ('BUY','SELL'):
            raise TradingBlocked('BOOK_INVALID')
        raw=b.get('aspr_acpt_hour','')
        if not re.fullmatch('[0-9]{6}',raw):
            raise TradingBlocked('BOOK_TIME_MISSING')
        at=dt.datetime.combine(now.date(),dt.datetime.strptime(raw,'%H%M%S').time(),now.tzinfo)
        end=dt.datetime.now(now.tzinfo)
        ask=exact_int(b.get('askp1')); bid=exact_int(b.get('bidp1'))
        if not 0<=(end-at).total_seconds()<=3 or not 0<bid<=ask or ask*1000>bid*1010:
            raise TradingBlocked('STALE_OR_WIDE_BOOK')
        price=ask if side=='BUY' else bid
        size=exact_int(b.get('askp_rsqn1' if side=='BUY' else 'bidp_rsqn1'))
        if size<1:
            raise TradingBlocked('EMPTY_BOOK')
        self.quote_evidence[(ticker,side)]={'price':price,'size':size,'at':at}
        return price

    def power(self, ticker, price):
        from oneil_v519_live_runner import buying_power
        with read_stage('BUYING_POWER'):
            return buying_power(self.access,self.key,self.secret,self.account,self.product,ticker,price)

    def buy_control(self):
        return read_buy_control()

    def reference_quote(self,ticker):
        from oneil_v519_live_runner import price_reference
        with read_stage('REFERENCE_QUOTE'):
            return price_reference(self.access,self.key,self.secret,ticker)

    def submit(self, plan):
        require_live_release()
        # Price evidence is obtained by the broker reader; no CLI attestation.
        evidence=self.quote_evidence.get((plan.ticker,plan.side))
        now=dt.datetime.now(ZoneInfo('Asia/Seoul'))
        if (not self.execution or not evidence or evidence['price']!=plan.price or
                not 0<=(now-evidence['at']).total_seconds()<=3):
            raise OrderNotSent('FRESH_EXECUTABLE_BOOK_REQUIRED')
        if plan.quantity>evidence['size']:
            raise OrderNotSent('BOOK_SIZE_EXCEEDED')
        return self._send_verified(plan)

    def _send_verified(self, plan):
        """Actual wire mapping; private and release-locked even when called directly."""
        require_live_release()
        if plan.side=='SELL' and not sell_plan_allowed(plan,self.buy_control()):
            raise SellDisabledBeforeSend('SELL_CONTROL_OFF_OR_UNAVAILABLE')
        ev=self.quote_evidence.get((plan.ticker,plan.side))
        if (not self.execution or not ev or ev['price']!=plan.price or
                not 0<=(dt.datetime.now(ev['at'].tzinfo)-ev['at']).total_seconds()<=3 or
                plan.quantity>ev['size']):
            raise OrderNotSent('EXECUTABLE_BOOK_REQUIRED')
        prepared = KisCashOrderAdapter(None).prepare(side=plan.side,ticker=plan.ticker,
            qty=plan.quantity,limit_price_krw=plan.price,cano=self.account,product=self.product)
        prepared['body'].update(EXCG_ID_DVSN_CD='KRX',
            SLL_TYPE='01' if plan.side=='SELL' else '',CNDT_PRIC='')
        try:
            _, response = self.request(prepared['path'],prepared['tr_id'],prepared['body'],post=True)
        except OrderNotSent as exc:
            # Once transport is entered this marker cannot certify absence of POST.
            raise TradingBlocked('ORDER_TRANSPORT_OUTCOME_UNCERTAIN') from exc
        return response


def broker_fill_state(row, quantity):
    """Validate KIS quantities before any durable state transition."""
    from oneil_v519_broker_recovery import exact_int
    filled=exact_int(row.get('tot_ccld_qty')); remaining=exact_int(row.get('rmn_qty'))
    if type(quantity) is not int or quantity<=0 or filled>quantity or filled+remaining>quantity:
        raise TradingBlocked('BROKER_FILL_CONFLICT')
    if filled==quantity and remaining==0:
        return 'FILLED',filled
    if row.get('cncl_yn')=='Y' and remaining==0:
        return 'CANCELLED',filled
    if 0<filled<quantity:
        return 'PART_FILLED',filled
    return 'ACKNOWLEDGED',filled


def audit_broker_evidence(broker, candidates, *, now, lookback_days=5):
    """READ ONLY operating evidence. Empty history cannot prove fill recovery.

    Shares the runtime's broker readers and fill parser. No reserve/submit/halt
    operation or order-ID/amount output exists in this path.
    """
    from oneil_v519_broker_recovery import exact_int
    if now.tzinfo is None or type(lookback_days) is not int or not 1<=lookback_days<=10:
        raise TradingBlocked('AUDIT_PARAMETERS_INVALID')
    now=now.astimezone(ZoneInfo('Asia/Seoul'))
    result={'status':'BROKER_EVIDENCE_AUDITED','gate':'DENY','orders_sent':0,
        'order_attempts':0,'actual_order_submission_verified':False,
        'restart_recovery_verified':False,'history_days_verified':0,'history_rows_verified':0,
        'fill_states_observed':{s:0 for s in ('ACKNOWLEDGED','PART_FILLED','FILLED','CANCELLED')},
        'account_read_verified':False,'fresh_book_verified':False,'buying_power_read_verified':False,
        'errors':[]}
    snapshot=broker.snapshot();result['account_read_verified']=True
    for offset in range(lookback_days):
        day=(now.date()-dt.timedelta(days=offset)).strftime('%Y%m%d')
        try:
            rows=broker.history(day)
            if not isinstance(rows,list):raise TradingBlocked('BROKER_HISTORY_INCOMPLETE')
            seen=set();counts={s:0 for s in result['fill_states_observed']}
            for row in rows:
                if not isinstance(row,dict):raise TradingBlocked('BROKER_HISTORY_ROW_INVALID')
                ident=row.get('odno')
                if (not isinstance(ident,str) or not re.fullmatch('[0-9]{1,20}',ident.strip()) or
                        ident.strip() in seen or row.get('ord_dt')!=day or
                        not isinstance(row.get('pdno'),str) or not CODE.fullmatch(row['pdno']) or
                        row.get('sll_buy_dvsn_cd') not in ('01','02')):
                    raise TradingBlocked('BROKER_HISTORY_IDENTITY_INVALID')
                seen.add(ident.strip())
                state,_=broker_fill_state(row,exact_int(row.get('ord_qty')))
                counts[state]+=1
            result['history_days_verified']+=1;result['history_rows_verified']+=len(rows)
            for state,n in counts.items():result['fill_states_observed'][state]+=n
        except Exception:
            result['errors'].append('HISTORY_RESPONSE_INVALID')
    result['order_fill_evidence']='REAL_ROWS_OBSERVED' if result['history_rows_verified'] else 'NO_REAL_ROWS'
    ticker=next((c.get('ticker') for c in candidates if isinstance(c,dict) and
        isinstance(c.get('ticker'),str) and CODE.fullmatch(c['ticker'])),None)
    ticker=ticker or next(iter(snapshot['positions']),None)
    if ticker:
        try:
            price=broker.execution_quote(ticker,'BUY')
            broker.execution_quote(ticker,'SELL')
            result['fresh_book_verified']=True
            amount,qty=broker.power(ticker,price);money(amount);money(qty)
            result['buying_power_read_verified']=True
        except Exception as exc:
            result['errors'].append(str(exc) if isinstance(exc,TradingBlocked) else 'QUOTE_OR_POWER_READ_FAILED')
    else:
        result['errors'].append('NO_AUDIT_INSTRUMENT')
    if result['errors']:result['status']='BLOCKED_BROKER_EVIDENCE'
    return result


def recover_pending(ledger, broker):
    """Exact account/day/ID/side/ticker/size match; unknown submissions stay locked.

    No fuzzy matching after a lost acknowledgement: KIS has no client idempotency
    key, so automatically guessing a matching order could unlock a duplicate.
    """
    from oneil_v519_broker_recovery import exact_int
    cache = {}; matched = 0
    for order in ledger.pending():
        if not order['broker_order_id'] or not order['trading_day']:
            continue
        day = order['trading_day'].strftime('%Y%m%d')
        if day not in cache:
            cache[day] = broker.history(day)
        rows = [r for r in cache[day] if str(r.get('odno','')).strip()==order['broker_order_id']
                and r.get('ord_dt')==day]
        if len(rows)!=1:
            continue
        r=rows[0]
        if (r.get('pdno')!=order['ticker'] or r.get('sll_buy_dvsn_cd')!=
                ('02' if order['side']=='BUY' else '01') or
                exact_int(r.get('ord_qty'))!=order['quantity']):
            raise TradingBlocked('BROKER_ORDER_IDENTITY_CONFLICT')
        state,filled=broker_fill_state(r,order['quantity'])
        ledger.reconcile(order, state, filled)
        matched+=1
    return {'matched':matched,'unresolved':len(ledger.pending())}


def risk_policy(control):
    """Missing/invalid risk settings disable new buys without stopping exits."""
    default={'connected':False,'reserve_pct':40,'per_stock_pct':30,'revision':None,'updated_at':None}
    risk=control.get('risk')
    if (not isinstance(risk,dict) or risk.get('connected') is not True or
            any(type(risk.get(k)) is not int or not 0<=risk[k]<=100 for k in ('reserve_pct','per_stock_pct')) or
            type(risk.get('revision')) is not int or not 0<=risk['revision']<2**53-1):
        return default
    return {k:risk.get(k) for k in default}


def same_risk(left,right):
    return right['connected'] and all(left[k]==right[k] for k in ('revision','reserve_pct','per_stock_pct'))


def sell_policy(control):
    value=control.get('sell') if isinstance(control,dict) else None
    if (not isinstance(value,dict) or value.get('connected') is not True or type(value.get('enabled')) is not bool
            or type(value.get('revision')) is not int or not 0<=value['revision']<2**53-1):
        return {'enabled':False,'connected':False,'revision':None,'reason':'SELL_CONTROL_UNAVAILABLE'}
    return {**value,'reason':'OWNER_SETTING','scope':'STOP_LOSS_ONLY','take_profit_enabled':True}


def sell_plan_allowed(plan,control):
    """The owner switch locks stop-loss only; take-profit remains automatic.

    Unknown control still fails closed. Session, halt, release and reconciliation
    checks remain independent prerequisites for every sell.
    """
    policy=sell_policy(control)
    return policy['connected'] and (policy['enabled'] or plan.reason in ('TAKE_PROFIT','TAKE_PROFIT_SIGNAL'))


def _automatic_cycle(ledger, broker, candidates, *, execute=False, now=None,
                     stop_loss_pct=5, take_profit_pct=10, clock=None, defer_for_manual=True):
    """Sequential candidate buys under one account lock and conservative cash caps.

    Each acknowledged buy must be fully reconciled before the next candidate.
    Distinct initial managed exits advance only after full fills and reflected
    balance reductions; residual quantities wait for a later run.
    Pending/unknown orders never gain an exception to the durable ledger guard.
    Enabled exits retain priority; explicit sell OFF records alerts and lets
    independently enabled buys continue. Sell proceeds are not
    funding for this batch. The initial cash budget can only shrink in this run.
    """
    clock = clock or (lambda: dt.datetime.now(ZoneInfo('Asia/Seoul')))
    now = now or clock()
    if now.tzinfo is None:
        raise TradingBlocked('CLOCK_INVALID')
    if weekend_closed(now):
        return market_closed_result()
    def order_time():
        current=clock()
        if current.tzinfo is None:raise TradingBlocked('CLOCK_INVALID')
        current=current.astimezone(ZoneInfo('Asia/Seoul'))
        if (current.date()!=now.date() or current.weekday()>=5 or
                not dt.time(9)<=current.time()<dt.time(15,15)):
            raise TradingBlocked('OUTSIDE_KRX_CONTINUOUS_SESSION')
        return current
    def account_snapshot():
        snap=broker.snapshot()
        with read_stage('LEDGER_HOLDINGS'):
            snap['managed']=ledger.managed()
            snap['bought_today']=ledger.bought_today(now.date())
        return snap
    with ledger.lock():
        today_history=broker.history(now.strftime('%Y%m%d'))
        if not isinstance(today_history,list):
            raise TradingBlocked('BROKER_HISTORY_INCOMPLETE')
        recovery = recover_pending(ledger, broker)
        result = {'status':'OBSERVED','gate':'DENY','orders_sent':0,'order_attempts':0,
                  'recovery':recovery,'candidate_count':len(candidates),'planned_buys':0,'planned_sells':0,
                  'live_release_approved':release_is_approved(),'execution_requested':execute,
                  'broker_history_read_verified':True,'history_rows_observed':len(today_history)}
        if recovery['unresolved']:
            result['status']='BLOCKED_UNRESOLVED_ORDERS'
            return result
        if execute and not ledger.release_authorized(release_fingerprint()):
            raise TradingBlocked('ACCOUNT_RELEASE_NOT_ACTIVATED')
        control=broker.buy_control() if callable(getattr(broker,'buy_control',None)) else {'enabled':False,'connected':False,'revision':None}
        risk=risk_policy(control)
        sell=sell_policy(control)
        if not risk['connected']:control={**control,'enabled':False,'reason':'RISK_CONTROL_INVALID'}
        snapshot=account_snapshot()
        observed_exits=set()
        def select_plans(rows,snap,current_control,max_buys):
            current_sell=sell_policy(current_control)
            # OFF still observes exits using a reference quote, never order evidence.
            def planning_quote(ticker,side):
                if side=='SELL' and not current_sell['enabled'] and isinstance(broker,KisBroker):
                    return broker.reference_quote(ticker)
                return broker.quote(ticker,side)
            kwargs=dict(stop_loss_pct=stop_loss_pct,take_profit_pct=take_profit_pct,
                max_buys=max_buys,reserve_pct=risk['reserve_pct'],per_stock_pct=risk['per_stock_pct'])
            eligible=rows if current_control.get('enabled') else []
            selected,budget=plan_orders(eligible,snap,planning_quote,broker.power,**kwargs)
            exits=[p for p in selected if p.side=='SELL']
            blocked=[p for p in exits if not sell_plan_allowed(p,current_control)]
            if blocked and current_sell['connected']:
                ledger.record_sell_alerts(blocked,now.date())
                observed_exits.update((p.ticker,p.reason) for p in blocked)
                result['sell_alert_count']=len(observed_exits)
                # Keep EVERY holding in exposure and duplicate checks. Only skip
                # creating sell plans; no hypothetical proceeds increase the budget.
                enabled_exits=[p for p in exits if sell_plan_allowed(p,current_control)]
                if enabled_exits:selected=enabled_exits
                else:selected,budget=plan_orders(eligible,snap,broker.quote,broker.power,include_exits=False,**kwargs)
                if not result['orders_sent'] and not any(p.side=='SELL' for p in selected):result['status']='SELL_ALERT_ONLY'
            return selected,budget
        # Check owner intent before reading unrelated automatic-buy quotes.
        # Exit planning still runs first, with the same holdings and controls.
        manual_pending=False
        if execute and defer_for_manual:
            for request in control.get('manual_buys',[]):
                try:
                    manual_buy_request(request,now=clock(),fingerprint=ledger.fingerprint,release=release_fingerprint())
                    manual_pending=True
                    break
                except TradingBlocked:
                    continue
        # Read-only runs preview the next buy, just as execution does. Scanning
        # every later candidate here delayed account publication/activation.
        with read_stage('NEXT_PLAN'):
            plans, allocation = select_plans([] if manual_pending else candidates,snapshot,control,1)
        ledger.dashboard_state={'snapshot':snapshot,'plans':plans,'candidates':candidates,'allocation':allocation,'at':now,'buy_control':control,'risk_control':risk,'sell_control':sell}
        result['planned_buys']=sum(p.side=='BUY' for p in plans)
        result['planned_sells']=sum(p.side=='SELL' for p in plans)
        def alert_only(exit_plans,latest):
            exits=[p for p in exit_plans if p.side=='SELL']
            ledger.record_sell_alerts(exits,now.date())
            ledger.dashboard_state['sell_control']=latest
            ledger.dashboard_state['plans']=[]
            result.update(status='ACKNOWLEDGED' if result['orders_sent'] else ('SELL_ALERT_ONLY' if latest['connected'] else 'SELL_CONTROL_UNAVAILABLE'),
                sell_alert_count=len(exits),planned_buys=0,planned_sells=0)
            if result['orders_sent']:result['batch_stop_reason']='SELL_CONTROL_OFF_OR_UNAVAILABLE'
            return result
        if result['planned_sells'] and not sell['connected']:
            return alert_only(plans,sell)  # Unknown control retains the safety block.
        if not execute:
            return result
        if manual_pending and not result['planned_sells']:
            result.update(status='MANUAL_QUEUE_PENDING',planned_buys=0)
            ledger.dashboard_state['plans']=[]
            return result
        initial_remaining=allocation['remaining'];initial_stock_cap=allocation['per_stock']
        spent=0; attempted=set();display_plans=[];uncertain=False
        exit_tickers={p.ticker for p in plans if p.side=='SELL'}
        try:
            # At most one attempt per candidate or initially eligible exit ticker.
            # Sell throughput must not depend on the number of buy candidates.
            for _ in range(max(1,len(candidates)+len(exit_tickers))):
                order_time()
                if ledger.halted():raise TradingBlocked('EMERGENCY_HALT_ACTIVE')
                if not ledger.release_authorized(release_fingerprint()):
                    raise TradingBlocked('ACCOUNT_RELEASE_NOT_ACTIVATED')
                if not plans:
                    if not result['orders_sent'] and not result.get('sell_alert_count'):result['status']='NO_ORDER'
                    result['batch_stop_reason']='NO_MORE_ELIGIBLE_CANDIDATES_OR_CASH'
                    return result
                plan=plans[0]
                if plan.side=='SELL':
                    latest_control=broker.buy_control()
                    latest_sell=sell_policy(latest_control)
                    ledger.dashboard_state['sell_control']=latest_sell
                    if not sell_plan_allowed(plan,latest_control):
                        if not latest_sell['connected']:return alert_only(plans,latest_sell)
                        plans,allocation=select_plans(
                            [] if manual_pending or display_plans else [c for c in candidates if c['ticker'] not in attempted],snapshot,latest_control,1)
                        plans=[p for p in plans if (p.side=='SELL' and p.ticker in exit_tickers-attempted) or
                            (p.side=='BUY' and not display_plans)]
                        ledger.dashboard_state.update(plans=display_plans+plans,allocation=allocation)
                        result['planned_sells']=sum(p.side=='SELL' for p in plans)
                        result['planned_buys']=sum(p.side=='BUY' for p in plans)
                        if not plans:
                            if manual_pending and not display_plans:result['status']='MANUAL_QUEUE_PENDING'
                            return result
                        plan=plans[0]
                if plan.side=='BUY' and any(p.side=='SELL' for p in display_plans):
                    result['batch_stop_reason']='EXIT_PRIORITY'
                    return result
                if plan.ticker in attempted:raise TradingBlocked('BATCH_DUPLICATE_CANDIDATE')
                attempted.add(plan.ticker)
                previous_quantity=snapshot['positions'].get(plan.ticker,{}).get('quantity',0)
                fresh=plan.price;quantity=plan.quantity;fresh_reason=plan.reason
                if isinstance(broker,KisBroker):
                    fresh=broker.execution_quote(plan.ticker,plan.side)
                    evidence=broker.quote_evidence[(plan.ticker,plan.side)]
                    quantity=min(quantity,evidence['size'])
                if plan.side=='BUY':
                    amount,broker_qty=broker.power(plan.ticker,fresh)
                    money(amount);money(broker_qty)
                    cap=min(initial_remaining-spent,allocation['remaining'],
                            initial_stock_cap,allocation['per_stock'],amount)
                    quantity=min(quantity,broker_qty,max(0,cap)*1000//(fresh*1005))
                elif isinstance(broker,KisBroker):
                    from decimal import Decimal
                    average=Decimal(str(snapshot['positions'][plan.ticker]['average']))
                    if fresh*100<=average*(100-stop_loss_pct):fresh_reason='STOP_LOSS'
                    elif fresh*100>=average*(100+take_profit_pct):fresh_reason='TAKE_PROFIT'
                    elif isinstance(snapshot['positions'][plan.ticker].get('technical'),dict) and snapshot['positions'][plan.ticker]['technical'].get('exit') is True:
                        fresh_reason='TAKE_PROFIT_SIGNAL' if fresh>average else 'STOP_LOSS_SIGNAL'
                    else:quantity=0
                if quantity<1:
                    if not result['orders_sent']:result['status']='QUOTE_CHANGED_NO_ORDER'
                    ledger.dashboard_state['plans']=display_plans
                    if plan.side=='SELL':return result
                else:
                    if plan.side=='BUY':
                        latest=broker.buy_control()
                        ledger.dashboard_state['buy_control']=latest
                        if not same_risk(risk,risk_policy(latest)):
                            if not result['orders_sent']:result['status']='RISK_SETTINGS_CHANGED'
                            result['batch_stop_reason']='RISK_SETTINGS_CHANGED'
                            ledger.dashboard_state['plans']=display_plans
                            return result
                        if not latest.get('enabled'):
                            if not result['orders_sent']:
                                result.update(status='BUY_SWITCH_OFF',planned_buys=0)
                            result['batch_stop_reason']='BUY_SWITCH_OFF'
                            ledger.dashboard_state['plans']=display_plans
                            return result
                    plan=OrderPlan(plan.ticker,plan.side,quantity,fresh,plan.signal,fresh_reason)
                    ledger.dashboard_state['plans']=display_plans+[plan]
                    order_time()
                    ident=ledger.reserve_plan(plan,now.date())  # commit BEFORE POST
                    result['order_attempts']+=1
                    submission_stage='SEND_GUARD'
                    try:
                        cancelled=False
                        with ledger.send_guard():
                            submission_stage='BEFORE_SUBMIT'
                            order_time()  # stop a batch that crosses the session boundary
                            # Real KIS does its final read inside the wire adapter;
                            # fixtures/other adapters are checked at the same boundary.
                            try:
                                if plan.side=='SELL' and not isinstance(broker,KisBroker) and not sell_plan_allowed(plan,broker.buy_control()):
                                    raise SellDisabledBeforeSend('SELL_CONTROL_OFF_OR_UNAVAILABLE')
                                uncertain=True
                                submission_stage='SUBMIT'
                                response=broker.submit(plan)
                            except SellDisabledBeforeSend:
                                uncertain=False
                                ledger.void_unsent_sell(ident)
                                cancelled=True
                        if cancelled:
                            result['pre_send_cancelled']=True
                            return alert_only([plan],sell_policy(broker.buy_control()))
                        submission_stage='ACK_VALIDATE'
                        out=response.get('output') if isinstance(response,dict) else None
                        order_id=out.get('ODNO') if isinstance(out,dict) else None
                        if str(response.get('rt_cd'))!='0' or not isinstance(order_id,str) or not re.fullmatch('[0-9]{1,20}',order_id):
                            raise TradingBlocked('BROKER_ACK_INVALID')
                        submission_stage='LEDGER_ACK'
                        ledger.ack(ident,order_id)
                        uncertain=False
                        result['orders_sent']+=1
                        result.update(status='ACKNOWLEDGED',gate='ALLOW')
                        display_plans.append(plan)
                    except Exception as exc:
                        if isinstance(exc,OrderNotSent):
                            uncertain=False
                            # This type originates only before the adapter enters POST.
                            # Keep the durable reservation/daily key; never retry here.
                            try:
                                ledger.void_unsent_order(ident)
                            except Exception:
                                ledger.halt()
                                raise
                            uncertain=False
                            if not result['orders_sent']:result['status']='PRE_SUBMIT_REJECTED'
                            result.update(batch_stop_reason=str(exc),pre_send_cancelled=True)
                            ledger.dashboard_state['plans']=display_plans+[plan]
                            return result
                        ledger.halt()
                        blocked=TradingBlocked('SUBMISSION_UNCERTAIN_NO_RETRY')
                        blocked._submission_exception=exc
                        blocked.submission_stage=submission_stage
                        raise blocked from None
                    if plan.side=='BUY':
                        spent+=(plan.quantity*plan.price*1005+999)//1000
                    # Read after submission even for a single order, so the private
                    # dashboard no longer always publishes the pre-order balance.
                    if isinstance(broker,KisBroker):
                        # The initial history read predates this POST. Never use
                        # its cached rows as evidence about the new order.
                        broker.history_cache.pop(now.strftime('%Y%m%d'),None)
                    checked=recover_pending(ledger,broker)
                    result['recovery']['matched']+=checked['matched']
                    result['recovery']['unresolved']=checked['unresolved']
                    snapshot=account_snapshot()
                    from v519_cash_policy import limits
                    refreshed=limits(snapshot['cash'],snapshot['invested'],snapshot['cash'],
                        reserve_pct=risk['reserve_pct'],per_stock_pct=risk['per_stock_pct'])
                    ledger.dashboard_state.update(snapshot=snapshot,allocation=refreshed,at=clock())
                    if checked['unresolved'] or snapshot['pending']:
                        result['batch_stop_reason']='PENDING_FILL_CONFIRMATION'
                        return result
                    # The broker must expose the confirmed filled quantity in the
                    # refreshed balance before that balance can fund another buy.
                    if plan.side=='BUY' and snapshot['positions'].get(plan.ticker,{}).get('quantity',0)<plan.quantity:
                        result['batch_stop_reason']='BALANCE_UPDATE_PENDING'
                        return result
                    if plan.side=='SELL':
                        rows=ledger.rows('SELECT status,filled_quantity FROM v519_order_intents WHERE account_fingerprint=%s AND intent_id=%s',
                            (ledger.fingerprint,ident))
                        if len(rows)!=1 or rows[0]['status']!='FILLED' or rows[0]['filled_quantity']!=plan.quantity:
                            result['batch_stop_reason']='EXIT_FILL_NOT_CONFIRMED'
                            return result
                        remaining_quantity=snapshot['positions'].get(plan.ticker,{}).get('quantity',0)
                        if remaining_quantity>previous_quantity-plan.quantity:
                            result['batch_stop_reason']='BALANCE_UPDATE_PENDING'
                            return result
                        if not exit_tickers-attempted:
                            result['batch_stop_reason']='EXIT_BATCH_COMPLETED'
                            return result
                        # Full fill and balance reduction are prerequisites, not
                        # proceeds available for buys. Keep every holding in the
                        # snapshot, but only consider the original distinct exits.
                        control=broker.buy_control()
                        ledger.dashboard_state.update(buy_control=control,sell_control=sell_policy(control))
                        if not sell_policy(control)['connected']:
                            result['batch_stop_reason']='SELL_CONTROL_UNAVAILABLE'
                            return result
                        plans,allocation=select_plans([],snapshot,control,1)
                        plans=[p for p in plans if p.side=='SELL' and p.ticker in exit_tickers-attempted]
                        ledger.dashboard_state.update(plans=display_plans+plans,allocation=allocation)
                        if not plans:
                            result['batch_stop_reason']='EXIT_BATCH_NO_MORE_ELIGIBLE'
                            return result
                        continue
                remaining_candidates=[c for c in candidates if c['ticker'] not in attempted]
                if not remaining_candidates:
                    result['batch_stop_reason']='CANDIDATES_EVALUATED'
                    return result
                # Refresh user control, held exposure, quote and buying power for
                # each successive buy; never reuse expected proceeds or budget.
                control=broker.buy_control()
                ledger.dashboard_state['buy_control']=control
                if not same_risk(risk,risk_policy(control)):
                    result['batch_stop_reason']='RISK_SETTINGS_CHANGED'
                    return result
                if not control.get('enabled'):
                    result['batch_stop_reason']='BUY_SWITCH_OFF'
                    return result
                ledger.dashboard_state['sell_control']=sell_policy(control)
                plans,allocation=select_plans(remaining_candidates,snapshot,control,1)
                # Enabled/unknown exits stop buys; explicit OFF alerts do not.
                if plans and plans[0].side=='SELL':
                    latest_sell=sell_policy(control)
                    if not sell_plan_allowed(plans[0],control):return alert_only(plans,latest_sell)
                    result['batch_stop_reason']='EXIT_DETECTED'
                    return result
                result['planned_buys']+=sum(p.side=='BUY' for p in plans)
                ledger.dashboard_state.update(allocation=allocation,plans=display_plans+plans)
            return result
        except Exception as exc:
            # Keep known acknowledgements when a later batch step fails. A lost
            # acknowledgement leaves total sends unknown, never reported as zero.
            exc.execution_progress={'orders_sent':None if uncertain else result['orders_sent'],
                'confirmed_orders_sent':result['orders_sent'],'order_attempts':result['order_attempts']}
            raise


def manual_buy_request(value, *, now, fingerprint, release):
    """Private owner queue; fail closed on changed account/release or expired intent."""
    if not isinstance(value,dict):raise TradingBlocked('MANUAL_REQUEST_INVALID')
    fields=('quantity','limit_price','holding_quantity','buy_revision','risk_revision')
    if any(type(value.get(k)) is not int or value[k] < (0 if k.endswith('revision') else 1) or value[k]>2147483647 for k in fields):
        raise TradingBlocked('MANUAL_REQUEST_INVALID')
    if (not re.fullmatch(r'[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}',str(value.get('id',''))) or
            not CODE.fullmatch(str(value.get('ticker',''))) or value.get('account_ref')!=fingerprint or value.get('approved_release')!=release):
        raise TradingBlocked('MANUAL_REQUEST_IDENTITY_CHANGED')
    try:
        created=dt.datetime.fromisoformat(value['created_at'].replace('Z','+00:00'))
        expires=dt.datetime.fromisoformat(value['expires_at'].replace('Z','+00:00'))
        local=now.astimezone(ZoneInfo('Asia/Seoul'))
        if (created.tzinfo is None or expires.tzinfo is None or not created<=now<expires or
            not 0<(expires-created).total_seconds()<=2400 or value['trading_day']!=local.date().isoformat() or
            created.astimezone(local.tzinfo).date()!=local.date() or
            expires.astimezone(local.tzinfo).date()!=local.date() or
            expires.astimezone(local.tzinfo).time()>dt.time(15,15)):
            raise ValueError()
    except Exception:raise TradingBlocked('MANUAL_REQUEST_EXPIRED_OR_INVALID')
    return value


def manual_buy_plan(request,snapshot,allocation,price,power,bought_today):
    ticker=request['ticker'];pos=snapshot['positions'].get(ticker)
    if snapshot['pending']:raise TradingBlocked('BROKER_PENDING_ORDERS')
    if ticker in bought_today:raise TradingBlocked('ALREADY_BOUGHT_TODAY')
    if not pos or pos['quantity']!=request['holding_quantity']:raise TradingBlocked('HOLDING_CHANGED')
    if type(price) is not int or not 0<price<=request['limit_price']:raise TradingBlocked('OWNER_LIMIT_PRICE_EXCEEDED')
    amount,broker_qty=power(ticker,price);money(amount);money(broker_qty)
    charge=(request['quantity']*price*1005+999)//1000
    exposure=max(money(pos['cost']),money(pos['valuation']))
    cap=min(allocation['remaining'],max(0,allocation['per_stock']-exposure),amount)
    # Exact owner quantity. Never silently increase or shrink the requested order.
    if request['quantity']>broker_qty or charge>cap:
        exc=TradingBlocked('MANUAL_BUY_LIMIT_EXCEEDED')
        exc.manual_limit_reasons=[code for code,blocked in (
            ('BROKER_QUANTITY_LIMIT',request['quantity']>broker_qty),
            ('TOTAL_ALLOCATION_LIMIT',charge>allocation['remaining']),
            ('PER_STOCK_EXPOSURE_LIMIT',charge>max(0,allocation['per_stock']-exposure)),
            ('BROKER_BUYING_POWER_LIMIT',charge>amount)) if blocked]
        raise exc
    return OrderPlan(ticker,'BUY',request['quantity'],price,'owner:'+request['id'],'OWNER_AVERAGING')


def process_manual_buy(ledger,broker,requests,*,now,clock):
    from v519_cash_policy import limits
    def time_check():
        current=clock().astimezone(ZoneInfo('Asia/Seoul'))
        if current.date()!=now.date() or current.weekday()>=5 or not dt.time(9)<=current.time()<dt.time(15,15):
            raise TradingBlocked('OUTSIDE_KRX_CONTINUOUS_SESSION')
        return current
    with ledger.lock():
        current=time_check();require_live_release()
        if ledger.halted() or not ledger.release_authorized(release_fingerprint()):raise TradingBlocked('MANUAL_ACCOUNT_NOT_READY')
        recovery=recover_pending(ledger,broker)
        if recovery['unresolved']:raise TradingBlocked('UNRESOLVED_ORDERS')
        control=broker.buy_control();risk=risk_policy(control)
        if not control.get('connected') or not control.get('enabled') or not risk['connected'] or (control.get('release') or {}).get('approved_release')!=release_fingerprint():raise TradingBlocked('MANUAL_BUY_CONTROL_UNAVAILABLE')
        live=control.get('manual_buys',[])
        request=next((r for r in requests if any(x.get('id')==r.get('id') for x in live if isinstance(x,dict))),None)
        request=manual_buy_request(request,now=current,fingerprint=ledger.fingerprint,release=release_fingerprint())
        if control['revision']!=request['buy_revision'] or risk['revision']!=request['risk_revision']:raise TradingBlocked('MANUAL_REQUEST_SETTINGS_CHANGED')
        if ledger.rows("SELECT intent_id FROM v519_order_intents WHERE account_fingerprint=%s AND signal=%s",(ledger.fingerprint,'owner:'+request['id'])):
            return {'status':'MANUAL_ALREADY_RECORDED','orders_sent':0,'order_attempts':0}
        snap=broker.snapshot();snap['managed']=ledger.managed();bought=ledger.bought_today(now.date())
        allocation=limits(snap['cash'],snap['invested'],snap['cash'],reserve_pct=risk['reserve_pct'],per_stock_pct=risk['per_stock_pct'])
        # Existing enabled or unknown exits retain priority over discretionary averaging.
        exits,_=plan_orders([],snap,broker.quote,broker.power,reserve_pct=risk['reserve_pct'],per_stock_pct=risk['per_stock_pct'])
        if any(sell_plan_allowed(p,control) or not sell_policy(control)['connected'] for p in exits):raise TradingBlocked('EXIT_PRIORITY')
        price=broker.execution_quote(request['ticker'],'BUY') if isinstance(broker,KisBroker) else broker.quote(request['ticker'],'BUY')
        plan=manual_buy_plan(request,snap,allocation,price,broker.power,bought)
        if isinstance(broker,KisBroker) and plan.quantity>broker.quote_evidence[(plan.ticker,'BUY')]['size']:raise TradingBlocked('BOOK_SIZE_EXCEEDED')
        latest=broker.buy_control()
        if (not latest.get('connected') or not latest.get('enabled') or latest.get('revision')!=request['buy_revision'] or
            not same_risk(risk,risk_policy(latest)) or (latest.get('release') or {}).get('approved_release')!=release_fingerprint() or not any(x==request for x in latest.get('manual_buys',[]))):raise TradingBlocked('MANUAL_SETTINGS_CHANGED')
        manual_buy_request(request,now=time_check(),fingerprint=ledger.fingerprint,release=release_fingerprint())
        if ledger.halted() or not ledger.release_authorized(release_fingerprint()):raise TradingBlocked('MANUAL_ACCOUNT_NOT_READY')
        # Durable daily/ticker key is unchanged. A second ID cannot evade the daily cap.
        ident=ledger.reserve_plan(plan,now.date());uncertain=False
        ledger.dashboard_state.update(snapshot=snap,allocation=allocation,plans=[plan],at=current)
        try:
            with ledger.send_guard():
                require_live_release()
                latest=broker.buy_control()
                if (not latest.get('connected') or not latest.get('enabled') or latest.get('revision')!=request['buy_revision'] or
                    not same_risk(risk,risk_policy(latest)) or (latest.get('release') or {}).get('approved_release')!=release_fingerprint() or
                    not any(x==request for x in latest.get('manual_buys',[]))):raise TradingBlocked('MANUAL_SETTINGS_CHANGED')
                if not ledger.release_authorized(release_fingerprint()):raise TradingBlocked('MANUAL_ACCOUNT_NOT_READY')
                manual_buy_request(request,now=time_check(),fingerprint=ledger.fingerprint,release=release_fingerprint())
                uncertain=True;response=broker.submit(plan)
                out=response.get('output') if isinstance(response,dict) else None
                order_id=out.get('ODNO') if isinstance(out,dict) else None
                if str(response.get('rt_cd'))!='0' or not isinstance(order_id,str) or not re.fullmatch('[0-9]{1,20}',order_id):raise TradingBlocked('BROKER_ACK_INVALID')
            ledger.ack(ident,order_id);uncertain=False
        except Exception as exc:
            if isinstance(exc,OrderNotSent):
                try:
                    ledger.void_unsent_order(ident)
                except Exception as void_error:
                    ledger.halt()
                    void_error.execution_progress={'orders_sent':0,'confirmed_orders_sent':0,'order_attempts':1}
                    raise
                return {'status':'MANUAL_PRE_SUBMIT_REJECTED','orders_sent':0,'order_attempts':1,
                    'gate':'DENY','manual_buy_reason':str(exc),'pre_send_cancelled':True}
            ledger.halt();exc.execution_progress={'orders_sent':None if uncertain else 0,'confirmed_orders_sent':0,'order_attempts':1};raise
        if isinstance(broker,KisBroker):broker.history_cache.pop(now.strftime('%Y%m%d'),None)
        # Acknowledgement is not a fill. Existing recovery owns every fill transition.
        try:
            recovery=recover_pending(ledger,broker);fresh=broker.snapshot();fresh['managed']=ledger.managed()
            ledger.dashboard_state.update(snapshot=fresh,at=clock())
        except Exception:
            return {'status':'ACKNOWLEDGED','orders_sent':1,'order_attempts':1,'gate':'ALLOW','manual_recovery':'CHECK_LEDGER'}
        return {'status':'ACKNOWLEDGED','orders_sent':1,'order_attempts':1,'gate':'ALLOW','recovery':recovery}


class ManualDiagnosticCheckpointError(RuntimeError):
    pass


def integrated_cycle(ledger,broker,candidates,*,execute=False,now=None,stop_loss_pct=5,take_profit_pct=10,clock=None):
    clock=clock or (lambda:dt.datetime.now(ZoneInfo('Asia/Seoul')));now=now or clock()
    result=_automatic_cycle(ledger,broker,candidates,execute=execute,now=now,stop_loss_pct=stop_loss_pct,take_profit_pct=take_profit_pct,clock=clock)
    if not execute or weekend_closed(now):return result
    state=getattr(ledger,'dashboard_state',None)
    requests=state.get('buy_control',{}).get('manual_buys',[]) if state else []
    if not execute or not requests or result.get('orders_sent') or result.get('planned_sells') or result.get('recovery',{}).get('unresolved'):return result
    checks=[]
    def record_check(check, *, progress=None, cause=None):
        # Private diagnostics only. Checkpoint before another request or fallback
        # can replace state or fail; never include this data in the public report.
        if isinstance(request,dict) and CODE.fullmatch(str(request.get('ticker',''))):
            check['ticker']=request['ticker']
        checks.append(check)
        ledger.dashboard_state['manual_buy_checks']=checks
        checkpoint=getattr(ledger,'manual_check_checkpoint',None)
        if callable(checkpoint):
            try:checkpoint(checks)
            except Exception:
                failed=cause or ManualDiagnosticCheckpointError('MANUAL_DIAGNOSTIC_CHECKPOINT_FAILED')
                failed.manual_diagnostic_checkpoint_failed=True
                if progress is not None:
                    failed.execution_progress={key:progress[key] for key in
                        ('orders_sent','confirmed_orders_sent','order_attempts') if key in progress}
                raise failed from None
    # Only request-local failures may advance the queue. Unknown exceptions,
    # unresolved orders, changed controls and account safety blocks stop the run.
    local_blocks={'MANUAL_REQUEST_INVALID','MANUAL_REQUEST_IDENTITY_CHANGED',
        'MANUAL_REQUEST_EXPIRED_OR_INVALID','HOLDING_CHANGED',
        'OWNER_LIMIT_PRICE_EXCEEDED','MANUAL_BUY_LIMIT_EXCEEDED',
        'ALREADY_BOUGHT_TODAY','BOOK_SIZE_EXCEEDED','MANUAL_REQUEST_SETTINGS_CHANGED'}
    for request in requests:
        request_id=request.get('id') if isinstance(request,dict) else None
        try:
            manual_buy_request(request,now=clock(),fingerprint=ledger.fingerprint,release=release_fingerprint())
            sent=process_manual_buy(ledger,broker,[request],now=now,clock=clock)
            record_check({'id':request_id,'status':sent['status'],
                **({'reason':sent['manual_buy_reason']} if 'manual_buy_reason' in sent else {})},progress=sent)
            state['manual_buy_checks']=checks
            if sent['status']=='MANUAL_ALREADY_RECORDED':continue
            result.update(sent)
            result['manual_buy_status']=sent['status']
            if 'manual_buy_reason' not in sent:result.pop('manual_buy_reason',None)
            return result  # Never send another order after an acknowledgement.
        except ManualDiagnosticCheckpointError:
            raise
        except TradingBlocked as exc:
            record_check({'id':request_id,'status':'BLOCKED','reason':str(exc),
                **({'limit_reasons':exc.manual_limit_reasons} if hasattr(exc,'manual_limit_reasons') else {})},
                progress=getattr(exc,'execution_progress',result),cause=exc)
            ledger.dashboard_state['manual_buy_checks']=checks
            if getattr(exc,'execution_progress',None):raise
            state['manual_buy_checks']=checks
            result['manual_buy_status']='BLOCKED';result['manual_buy_reason']=str(exc)
            if str(exc) not in local_blocks:return result
        except Exception as exc:
            record_check({'id':request_id,'status':'SUBMISSION_CHECK_FAILED'},
                progress=getattr(exc,'execution_progress',None),cause=exc)
            ledger.dashboard_state['manual_buy_checks']=checks
            raise
    if any(check['status']=='BLOCKED' for check in checks):
        # A considered owner request failure must not silently become an unrelated buy.
        # Expired-only queues are ignored by the initial automatic eligibility check.
        result.update(status='MANUAL_QUEUE_BLOCKED',batch_stop_reason='OWNER_REQUEST_BLOCKED')
        return result
    if result.get('status')=='MANUAL_QUEUE_PENDING':
        # Only an entirely already-recorded queue may resume automatic selection.
        # _automatic_cycle replaces dashboard_state, including on exceptions.
        try:
            result=_automatic_cycle(ledger,broker,candidates,execute=execute,now=now,
                stop_loss_pct=stop_loss_pct,take_profit_pct=take_profit_pct,clock=clock,defer_for_manual=False)
        finally:
            ledger.dashboard_state['manual_buy_checks']=checks
    return result


def publish_dashboard_if_collected(ledger,result,public_key,output):
    # Unresolved orders return before account collection by design. Do not
    # replace that safety result with a secondary missing-snapshot exception,
    # or relabel an old account snapshot with a new collection timestamp.
    if result.get('status')=='BLOCKED_UNRESOLVED_ORDERS' and not getattr(ledger,'dashboard_state',None):
        return False
    encrypt_dashboard(dashboard_snapshot(ledger,result),public_key,output)
    return True


def run_failure_context(result):
    # Preserve only safe observation metadata if publication/storage fails.
    # Never copy account snapshots, broker payloads or inferred order outcomes.
    return {key:result[key] for key in ('recovery','scanner_status','session_closed',
                                      'dashboard_snapshot_status') if key in result}


def failure_details(exc, *, _include_submission=True):
    """Public diagnostic metadata only: never exception text, locals or URLs."""
    from urllib.error import HTTPError, URLError
    kinds=(('HTTP_ERROR',HTTPError),('NETWORK_ERROR',URLError),
           ('TIMEOUT',TimeoutError),('INVALID_VALUE',ValueError),
           ('MISSING_FIELD',KeyError),('RUNTIME_ERROR',RuntimeError))
    data={'failure_kind':next((name for name,kind in kinds if isinstance(exc,kind)),'UNEXPECTED_ERROR')}
    if getattr(exc,'manual_diagnostic_checkpoint_failed',False) is True:
        data['manual_diagnostic_checkpoint_failed']=True
    sqlstate=getattr(exc,'sqlstate',None)
    if isinstance(sqlstate,str) and re.fullmatch('[0-9A-Z]{5}',sqlstate):
        data.update(failure_kind='DATABASE_ERROR',sqlstate=sqlstate)
    if isinstance(exc,HTTPError) and type(exc.code) is int and 100<=exc.code<=599:
        data['http_status']=exc.code
    if getattr(exc,'broker_api',None) in {*KIS_READ_APIS.values(),'CASH_ORDER','OTHER'}:
        data['broker_api']=exc.broker_api
    if type(getattr(exc,'broker_attempts',None)) is int and 1<=exc.broker_attempts<=3:
        data['broker_attempts']=exc.broker_attempts
    broker_code=safe_kis_error_code({'msg_cd':getattr(exc,'broker_code',None)})
    if broker_code:data['broker_code']=broker_code
    frames=[];tb=exc.__traceback__
    allowed={'oneil_v519_live_engine.py','v519_pg_ledger.py','oneil_live_v5_9.py',
             'oneil_v519_live_runner.py','kis_safety_v3.py','oneil_v519_broker_recovery.py',
             'oneil_v519_history_observe.py','kis_buying_power_readonly.py'}
    while tb:
        code=tb.tb_frame.f_code;file=Path(code.co_filename).name
        if file in allowed and re.fullmatch('[A-Za-z_][A-Za-z_0-9]{0,80}',code.co_name):
            frames.append({'file':file,'function':code.co_name,'line':tb.tb_lineno})
        tb=tb.tb_next
    data['failure_frames']=frames[-5:]
    # Preserve the original failure without exposing exception text, broker
    # response bodies, account identifiers or credentials. This is diagnostic
    # evidence only; it NEVER changes uncertainty, halt or replay decisions.
    if _include_submission:
        original=getattr(exc,'_submission_exception',None)
        if isinstance(original,Exception) and original is not exc:
            data['submission_failure']=failure_details(original,_include_submission=False)
        stage=getattr(exc,'submission_stage',None)
        if stage in {'SEND_GUARD','BEFORE_SUBMIT','SUBMIT','ACK_VALIDATE','LEDGER_ACK'}:
            data['submission_stage']=stage
    if isinstance(exc,TradingBlocked) and str(exc) in {
            'RELEASE_LOCKED_PENDING_OPERATOR_ACTIVATION','USER_APPROVAL_MISSING',
            'FRESH_EXECUTABLE_BOOK_REQUIRED','EXECUTABLE_BOOK_REQUIRED',
            'BOOK_SIZE_EXCEEDED','OUTSIDE_KRX_CONTINUOUS_SESSION','BROKER_ACK_INVALID'}:
        data['failure_code']=str(exc)
    return data


def activate_live_account(ledger, broker, candidates, *, now, operator, acknowledged=False, dashboard_review=None):
    if not acknowledged or not isinstance(operator,str) or not re.fullmatch('[A-Za-z0-9-]{1,39}',operator):
        raise TradingBlocked('OPERATOR_ACTIVATION_CONFIRMATION_REQUIRED')
    if os.getenv('GITHUB_ACTIONS')=='true' and operator!=os.getenv('GITHUB_REPOSITORY_OWNER'):
        raise TradingBlocked('ACCOUNT_OWNER_ACTIVATION_REQUIRED')
    require_live_release()
    # Same account/history/position/control readers, with execution=False:
    # operator activation itself never submits an order.
    result=integrated_cycle(ledger,broker,candidates,execute=False,now=now)
    if result['recovery']['unresolved'] or ledger.dashboard_state['snapshot']['pending']:
        raise TradingBlocked('ACTIVATION_REQUIRES_RECONCILED_ORDERS')
    if dashboard_review is not None:
        fresh=release_review_policy(broker.buy_control().get('release'))
        if fresh!=dashboard_review or fresh['approved_release']!=release_fingerprint():
            raise TradingBlocked('OWNER_RELEASE_REVIEW_CHANGED')
    with ledger.lock():
        ledger.activate_release(release_fingerprint(),operator,preserve_halt=dashboard_review is not None)
    result.update(status='LIVE_ACCOUNT_ACTIVATED',execution_requested=False,orders_sent=0,
        live_release_approved=True)
    if dashboard_review is not None:result['owner_review_revision']=dashboard_review['revision']
    return result


def release_review_policy(value):
    """Only an authenticated owner's exact, immutable release review can arm code."""
    if not isinstance(value,dict):return None
    if (value.get('schema_version')!=1 or value.get('operator')!='asura2367-boop'
            or not isinstance(value.get('approved_release'),str) or not re.fullmatch('[0-9a-f]{64}',value['approved_release'])
            or not isinstance(value.get('source_commit'),str) or not re.fullmatch('[0-9a-f]{40}',value['source_commit'])
            or type(value.get('revision')) is not int or not 1<=value['revision']<2**53-1
            or type(value.get('review_run_id')) is not int or value['review_run_id']<1):return None
    try:
        at=dt.datetime.fromisoformat(value['approved_at'])
        if at.tzinfo is None:return None
    except (ValueError,TypeError,KeyError):return None
    return {k:value[k] for k in ('schema_version','operator','approved_release','source_commit','revision','review_run_id','approved_at')}


def dashboard_release_env(control=None):
    """Resolve a human review for this schedule; never change GitHub variables."""
    if not (os.getenv('GITHUB_ACTIONS')=='true' and os.getenv('GITHUB_REF')=='refs/heads/main'
            and os.getenv('GITHUB_REPOSITORY')=='asura2367-boop/oneil-rs-scanner' and os.getenv('OPERATION') in {'auto','apply-approval'}):
        return None
    review=release_review_policy((read_buy_control() if control is None else control).get('release'))
    if review and review['approved_release']==release_fingerprint():
        if os.getenv('OPERATION')=='apply-approval' and (
                os.getenv('REQUESTED_RELEASE')!=review['approved_release'] or
                os.getenv('REQUESTED_APPROVAL_REVISION')!=str(review['revision'])):
            return None
        return review
    return None


def public_release_review():
    """Public source/test pointers only. No broker/account/credential fields."""
    import subprocess
    root=Path(__file__).resolve().parent
    sha=os.getenv('GITHUB_SHA','')
    run=os.getenv('GITHUB_RUN_ID','')
    if not re.fullmatch('[0-9a-f]{40}',sha) or not re.fullmatch('[0-9]{1,18}',run):
        raise TradingBlocked('RELEASE_REVIEW_CONTEXT_INVALID')
    def git(*args):return subprocess.check_output(['git',*args],cwd=root,text=True).strip()
    if git('rev-parse','HEAD')!=sha:raise TradingBlocked('RELEASE_REVIEW_SOURCE_CHANGED')
    return {'schema_version':1,'expected_release':release_fingerprint(),'source_commit':sha,
        'review_run_id':int(run),'summary':git('show','-s','--format=%s',sha)[:1000],
        'source_files':{name:git('rev-parse',sha+':'+name) for name in RELEASE_FILES}}


def scan_candidates(source, now):
    """Select by ONeil RS >= 90 only; quote/account/order safety gates remain separate."""
    from oneil_live_v5_9 import analyze_candidates
    report=analyze_candidates(Path(source),now,min_rs=90)
    fatal={'SOURCE_FILE_MISSING','REQUIRED_COLUMNS_MISSING','INVALID_ROWS_PRESENT','STALE_SIGNAL_ROWS'}
    if fatal.intersection(report['reason_codes']):
        raise TradingBlocked('CANDIDATE_SOURCE_INVALID_OR_STALE')
    return report['review_candidates']


def read_buy_control(*, opener=None, now=None, access_token=None):
    """Read owner-private desired buy setting. Failure blocks BUY only."""
    import urllib.request
    class NoControlRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            # A Site service credential must never follow a redirect, including
            # login redirects, to another origin or endpoint.
            raise TradingBlocked('CONTROL_REDIRECT_REJECTED')
    supplied_now=now
    token=os.getenv('DASHBOARD_SITE_ACCESS_TOKEN','') if access_token is None else access_token
    denied={'enabled':False,'connected':False,'revision':None,'reason':'CONTROL_CONNECTION_MISSING'}
    if not token:
        return denied
    try:
        req=urllib.request.Request('https://oneil-control-desk.asura2367.chatgpt.site/api/runtime-control',
            headers={'OAI-Sites-Authorization':'Bearer '+token,'Cache-Control':'no-cache'},method='GET')
        with (opener or urllib.request.build_opener(NoControlRedirect()).open)(req,timeout=5) as response:
            body=json.load(response)
        at=dt.datetime.fromisoformat(body['observed_at'])
        checked_at=supplied_now or dt.datetime.now(dt.timezone.utc)
        if (body.get('schema_version')!=1 or type(body.get('enabled')) is not bool or
                type(body.get('revision')) is not int or body['revision']<0 or
                at.tzinfo is None or not -2<=(checked_at-at).total_seconds()<=15):
            raise TradingBlocked('CONTROL_RESPONSE_INVALID')
        risk=risk_policy({'risk':{**body.get('risk',{}),'connected':True}})
        if not risk['connected']:raise TradingBlocked('RISK_CONTROL_INVALID')
        raw_sell=body.get('sell')
        sell=sell_policy({'sell':{**raw_sell,'connected':True} if isinstance(raw_sell,dict) else None})
        return {'enabled':body['enabled'],'connected':True,'revision':body['revision'],'risk':risk,'sell':sell,
                'release':release_review_policy(body.get('release')),
                'manual_buys':body.get('manual_buys',[]) if isinstance(body.get('manual_buys'),list) and len(body['manual_buys'])<=30 else [],
                'updated_at':body.get('updated_at'),'reason':'OWNER_SETTING'}
    except Exception:
        return {**denied,'reason':'CONTROL_READ_FAILED'}


def weekend_closed(now):
    """KST weekend closure; no quote, account, ledger or alert side effects."""
    return now.astimezone(ZoneInfo('Asia/Seoul')).weekday() >= 5


def market_closed_result():
    return {'status':'MARKET_CLOSED','gate':'DENY','reason':'WEEKEND_KRX_CLOSED',
            'orders_sent':0,'order_attempts':0,'execution_requested':False,
            'account_checked':False,'recovery_checked':False}


def monitoring_open(now):
    local=now.astimezone(ZoneInfo('Asia/Seoul'))
    return not weekend_closed(now) and dt.time(9)<=local.time().replace(tzinfo=None)<dt.time(20)


def dashboard_snapshot(ledger,result):
    """Private account payload; must only leave the runner through encryption."""
    from dataclasses import asdict
    state=getattr(ledger,'dashboard_state',None)
    if not state:
        raise TradingBlocked('DASHBOARD_ACCOUNT_SNAPSHOT_MISSING')
    snap=state['snapshot']; positions=[]
    for ticker,p in snap['positions'].items():
        cost=p['cost']; value=p['valuation']
        positions.append({'ticker':ticker,'name':p.get('name',ticker),
            'quantity':p['quantity'],'sellable':p['sellable'],'average':p['average'],
            'valuation':value,'cost':cost,'unrealized_pnl':value-cost,
            'return_pct':(value-cost)/cost*100 if cost else None,
            'technical':p.get('technical'),'strategy_quantity':snap['managed'].get(ticker,0)})
    orders=ledger.rows("""SELECT ticker,side,venue,quantity,filled_quantity,status,signal,
        broker_order_id,trading_day,limit_price,verified_not_sent,created_at,updated_at
        FROM v519_order_intents WHERE account_fingerprint=%s ORDER BY created_at DESC LIMIT 100""",
        (ledger.fingerprint,))
    runs=ledger.rows("""SELECT started_at,finished_at,result FROM v519_runs
        WHERE account_fingerprint=%s ORDER BY started_at DESC LIMIT 50""",(ledger.fingerprint,))
    equity_history=[{'at':r['finished_at'],'equity':r['result']['account_snapshot']['equity']}
        for r in reversed(runs) if isinstance(r.get('result'),dict) and
        isinstance(r['result'].get('account_snapshot'),dict) and
        r['result']['account_snapshot'].get('equity') is not None]
    approved=release_is_approved();authorized=ledger.release_authorized(release_fingerprint())
    halted=ledger.halted();armed=approved and authorized and not halted
    return {'schema_version':1,'generated_at':state['at'].isoformat(),
        'account_reference':ledger.fingerprint,'manual_buy_supported':True,
        'manual_buy_checks':state.get('manual_buy_checks',[]),
        'mode':('LIVE_EXECUTION' if result.get('execution_requested') else 'LIVE_ARMED') if armed else 'OBSERVE_ONLY',
        'orders_enabled':armed,'halted':halted,
        'activation':{'expected_release':release_fingerprint(),'release_configured':approved,
            'account_activated':authorized,'halted':halted,'live_armed':armed,
            'trading_hours':'09:00–15:15 KST','stop_loss_pct':5,'take_profit_pct':10,
            'broker_receipt_observed':any(o['broker_order_id'] for o in orders),
            'broker_fill_recovery_observed':any(o['filled_quantity']>0 for o in orders),
            'restart_failure_drill_verified':False},
        'monitoring_open':monitoring_open(state['at']) and not result.get('session_closed',False) and result.get('status')!='DAILY_CLOSED',
        'buy_control':state['buy_control'],'risk_control':state.get('risk_control'),
        'sell_control':state.get('sell_control'),'sell_alerts':ledger.sell_alerts(),
        'monitor_hours':'09:00–20:00 KST','poll_minutes':20,'venue':'KRX',
        'result':result,'allocation':state['allocation'],
        'account':{'cash':snap['cash'],'equity':snap.get('equity'),
            'valuation':snap.get('valuation'),
            'unrealized_pnl':sum(p['unrealized_pnl'] for p in positions),
            'strategy_realized_pnl':0 if not orders else None},
        'positions':positions,'candidates':state['candidates'],
        'plans':[asdict(p) for p in state['plans']],
        'orders':orders,'runs':runs,'equity_history':equity_history,
        'pnl_note':'평가손익은 평가금액−매입원가 단순 계산이며 수수료·세금 전입니다. 자산 추이에는 입출금이 포함될 수 있습니다.'}


def encrypt_dashboard(snapshot, public_key_path, output_path):
    """RSA-OAEP/SHA256 + AES-256-GCM. No plaintext file, key or balance logs."""
    import base64
    from cryptography.hazmat.primitives import serialization,hashes
    from cryptography.hazmat.primitives.asymmetric import padding,rsa
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    public_bytes=Path(public_key_path).read_bytes()
    public=serialization.load_pem_public_key(public_bytes)
    if not isinstance(public,rsa.RSAPublicKey) or public.key_size<3072:
        raise TradingBlocked('DASHBOARD_PUBLIC_KEY_INVALID')
    key_id=hashlib.sha256(public_bytes).hexdigest()[:16]
    aad=('oneil-dashboard-v1:'+key_id).encode()
    key=os.urandom(32); nonce=os.urandom(12)
    plaintext=json.dumps(snapshot,ensure_ascii=False,default=str,separators=(',',':')).encode()
    encrypted=AESGCM(key).encrypt(nonce,plaintext,aad)
    wrapped=public.encrypt(key,padding.OAEP(mgf=padding.MGF1(hashes.SHA256()),algorithm=hashes.SHA256(),label=None))
    b64=lambda data:base64.b64encode(data).decode('ascii')
    out={'schema_version':1,'key_id':key_id,'algorithm':'RSA-OAEP-256+A256GCM',
         'wrapped_key':b64(wrapped),'nonce':b64(nonce),'ciphertext':b64(encrypted)}
    target=Path(output_path);target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(out,separators=(',',':'))+'\n',encoding='utf8')


def runtime_main():
    import argparse
    import uuid
    from urllib.parse import urlparse
    from v519_pg_ledger import connect, migrate_runtime, PgRuntimeLedger
    p=argparse.ArgumentParser(description='Integrated V5.19 automatic runtime; operator activation required')
    modes=p.add_mutually_exclusive_group()
    modes.add_argument('--run',action='store_true')
    modes.add_argument('--migrate',action='store_true')
    modes.add_argument('--halt',action='store_true')
    modes.add_argument('--recover',action='store_true')
    modes.add_argument('--close',action='store_true')
    modes.add_argument('--broker-audit',action='store_true')
    modes.add_argument('--activate',action='store_true')
    modes.add_argument('--apply-owner-approval',action='store_true')
    modes.add_argument('--release-info',action='store_true')
    modes.add_argument('--release-review',action='store_true')
    modes.add_argument('--dashboard-release-env',action='store_true')
    p.add_argument('--operator')
    p.add_argument('--acknowledge-live-risk',action='store_true')
    p.add_argument('--execute',action='store_true')
    p.add_argument('--source',default='data/v4b/candidates.csv')
    p.add_argument('--output',default='v5_19_report.json')
    p.add_argument('--dashboard-public-key')
    p.add_argument('--dashboard-output',default='data/live/dashboard.enc.json')
    p.add_argument('--stop-loss-pct',type=int,default=5)
    p.add_argument('--take-profit-pct',type=int,default=10)
    args=p.parse_args()
    result=readiness(); code=0; ledger=None; run_id=uuid.uuid4().hex
    try:
        if args.release_review:
            print(json.dumps(public_release_review(),ensure_ascii=False));return
        if args.dashboard_release_env:
            review=dashboard_release_env()
            if review:
                path=os.getenv('GITHUB_ENV')
                if not path:raise TradingBlocked('GITHUB_ENV_MISSING')
                with open(path,'a') as stream:
                    stream.write('APPROVED_RELEASE='+review['approved_release']+'\nV519_APPROVED_RELEASE='+review['approved_release']+'\nV519_DASHBOARD_REVIEW_REVISION='+str(review['revision'])+'\n')
            print(json.dumps({'owner_review_available':bool(review),'orders_sent':0}));return
        if args.release_info:
            print(json.dumps({'expected_release':release_fingerprint(),'release_configured':release_is_approved(),'orders_sent':0}))
            return
        requested_review=None
        if args.apply_owner_approval:
            if args.execute:raise TradingBlocked('ACTIVATION_CANNOT_SUBMIT')
            requested_review=dashboard_release_env()
            if not requested_review:raise TradingBlocked('OWNER_RELEASE_REVIEW_CHANGED')
            local=dt.datetime.now(ZoneInfo('Asia/Seoul'))
            if local.weekday()>=5 or not dt.time(9)<=local.time().replace(tzinfo=None)<dt.time(15,15):
                raise TradingBlocked('OUTSIDE_KRX_CONTINUOUS_SESSION')
            args.activate=True
        if args.activate:
            if args.execute:raise TradingBlocked('ACTIVATION_CANNOT_SUBMIT')
            if not args.acknowledge_live_risk:raise TradingBlocked('OPERATOR_ACTIVATION_CONFIRMATION_REQUIRED')
            require_live_release()
        if args.execute:
            require_live_release()  # deny before DB, token or any network order
        if not any((args.run,args.migrate,args.halt,args.recover,args.close,args.broker_audit,args.activate)):
            print(json.dumps(result))
            return
        if (args.run or args.close or args.activate or args.broker_audit) and weekend_closed(dt.datetime.now(ZoneInfo('Asia/Seoul'))):
            result=market_closed_result()
            Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
            print(json.dumps(result,ensure_ascii=False))
            return
        if urlparse(os.getenv('SUPABASE_DB_URL','')).port==6543:
            raise TradingBlocked('SESSION_DATABASE_CONNECTION_REQUIRED')
        print(json.dumps({'event':'RUNTIME_DATABASE_CONNECT_STARTED'}),flush=True)
        with connect() as db:
            print(json.dumps({'event':'RUNTIME_DATABASE_CONNECTED'}),flush=True)
            db.autocommit=True
            if args.migrate:
                # Migration is explicit, never silently change schema during trading.
                with db.transaction():
                    migrate_runtime(db)
                result.update(status='MIGRATED_HALTED_BY_DEFAULT')
            else:
                if args.halt:
                    # Emergency stop must work even when broker auth/scan is broken.
                    with db.cursor() as cur:
                        cur.execute('UPDATE v519_control SET halted=true,updated_at=now() WHERE singleton=true')
                        if cur.rowcount!=1:
                            raise TradingBlocked('HALT_CONTROL_MISSING')
                    result.update(status='EMERGENCY_HALTED')
                else:
                    # Resolve fingerprint before acquiring a token so early auth
                    # failures can still be recorded in the permanent run log.
                    from kis_buying_power_readonly import credentials
                    _,_,account,product=credentials(read_only=not (args.execute or args.activate))
                    fingerprint=hashlib.sha256((account+'|'+product).encode()).hexdigest()
                    ledger=PgRuntimeLedger(db,fingerprint)
                    print(json.dumps({'event':'RUNTIME_LEDGER_START'}),flush=True)
                    ledger.start_run(run_id)
                    ledger.manual_check_checkpoint=lambda checks:ledger.save_manual_buy_checks(run_id,checks)
                    print(json.dumps({'event':'RUNTIME_LEDGER_READY'}),flush=True)
                    # Daily close is a time boundary, not an emergency halt.
                    # Emergency stops persist until an operator explicitly activates again.
                    try:
                        review=dashboard_release_env() if args.run and args.execute and os.getenv('V519_DASHBOARD_REVIEW_REVISION') else None
                        pending_review=review and not ledger.release_authorized(release_fingerprint())
                        broker=KisBroker(execution=args.execute and not pending_review,operator_activation=bool(args.activate or pending_review))
                        if pending_review:
                            now=dt.datetime.now(ZoneInfo('Asia/Seoul'))
                            candidates=scan_candidates(args.source,now)
                            result=activate_live_account(ledger,broker,candidates,now=now,
                                operator=review['operator'],acknowledged=True,dashboard_review=review)
                        elif args.activate:
                            now=dt.datetime.now(ZoneInfo('Asia/Seoul'))
                            candidates=scan_candidates(args.source,now)
                            result=activate_live_account(ledger,broker,candidates,now=now,
                                operator=requested_review['operator'] if requested_review else args.operator,
                                acknowledged=args.acknowledge_live_risk,dashboard_review=requested_review)
                        elif args.broker_audit:
                            now=dt.datetime.now(ZoneInfo('Asia/Seoul'))
                            try:candidates=scan_candidates(args.source,now)
                            except Exception:candidates=[]
                            with ledger.lock():
                                result=audit_broker_evidence(broker,candidates,now=now)
                            if result['status'].startswith('BLOCKED'):code=2
                        elif args.recover:
                            with ledger.lock():
                                recovery=recover_pending(ledger,broker)
                            result.update(status='RECOVERY_CHECKED',recovery=recovery)
                        else:
                            now=dt.datetime.now(ZoneInfo('Asia/Seoul'))
                            # Recovery/exits still run when scanning fails. Buys
                            # stay disabled; record the scanner failure as nonzero.
                            scan_failed=False
                            try:
                                candidates=scan_candidates(args.source,now)
                            except Exception:
                                candidates=[]; scan_failed=True
                            result=integrated_cycle(ledger,broker,candidates,execute=args.execute,now=now,
                                stop_loss_pct=args.stop_loss_pct,take_profit_pct=args.take_profit_pct)
                            if scan_failed:
                                result['scanner_status']='BLOCKED_INVALID_OR_STALE_SOURCE'; code=2
                            else:
                                result['scanner_status']='VALID'
                        if args.close:
                            # Closing is a session marker, not evidence that
                            # recovery or account observation succeeded.
                            result['session_closed']=True
                            if (result.get('status') in ('OBSERVED','SELL_ALERT_ONLY') and
                                    result.get('scanner_status')=='VALID'):
                                result['status']='DAILY_CLOSED'
                        private_record=dict(result)
                        if getattr(ledger,'dashboard_state',None):
                            snap=ledger.dashboard_state['snapshot']
                            private_record['account_snapshot']={'cash':snap['cash'],
                                'equity':snap.get('equity'),'valuation':snap.get('valuation')}
                        ledger.finish_run(run_id,private_record)
                        if args.dashboard_public_key:
                            if not publish_dashboard_if_collected(ledger,result,args.dashboard_public_key,args.dashboard_output):
                                result['dashboard_snapshot_status']='NOT_COLLECTED_UNRESOLVED_ORDERS'
                    except Exception as exc:
                        failed={**run_failure_context(result),'status':'BLOCKED','gate':'DENY','order_outcome':'CHECK_LEDGER',
                                'orders_sent':None if args.execute else 0,**failure_details(exc),
                                **getattr(exc,'execution_progress',{})}
                        ledger.finish_run(run_id,failed)
                        raise
    except Exception as exc:
        # Never log raw DB/KIS exception strings or credential-bearing URLs.
        reason=str(exc) if isinstance(exc,TradingBlocked) else 'BROKER_OR_DATABASE_FAILURE'
        result={**run_failure_context(result),'status':'BLOCKED','gate':'DENY','reason':reason,
                'orders_sent':None if args.execute and release_is_approved() else 0,
                **failure_details(exc),**getattr(exc,'execution_progress',{})}
        code=2
    Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps(result,ensure_ascii=False))
    raise SystemExit(code)


if __name__=='__main__':
    runtime_main()

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

class TradingBlocked(ValueError): pass
class SellDisabledBeforeSend(TradingBlocked): pass
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
    'v519_cash_policy.py','oneil_v519_live_runner.py','oneil_v519_history_observe.py',
    'oneil_v519_broker_recovery.py','oneil_v519_order_recovery.py',
    'kis_buying_power_readonly.py','kis_safety_v3.py','oneil_live_v5_9.py',
    '.github/workflows/oneil_v519_live_engine_review.yml',
    'build_v4b_candidates.py','quote_collector_v4a.py','kis_live_v4_5_2.py',
    '.github/workflows/v4b_candidate_save_auto.yml',
    'build_data.py','build_investor.py','collect_shared_token.py',
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
                  'TAKE_PROFIT' if price * 100 >= avg * (100+take_profit_pct) else None)
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
        return (-rs,0 if c.get('flow_category')=='BOTH' else 1,str(c.get('ticker','')))
    for c in sorted(candidates,key=rank):
        ticker = c['ticker']
        if not isinstance(ticker, str) or not CODE.fullmatch(ticker) or ticker in seen:
            raise TradingBlocked('CANDIDATE_INVALID')
        seen.add(ticker)
        if ticker in snapshot['positions'] or ticker in snapshot.get('bought_today', set()):
            continue
        price = quote(ticker, 'BUY')
        if type(price) is not int or price <= 0:
            raise TradingBlocked('PRICE_INVALID')
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
        from oneil_v519_live_runner import token
        self.key, self.secret, self.account, self.product = credentials(read_only=not (execution or operator_activation))
        self.access = token(self.key, self.secret)
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
            try:
                with urllib.request.urlopen(request, timeout=15) as response:
                    body=json.load(response)
                    continuation=response.headers.get('tr_cont')
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
                    'valuation':exact_int(r.get('evlu_amt'))}
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
        pending = unfilled_codes(self.key,self.secret,self.account,self.product,self.access)
        return {'cash':cash,'invested':invested,'positions':positions,'pending':pending,
                'equity':equity,'valuation':valuation}

    def history(self, day):
        from oneil_v519_history_observe import fetch_page
        from oneil_v519_broker_recovery import read_full_day
        if (dt.date.today()-dt.datetime.strptime(day,'%Y%m%d').date()).days > 85:
            raise TradingBlocked('HISTORY_TOO_OLD_MANUAL_RECONCILIATION')
        if day not in self.history_cache:
            self.history_cache[day]=read_full_day(lambda date,fk,nk,again: fetch_page(self.key,self.secret,
                self.account,self.product,self.access,date,fk,nk,again), day)
        return self.history_cache[day]

    def quote(self, ticker, side):
        from oneil_v519_live_runner import price_reference
        if not self.execution:
            return price_reference(self.access,self.key,self.secret,ticker)
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
        return buying_power(self.access,self.key,self.secret,self.account,self.product,ticker,price)

    def buy_control(self):
        return read_buy_control()

    def reference_quote(self,ticker):
        from oneil_v519_live_runner import price_reference
        return price_reference(self.access,self.key,self.secret,ticker)

    def submit(self, plan):
        require_live_release()
        # Price evidence is obtained by the broker reader; no CLI attestation.
        evidence=self.quote_evidence.get((plan.ticker,plan.side))
        now=dt.datetime.now(ZoneInfo('Asia/Seoul'))
        if (not self.execution or not evidence or evidence['price']!=plan.price or
                not 0<=(now-evidence['at']).total_seconds()<=3):
            raise TradingBlocked('FRESH_EXECUTABLE_BOOK_REQUIRED')
        if plan.quantity>evidence['size']:
            raise TradingBlocked('BOOK_SIZE_EXCEEDED')
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
            raise TradingBlocked('EXECUTABLE_BOOK_REQUIRED')
        prepared = KisCashOrderAdapter(None).prepare(side=plan.side,ticker=plan.ticker,
            qty=plan.quantity,limit_price_krw=plan.price,cano=self.account,product=self.product)
        prepared['body'].update(EXCG_ID_DVSN_CD='KRX',
            SLL_TYPE='01' if plan.side=='SELL' else '',CNDT_PRIC='')
        _, response = self.request(prepared['path'],prepared['tr_id'],prepared['body'],post=True)
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
    return policy['connected'] and (policy['enabled'] or plan.reason=='TAKE_PROFIT')


def integrated_cycle(ledger, broker, candidates, *, execute=False, now=None,
                     stop_loss_pct=5, take_profit_pct=10, clock=None):
    """Sequential candidate buys under one account lock and conservative cash caps.

    Each acknowledged buy must be fully reconciled before the next candidate.
    Pending/unknown orders never gain an exception to the durable ledger guard.
    Enabled exits retain priority; explicit sell OFF records alerts and lets
    independently enabled buys continue. Sell proceeds are not
    funding for this batch. The initial cash budget can only shrink in this run.
    """
    clock = clock or (lambda: dt.datetime.now(ZoneInfo('Asia/Seoul')))
    now = now or clock()
    if now.tzinfo is None:
        raise TradingBlocked('CLOCK_INVALID')
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
        plans, allocation = select_plans(candidates,snapshot,control,1 if execute else None)
        ledger.dashboard_state={'snapshot':snapshot,'plans':plans,'candidates':candidates,'allocation':allocation,'at':now,'buy_control':control,'risk_control':risk,'sell_control':sell}
        result['planned_buys']=sum(p.side=='BUY' for p in plans)
        result['planned_sells']=sum(p.side=='SELL' for p in plans)
        def alert_only(exit_plans,latest):
            exits=[p for p in exit_plans if p.side=='SELL']
            ledger.record_sell_alerts(exits,now.date())
            ledger.dashboard_state['sell_control']=latest
            ledger.dashboard_state['plans']=[]
            result.update(status='SELL_ALERT_ONLY' if latest['connected'] else 'SELL_CONTROL_UNAVAILABLE',
                sell_alert_count=len(exits),planned_buys=0,planned_sells=0)
            return result
        if result['planned_sells'] and not sell['connected']:
            return alert_only(plans,sell)  # Unknown control retains the safety block.
        if not execute:
            return result
        initial_remaining=allocation['remaining'];initial_stock_cap=allocation['per_stock']
        spent=0; attempted=set();display_plans=[];uncertain=False
        try:
            # At most one attempt per candidate; no waits, retries or random IDs.
            for _ in range(max(1,len(candidates))):
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
                            [c for c in candidates if c['ticker'] not in attempted],snapshot,latest_control,1)
                        ledger.dashboard_state.update(plans=display_plans+plans,allocation=allocation)
                        result['planned_sells']=0
                        result['planned_buys']=sum(p.side=='BUY' for p in plans)
                        if not plans:return result
                        plan=plans[0]
                if plan.ticker in attempted:raise TradingBlocked('BATCH_DUPLICATE_CANDIDATE')
                attempted.add(plan.ticker)
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
                    if not (fresh*100<=average*(100-stop_loss_pct) or fresh*100>=average*(100+take_profit_pct)):
                        quantity=0
                    else:fresh_reason='STOP_LOSS' if fresh*100<=average*(100-stop_loss_pct) else 'TAKE_PROFIT'
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
                    try:
                        cancelled=False
                        with ledger.send_guard():
                            order_time()  # stop a batch that crosses the session boundary
                            # Real KIS does its final read inside the wire adapter;
                            # fixtures/other adapters are checked at the same boundary.
                            try:
                                if plan.side=='SELL' and not isinstance(broker,KisBroker) and not sell_plan_allowed(plan,broker.buy_control()):
                                    raise SellDisabledBeforeSend('SELL_CONTROL_OFF_OR_UNAVAILABLE')
                                uncertain=True
                                response=broker.submit(plan)
                            except SellDisabledBeforeSend:
                                uncertain=False
                                ledger.void_unsent_sell(ident)
                                cancelled=True
                        if cancelled:
                            result['pre_send_cancelled']=True
                            return alert_only([plan],sell_policy(broker.buy_control()))
                        out=response.get('output') if isinstance(response,dict) else None
                        order_id=out.get('ODNO') if isinstance(out,dict) else None
                        if str(response.get('rt_cd'))!='0' or not isinstance(order_id,str) or not re.fullmatch('[0-9]{1,20}',order_id):
                            raise TradingBlocked('BROKER_ACK_INVALID')
                        ledger.ack(ident,order_id)
                        uncertain=False
                        result['orders_sent']+=1
                        result.update(status='ACKNOWLEDGED',gate='ALLOW')
                        display_plans.append(plan)
                    except Exception:
                        ledger.halt()
                        raise TradingBlocked('SUBMISSION_UNCERTAIN_NO_RETRY') from None
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
                        result['batch_stop_reason']='EXIT_PRIORITY'
                        return result
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


def failure_details(exc):
    """Public diagnostic metadata only: never exception text, locals or URLs."""
    from urllib.error import HTTPError, URLError
    kinds=(('HTTP_ERROR',HTTPError),('NETWORK_ERROR',URLError),
           ('TIMEOUT',TimeoutError),('INVALID_VALUE',ValueError),
           ('MISSING_FIELD',KeyError),('RUNTIME_ERROR',RuntimeError))
    data={'failure_kind':next((name for name,kind in kinds if isinstance(exc,kind)),'UNEXPECTED_ERROR')}
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
            and os.getenv('GITHUB_REPOSITORY')=='asura2367-boop/oneil-rs-scanner' and os.getenv('OPERATION')=='auto'):
        return None
    review=release_review_policy((read_buy_control() if control is None else control).get('release'))
    if review and review['approved_release']==release_fingerprint():return review
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
    """Reuse proven RS/volume/channel/flow filtering without stale report files."""
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
                'updated_at':body.get('updated_at'),'reason':'OWNER_SETTING'}
    except Exception:
        return {**denied,'reason':'CONTROL_READ_FAILED'}


def monitoring_open(now):
    local=now.astimezone(ZoneInfo('Asia/Seoul'))
    return dt.time(9)<=local.time().replace(tzinfo=None)<dt.time(20)


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
            'strategy_quantity':snap['managed'].get(ticker,0)})
    orders=ledger.rows("""SELECT ticker,side,venue,quantity,filled_quantity,status,
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
        'mode':('LIVE_EXECUTION' if result.get('execution_requested') else 'LIVE_ARMED') if armed else 'OBSERVE_ONLY',
        'orders_enabled':armed,'halted':halted,
        'activation':{'expected_release':release_fingerprint(),'release_configured':approved,
            'account_activated':authorized,'halted':halted,'live_armed':armed,
            'trading_hours':'09:00–15:15 KST','stop_loss_pct':5,'take_profit_pct':10,
            'broker_receipt_observed':any(o['broker_order_id'] for o in orders),
            'broker_fill_recovery_observed':any(o['filled_quantity']>0 for o in orders),
            'restart_failure_drill_verified':False},
        'monitoring_open':monitoring_open(state['at']) and result.get('status')!='DAILY_CLOSED',
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
        if args.activate:
            if args.execute:raise TradingBlocked('ACTIVATION_CANNOT_SUBMIT')
            if not args.acknowledge_live_risk:raise TradingBlocked('OPERATOR_ACTIVATION_CONFIRMATION_REQUIRED')
            require_live_release()
        if args.execute:
            require_live_release()  # deny before DB, token or any network order
        if not any((args.run,args.migrate,args.halt,args.recover,args.close,args.broker_audit,args.activate)):
            print(json.dumps(result))
            return
        if urlparse(os.getenv('SUPABASE_DB_URL','')).port==6543:
            raise TradingBlocked('SESSION_DATABASE_CONNECTION_REQUIRED')
        with connect() as db:
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
                    ledger.start_run(run_id)
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
                                operator=args.operator,acknowledged=args.acknowledge_live_risk)
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
                            result['status']='DAILY_CLOSED'
                        private_record=dict(result)
                        if getattr(ledger,'dashboard_state',None):
                            snap=ledger.dashboard_state['snapshot']
                            private_record['account_snapshot']={'cash':snap['cash'],
                                'equity':snap.get('equity'),'valuation':snap.get('valuation')}
                        ledger.finish_run(run_id,private_record)
                        if args.dashboard_public_key:
                            snapshot=dashboard_snapshot(ledger,result)
                            encrypt_dashboard(snapshot,args.dashboard_public_key,args.dashboard_output)
                    except Exception as exc:
                        failed={'status':'BLOCKED','gate':'DENY','order_outcome':'CHECK_LEDGER',
                                'orders_sent':None if args.execute else 0,**failure_details(exc),
                                **getattr(exc,'execution_progress',{})}
                        ledger.finish_run(run_id,failed)
                        raise
    except Exception as exc:
        # Never log raw DB/KIS exception strings or credential-bearing URLs.
        reason=str(exc) if isinstance(exc,TradingBlocked) else 'BROKER_OR_DATABASE_FAILURE'
        result={'status':'BLOCKED','gate':'DENY','reason':reason,
                'orders_sent':None if args.execute and release_is_approved() else 0,
                **failure_details(exc),**getattr(exc,'execution_progress',{})}
        code=2
    Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps(result,ensure_ascii=False))
    raise SystemExit(code)


if __name__=='__main__':
    runtime_main()

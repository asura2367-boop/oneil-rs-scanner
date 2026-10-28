#!/usr/bin/env python3
"""V5.19 live-engine core: budget, idempotency, reconciliation and order adapter.
No live execution entry point is exported. Real order transmission is locked.
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

class TradingBlocked(ValueError): pass
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
    return {"version":"5.19-live-engine-core","gate":"DENY","orders_sent":0,
            "LIVE_ENABLED":False,"KIS_ALLOW_ORDER":False,
            "broker_order_transport_enabled":False,"current_cash_not_fixed":True}
if __name__=="__main__":
    print(json.dumps(readiness(),ensure_ascii=False))

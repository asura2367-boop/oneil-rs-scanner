#!/usr/bin/env python3
"""Cash-based risk sizing, simulation only. No fixed starting capital, no orders."""
def size_from_cash(verified_cash_krw, broker_orderable_krw, price_krw, *,
                   total_open_exposure_krw=0, stock_exposure_krw=0,
                   cash_verified=False, orderable_verified=False):
    values=(verified_cash_krw,broker_orderable_krw,price_krw,total_open_exposure_krw,stock_exposure_krw)
    if any(type(v) is not int or v<0 for v in values) or price_krw==0:
        raise ValueError("BAD_AMOUNT")
    if not cash_verified or not orderable_verified:
        raise ValueError("BROKER_CASH_NOT_VERIFIED")
    trading_budget=verified_cash_krw*60//100
    per_stock=trading_budget*30//100
    available=min(max(0,trading_budget-total_open_exposure_krw),
                  max(0,per_stock-stock_exposure_krw),
                  broker_orderable_krw)
    return {"simulated_quantity":available//price_krw,
            "authorized":False,"gate":"DENY","orders_sent":0,
            "reserve_pct":40,"trading_pct":60,"stock_pct_of_trading_budget":30}

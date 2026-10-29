"""Cash-based allocation, no fixed capital. Read-only calculation."""
def limits(cash, invested, orderable, *, reserve_pct=40, per_stock_pct=30):
    if any(type(x) is not int or x < 0 for x in (cash, invested, orderable)):
        raise ValueError('INVALID_AMOUNT')
    if any(type(x) is not int or not 0 <= x <= 100 for x in (reserve_pct,per_stock_pct)):
        raise ValueError('INVALID_RISK_PERCENT')
    trading = cash * (100-reserve_pct) // 100
    per_stock = trading * per_stock_pct // 100
    return {'cash':cash,'reserve':cash-trading,'trading':trading,
            'reserve_pct':reserve_pct,'trading_pct':100-reserve_pct,'per_stock_pct':per_stock_pct,
            'per_stock':per_stock,'remaining':min(max(0,trading-invested),orderable)}

def quantity(cash, invested, orderable, price, broker_qty):
    if type(price) is not int or price <= 0 or type(broker_qty) is not int or broker_qty < 0:
        raise ValueError('INVALID_PRICE_OR_QTY')
    b=limits(cash,invested,orderable)
    return min(broker_qty,b['per_stock']//price,b['remaining']//price)

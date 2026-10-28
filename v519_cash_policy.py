"""Cash-based allocation, no fixed capital. Read-only calculation."""
def limits(cash, invested, orderable):
    if any(type(x) is not int or x < 0 for x in (cash, invested, orderable)):
        raise ValueError('INVALID_AMOUNT')
    trading = cash * 60 // 100
    per_stock = trading * 30 // 100
    return {'cash':cash,'reserve':cash-trading,'trading':trading,
            'per_stock':per_stock,'remaining':min(max(0,trading-invested),orderable)}

def quantity(cash, invested, orderable, price, broker_qty):
    if type(price) is not int or price <= 0 or type(broker_qty) is not int or broker_qty < 0:
        raise ValueError('INVALID_PRICE_OR_QTY')
    b=limits(cash,invested,orderable)
    return min(broker_qty,b['per_stock']//price,b['remaining']//price)

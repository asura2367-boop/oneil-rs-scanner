"""Apply fail-closed cash-basis fix to existing repository files. Run in repo root."""
from pathlib import Path

p=Path('v519_order_decisions.py')
s=p.read_text()
if 'CAPITAL=1_000_000' not in s or 'STOCK_LIMIT=180_000' not in s:
    raise SystemExit('Planner differs; abort without changes')
s=s.replace('CAPITAL=1_000_000\nRESERVE=400_000\nTRADING=600_000\nSTOCK_LIMIT=180_000',
'''# No fixed capital. This plan is NOT an order authorization.
# KIS deposit and total exposure must be verified separately.''')
s=s.replace('    remaining=TRADING','    remaining=None  # No deposit attestation in broker review')
s=s.replace('        quantity=min(qty,STOCK_LIMIT//price,remaining//price)\n        remaining-=quantity*price',
'''        quantity=qty  # indicative broker quantity only, NOT deposit-based sizing''')
s=s.replace("'preview_notional_total':TRADING-remaining,'orders':orders", "'preview_notional_total':None,'orders':orders")
s=s.replace("'limits':{'capital':CAPITAL,'reserve':RESERVE,'trading':TRADING,'per_stock':STOCK_LIMIT}",
"'limits':{'basis':'KIS_DEPOSIT_UNVERIFIED','reserve_pct':40,'trading_pct':60,'per_stock_pct_of_trading':30}")

q=Path('v519_order_integration.py')
t=q.read_text()
old='MAX_TOTAL = 1_000_000\nRESERVE = 400_000\nMAX_TRADING = MAX_TOTAL - RESERVE\nMAX_STOCK = 180_000'
if old not in t:raise SystemExit('Integration differs; abort without changes')
t=t.replace(old,'from v519_cash_policy import limits')
t=t.replace("    if snapshot['pending']:\n        raise LedgerBlocked('BROKER_PENDING_ORDERS')",
"    if snapshot['pending']:\n        raise LedgerBlocked('BROKER_PENDING_ORDERS')\n    if snapshot.get('cash_verified') is not True:\n        raise LedgerBlocked('KIS_DEPOSIT_NOT_VERIFIED')\n    budget=limits(snapshot['cash_krw'],snapshot['invested'],snapshot['orderable_krw'])")
t=t.replace("    if snapshot['invested'] > MAX_TRADING:\n        raise LedgerBlocked('TRADING_EXPOSURE_CAP')\n    remaining = MAX_TRADING - snapshot['invested']", "    remaining = budget['remaining']")
t=t.replace('MAX_STOCK // price','budget[\'per_stock\'] // price')
t=t.replace("'invested': invested, 'pending': pending,", "'invested': invested, 'pending': pending, 'cash_verified': False,")

# Check both before writing; existing live gate remains closed.
compile(s,str(p),'exec');compile(t,str(q),'exec')
p.write_text(s);q.write_text(t)
print('Updated both modules to remove fixed capital; live orders still blocked.')

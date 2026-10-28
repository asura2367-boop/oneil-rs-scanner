#!/usr/bin/env python3
"""Apply cash-driven, no-order sizing integration to existing V5.19 repo files."""
from pathlib import Path
import ast


def apply(root: Path):
    phase = root / 'oneil_v519_phase234.py'
    stage = root / 'oneil_v519_stage5_readiness.py'
    if not phase.exists() or not stage.exists():
        raise RuntimeError('REQUIRED_REPOSITORY_FILES_MISSING')
    a = phase.read_text(encoding='utf8')
    b = stage.read_text(encoding='utf8')
    old = '''def simulate(candidate,account,quote,history,*,now=None,initial_capital=1000000,events=None):'''
    new = '''def simulate(candidate,account,quote,history,*,now=None,initial_capital=None,events=None):'''
    if old in a:
        a = a.replace(old,new,1)
    elif new not in a:
        raise RuntimeError('UNKNOWN_PHASE234_VERSION')
    old_return = "'initial_capital_krw':initial_capital,'simulated_scenarios':"
    new_return = "'capital_source':'VERIFIED_KIS_CASH_REQUIRED','initial_capital_krw':None,'simulated_scenarios':"
    if old_return in a:
        a=a.replace(old_return,new_return,1)
    elif new_return not in a:
        raise RuntimeError('UNKNOWN_CAPITAL_REPORT_VERSION')
    # Explicitly prohibit using the obsolete initial_capital override.
    anchor=" off();now=now or dt.datetime.now(KST)"
    if " if initial_capital is not None:raise Blocked('FIXED_CAPITAL_FORBIDDEN')" not in a:
        if anchor not in a: raise RuntimeError('UNKNOWN_SIMULATE_BODY')
        a=a.replace(anchor," if initial_capital is not None:raise Blocked('FIXED_CAPITAL_FORBIDDEN')\n"+anchor,1)
    # Allocation now consumes verified current cash, not a user-selected fixed cap.
    if 'def allocation(initial_capital,broker_buying_cash,price,*' in a:
        a=a.replace('def allocation(initial_capital,broker_buying_cash,price,*',
                    'def allocation(verified_cash_krw,broker_buying_cash,price,*',1)
        a=a.replace('current_trading_exposure=0,stock_exposure=0,\n               risk_verified=False,price_certified=False,account_verified=False,held=False,unfilled=False):',
                    'current_trading_exposure=0,stock_exposure=0,\n               risk_verified=False,price_certified=False,account_verified=False,cash_verified=False,held=False,unfilled=False):',1)
        a=a.replace('(initial_capital,broker_buying_cash,price,current_trading_exposure,stock_exposure)',
                    '(verified_cash_krw,broker_buying_cash,price,current_trading_exposure,stock_exposure)',1)
        a=a.replace('if not risk_verified or not price_certified or not account_verified:',
                    'if not risk_verified or not price_certified or not account_verified or not cash_verified:',1)
        a=a.replace('trade_cap=initial_capital*60//100','trade_cap=verified_cash_krw*60//100',1)
    elif 'def allocation(verified_cash_krw,broker_buying_cash,price,*' not in a:
        raise RuntimeError('UNKNOWN_ALLOCATION_VERSION')
    # Stage 5 must not display old fixed budget as actual allocation.
    if '"capital_krw":1000000' in b:
        b=b.replace('"capital_krw":1000000','"capital_krw":None,"capital_source":"VERIFIED_CURRENT_KIS_CASH"',1)
    elif '"capital_source":"VERIFIED_CURRENT_KIS_CASH"' not in b:
        raise RuntimeError('UNKNOWN_STAGE5_VERSION')
    ast.parse(a); ast.parse(b)
    phase.write_text(a,encoding='utf8'); stage.write_text(b,encoding='utf8')
    print('V519_CASH_INTEGRATION=PATCHED; FIXED_1000000_REMOVED; ORDERS_SENT=0')

if __name__=='__main__':apply(Path(__file__).resolve().parent)

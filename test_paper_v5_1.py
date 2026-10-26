import importlib.util, tempfile, json
from pathlib import Path
spec=importlib.util.spec_from_file_location('paper_trade',Path(__file__).with_name('paper_trade.py'))
m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
assert m.RESERVE_PCT == 40 and m.STOCK_PCT == 30
with tempfile.TemporaryDirectory() as tmp:
    m.OUT=Path(tmp)
    state={'mode':'paper_only_v1','initial_cash':10_000_000,'cash':6_000_000,
           'last_processed':'2026-10-26',
           'pending':[],'positions':{'052460':{'name':'아이크래프트','qty':340,'entry':5865.86,'last_close':5860}}}
    eq=[{'date':'2026-10-23','equity':10000000,'cash':10000000,'holdings_value':0},
        {'date':'2026-10-26','equity':9995420,'cash':6000000,'holdings_value':3995420}]
    m.create_daily_report(state,[],eq)
    result=json.loads((m.OUT/'daily_review.json').read_text())
    assert result['daily_pnl_mark_to_market']==-4580
    assert result['realized_pnl_today'] is None
    assert result['orders_sent']==0
    assert result['next_session_strategy']['mode']=='REVIEW_ONLY'
print('PASS: report, rates, mark-to-market, no fabricated realized PnL, no orders')

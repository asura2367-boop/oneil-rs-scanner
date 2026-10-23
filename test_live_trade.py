import datetime as dt
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from live_trade import make_plan

def test_preview():
    with TemporaryDirectory() as tmp:
        p=Path(tmp)
        (p/'state.json').write_text(json.dumps({'pending':[{'side':'BUY','ticker':'052460','name':'아이크래프트','signal_date':'2026-10-23'},{'side':'BUY','ticker':'052460'},{'side':'BUY','ticker':'048910','name':'대원미디어'}]}),encoding='utf-8')
        (p/'summary.json').write_text(json.dumps({'pending':[{'ticker':'052460','reference_date':'2026-10-23','reference_close':5980,'estimated_qty':334},{'ticker':'048910','reference_date':'2026-10-23','reference_close':8650,'estimated_qty':230}]}),encoding='utf-8')
        report=make_plan(p/'state.json',p/'summary.json',today=dt.date(2026,10,23))
        assert report['live_enabled'] is False and report['execution_capable'] is False
        assert len(report['plans'])==2
        assert report['plans'][0]['preview_qty']==33
        assert report['plans'][1]['preview_qty']==23
        assert all(x['preview_notional'] <=200000 for x in report['plans'])
        stale=make_plan(p/'state.json',p/'summary.json',today=dt.date(2026,11,2))
        assert all(x['status']=='BLOCKED_STALE_PRICE' for x in stale['plans'])

if __name__ == '__main__':
    test_preview()
    print('PASS')

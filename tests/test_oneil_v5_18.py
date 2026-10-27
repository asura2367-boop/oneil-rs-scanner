import datetime as dt
import unittest
from zoneinfo import ZoneInfo
from unittest.mock import patch
from oneil_live_v5_18 import check,phase
KST=ZoneInfo('Asia/Seoul')

def sample(hh='145000',code='085910'):
    # Original V5.17 prefix: ticker, time, phase, ask 10, bid 10, ask qty 10, bid qty 10
    row=[code,hh,'2']+[str(1000+i) for i in range(10)]+['999']*10+['100']*10+['200']*10+['0']*18
    return '0|H0STASP0|1|'+'^'.join(row)

class TestV518(unittest.TestCase):
    def test_window(self):
        self.assertEqual(phase('KRX',dt.datetime(2026,10,27,19,tzinfo=KST)),'CLOSED_OR_UNVERIFIED')
        self.assertEqual(phase('NXT',dt.datetime(2026,10,27,19,tzinfo=KST)),'EVENING_WINDOW_ONLY')
    def test_duplicates(self):
        seen={};now=dt.datetime(2026,10,27,14,50,1,tzinfo=KST)
        self.assertEqual(check(sample(),{'085910'},now,seen)[0],'PLAUSIBLE_UNATTESTED_SESSION')
        self.assertEqual(check(sample(),{'085910'},now,seen)[0],'DUPLICATE_EXCHANGE_SECOND')
    def test_out_of_order(self):
        seen={};now=dt.datetime(2026,10,27,14,50,1,tzinfo=KST)
        check(sample('145001'),{'085910'},now,seen)
        self.assertEqual(check(sample('145000'),{'085910'},now,seen)[0],'OUT_OF_ORDER_EXCHANGE_SECOND')
    def test_closed(self):
        now=dt.datetime(2026,10,27,19,0,1,tzinfo=KST)
        self.assertEqual(check(sample('190000'),{'085910'},now,{})[0],'VENUE_WINDOW_UNVERIFIED')
    def test_no_order(self):
        from oneil_live_v5_18 import run
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);s=root/'c.csv';s.write_text('date,ticker\n2026-10-27,085910\n')
            with patch.dict('os.environ', {'LIVE_ENABLED':'false','KIS_ALLOW_ORDER':'false'}):
                r=run(s,root/'report.json')
            self.assertEqual(r['orders_sent'],0)
            self.assertFalse(r['exchange_session_phase_attested'])
if __name__=='__main__':unittest.main()

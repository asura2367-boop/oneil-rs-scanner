import unittest
import tempfile
import csv
import os
from pathlib import Path
from unittest.mock import patch
from oneil_live_v5_16 import subscribe_message,frame_type,run
class TestWS(unittest.TestCase):
    def test_venues(self):
        self.assertEqual(subscribe_message('dummy','H0STASP0','005930')['body']['input']['tr_key'],'005930')
        self.assertEqual(frame_type('0|H0NXASP0|001|payload'),'MARKET_DATA_NXT')
        self.assertEqual(frame_type('0|H0STASP0|001|payload'),'MARKET_DATA_KRX')
    def test_unknown(self):
        self.assertEqual(frame_type('0|BAD|001|payload'),'UNRECOGNIZED')
    def test_offline(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'c.csv';out=Path(d)/'out.json'
            p.write_text('date,ticker\n'+datetime.now(ZoneInfo('Asia/Seoul')).date().isoformat()+',085910\n')
            with patch.dict(os.environ,{'LIVE_ENABLED':'false','KIS_ALLOW_ORDER':'false'}):
                result=run(p,out)
            self.assertEqual(result['orders_sent'],0)
            self.assertEqual(result['status'],'OFFLINE_ONLY')
    def test_fail_closed(self):
        with patch.dict(os.environ,{'LIVE_ENABLED':'true','KIS_ALLOW_ORDER':'false'}):
            with self.assertRaises(RuntimeError):
                run(Path('nothing.csv'),Path('nothing.json'))
if __name__=='__main__': unittest.main()

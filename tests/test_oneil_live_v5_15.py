import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from oneil_live_v5_15 import run, verify, KST

class Tests(unittest.TestCase):
    def setUp(self):
        self.now=dt.datetime(2026,10,27,14,0,tzinfo=KST)
        self.record=dict(ticker='085910',venue='KRX',channel='H0STASP0',source_verified=True,
                         schema_verified=True,exchange_timestamp=self.now.isoformat(),
                         received_at=self.now.isoformat(),best_bid='10000',best_ask='10010',
                         bid_qty=50,ask_qty=60,market_phase='REGULAR')
    def test_cannot_approve_arbitrary_json(self):
        result=verify(self.record,self.now)
        self.assertEqual(result['review_status'],'DENY')
        self.assertIn('LIVE_FEED_ATTESTATION_NOT_IMPLEMENTED',result['reason_codes'])
    def test_stale(self):
        self.record['received_at']=(self.now-dt.timedelta(seconds=10)).isoformat()
        self.assertIn('QUOTE_STALE_OR_FUTURE',verify(self.record,self.now)['reason_codes'])
    def test_wrong_channel(self):
        self.record['venue']='NXT'
        self.assertIn('VENUE_CHANNEL_MISMATCH',verify(self.record,self.now)['reason_codes'])
    def test_spread(self):
        self.record['best_ask']='10400'
        self.assertIn('SPREAD_TOO_WIDE',verify(self.record,self.now)['reason_codes'])
    def test_missing(self):
        self.assertIn('INCOMPLETE_OR_INVALID_OBSERVATION',verify({},self.now)['reason_codes'])
    def test_no_input(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.dict('os.environ',{'LIVE_ENABLED':'false','KIS_ALLOW_ORDER':'false'}):
                r=run(None,Path(d)/'out.json',self.now)
            self.assertEqual(r['source_status'],'NO_TRUSTED_STREAM_INPUT')
    def test_order_flag_hard_stop(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.dict('os.environ',{'LIVE_ENABLED':'true','KIS_ALLOW_ORDER':'false'}):
                with self.assertRaises(RuntimeError): run(None,Path(d)/'out.json',self.now)
if __name__=='__main__':unittest.main()

import os
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from oneil_live_v5_14 import review
KST=ZoneInfo('Asia/Seoul')
OBJ={'mode':'LIVE_INTENTS_PREVIEW_ONLY','gate':'DENY','orders_sent':0,
     'intents':[{'status':'REVIEW_ONLY_UNPRICED','ticker':'085910','price_krw':None,'quantity':None}]}
NOW=datetime(2026,10,27,13,30,tzinfo=KST)
class Safety(unittest.TestCase):
 def test_offline(self):
  r=review(OBJ,NOW,env={'LIVE_ENABLED':'false','KIS_ALLOW_ORDER':'false'})
  self.assertEqual(r['rows'][0]['read_status'],'NOT_REQUESTED')
  self.assertEqual(r['gate'],'DENY')
 def test_observed_doesnt_verify_price(self):
  r=review(OBJ,NOW,broker_read=True,observer=lambda codes,now:{'085910':'OBSERVED_TIMESTAMP_AND_VENUE_UNVERIFIED'},env={'LIVE_ENABLED':'false','KIS_ALLOW_ORDER':'false'})
  self.assertIsNone(r['rows'][0]['approved_quantity'])
  self.assertFalse(r['rows'][0]['quote_fresh_verified'])
 def test_incomplete_fails_closed(self):
  r=review(OBJ,NOW,broker_read=True,observer=lambda codes,now:{},env={'LIVE_ENABLED':'false','KIS_ALLOW_ORDER':'false'})
  self.assertEqual(r['rows'][0]['read_status'],'BROKER_READ_BLOCKED')
 def test_live_flag_blocks(self):
  with self.assertRaises(RuntimeError):
   review(OBJ,NOW,env={'LIVE_ENABLED':'true','KIS_ALLOW_ORDER':'false'})
 def test_bad_intent_blocks(self):
  with self.assertRaises(ValueError):
   review({**OBJ,'orders_sent':1},NOW,env={'LIVE_ENABLED':'false','KIS_ALLOW_ORDER':'false'})
if __name__=='__main__':unittest.main()

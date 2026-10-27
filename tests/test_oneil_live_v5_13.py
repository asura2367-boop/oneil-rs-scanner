import unittest
from datetime import datetime
from zoneinfo import ZoneInfo
from oneil_live_v5_13 import review

OBJ={'mode':'LIVE_INTENTS_PREVIEW_ONLY','gate':'DENY','orders_sent':0,'intents':[
    {'ticker':'085910','status':'REVIEW_ONLY_UNPRICED','quantity':None,'price_krw':None},
    {'ticker':'126340','status':'REVIEW_ONLY_UNPRICED','quantity':None,'price_krw':None}]}
NOW=datetime(2026,10,27,12,30,tzinfo=ZoneInfo('Asia/Seoul'))
class Tests(unittest.TestCase):
    def test_default_offline(self):
        r=review(OBJ,NOW,env={})
        self.assertEqual(r['broker_status'],'NOT_REQUESTED')
        self.assertEqual(r['orders_sent'],0)
    def test_observed_holdings(self):
        r=review(OBJ,NOW,broker_read=True,observer=lambda: ({'085910'},{'126340'}),env={})
        self.assertEqual(r['checks'][0]['held'],'YES')
        self.assertEqual(r['checks'][1]['unfilled'],'YES')
        self.assertIsNone(r['checks'][0]['preview_quantity'])
    def test_failure_closes(self):
        def fail():raise RuntimeError('DO_NOT_LEAK_SECRETS')
        r=review(OBJ,NOW,broker_read=True,observer=fail,env={})
        self.assertEqual(r['broker_status'],'BROKER_READ_BLOCKED')
        self.assertNotIn('DO_NOT_LEAK',str(r))
    def test_live_flag_denied(self):
        with self.assertRaises(RuntimeError):
            review(OBJ,NOW,env={'LIVE_ENABLED':'true'})
    def test_duplicate_denied(self):
        data={**OBJ,'intents':OBJ['intents']*2}
        with self.assertRaises(ValueError):review(data,NOW,env={})
if __name__=='__main__': unittest.main()

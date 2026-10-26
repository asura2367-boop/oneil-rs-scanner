import unittest
from kis_live_v4_2 import build
from kis_live_v4 import SafetyBlock

ID='a'*20

def sample(status='PREVIEW_ELIGIBLE_NO_ORDER'):
    return {'mode':'READ_ONLY_PREFLIGHT_V4_1','live_enabled':False,'orders_sent':0,
            'decisions':[{'intent_id':ID,'ticker':'052460','status':status,
                          'preview_qty':34,'preview_amount_krw':196520,'orders_sent':0}]}

class V42Tests(unittest.TestCase):
    def test_first_record(self):
        ledger,report=build(sample(),None)
        self.assertEqual(report['results'][0]['status'],'RECORDED_ONCE_NO_ORDER')
        self.assertIn(ID,ledger['recorded'])
    def test_replay_is_blocked(self):
        ledger,_=build(sample(),None)
        _,report=build(sample(),ledger)
        self.assertEqual(report['results'][0]['status'],'BLOCKED_ALREADY_RECORDED')
    def test_noneligible_not_reserved(self):
        ledger,_=build(sample('BLOCKED_QUOTE_ERROR'),None)
        self.assertFalse(ledger['recorded'])
    def test_live_enabled_denied(self):
        p=sample();p['live_enabled']=True
        with self.assertRaises(SafetyBlock):build(p,None)
    def test_nonzero_orders_denied(self):
        p=sample();p['decisions'][0]['orders_sent']=1
        with self.assertRaises(SafetyBlock):build(p,None)
    def test_bad_amount_denied(self):
        p=sample();p['decisions'][0]['preview_amount_krw']=200001
        with self.assertRaises(SafetyBlock):build(p,None)
    def test_conflict_denied(self):
        with self.assertRaises(SafetyBlock):build(sample(),{'schema_version':1,'recorded':{ID:{'ticker':'048910'}}})
    def test_bad_ledger_denied(self):
        with self.assertRaises(SafetyBlock):build(sample(),{'schema_version':0,'recorded':{}})

if __name__=='__main__':unittest.main()

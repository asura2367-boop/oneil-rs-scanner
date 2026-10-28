import unittest
from unittest.mock import patch
from v519_pg_ledger import LedgerBlocked
import v519_durable_submit as m

class DurableTests(unittest.TestCase):
    def test_id_is_deterministic(self):
        args=('12345678','2026-10-29','222800','BUY','rs97')
        self.assertEqual(m.make_intent_id(*args),m.make_intent_id(*args))
    def test_unapproved_never_submits(self):
        calls=[]
        with self.assertRaisesRegex(LedgerBlocked,'APPROVAL'):
            m.submit_once(intent_id='a'*64,fingerprint='b'*64,ticker='222800',side='BUY',quantity=1,
                price=129100,verified=False,approved=True,broker_submit=lambda *x:calls.append(x))
        self.assertEqual(calls,[])
    def test_legacy_boolean_approval_cannot_submit(self):
        calls=[]
        with patch.object(m,'record_intent') as reserve:
            with self.assertRaisesRegex(LedgerBlocked,'RELEASE_LOCKED'):
                m.submit_once(intent_id='a'*64,fingerprint='b'*64,ticker='222800',side='BUY',quantity=10,
                    price=129100,verified=True,approved=True,broker_submit=lambda *a:calls.append(a))
        reserve.assert_not_called()
        self.assertEqual(calls,[])
if __name__=='__main__': unittest.main()

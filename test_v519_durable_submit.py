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
    def test_timeout_never_retries(self):
        calls=[]
        def broker(*args):
            calls.append(args)
            raise TimeoutError()
        with patch.object(m,'record_intent'):
            with self.assertRaisesRegex(LedgerBlocked,'NO_RETRY'):
                m.submit_once(intent_id='a'*64,fingerprint='b'*64,ticker='222800',side='BUY',quantity=1,
                    price=129100,verified=True,approved=True,broker_submit=broker)
        self.assertEqual(len(calls),1)
    def test_acknowledgement(self):
        with patch.object(m,'record_intent'), patch.object(m,'persist_acknowledgement') as ack:
            out=m.submit_once(intent_id='a'*64,fingerprint='b'*64,ticker='222800',side='BUY',quantity=1,
                price=129100,verified=True,approved=True,
                broker_submit=lambda *args:{'rt_cd':'0','output':{'ODNO':'12345'}})
        self.assertEqual(out['status'],'ACKNOWLEDGED')
        ack.assert_called_once_with('a'*64,'12345')
if __name__=='__main__': unittest.main()

import unittest
from v519_pg_ledger import reserve, LedgerBlocked
class FakeCursor:
    def __init__(self): self.calls=[]; self.i=0
    def execute(self,sql,params=None): self.calls.append((sql,params))
    def fetchone(self):
        self.i+=1
        return [(False,), (0,), ('intent',)][self.i-1]
    def __enter__(self): return self
    def __exit__(self,*args): pass
class FakeConn:
    def __init__(self): self.cur=FakeCursor()
    def cursor(self): return self.cur
class Tests(unittest.TestCase):
    def test_reservation_does_not_submit_order(self):
        c=FakeConn()
        r=reserve(c,intent_id='a'*32,account_fingerprint='b'*64,ticker='005930',side='BUY',venue='KRX',quantity=1)
        self.assertEqual(r['orders_sent'],0)
        self.assertEqual(len(c.cur.calls),3)
    def test_invalid_quantity_rejected(self):
        with self.assertRaises(LedgerBlocked):
            reserve(FakeConn(),intent_id='a'*32,account_fingerprint='b'*64,ticker='005930',side='BUY',venue='KRX',quantity=0)
if __name__=='__main__': unittest.main()

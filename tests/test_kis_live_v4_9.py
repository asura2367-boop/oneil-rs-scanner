import unittest
from kis_live_v4_9 import allocate, Blocked

class BudgetTests(unittest.TestCase):
    def test_default(self):
        r=allocate(1000000,[{'ticker':'052460','price':5000,'broker_max_qty':100}])
        self.assertEqual((r['reserve_krw'],r['trading_budget_krw'],r['per_stock_max_krw']), (400000,600000,180000))
        self.assertEqual(r['orders_sent'],0)
        self.assertEqual(r['gate'],'DENY')
    def test_custom(self):
        r=allocate(1000000,[],25,20)
        self.assertEqual((r['reserve_krw'],r['trading_budget_krw'],r['per_stock_max_krw']), (250000,750000,150000))
    def test_zero(self): self.assertEqual(allocate(0,[])['trading_budget_krw'],0)
    def test_overallocation(self):
        r=allocate(1000000,[{'ticker':str(i).zfill(6),'price':1000,'broker_max_qty':100000} for i in range(10)])
        self.assertGreaterEqual(r['unallocated_budget_krw'],0)
        self.assertLessEqual(sum(x['preview_amount_krw'] for x in r['rows']),600000)
    def test_duplicate(self):
        with self.assertRaises(Blocked): allocate(10000,[{'ticker':'123456','price':100,'broker_max_qty':10}]*2)
    def test_invalid_pct(self):
        for value in (-1,101,'NaN','abc'):
            with self.assertRaises(Blocked): allocate(10000,[],value,30)
    def test_non_integer(self):
        with self.assertRaises(Blocked): allocate(10.5,[])
    def test_buffer(self):
        r=allocate(1000000,[{'ticker':'111111','price':10000,'broker_max_qty':10000}],40,100)
        self.assertLessEqual(r['rows'][0]['preview_qty']*10000*1.002,600000)
if __name__=='__main__': unittest.main()

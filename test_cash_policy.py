import unittest
from v519_cash_policy import limits,quantity
class CashTests(unittest.TestCase):
 def test_three_million(self):
  b=limits(3000000,0,3000000)
  self.assertEqual((b['reserve'],b['trading'],b['per_stock']),(1200000,1800000,540000))
 def test_existing_positions(self):
  self.assertEqual(quantity(1000000,550000,1000000,10000,1000),5)
 def test_invalid(self):
  with self.assertRaises(ValueError):limits(-1,0,0)
if __name__=='__main__':unittest.main()

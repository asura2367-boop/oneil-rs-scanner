import unittest
from oneil_v519_cash_sizing import size_from_cash
class T(unittest.TestCase):
 def test_five_million(self):
  x=size_from_cash(5000000,5000000,10000,cash_verified=True,orderable_verified=True)
  self.assertEqual(x["simulated_quantity"],90)
 def test_variable_cash(self):
  x=size_from_cash(2000000,2000000,10000,cash_verified=True,orderable_verified=True)
  self.assertEqual(x["simulated_quantity"],36)
 def test_broker_limit(self):
  x=size_from_cash(5000000,200000,10000,cash_verified=True,orderable_verified=True)
  self.assertEqual(x["simulated_quantity"],20)
 def test_positions_reduce_limit(self):
  x=size_from_cash(5000000,5000000,10000,stock_exposure_krw=800000,cash_verified=True,orderable_verified=True)
  self.assertEqual(x["simulated_quantity"],10)
 def test_unknown_cash(self):
  with self.assertRaises(ValueError):size_from_cash(5000000,5000000,10000)
 def test_no_order(self):
  x=size_from_cash(1000000,1000000,10000,cash_verified=True,orderable_verified=True)
  self.assertEqual((x["gate"],x["orders_sent"],x["authorized"]),("DENY",0,False))

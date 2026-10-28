import datetime as dt
import unittest
from oneil_v519_cash_bridge import *

class CashBridgeTests(unittest.TestCase):
    def setUp(self):
        self.balance={'rt_cd':'0','output2':[{'dnca_tot_amt':'5000000'}]}
        self.power={'rt_cd':'0','output':{'nrcvb_buy_amt':'3000000','ord_psbl_cash':'4000000'}}
    def assess(self):return assess_readonly(self.balance,self.power,'144960',10000,held=set(),unfilled=set(),env={})
    def test_readonly_cash(self):self.assertTrue(self.assess()['cash_and_buying_power_observed'])
    def test_hard_deny(self):self.assertEqual((self.assess()['gate'],self.assess()['orders_sent']),('DENY',0))
    def test_no_cash_output(self):self.assertNotIn('5000000',str(self.assess()))
    def test_no_qty_output(self):self.assertNotIn('simulated_qty',self.assess())
    def test_variable_cash(self):
        self.balance['output2'][0]['dnca_tot_amt']='2000000'
        self.assertTrue(self.assess()['cash_and_buying_power_observed'])
    def test_missing_cash(self):
        del self.balance['output2'][0]['dnca_tot_amt']
        self.assertFalse(self.assess()['cash_and_buying_power_observed'])
    def test_missing_orderable(self):
        del self.power['output']['ord_psbl_cash']
        self.assertFalse(self.assess()['cash_and_buying_power_observed'])
    def test_held(self):
        r=assess_readonly(self.balance,self.power,'144960',10000,held={'144960'},unfilled=set(),env={})
        self.assertIn('ALREADY_HELD_OR_UNFILLED',r['issues'])
    def test_unverified_positions(self):self.assertIn('ACCOUNT_POSITIONS_UNVERIFIED',assess_readonly(self.balance,self.power,'144960',10000,env={})['issues'])
    def test_order_flag(self):
        with self.assertRaises(EvidenceBlocked):assess_readonly(self.balance,self.power,'144960',10000,env={'KIS_ALLOW_ORDER':'true'})
    def test_negative_is_invalid(self):
        self.balance['output2'][0]['dnca_tot_amt']='-1'
        self.assertFalse(self.assess()['cash_and_buying_power_observed'])
    def test_bool_is_invalid(self):
        self.balance['output2'][0]['dnca_tot_amt']=True
        self.assertFalse(self.assess()['cash_and_buying_power_observed'])

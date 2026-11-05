import unittest
from dataclasses import replace
from datetime import timedelta
from oneil_execution_v2.domain import Blocked
from oneil_policy_v2.evidence import Candidate, TechnicalExit
from oneil_policy_v2.planner import budget, charged, plan_automatic, plan_manual_add, plan_manual_exit
from policy_support import (NOW, ACCOUNT, account, candidates, controls, evidence, manual, market,
                            policy, position, sessions)


class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.p = policy()
        self.a = account()
        self.c = controls(self.p)
        self.rows = candidates()
        self.markets = {('005930','BUY'): market()}
        self.sessions = sessions()

    def plan(self, **overrides):
        args = dict(policy=self.p, account=self.a, controls=self.c, candidates=self.rows,
                    markets=self.markets, sessions=self.sessions, now=NOW, technical={})
        args.update(overrides)
        return plan_automatic(**args)

    def test_current_deposit_allocation_no_equity_sizing(self):
        b = budget(self.p, self.a, NOW)
        self.assertEqual((b.trading_krw, b.reserve_krw, b.per_stock_krw), (630000,370000,176400))
        b2 = budget(self.p, replace(self.a, deposit_krw=2000000, equity_krw=100000000), NOW)
        self.assertEqual(b2.per_stock_krw, 352800)
        self.assertEqual(budget(self.p, replace(self.a, equity_krw=100000000), NOW), b)

    def test_all_positions_conservatively_count(self):
        a = account((position(cost_krw=100000, market_value_krw=150000, managed_quantity=0),),
                    reported_invested_krw=200000)
        self.assertEqual(budget(self.p,a,NOW).remaining_krw, 430000)

    def test_simulation_proposal_has_no_authority(self):
        row = self.plan()[0]
        self.assertFalse(row.authorized)
        self.assertEqual((row.gate, row.orders_sent), ('DENY',0))
        self.assertEqual(row.order.policy_version, self.p.core_risk.version)
        self.assertEqual(row.order.quantity,175)
        self.assertEqual(row.buffered_cost_krw,175875)
        self.assertEqual(row.order.expires_at,NOW+timedelta(seconds=5))

    def test_fee_ceiling_property(self):
        for price in range(1,1000,17):
            for cap in range(0,2000,19):
                quantity = cap*1000//(price*1005)
                self.assertLessEqual(charged(quantity,price),cap)
                self.assertGreater(charged(quantity+1,price),cap)

    def test_fee_share_boundary(self):
        self.markets[('005930','BUY')] = market(broker_orderable_krw=1004)
        self.assertEqual(self.plan(), ())
        self.markets[('005930','BUY')] = market(broker_orderable_krw=1005)
        self.assertEqual(self.plan()[0].order.quantity,1)

    def test_broker_quantity_and_book_caps(self):
        self.markets[('005930','BUY')] = market(broker_orderable_quantity=8, available_quantity=3)
        self.assertEqual(self.plan()[0].order.quantity,3)

    def test_core_limits_are_not_replaced_by_allocation(self):
        p = replace(self.p, risk=replace(self.p.risk, max_order_krw=10050))
        self.assertEqual(self.plan(policy=p, controls=controls(p))[0].order.quantity,10)

    def test_reserve_100_or_per_stock_zero(self):
        for changes in ({'reserve_pct':100},{'per_stock_pct':0}):
            p=replace(self.p,**changes)
            self.assertEqual(self.plan(policy=p,controls=controls(p)),())

    def test_invalid_evidence_fail_closed(self):
        for value in (False,1,None):
            m = market(evidence=evidence('quote',verified=value))
            with self.subTest(value=value), self.assertRaises(Blocked):
                self.plan(markets={('005930','BUY'):m})

    def test_stale_and_future_market(self):
        for delta in (-6,1):
            m = market(evidence=evidence('quote',at=NOW+timedelta(seconds=delta)))
            with self.subTest(delta=delta), self.assertRaisesRegex(Blocked,'EVIDENCE_STALE'):
                self.plan(markets={('005930','BUY'):m})

    def test_expiry_uses_remaining_evidence_age(self):
        m=market(evidence=evidence('quote',at=NOW-timedelta(seconds=4)))
        self.assertEqual(self.plan(markets={('005930','BUY'):m})[0].order.expires_at,NOW+timedelta(seconds=1))
        m=replace(m,evidence=evidence('quote',at=NOW-timedelta(seconds=5)))
        with self.assertRaisesRegex(Blocked,'PROPOSAL_EVIDENCE_EXPIRED'):
            self.plan(markets={('005930','BUY'):m})

    def test_account_and_history_required(self):
        for changes in ({'complete':False}, {'history_complete':False},
                        {'reflected_history_reference':'different'}, {'unresolved_orders':('ack',)},
                        {'evidence':evidence('balance',at=NOW-timedelta(seconds=11))},
                        {'history_evidence':evidence('history',at=NOW-timedelta(seconds=11))}):
            with self.subTest(changes=changes), self.assertRaises(Blocked):
                self.plan(account=replace(self.a,**changes))

    def test_halt_and_unknown_halt(self):
        for value in (True,None,0):
            with self.subTest(value=value),self.assertRaisesRegex(Blocked,'HALTED_OR_UNKNOWN'):
                self.plan(controls=replace(self.c,halted=value))

    def test_policy_change_invalidates_controls(self):
        with self.assertRaisesRegex(Blocked,'POLICY_CHANGED'):
            self.plan(policy=replace(self.p,reserve_pct=38))

    def test_broker_power_account_mismatch(self):
        with self.assertRaisesRegex(Blocked,'QUOTE_IDENTITY_MISMATCH'):
            self.plan(markets={('005930','BUY'):market(account='b'*64)})

    def test_market_identity_venue_and_executable(self):
        for changes in ({'ticker':'000660'},{'side':'SELL'},{'venue':'UNKNOWN'},{'executable':False}):
            with self.subTest(changes=changes),self.assertRaises(Blocked):
                self.plan(markets={('005930','BUY'):market(**changes)})

    def test_weekday_does_not_override_holiday_or_missing_calendar(self):
        for changes in ({'business_day':False},{'business_day':None},
                        {'calendar_evidence':evidence('calendar',verified=False)}):
            with self.subTest(changes=changes),self.assertRaises(Blocked):
                self.plan(sessions=sessions(**changes))

    def test_unverified_capabilities_fail(self):
        for changes in ({'capability_evidence':evidence('capability',verified=False)},
                        {'eligible_tickers':frozenset()}, {'allowed_sides':frozenset({'SELL'})},
                        {'order_code':''}, {'venue':'NXT'}, {'trading_day':'2026-11-04'}):
            with self.subTest(changes=changes),self.assertRaises(Blocked):
                self.plan(sessions=sessions(**changes))

    def test_exact_session_close_blocks(self):
        with self.assertRaisesRegex(Blocked,'OUTSIDE_VERIFIED_SESSION'):
            self.plan(sessions=sessions(closes_at=NOW))

    def test_explicit_session_can_cover_extraordinary_schedule(self):
        # No built-in weekday/window assumptions override explicit evidence.
        self.assertEqual(len(self.plan(sessions=sessions(opens_at=NOW))),1)

    def test_candidate_rs_order_and_old_filters_not_added(self):
        rows=candidates(Candidate('005930',90),Candidate('000660',99),Candidate('035420',99))
        markets={('000660','BUY'):market('000660'),('035420','BUY'):market('035420')}
        self.assertEqual(self.plan(candidates=rows,markets=markets)[0].order.ticker,'000660')

    def test_candidate_threshold(self):
        self.assertEqual(self.plan(candidates=candidates(Candidate('005930',89))),())

    def test_candidate_invalid_rows_not_silently_dropped(self):
        for rows in ((Candidate('005930',100),),(Candidate('005930',True),),
                     (Candidate('005930',99),Candidate('005930',98))):
            with self.subTest(rows=rows),self.assertRaises(Blocked):
                self.plan(candidates=candidates(*rows))

    def test_candidate_source_session_must_be_attested(self):
        for changes in ({'accepted_source_session':'2026-11-03'},
                        {'session_evidence':evidence('calendar',verified=False)},
                        {'selection_policy':'V4B_FLOW_V1'}, {'trading_day':'2026-11-04'}):
            with self.subTest(changes=changes),self.assertRaises(Blocked):
                self.plan(candidates=candidates(**changes))

    def test_no_rebuy_or_auto_add(self):
        p=replace(self.p,automatic_sell_enabled=False)
        for a in (account(bought_today=frozenset({'005930'})),account((position(),))):
            self.assertEqual(self.plan(policy=p,controls=controls(p),account=a),())

    def test_stock_count_includes_unmanaged(self):
        p=replace(self.p,max_stock_count=1,automatic_sell_enabled=False)
        a=account((position('000660',managed_quantity=0),))
        self.assertEqual(self.plan(policy=p,controls=controls(p),account=a),())

    def test_buy_disabled_does_not_need_candidate(self):
        p=replace(self.p,automatic_buy_enabled=False)
        self.assertEqual(self.plan(policy=p,controls=controls(p),candidates=None),())

    def test_daily_loss_blocks_buy(self):
        with self.assertRaisesRegex(Blocked,'DAILY_LOSS_LIMIT'):
            self.plan(account=replace(self.a,daily_loss_krw=self.p.risk.max_daily_loss_krw))

    def test_exits_preserve_independent_switches(self):
        a=account((position(),))
        profit={('005930','SELL'):market(side='SELL',price=1120)}
        loss={('005930','SELL'):market(side='SELL',price=940)}
        p=replace(self.p,automatic_buy_enabled=False)
        self.assertEqual(self.plan(policy=p,controls=controls(p),account=a,markets=profit,candidates=None)[0].reason,'TAKE_PROFIT')
        self.assertEqual(self.plan(policy=p,controls=controls(p),account=a,markets=loss,candidates=None),())
        p=replace(p,automatic_sell_enabled=False,stop_loss_enabled=True)
        self.assertEqual(self.plan(policy=p,controls=controls(p),account=a,markets=loss,candidates=None)[0].reason,'STOP_LOSS')
        self.assertEqual(self.plan(policy=p,controls=controls(p),account=a,markets=profit,candidates=None),())

    def test_exit_priority_does_not_read_candidates(self):
        row=self.plan(account=account((position(),)),markets={('005930','SELL'):market(side='SELL',price=1120)},candidates=None)[0]
        self.assertEqual(row.order.side,'SELL')

    def test_only_managed_sellable_shares(self):
        a=account((position(sellable_quantity=7,managed_quantity=4),))
        row=self.plan(account=a,markets={('005930','SELL'):market(side='SELL',price=1120)})[0]
        self.assertEqual(row.order.quantity,4)

    def test_daily_loss_does_not_disable_protective_exit(self):
        p=replace(self.p,stop_loss_enabled=True)
        a=account((position(),),daily_loss_krw=p.risk.max_daily_loss_krw)
        self.assertEqual(self.plan(policy=p,controls=controls(p),account=a,
            markets={('005930','SELL'):market(side='SELL',price=940)})[0].order.side,'SELL')

    def test_technical_exit_needs_evidence_and_uses_profit_sign(self):
        signal=TechnicalExit('005930','2026-11-04','2026-11-04','BB_ICHI_TREND_V1',True,evidence('technical'))
        args=dict(account=account((position(),)),markets={('005930','SELL'):market(side='SELL',price=1001)},
                  technical={'005930':signal})
        self.assertEqual(self.plan(**args)[0].reason,'TAKE_PROFIT_SIGNAL')
        args['technical']={'005930':replace(signal,evidence=evidence('technical',verified=False))}
        with self.assertRaisesRegex(Blocked,'EVIDENCE_UNVERIFIED'):
            self.plan(**args)

    def test_invalid_managed_provenance_and_cost_fail(self):
        for changes in ({'managed_quantity':11},{'sellable_quantity':11},{'managed_reference':''},
                        {'average_price':'NaN'},{'cost_krw':9999}):
            with self.subTest(changes=changes),self.assertRaises(Blocked):
                self.plan(account=account((position(**changes),)))

    def test_manual_add_exact_quantity_and_identity(self):
        a=account((position(),)); req=manual(self.p)
        markets={('005930','BUY'):market(),('005930','SELL'):market(side='SELL')}
        row=plan_manual_add(self.p,a,self.c,req,markets,self.sessions,now=NOW,technical={})
        self.assertEqual((row.order.quantity,row.reason,row.manual_request_id),(10,'OWNER_AVERAGING',req.request_id))
        self.assertFalse(row.authorized)

    def test_manual_add_rejects_instead_of_clipping(self):
        a=account((position(),)); req=manual(self.p)
        markets={('005930','BUY'):market(broker_orderable_quantity=9),('005930','SELL'):market(side='SELL')}
        with self.assertRaisesRegex(Blocked,'MANUAL_BUY_LIMIT_EXCEEDED'):
            plan_manual_add(self.p,a,self.c,req,markets,self.sessions,now=NOW,technical={})

    def test_manual_add_fences(self):
        a=account((position(),)); req=manual(self.p)
        markets={('005930','BUY'):market(),('005930','SELL'):market(side='SELL')}
        for changes in ({'expected_holding_quantity':9},{'limit_price':999},{'policy_version':'old'},
                        {'account':'b'*64},{'created_at':NOW+timedelta(seconds=1)},
                        {'expires_at':NOW},{'expires_at':NOW+timedelta(seconds=2401)},
                        {'owner_confirmation_reference':''}):
            with self.subTest(changes=changes),self.assertRaises(Blocked):
                plan_manual_add(self.p,a,self.c,replace(req,**changes),markets,self.sessions,now=NOW,technical={})

    def test_manual_add_has_exit_priority(self):
        markets={('005930','BUY'):market(),('005930','SELL'):market(side='SELL',price=1120)}
        with self.assertRaisesRegex(Blocked,'EXIT_PRIORITY'):
            plan_manual_add(self.p,account((position(),)),self.c,manual(self.p),markets,self.sessions,now=NOW,technical={})


    def test_manual_add_buffer_applies_to_every_core_money_cap(self):
        p=replace(self.p,automatic_sell_enabled=False,stop_loss_enabled=False)
        a=account((position(),))
        for key, value in (('max_order_krw',10000),('max_daily_buy_krw',10000),
                           ('max_position_krw',20000),('max_gross_exposure_krw',20000)):
            restricted=replace(p,risk=replace(p.risk,**{key:value}))
            with self.subTest(key=key),self.assertRaisesRegex(Blocked,'MANUAL_BUY_LIMIT_EXCEEDED'):
                plan_manual_add(restricted,a,controls(restricted),manual(restricted),
                    {('005930','BUY'):market()},self.sessions,now=NOW,technical={})

    def test_valid_first_exit_survives_later_missing_quote(self):
        p=replace(self.p,stop_loss_enabled=True)
        a=account((position('000660'),position('005930')))
        result=self.plan(policy=p,controls=controls(p),account=a,candidates=None,
            markets={('000660','SELL'):market('000660',side='SELL',price=900)})
        self.assertEqual((len(result),result[0].order.ticker,result[0].reason),(1,'000660','STOP_LOSS'))

    def test_unknown_exit_still_blocks_discretionary_buy(self):
        with self.assertRaisesRegex(Blocked,'MARKET_EVIDENCE_REQUIRED'):
            self.plan(account=account((position('000660'),)))

    def test_manual_depth_limit_rejects_exact_quantity(self):
        p=replace(self.p,automatic_sell_enabled=False,stop_loss_enabled=False)
        with self.assertRaisesRegex(Blocked,'BOOK_SIZE_EXCEEDED'):
            plan_manual_add(p,account((position(),)),controls(p),manual(p),
                {('005930','BUY'):market(available_quantity=9)},self.sessions,now=NOW,technical={})

    def test_manual_rebuy_and_buy_switch(self):
        p=replace(self.p,automatic_sell_enabled=False,stop_loss_enabled=False)
        with self.assertRaisesRegex(Blocked,'ALREADY_BOUGHT_TODAY'):
            plan_manual_add(p,account((position(),),bought_today=frozenset({'005930'})),controls(p),manual(p),
                {('005930','BUY'):market()},self.sessions,now=NOW,technical={})
        p=replace(p,automatic_buy_enabled=False)
        with self.assertRaisesRegex(Blocked,'BUY_DISABLED'):
            plan_manual_add(p,account((position(),)),controls(p),manual(p),{},self.sessions,now=NOW,technical={})

    def test_technical_absent_does_not_block_threshold_exit(self):
        p=replace(self.p,stop_loss_enabled=True)
        self.assertEqual(self.plan(policy=p,controls=controls(p),account=account((position(),)),
            markets={('005930','SELL'):market(side='SELL',price=940)},technical={})[0].reason,'STOP_LOSS')

    def test_manual_exit_unverified(self):
        with self.assertRaisesRegex(Blocked,'MANUAL_EXIT_UNVERIFIED_NOT_IMPLEMENTED'):
            plan_manual_exit()

if __name__ == '__main__':
    unittest.main()

import unittest
from dataclasses import replace
from datetime import datetime
from oneil_execution_v2.domain import Blocked
from oneil_policy_v2.policy import import_legacy_policy
from policy_support import policy, export, runtime, constraints


class ImportTests(unittest.TestCase):
    def load(self, ex=None, rt=None, cs=None):
        return import_legacy_policy(export() if ex is None else ex, runtime=runtime() if rt is None else rt,
            constraints=constraints() if cs is None else cs, source_reference='simulation:export',
            migration_reference='simulation:constraints-review')

    def test_nondefault_values_preserved(self):
        p = self.load()
        self.assertEqual((p.reserve_pct, p.per_stock_pct, p.stop_loss_pct, p.take_profit_pct), (37, 28, 6, 12))
        self.assertEqual((p.buy_revision, p.sell_revision, p.risk_revision), (9, 7, 8))
        self.assertTrue(p.automatic_buy_enabled)
        self.assertTrue(p.automatic_sell_enabled)
        self.assertFalse(p.stop_loss_enabled)

    def test_every_export_key_required(self):
        for key in export():
            with self.subTest(key=key), self.assertRaises(Blocked):
                ex = export(); del ex[key]; self.load(ex=ex)

    def test_every_runtime_key_required(self):
        for key in runtime():
            with self.subTest(key=key), self.assertRaises(Blocked):
                rt = runtime(); del rt[key]; self.load(rt=rt)

    def test_every_constraint_required(self):
        for key in constraints():
            with self.subTest(key=key), self.assertRaises(Blocked):
                cs = constraints(); del cs[key]; self.load(cs=cs)
        for key in constraints()['risk']:
            with self.subTest(risk=key), self.assertRaises(Blocked):
                cs = constraints(); del cs['risk'][key]; self.load(cs=cs)

    def test_nested_export_required(self):
        for section in ('risk', 'sell'):
            for key in export()[section]:
                with self.subTest(section=section, key=key), self.assertRaises(Blocked):
                    ex = export(); del ex[section][key]; self.load(ex=ex)

    def test_rejects_full_response_and_credentials(self):
        ex = export(); ex['credential'] = 'not-a-real-secret'
        with self.assertRaisesRegex(Blocked, 'EXPLICIT_LEGACY_EXPORT_REQUIRED'):
            self.load(ex=ex)

    def test_never_reinterprets_legacy_sell(self):
        for invalid in (False, None, 1, 'true'):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(Blocked, 'LEGACY_SELL_SEMANTICS_CHANGED'):
                rt = runtime(); rt['automatic_sell_enabled'] = invalid; self.load(rt=rt)

    def test_unknown_strategy_fails(self):
        rt = runtime(); rt['selection_policy'] = 'V519_FLOW_CHANNEL'
        with self.assertRaisesRegex(Blocked, 'UNVERIFIED_STRATEGY_VARIANT'):
            self.load(rt=rt)

    def test_invalid_controls_and_risk(self):
        for value in (1, None, 'false'):
            with self.subTest(value=value), self.assertRaises(Blocked):
                ex = export(); ex['enabled'] = value; self.load(ex=ex)
        for value in (-1, 101, True, '37'):
            with self.subTest(value=value), self.assertRaises(Blocked):
                ex = export(); ex['risk']['reserve_pct'] = value; self.load(ex=ex)

    def test_thresholds_require_explicit_valid_integer(self):
        for key in ('stop_loss_pct', 'take_profit_pct'):
            for value in (0, 51, True, 5.0):
                with self.subTest(key=key, value=value), self.assertRaises(Blocked):
                    rt = runtime(); rt[key] = value; self.load(rt=rt)

    def test_naive_source_timestamp_rejected(self):
        ex = export(); ex['observed_at'] = '2026-11-05T00:00:00'
        with self.assertRaisesRegex(Blocked, 'AWARE_CLOCK_REQUIRED'):
            self.load(ex=ex)

    def test_version_deterministic_and_change_sensitive(self):
        p = policy()
        self.assertEqual(p.version, policy().version)
        for key, value in (('reserve_pct', 38), ('automatic_sell_enabled', False),
                           ('max_stock_count', 5), ('risk_revision', 9), ('stop_loss_pct', 7)):
            with self.subTest(key=key):
                self.assertNotEqual(p.version, replace(p, **{key:value}).version)
        self.assertEqual(p.version, p.core_risk.version)

    def test_policy_has_no_fixed_starting_capital(self):
        self.assertNotIn('capital', repr(policy()).lower())

    def test_stock_count_zero_valid_and_no_unbounded_default(self):
        self.assertEqual(policy(max_stock_count=0).validate().max_stock_count, 0)
        for value in (None, -1, True, 10001):
            with self.subTest(value=value), self.assertRaises(Blocked):
                policy(max_stock_count=value).validate()

if __name__ == '__main__':
    unittest.main()

"""Bridge transaction choreography only. These doubles do not prove PG locking."""
from contextlib import contextmanager
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from oneil_control_v2.bridge import CoreSimulationBackend
from oneil_execution_v2.domain import Blocked


class FakeConnection:
    def __init__(self):
        self.events = []
        self.row = {'mode': 'SIMULATION', 'revision': 7}

    @contextmanager
    def transaction(self):
        self.events.append('begin')
        try:
            yield
        except BaseException:
            self.events.append('rollback')
            raise
        else:
            self.events.append('commit')

    def execute(self, sql, params):
        assert 'FOR UPDATE' in sql
        assert params == ('a' * 64,)
        self.events.append('account_row_locked')
        return SimpleNamespace(fetchone=lambda: self.row)


class FakeStore:
    def __init__(self):
        self.conn = FakeConnection()

    def assert_owner(self):
        self.conn.events.append('check_dedicated_thread')


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.store = FakeStore()
        # Constructor's exact real-store check stays enabled in runtime.
        with patch('oneil_control_v2.bridge.PostgresStore', FakeStore):
            self.backend = CoreSimulationBackend(self.store)

    def test_rejects_unreviewed_store_type(self):
        with self.assertRaisesRegex(Blocked, 'ISOLATED_CORE_STORE_REQUIRED'):
            CoreSimulationBackend(FakeStore())

    def test_account_lock_surrounds_core_command(self):
        def effect(owner, revision):
            self.assertEqual((owner.principal, owner.account, revision), ('owner', 'a' * 64, 7))
            self.store.conn.events.append('core_effect')
            return 8
        self.backend.api.halt = effect
        self.assertEqual(self.backend.halt('owner', 'a' * 64, 7), 8)
        self.assertEqual(self.store.conn.events, ['check_dedicated_thread', 'begin', 'account_row_locked', 'core_effect', 'commit'])

    def test_stale_revision_never_calls_core(self):
        self.store.conn.row['revision'] = 8
        self.backend.api.halt = lambda *args: self.fail('must not execute')
        with self.assertRaisesRegex(Blocked, 'CONTROL_REVISION_CONFLICT'):
            self.backend.halt('owner', 'a' * 64, 7)
        self.assertEqual(self.store.conn.events[-1], 'rollback')

    def test_live_mode_never_calls_core(self):
        self.store.conn.row['mode'] = 'LIVE'
        self.backend.api.halt = lambda *args: self.fail('must not execute')
        with self.assertRaisesRegex(Blocked, 'ACCOUNT_NOT_SIMULATION'):
            self.backend.halt('owner', 'a' * 64, 7)

    def test_failure_rolls_back_outer_transaction(self):
        def effect(*args):
            raise RuntimeError('commit uncertain')
        self.backend.api.resume_simulation = effect
        with self.assertRaisesRegex(RuntimeError, 'commit uncertain'):
            self.backend.resume_simulation('owner', 'a' * 64, 7)
        self.assertEqual(self.store.conn.events[-1], 'rollback')

    def test_confirmation_reference_becomes_core_stable_key(self):
        def effect(owner, order, *, idempotency_key):
            self.assertEqual((owner.principal, owner.account, order, idempotency_key),
                             ('owner', 'a' * 64, 'order-object', 'confirmation-reference'))
            return {'state': 'QUEUED'}
        self.backend.api.confirm_order = effect
        self.assertEqual(self.backend.confirm_order('owner', 'a' * 64, 'order-object', 7,
                                                    'confirmation-reference'), {'state': 'QUEUED'})


if __name__ == '__main__':
    unittest.main()

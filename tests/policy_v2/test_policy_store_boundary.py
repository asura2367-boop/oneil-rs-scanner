"""Store callback/SQL-binding contract using a scripted connection, NOT DB proof."""
from contextlib import nullcontext
from dataclasses import replace
from datetime import timedelta
import sys
from types import ModuleType
import unittest
from unittest.mock import patch
import uuid

from oneil_execution_v2.domain import Blocked, Order
from oneil_execution_v2.store import PostgresStore
from oneil_policy_v2.admission import PolicyAdmissionGate
import test_policy_admission as fixtures
from policy_support import ACCOUNT, NOW


class StoreBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PolicyAdmissionTests('test_exact_policy_admitted_and_full_evidence_audited')
        self.fixture.setUp(); self.fixture.queue()
        f = self.fixture
        self.row = dict(f.row, state='VALIDATING', claim_token=uuid.uuid4(),
                        lease_until=NOW + timedelta(seconds=20),
                        approval_expires_at=f.admission.proposal.order.expires_at,
                        expires_at=f.admission.proposal.order.expires_at)
        self.control = dict(f.store.control_row, mode='SIMULATION')
        self.store = PostgresStore.__new__(PostgresStore)
        self.store.runtime_guard = None
        self.store.assert_owner = lambda: None
        self.store.now = lambda: NOW
        self.store.unresolved = lambda _: []
        self.store.daily_buy = lambda *_: 0
        self.store.control = lambda _: self.control
        self.store.admission_ledger = lambda *_: {'orders': ()}
        self.store._event = lambda *args: None
        self.updated = False
        outer = self
        class Connection:
            def transaction(self): return nullcontext()
            def execute(self, sql, params):
                if sql.startswith('SELECT * FROM oneil_execution_v2.accounts'):
                    value = outer.control
                elif sql.startswith('SELECT * FROM oneil_execution_v2.requests'):
                    value = outer.row
                elif sql.startswith('UPDATE oneil_execution_v2.requests'):
                    outer.updated = True
                    value = dict(outer.row, admission_evidence=params[2], state='SUBMISSION_UNKNOWN', attempts=1)
                else:
                    raise AssertionError('Unexpected scripted SQL: ' + sql)
                class Result:
                    def fetchone(self): return value
                return Result()
        self.store.conn = Connection()
        fake_json = ModuleType('psycopg.types.json'); fake_json.Jsonb = lambda x: x
        self.modules = patch.dict(sys.modules, {'psycopg.types.json': fake_json})
        self.modules.start(); self.addCleanup(self.modules.stop)
        order = f.admission.proposal.order
        self.gate = PolicyAdmissionGate(self.store, f.source, self.row, order, f.p.core_risk,
            f.broker.quote(order), f.broker.account(ACCOUNT), f.broker.session(order),
            f.broker.history(ACCOUNT), {})

    def reserve(self, gate):
        return self.store.reserve_attempt(self.row, self.fixture.a.trading_day, self.fixture.p.core_risk, gate)

    def test_no_op_callback_cannot_admit_policy_request(self):
        with self.assertRaisesRegex(Blocked, 'POLICY_ADMISSION_REQUIRED'):
            self.reserve(lambda now, daily: None)
        self.assertFalse(self.updated)

    def test_forged_audit_callback_cannot_admit_policy_request(self):
        with self.assertRaisesRegex(Blocked, 'POLICY_ADMISSION_REQUIRED'):
            self.reserve(lambda now, daily: {'mode': 'SIMULATION', 'live_capable': False,
                'order_digest': self.row['digest'], 'request_id': str(self.row['request_id']),
                'control_revision': 7})
        self.assertFalse(self.updated)

    def test_validated_record_written_in_same_attempt_update(self):
        result = self.reserve(self.gate)
        self.assertTrue(self.updated)
        self.assertEqual(result['admission_evidence']['request_id'], str(self.row['request_id']))
        self.assertEqual(result['admission_evidence']['admission']['original_context']['policy']['reserve_pct'], 37)
        self.assertEqual(result['attempts'], 1)

    def test_gate_bound_to_exact_store_instance(self):
        with self.assertRaisesRegex(Blocked, 'POLICY_ADMISSION_REQUIRED'):
            self.reserve(replace(self.gate, store=self.fixture.store))
        self.assertFalse(self.updated)

    def test_gate_bound_to_exact_request(self):
        with self.assertRaisesRegex(Blocked, 'POLICY_ADMISSION_REQUIRED'):
            self.reserve(replace(self.gate, row=dict(self.row, request_id=uuid.uuid4())))
        self.assertFalse(self.updated)

    def test_gate_bound_to_exact_order(self):
        with self.assertRaisesRegex(Blocked, 'POLICY_ADMISSION_REQUIRED'):
            self.reserve(replace(self.gate, order=replace(self.gate.order, quantity=1)))
        self.assertFalse(self.updated)

    def test_bound_runtime_guard_cannot_be_omitted_at_reservation(self):
        self.store.runtime_guard = object()  # a bound identity cannot be substituted
        with self.assertRaisesRegex(Blocked, 'RUNTIME_ADMISSION_GUARD_REQUIRED'):
            self.reserve(self.gate)
        self.assertFalse(self.updated)

    def test_runtime_binding_rejects_arbitrary_callback(self):
        with self.assertRaisesRegex(Blocked, 'STRICT_RUNTIME_GUARD_REQUIRED'):
            self.store.bind_runtime_guard(lambda _: True)
        self.assertIsNone(self.store.runtime_guard)

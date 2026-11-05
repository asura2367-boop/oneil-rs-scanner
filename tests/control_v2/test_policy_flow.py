"""In-process authenticated API -> strict Worker rehearsals, not PostgreSQL proof.

Real ControlState SQLite, ControlAPI, coordinator, bridge, SimulationAPI, planners,
PolicyAdmissionGate and SimulatedBroker. Only core SQL storage is a named double.
No socket, provider login, real broker, live activation or financial transaction.
"""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import timedelta
from io import BytesIO
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'policy_v2'))
import test_policy_admission as fixtures
from policy_support import ACCOUNT, NOW, position, market, manual
from oneil_control_v2 import ControlAPI, ControlState, SessionRecord, Settings, SimulationPolicyCoordinator
from oneil_control_v2.app import COOKIE
from oneil_control_v2.bridge import CoreSimulationBackend
from oneil_control_v2.state import APIError, canonical, digest
from oneil_execution_v2.api import AuthenticatedOwner
from oneil_execution_v2.domain import Blocked, require
from oneil_execution_v2.worker import Worker
from oneil_monitor_v2 import project_status
from oneil_policy_v2.admission import SimulationPolicySource

TOKEN, CSRF = 't' * 43, 'c' * 43  # Public synthetic fixtures, not usable credentials.


class CoreConnectionDouble:
    def __init__(self, store):
        self.store, self.closed = store, False
        self.depth, self.fail_commit = 0, False
        self.commit_exception = RuntimeError
        self.after_lock = None

    @contextmanager
    def transaction(self):
        rows = deepcopy(self.store.rows)
        self.depth += 1
        self.store.events.append('SQL_BEGIN')
        try:
            yield
        except BaseException:
            self.store.rows = rows
            self.store.events.append('SQL_ROLLBACK')
            raise
        else:
            self.store.events.append('SQL_COMMIT')
            if self.fail_commit:
                raise self.commit_exception('synthetic lost commit acknowledgement')
        finally:
            self.depth -= 1

    def execute(self, sql, params):
        assert 'FOR UPDATE' in sql and params == (ACCOUNT,)
        assert self.store.locked
        self.store.events.append('ACCOUNT_ROW_LOCKED')
        if self.after_lock:
            self.after_lock()
        return SimpleNamespace(fetchone=lambda: self.store.control_row)


class ControlPolicyStoreDouble(fixtures.PolicyStore):
    def __init__(self, p):
        super().__init__(p)
        self.control_row['mode'] = 'SIMULATION'
        self.conn = CoreConnectionDouble(self)
        self.fail_enqueue_response = False

    def assert_owner(self):
        pass

    def enqueue(self, order, approval, idempotency_key):
        assert self.locked and self.conn.depth > 0
        existing = next((r for r in self.rows if r['idempotency_key'] == idempotency_key), None)
        if existing:
            require(existing['digest'] == order.digest and existing['principal'] == approval.principal,
                    'IDEMPOTENCY_CONFLICT')
            return existing
        approval.validate(order, self.now())
        require(not self.control_row['halted'], 'HALTED')
        require(order.policy_version == self.control_row['policy']['version'], 'POLICY_CHANGED')
        row = super().enqueue(**order.__dict__)
        row.update(control_revision=self.control_row['revision'], idempotency_key=idempotency_key,
                   digest=order.digest, principal=approval.principal, approval_expires_at=approval.expires_at)
        self.events.append('CORE_ENQUEUED')
        if self.fail_enqueue_response:
            raise RuntimeError('synthetic uncertain enqueue')
        return row


class PolicyFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = ControlState(Path(self.tmp.name) / 'control.sqlite')
        self.state.initialize()
        self.record = SessionRecord(digest(TOKEN), digest(CSRF), 'fixture-owner', ACCOUNT,
            frozenset({'monitor.read', 'simulation.command'}), int(NOW.timestamp()) + 300, int(NOW.timestamp()) - 60)
        self.state.install_session_record(self.record)
        self.owner = AuthenticatedOwner(self.record.principal, ACCOUNT)
        self.counter = 0
        self.configure()

    def configure(self, *, manual_mode=False):
        f = fixtures.PolicyAdmissionTests()
        f.setUp()
        if manual_mode:
            f.configure((position(),), markets={('005930', 'BUY'): market(), ('005930', 'SELL'): market(side='SELL')})
        self.f = f
        self.store = ControlPolicyStoreDouble(f.p)
        f.broker.now = self.store.now
        self.worker = Worker(self.store, f.broker, policy_source=SimulationPolicySource())
        with patch('oneil_control_v2.bridge.PostgresStore', ControlPolicyStoreDouble):
            self.backend = CoreSimulationBackend(self.store)
        self.coordinator = SimulationPolicyCoordinator(state=self.state, backend=self.backend, worker=self.worker)
        self.app = ControlAPI(state=self.state, backend=self.backend, projector=project_status,
            settings=Settings('https://monitor.invalid', 10, True), clock=lambda: self.store.now().timestamp())
        self.manual = manual(f.p) if manual_mode else None

    def propose(self):
        return self.coordinator.propose(self.owner, self.f.context,
            mode='MANUAL_ADD' if self.manual else 'AUTOMATIC', manual=self.manual)['proposal_id']

    def post(self, body, *, preview=False, key=None):
        self.counter += 1
        raw = canonical(body).encode()
        env = {'REQUEST_METHOD': 'POST', 'PATH_INFO': '/api/v2/previews' if preview else '/api/v2/commands',
            'wsgi.url_scheme': 'https', 'HTTP_HOST': 'monitor.invalid',
            'HTTP_ORIGIN': 'https://monitor.invalid', 'HTTP_SEC_FETCH_SITE': 'same-origin',
            'HTTP_COOKIE': f'{COOKIE}={TOKEN}', 'HTTP_X_CSRF_TOKEN': CSRF,
            'HTTP_X_REQUEST_NONCE': f'nonce{self.counter:020d}',
            'HTTP_X_REQUEST_TIMESTAMP': str(int(self.store.now().timestamp())),
            'HTTP_IDEMPOTENCY_KEY': key or f'key{self.counter:020d}',
            'CONTENT_TYPE': 'application/json', 'CONTENT_LENGTH': str(len(raw)), 'wsgi.input': BytesIO(raw)}
        response = []
        body = b''.join(self.app(env, lambda status, headers: response.append(int(status.split()[0]))))
        return response[0], json.loads(body)

    def preview(self, proposal_id=None):
        return self.post({'action': 'policy_order', 'expected_revision': 7,
                          'proposal_id': proposal_id or self.propose()}, preview=True)

    def command(self, preview):
        return {'action': 'policy_order', 'confirmation_id': preview['confirmation_id'],
                'content_digest': preview['content_digest']}

    def test_api_automatic_confirmation_registers_strict_admission_and_submits_once(self):
        status, preview = self.preview()
        self.assertEqual(status, 201)
        self.assertEqual(self.store.rows, [])
        self.assertEqual(self.f.broker.calls, [])
        self.assertNotIn(ACCOUNT, canonical(preview))
        self.assertEqual(preview['display']['policy_mode'], 'AUTOMATIC')
        command = self.command(preview)
        status, result = self.post(command, key='confirmed-key-0001')
        self.assertEqual(status, 202)
        admission = self.worker.policy_source.read(result['request_id'])
        self.assertEqual(admission.proposal.order.payload(), self.store.rows[0]['payload'])
        self.assertEqual(self.worker.tick(ACCOUNT), 'ACKNOWLEDGED')
        self.assertEqual(self.post(command, key='confirmed-key-0001'), (status, result))
        self.assertEqual(len(self.store.rows), 1)
        self.assertEqual(len(self.f.broker.calls), 1)
        self.assertIn('admission_evidence', self.store.rows[0])
        self.assertEqual(self.store.rows[0]['filled_quantity'], 0)

    def test_manual_end_to_end_uses_manual_key_not_confirmation_id(self):
        self.configure(manual_mode=True)
        status, preview = self.preview()
        self.assertEqual(status, 201)
        self.assertEqual(preview['display']['manual_intent']['expected_holding_quantity'], 10)
        self.assertEqual(self.post(self.command(preview))[0], 202)
        row = self.store.rows[0]
        self.assertEqual(row['idempotency_key'], 'policy-manual-' + self.manual.request_id)
        self.assertNotEqual(row['idempotency_key'], preview['confirmation_id'])
        self.assertEqual(self.worker.tick(ACCOUNT), 'ACKNOWLEDGED')
        self.assertEqual(row['payload']['quantity'], 10)

    def test_account_lock_covers_enqueue_broker_install_and_source_registration(self):
        _, preview = self.preview()
        original = self.worker.policy_source.register
        def register(*args):
            self.assertTrue(self.store.locked)
            self.assertGreater(self.store.conn.depth, 0)
            self.assertEqual(self.worker.tick(ACCOUNT), 'ACCOUNT_BUSY')
            original(*args)
            self.store.events.append('POLICY_REGISTERED')
        with patch.object(self.worker.policy_source, 'register', side_effect=register):
            self.assertEqual(self.post(self.command(preview))[0], 202)
        events = self.store.events
        self.assertLess(events.index('ACCOUNT_ROW_LOCKED'), events.index('CORE_ENQUEUED'))
        self.assertLess(events.index('CORE_ENQUEUED'), events.index('POLICY_REGISTERED'))
        self.assertLess(events.index('POLICY_REGISTERED'), events.index('SQL_COMMIT'))

    def test_many_confirmations_for_one_proposal_can_dispatch_only_once(self):
        proposal_id = self.propose()
        _, first = self.preview(proposal_id)
        _, second = self.preview(proposal_id)
        self.assertEqual(self.post(self.command(first))[0], 202)
        self.assertEqual(self.post(self.command(second)), (409, {'error': 'POLICY_PROPOSAL_ALREADY_DISPATCHED'}))
        self.assertEqual(len(self.store.rows), 1)

    def test_tampered_digest_or_extra_economics_cannot_enqueue(self):
        _, preview = self.preview()
        command = self.command(preview)
        self.assertEqual(self.post({**command, 'content_digest': '0' * 64})[0], 409)
        self.assertEqual(self.post({**command, 'quantity': 1}), (400, {'error': 'INVALID_FIELDS'}))
        self.assertEqual(self.store.rows, [])

    def test_browser_cannot_supply_policy_evidence_or_manual_id(self):
        proposal_id = self.propose()
        base = {'action': 'policy_order', 'expected_revision': 7, 'proposal_id': proposal_id}
        for extra in ({'context': {}}, {'manual_request_id': 'arbitrary'}, {'approved': True}, {'account': ACCOUNT}):
            self.assertEqual(self.post({**base, **extra}, preview=True), (400, {'error': 'INVALID_FIELDS'}))
        self.assertEqual(self.store.rows, [])

    def test_raw_order_preview_and_direct_bridge_are_disabled(self):
        raw = dict(ticker='005930', side='BUY', venue='KRX', quantity=2, limit_price=1000,
                   expires_at=(NOW + timedelta(seconds=5)).isoformat())
        self.assertEqual(self.post({'action': 'order', 'expected_revision': 7, 'order': raw}, preview=True),
                         (409, {'error': 'SERVER_POLICY_PROPOSAL_REQUIRED'}))
        with self.assertRaisesRegex(Blocked, 'SERVER_POLICY_PROPOSAL_REQUIRED'):
            self.backend.confirm_order(self.owner.principal, ACCOUNT, None, 7, 'any-key')
        self.assertEqual(self.store.rows, [])

    def test_proposal_bound_to_principal_and_account(self):
        proposal_id = self.propose()
        for principal, account in (('other-owner', ACCOUNT), (self.owner.principal, 'b' * 64)):
            with self.assertRaisesRegex(APIError, 'POLICY_PROPOSAL_NOT_FOUND'):
                self.coordinator.preview(principal, account, proposal_id, 7)
        self.assertEqual(self.store.rows, [])

    def test_original_nested_context_and_preview_mutation_cannot_change_intent(self):
        proposal_id = self.propose()
        self.f.context.markets.clear()
        _, preview = self.preview(proposal_id)
        preview['display']['quantity'] = 1
        self.assertEqual(self.post(self.command(preview))[0], 202)
        self.assertNotEqual(self.store.rows[0]['payload']['quantity'], 1)
        self.assertEqual(self.worker.tick(ACCOUNT), 'ACKNOWLEDGED')

    def test_manual_same_id_changed_original_evidence_conflicts_even_same_order(self):
        self.configure(manual_mode=True)
        proposal_id = self.propose()
        self.assertEqual(self.propose(), proposal_id)
        changed = replace(self.manual, owner_confirmation_reference='simulation:changed-review')
        with self.assertRaisesRegex(APIError, 'POLICY_INTENT_CONFLICT'):
            self.coordinator.propose(self.owner, self.f.context, mode='MANUAL_ADD', manual=changed)
        self.assertEqual(self.store.rows, [])

    def test_current_policy_change_requires_new_proposal(self):
        proposal_id = self.propose()
        changed = replace(self.f.context, policy=replace(self.f.p, reserve_pct=38))
        with self.assertRaisesRegex(Blocked, 'POLICY_CHANGED'):
            self.coordinator.refresh(self.owner, proposal_id, changed)

    def test_control_revision_changes_after_preview_block_queue(self):
        _, preview = self.preview()
        self.store.control_row['revision'] += 1
        self.assertEqual(self.post(self.command(preview)), (409, {'error': 'CONTROL_REVISION_CONFLICT'}))
        self.assertEqual(self.store.rows, [])

    def test_current_evidence_changes_are_rechecked_by_worker_at_final_boundary(self):
        _, preview = self.preview()
        self.assertEqual(self.post(self.command(preview))[0], 202)
        row = self.store.rows[0]
        admission = self.worker.policy_source.read(row['request_id'])
        changed = replace(admission.context, broker_history_digest='0' * 64)
        self.worker.policy_source.register(row['request_id'], replace(admission, context=changed))
        self.assertEqual(self.worker.tick(ACCOUNT), 'BLOCKED')
        self.assertEqual(row['reason'], 'POLICY_HISTORY_MISMATCH')
        self.assertEqual(row['attempts'], 0)
        self.assertEqual(self.f.broker.calls, [])

    def test_session_revoked_while_waiting_core_lock_blocks_queue(self):
        _, preview = self.preview()
        self.store.conn.after_lock = lambda: self.state.revoke_session(self.record.token_digest)
        self.assertEqual(self.post(self.command(preview)), (401, {'error': 'AUTHENTICATION_REQUIRED'}))
        self.assertEqual(self.store.rows, [])

    def test_confirmation_expiry_while_waiting_core_lock_blocks_queue(self):
        _, preview = self.preview()
        self.store.conn.after_lock = lambda: setattr(self.store, 'clock', NOW + timedelta(seconds=6))
        self.assertEqual(self.post(self.command(preview)), (409, {'error': 'REQUEST_EXPIRED'}))
        self.assertEqual(self.store.rows, [])

    def test_registration_failure_is_unknown_and_new_confirmation_cannot_retry(self):
        proposal_id = self.propose()
        _, preview = self.preview(proposal_id)
        command = self.command(preview)
        with patch.object(self.worker.policy_source, 'register', side_effect=Blocked('synthetic registration failure')):
            self.assertEqual(self.post(command, key='unknown-key-00001'), (503, {'error': 'COMMAND_OUTCOME_UNKNOWN'}))
        self.assertEqual(self.post(command, key='unknown-key-00001'), (409, {'error': 'COMMAND_OUTCOME_UNKNOWN'}))
        _, new_preview = self.preview(proposal_id)
        self.assertEqual(self.post(self.command(new_preview)), (409, {'error': 'POLICY_PROPOSAL_ALREADY_DISPATCHED'}))
        self.assertEqual(self.f.broker.calls, [])

    def test_uncertain_core_commit_keeps_unknown_and_never_reenqueues(self):
        proposal_id = self.propose()
        _, preview = self.preview(proposal_id)
        self.store.conn.fail_commit = True
        self.assertEqual(self.post(self.command(preview), key='uncertain-key-001')[0], 503)
        self.store.conn.fail_commit = False
        self.assertEqual(self.post(self.command(preview), key='uncertain-key-001'), (409, {'error': 'COMMAND_OUTCOME_UNKNOWN'}))
        self.assertEqual(len(self.store.rows), 1)
        self.assertEqual(self.worker.tick(ACCOUNT), 'ACKNOWLEDGED')
        self.assertEqual(len(self.f.broker.calls), 1)

    def test_typed_commit_failure_is_unknown_not_a_safe_rejection(self):
        _, preview = self.preview()
        self.store.conn.fail_commit = True
        self.store.conn.commit_exception = Blocked
        command = self.command(preview)
        self.assertEqual(self.post(command, key='typed-commit-0001'), (503, {'error': 'COMMAND_OUTCOME_UNKNOWN'}))
        self.store.conn.fail_commit = False
        self.assertEqual(self.post(command, key='typed-commit-0001'), (409, {'error': 'COMMAND_OUTCOME_UNKNOWN'}))

    def test_process_exit_after_enqueue_cannot_resend_on_restart(self):
        proposal_id = self.propose()
        _, preview = self.preview(proposal_id)
        command = self.command(preview)
        with patch.object(self.worker.policy_source, 'register', side_effect=SystemExit('synthetic process kill')):
            with self.assertRaises(SystemExit):
                self.post(command, key='hard-crash-key-01')
        self.assertEqual(self.post(command, key='hard-crash-key-01'), (409, {'error': 'COMMAND_OUTCOME_UNKNOWN'}))
        _, next_preview = self.preview(proposal_id)
        self.assertEqual(self.post(self.command(next_preview)), (409, {'error': 'POLICY_PROPOSAL_ALREADY_DISPATCHED'}))
        self.assertEqual(self.f.broker.calls, [])

    def test_completed_receipt_survives_coordinator_restart_without_current_context(self):
        _, preview = self.preview()
        command = self.command(preview)
        receipt = self.post(command, key='restart-receipt-01')
        self.assertEqual(receipt[0], 202)
        self.backend.policy_coordinator = None
        self.coordinator = SimulationPolicyCoordinator(state=ControlState(self.state.path),
            backend=self.backend, worker=self.worker)
        self.assertEqual(self.post(command, key='restart-receipt-01'), receipt)
        self.assertEqual(len(self.store.rows), 1)

    def test_busy_account_does_not_enqueue_and_proposal_stays_consumed(self):
        proposal_id = self.propose()
        _, preview = self.preview(proposal_id)
        self.store.locked = True
        self.assertEqual(self.post(self.command(preview)), (409, {'error': 'ACCOUNT_BUSY'}))
        self.store.locked = False
        _, next_preview = self.preview(proposal_id)
        self.assertEqual(self.post(self.command(next_preview)), (409, {'error': 'POLICY_PROPOSAL_ALREADY_DISPATCHED'}))
        self.assertEqual(self.store.rows, [])

    def test_failed_journal_completion_and_restart_preserve_unknown_tombstone(self):
        proposal_id = self.propose()
        _, preview = self.preview(proposal_id)
        command = self.command(preview)
        with patch.object(self.state, 'complete', side_effect=RuntimeError('synthetic disk failure')):
            self.assertEqual(self.post(command, key='journal-unknown-01')[0], 503)
        self.state = ControlState(self.state.path)
        self.app.state = self.state
        self.backend.policy_coordinator = None
        self.coordinator = SimulationPolicyCoordinator(state=self.state, backend=self.backend, worker=self.worker)
        self.assertEqual(self.post(command, key='journal-unknown-01'), (409, {'error': 'COMMAND_OUTCOME_UNKNOWN'}))
        self.coordinator.refresh(self.owner, proposal_id, self.f.context)
        _, new_preview = self.preview(proposal_id)
        self.assertEqual(self.post(self.command(new_preview)), (409, {'error': 'POLICY_PROPOSAL_ALREADY_DISPATCHED'}))
        self.assertEqual(len(self.store.rows), 1)

    def test_restart_never_treats_archived_original_as_current_evidence(self):
        proposal_id = self.propose()
        self.backend.policy_coordinator = None
        self.coordinator = SimulationPolicyCoordinator(state=self.state, backend=self.backend, worker=self.worker)
        self.assertEqual(self.preview(proposal_id), (409, {'error': 'EXPLICIT_CURRENT_POLICY_CONTEXT_REQUIRED'}))
        self.coordinator.refresh(self.owner, proposal_id, self.f.context)
        self.assertEqual(self.preview(proposal_id)[0], 201)

    def test_dispatch_tombstones_and_original_intents_are_immutable(self):
        _, preview = self.preview()
        self.assertEqual(self.post(self.command(preview))[0], 202)
        for table in ('policy_proposals', 'policy_dispatches'):
            with self.state.transaction() as conn:
                with self.assertRaises(sqlite3.IntegrityError):
                    conn.execute('DELETE FROM ' + table)

    def test_unsafe_worker_modes_and_runtime_rebinding_are_refused(self):
        self.worker.core_fixture_only = True
        with self.assertRaisesRegex(Blocked, 'STRICT_POLICY_WORKER_REQUIRED'):
            self.propose()
        self.worker.core_fixture_only = False
        self.worker.runtime_guard = object()
        with self.assertRaisesRegex(Blocked, 'EXPLICIT_STARTED_RUNTIME_REQUIRED'):
            self.propose()

    def test_new_policy_source_is_resolved_at_dispatch(self):
        _, preview = self.preview()
        old = self.worker.policy_source
        self.worker.policy_source = SimulationPolicySource()
        status, result = self.post(self.command(preview))
        self.assertEqual(status, 202)
        with self.assertRaisesRegex(Blocked, 'POLICY_ADMISSION_REQUIRED'):
            old.read(result['request_id'])
        self.worker.policy_source.read(result['request_id'])


if __name__ == '__main__':
    unittest.main()

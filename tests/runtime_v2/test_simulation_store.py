"""Real SQLite + OS locks + strict runtime/policy/simulator, no SQL doubles."""
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'policy_v2'))
import policy_support as f
from oneil_execution_v2.api import AuthenticatedOwner, SimulationAPI
from oneil_execution_v2.domain import Approval, Blocked, BrokerOrder, Order
from oneil_execution_v2.broker import SimulatedBroker
from oneil_execution_v2.worker import Worker
from oneil_policy_v2.admission import PolicyAdmission, PolicyContext, SimulationPolicySource, history_digest
from oneil_policy_v2.planner import plan_automatic
from oneil_runtime_v2.health import ClockSample, Evidence, HealthSample
from oneil_runtime_v2.orchestrator import SimulationRuntime
from oneil_runtime_v2.simulation_store import SQLiteSimulationStore
from oneil_runtime_v2.state import RuntimeState
from oneil_runtime_v2.supervisor import ACCOUNT


class SimulationStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/'core.sqlite'
        self.now = f.NOW
        self.policy = f.policy(account=ACCOUNT)
        self.store = SQLiteSimulationStore.initialize(self.path,ACCOUNT,self.policy.core_risk,now=lambda:self.now)
        self.addCleanup(self.close_store)
        self.api = SimulationAPI(self.store)
        self.owner = AuthenticatedOwner('explicit-offline-fixture-owner',ACCOUNT)

    def close_store(self):
        self.store.close()

    def resume(self):
        return self.api.resume_simulation(self.owner,self.store.control(ACCOUNT)['revision'])

    def order(self):
        return Order(ACCOUNT,'005930','BUY','KRX',10,1000,self.now+timedelta(minutes=2),self.policy.version)

    def queue(self, key='offline-request-0001'):
        return self.api.confirm_order(self.owner,self.order(),idempotency_key=key)

    def test_private_fixed_account_explicit_policy_and_reopen(self):
        self.assertTrue(self.store.control(ACCOUNT)['halted'])
        with self.assertRaisesRegex(Blocked,'FIXED_OFFLINE'):
            self.store.control('a'*64)
        self.resume(); receipt = self.queue()
        self.store.close(); self.store = SQLiteSimulationStore(self.path,now=lambda:self.now)
        self.api = SimulationAPI(self.store)
        self.assertEqual(self.queue(),receipt)
        self.assertEqual(self.store.get(ACCOUNT,receipt['request_id'])['attempts'],0)
        if os.name != 'nt':
            self.assertEqual(self.path.stat().st_mode & 0o777,0o600)

    def test_approval_exact_idempotency_principal_and_reuse(self):
        self.resume(); order = self.order()
        approval = Approval('approval-fixture-001','owner',order.digest,order.expires_at)
        first = self.store.enqueue(order,approval,'idempotency-fixture-001')
        with self.assertRaisesRegex(Blocked,'APPROVAL_ALREADY_USED'):
            self.store.enqueue(order,approval,'idempotency-fixture-002')
        with self.assertRaisesRegex(Blocked,'EXACT_APPROVAL'):
            self.store.enqueue(order,True,'idempotency-fixture-003')
        with self.assertRaisesRegex(Blocked,'IDEMPOTENCY_CONFLICT'):
            self.store.enqueue(order,replace(approval,principal='other'),'idempotency-fixture-001')
        self.now += timedelta(minutes=3)
        self.assertEqual(self.store.enqueue(order,approval,'idempotency-fixture-001'),first)

    def test_account_lock_is_os_exclusion_not_transaction(self):
        other = SQLiteSimulationStore(self.path,now=lambda:self.now)
        self.addCleanup(other.close)
        with self.store.account_lock(ACCOUNT) as acquired:
            self.assertTrue(acquired); self.assertFalse(self.store.conn.in_transaction)
            with self.store.account_lock(ACCOUNT) as nested:
                self.assertFalse(nested)
            with other.account_lock(ACCOUNT) as competing:
                self.assertFalse(competing)
            self.resume()
            self.assertFalse(other.control(ACCOUNT)['halted'])
        with other.account_lock(ACCOUNT) as acquired:
            self.assertTrue(acquired)

    def test_lock_is_cross_process_and_not_only_python_mutex(self):
        import subprocess
        script = (
            "import sys; from oneil_runtime_v2.simulation_store import SQLiteSimulationStore; "
            "from oneil_runtime_v2.supervisor import ACCOUNT; s=SQLiteSimulationStore(sys.argv[1]); "
            "scope=s.account_lock(ACCOUNT); acquired=scope.__enter__(); "
            "scope.__exit__(None,None,None); s.close(); sys.exit(0 if acquired else 42)"
        )
        with self.store.account_lock(ACCOUNT):
            completed = subprocess.run([sys.executable,'-c',script,str(self.path)],check=False,
                                       capture_output=True,timeout=10)
            self.assertEqual(completed.returncode,42,completed.stderr.decode())
        completed = subprocess.run([sys.executable,'-c',script,str(self.path)],check=False,
                                   capture_output=True,timeout=10)
        self.assertEqual(completed.returncode,0,completed.stderr.decode())

    def test_unknown_metadata_or_missing_path_never_creates_fallback(self):
        missing = Path(self.tmp.name)/'missing.sqlite'
        with self.assertRaises(Blocked):
            SQLiteSimulationStore(missing)
        self.assertFalse(missing.exists())
        self.store.conn.execute("UPDATE metadata SET mode='PRODUCTION'")
        with self.assertRaisesRegex(Blocked,'ISOLATED_SIMULATION_STORE_REQUIRED'):
            SQLiteSimulationStore(self.path)

    def test_nonprivate_file_rejected(self):
        if os.name == 'nt': self.skipTest('POSIX mode check only; Windows ACL not claimed')
        self.path.chmod(0o644)
        with self.assertRaisesRegex(Blocked,'PRIVATE_SIMULATION_STATE_REQUIRED'):
            SQLiteSimulationStore(self.path)
        self.path.chmod(0o600)

    def test_policy_omission_or_nonpolicy_namespace_rejected(self):
        from oneil_execution_v2.domain import RiskPolicy
        with self.assertRaisesRegex(Blocked,'EXPLICIT_CORE_RISK_REQUIRED'):
            SQLiteSimulationStore.initialize(Path(self.tmp.name)/'other.sqlite',ACCOUNT,
                                            replace(self.policy.core_risk,version='inferred-default'))
        self.assertFalse((Path(self.tmp.name)/'other.sqlite').exists())

    def test_revision_fence_rolls_back_enqueue_and_rejects_stale_revision(self):
        revision = self.resume()
        with self.assertRaisesRegex(RuntimeError,'rollback'):
            with self.store.revision_fence(ACCOUNT,revision):
                self.queue()
                raise RuntimeError('rollback')
        self.assertEqual(self.store.recovery_requests(ACCOUNT),[])
        with self.assertRaisesRegex(Blocked,'CONTROL_REVISION_CONFLICT'):
            with self.store.revision_fence(ACCOUNT,revision-1): pass

    def test_expired_claim_reclaimed_but_callback_cannot_admit(self):
        self.resume(); self.queue()
        row = self.store.claim(ACCOUNT,1)
        self.now += timedelta(seconds=2)
        reclaimed = self.store.claim(ACCOUNT)
        self.assertNotEqual(row['claim_token'],reclaimed['claim_token'])
        self.assertFalse(self.store.finish_before_send(row,'BLOCKED','CLAIM_LOST'))
        with self.store.account_lock(ACCOUNT):
            with self.assertRaisesRegex(Blocked,'RUNTIME_ADMISSION_GUARD_REQUIRED'):
                self.store.reserve_attempt(reclaimed,self.now.date().isoformat(),self.policy.core_risk,lambda *_:{})
        self.assertEqual(self.store.get(ACCOUNT,row['request_id'])['attempts'],0)

    def ready_runtime(self):
        self.state = RuntimeState.initialize(Path(self.tmp.name)/'runtime.sqlite')
        self.addCleanup(self.state.close)
        self.state.register_simulation_account(ACCOUNT)
        self.broker = SimulatedBroker(lambda:self.now,cash_krw=1_000_000)
        self.worker = Worker(self.store,self.broker,policy_source=SimulationPolicySource())
        self.clock = ClockSample(self.now.timestamp(),1000,900)
        self.runtime = SimulationRuntime(self.state,self.worker,ACCOUNT,lambda:self.clock)
        self.runtime.start()
        self.assertEqual(self.runtime.step(self.sample()),'RECONCILED_ONLY')
        self.runtime.restore_pending({})
        self.now += timedelta(seconds=1)
        self.clock = ClockSample(self.now.timestamp(),1001,901)
        self.assertEqual(self.runtime.step(self.sample()),'RECONCILED_ONLY')
        revision = self.resume()
        self.runtime.activate_simulation(self.owner,expected_control_revision=revision)
        context = PolicyContext(self.policy,replace(f.account(),account=ACCOUNT,equity_krw=1_000_000),
            replace(f.controls(self.policy),account=ACCOUNT),revision,history_digest(self.broker.history(ACCOUNT)),{},
            f.candidates(),{('005930','BUY'):replace(f.market(),account=ACCOUNT)},f.sessions(),{})
        proposal = plan_automatic(context.policy,context.account,context.controls,context.candidates,
            context.markets,context.sessions,now=self.now,technical={})[0]
        self.broker.install_policy_context(context)
        receipt = self.api.confirm_order(self.owner,proposal.order,idempotency_key='runtime-request-001')
        self.runtime.register_intent(receipt['request_id'],PolicyAdmission(proposal,context,'AUTOMATIC',None,context))
        return receipt['request_id']

    def sample(self):
        c = self.clock
        return HealthSample(c,True,c.utc,Evidence(True,c.utc),Evidence(True,c.utc),2**31,False)

    def test_real_gate_commits_unknown_before_submit_and_never_replays(self):
        request = self.ready_runtime()
        def inspect_commit():
            with sqlite3.connect(self.path) as independent:
                import json
                row = json.loads(independent.execute('SELECT data FROM requests WHERE request_id=?',(request,)).fetchone()[0])
            self.assertEqual((row['state'],row['attempts']),('SUBMISSION_UNKNOWN',1))
            self.assertFalse(self.store.conn.in_transaction)
            self.assertTrue(row['admission_evidence']['runtime'])
        self.broker.submit_hook = inspect_commit
        self.assertEqual(self.runtime.step(self.sample()),'ACKNOWLEDGED')
        self.assertEqual(len(self.broker.calls),1)
        self.assertEqual(self.store.claim(ACCOUNT),None)
        self.assertEqual(self.store.recovery_requests(ACCOUNT),[])
        self.store.close(); self.store = SQLiteSimulationStore(self.path,now=lambda:self.now)
        self.assertEqual(self.store.get(ACCOUNT,request)['attempts'],1)
        self.assertEqual(self.store.unresolved(ACCOUNT)[0]['request_id'],request)

    def test_exact_monotonic_observations_fill_watermarks_and_day_ledger(self):
        request = self.ready_runtime()
        self.assertEqual(self.runtime.step(self.sample()),'ACKNOWLEDGED')
        row = self.store.get(ACCOUNT,request)
        observation = self.broker.orders[request]
        quantity = row['payload']['quantity']
        with self.store.account_lock(ACCOUNT):
            partial = replace(observation,status='PART_FILLED',filled_quantity=1)
            self.store.apply_observation(row,partial)
            with self.assertRaisesRegex(Blocked,'FILL_REGRESSION'):
                self.store.apply_observation(row,observation)
            with self.assertRaisesRegex(Blocked,'BROKER_ORDER_IDENTITY_MISMATCH'):
                self.store.apply_observation(row,replace(partial,ticker='000660'))
            with self.assertRaisesRegex(Blocked,'ATTEMPT_MISMATCH'):
                self.store.apply_observation({**row,'attempt_id':'wrong'},partial)
            self.store.apply_observation(row,replace(observation,status='FILLED',filled_quantity=quantity))
            with self.assertRaisesRegex(Blocked,'INVALID_BROKER_TRANSITION'):
                self.store.apply_observation(row,partial)
        self.assertEqual(self.store.fill_requirements(ACCOUNT),{request:quantity})
        self.assertEqual(self.store.daily_buy(ACCOUNT,row['trading_day']),quantity*row['payload']['limit_price'])
        self.assertEqual(self.store.unresolved(ACCOUNT),[])

    def test_simulated_timeout_is_durable_unknown_and_halted(self):
        request = self.ready_runtime(); self.broker.behavior='timeout_before_accept'
        self.assertEqual(self.runtime.step(self.sample()),'SUBMISSION_UNKNOWN')
        self.assertTrue(self.store.control(ACCOUNT)['halted'])
        self.assertEqual(self.store.get(ACCOUNT,request)['attempts'],1)
        with self.assertRaisesRegex(Blocked,'UNRESOLVED_ORDER'):
            self.resume()


if __name__ == '__main__': unittest.main()

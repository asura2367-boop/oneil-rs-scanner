"""One long-lived process, durable polling, reconciliation before fresh work.

The shutdown event stops new iterations; an admitted in-flight attempt must be
reconciled. No order POST is retried, even after a timeout or process restart.
"""
import uuid
from .broker import SimulatedBroker
from .domain import Blocked, History, Order, RiskPolicy, UNRESOLVED, check_risk, fresh, require


class Worker:
    def __init__(self, store, broker, *, policy_source=None, core_fixture_only=False, runtime_guard=None):
        require(type(broker) is SimulatedBroker, 'LIVE_BROKER_NOT_IMPLEMENTED')
        require(type(core_fixture_only) is bool, 'INVALID_FIXTURE_MODE')
        if runtime_guard is not None:
            from oneil_runtime_v2.orchestrator import SimulationRuntimeGuard
            require(type(runtime_guard) is SimulationRuntimeGuard and not core_fixture_only,
                    'STRICT_RUNTIME_GUARD_REQUIRED')
        self.runtime_guard = runtime_guard
        self.policy_source = policy_source
        self.core_fixture_only = core_fixture_only
        self.store = store
        self.broker = broker
        self.worker_id = str(uuid.uuid4())

    def _history(self, account, policy):
        history = self.broker.history(account)
        require(isinstance(history, History) and history.account==account and history.complete is True,
                'HISTORY_INCOMPLETE')
        require(fresh(history.observed_at, self.store.now(), policy.max_history_age_seconds), 'HISTORY_STALE')
        seen = set()
        for observed in history.orders:
            require(observed.request_id not in seen, 'AMBIGUOUS_BROKER_HISTORY')
            seen.add(observed.request_id)
            row = self.store.get(account, observed.request_id)
            require(row['attempts']==1, 'UNEXPECTED_BROKER_ORDER')
            self.store.apply_observation(row, observed)
        unresolved = self.store.unresolved(account)
        if any(row['state']=='SUBMISSION_UNKNOWN' for row in unresolved):
            self.store.fail_closed(account, 'UNKNOWN_OUTCOME_REQUIRES_RECONCILIATION')
        # Empty but complete history is never proof that an unknown order was not sent.
        return unresolved, history

    def tick(self, account, *, reconciliation_only=False):
        require(type(reconciliation_only) is bool, 'INVALID_RECONCILIATION_MODE')
        if self.runtime_guard is not None:
            with self.runtime_guard.scope(account):
                return self._tick(account, reconciliation_only=reconciliation_only)
        return self._tick(account, reconciliation_only=reconciliation_only)

    def _tick(self, account, *, reconciliation_only):
        with self.store.account_lock(account) as acquired:
            if not acquired:
                return 'ACCOUNT_BUSY'
            control = self.store.control(account)
            policy = RiskPolicy(**control['policy']).validate()
            self.store.heartbeat(account, self.worker_id, 'RECONCILING')
            try:
                unresolved, history = self._history(account, policy)
            except Exception as exc:
                reason = str(exc) if isinstance(exc, Blocked) else 'BROKER_HISTORY_UNAVAILABLE'
                self.store.fail_closed(account, reason)
                self.store.heartbeat(account, self.worker_id, 'RECONCILIATION_BLOCKED')
                return 'RECONCILIATION_BLOCKED'
            self.store.heartbeat(account, self.worker_id, 'RECONCILED', reconciled=True)
            if unresolved:
                return 'UNRESOLVED_ORDER'
            if self.runtime_guard is not None:
                self.runtime_guard.note_reconciled(self.store.now())
            if reconciliation_only:
                return 'RECONCILED_ONLY'
            if self.store.control(account)['halted']:
                return 'HALTED'
            if self.runtime_guard is not None:
                try:
                    self.runtime_guard.check(self.store.now(), control_revision=self.store.control(account)['revision'])
                except Exception:
                    self.store.fail_closed(account, 'RUNTIME_ADMISSION_BLOCKED')
                    return 'RUNTIME_BLOCKED'
            row = self.store.claim(account)
            if row is None:
                self.store.heartbeat(account, self.worker_id, 'IDLE')
                return 'IDLE'
            try:
                order = Order.from_payload(row['payload'])
                from oneil_policy_v2.admission import SimulationPolicySource, PolicyAdmissionGate
                if self.core_fixture_only:
                    require(not order.policy_version.startswith('policy-v2-'), 'POLICY_ADMISSION_REQUIRED')
                    require(self.policy_source is None, 'FIXTURE_POLICY_MODE_CONFLICT')
                    admission = None
                else:
                    require(type(self.policy_source) is SimulationPolicySource, 'POLICY_ADMISSION_REQUIRED')
                    admission = self.policy_source.read(row['request_id'])
                quote = self.broker.quote(order)
                snapshot = self.broker.account(account)
                permit = self.broker.session(order)
                fill_requirements = self.store.fill_requirements(account)
                # Includes approval expiry, current halt revision and evidence freshness
                # under DB locks at the last durable admission boundary.
                def validate(now, daily):
                    require(fresh(history.observed_at, now, policy.max_history_age_seconds), 'HISTORY_STALE')
                    require(isinstance(snapshot.fill_watermarks, dict) and all(
                        type(snapshot.fill_watermarks.get(key)) is int and snapshot.fill_watermarks[key] >= quantity
                        for key, quantity in fill_requirements.items()),
                        'ACCOUNT_NOT_CAUSALLY_RECONCILED')
                    check_risk(order, policy, quote, snapshot, permit, now, daily)
                if admission is not None:
                    validate = PolicyAdmissionGate(self.store, self.policy_source, row, order,
                        policy, quote, snapshot, permit, history, fill_requirements, self.runtime_guard)
                row = self.store.reserve_attempt(row, permit.trading_day, policy, validate)
            except Exception as exc:
                reason = str(exc) if isinstance(exc, Blocked) else 'PREFLIGHT_FAILED'
                state = 'EXPIRED' if reason in {'REQUEST_EXPIRED','APPROVAL_EXPIRED','APPROVAL_OR_LEASE_EXPIRED'} else 'BLOCKED'
                finished = self.store.finish_before_send(row, state, reason)
                if not finished:
                    # A commit failure can have an uncertain outcome. Never call
                    # a durable attempt 'blocked before send' unless CAS proves it.
                    current = self.store.get(account, row['request_id'])
                    if current['state'] in UNRESOLVED or current['attempts']:
                        self.store.fail_closed(account, 'ADMISSION_OUTCOME_REQUIRES_RECONCILIATION')
                        self.store.heartbeat(account, self.worker_id, 'SUBMISSION_UNKNOWN')
                    return current['state']
                self.store.heartbeat(account, self.worker_id, 'REQUEST_BLOCKED')
                return state
            # From this point even a process crash leaves a committed UNKNOWN.
            # Halt cannot retract an already-admitted network operation.
            try:
                if self.runtime_guard is not None:
                    self.runtime_guard.check(self.store.now(), control_revision=self.store.control(account)['revision'])
                observed = self.broker.submit(str(row['request_id']), order, permit)
                self.store.apply_observation(row, observed)
            except Exception:
                # The broker could have accepted the order. Never mark REJECTED
                # merely because of transport, parsing or DB persistence errors.
                self.store.fail_closed(account, 'SUBMISSION_OUTCOME_UNKNOWN')
                self.store.heartbeat(account, self.worker_id, 'SUBMISSION_UNKNOWN')
                return 'SUBMISSION_UNKNOWN'
            self.store.heartbeat(account, self.worker_id, observed.status)
            return observed.status

    def run(self, accounts, stop_event, poll_seconds=1.0):
        require(isinstance(poll_seconds, (int,float)) and 0.1 <= poll_seconds <= 10, 'INVALID_POLL_INTERVAL')
        require(accounts and len(set(accounts))==len(accounts), 'INVALID_ACCOUNTS')
        while not stop_event.is_set():
            for account in accounts:
                if stop_event.is_set():
                    break
                try:
                    self.tick(account)
                except Exception:
                    # DB failure aborts this iteration; no bypass or local fallback.
                    # If the attempt was committed it remains UNKNOWN on restart.
                    # The external supervisor must expose stale heartbeat as failure.
                    if self.store.conn.closed:
                        raise
            stop_event.wait(poll_seconds)

"""In-process, simulation-only service composition; no listener/installer/launcher.

Adapters are trusted test providers. Construction does not run anything. Startup
and each process restart require reconciliation and a fresh explicit owner action.
"""
from dataclasses import replace
import hashlib
import json

from oneil_execution_v2.api import AuthenticatedOwner
from oneil_execution_v2.domain import Blocked, Order, aware, require
from oneil_execution_v2.worker import Worker
from oneil_policy_v2.admission import SimulationPolicySource
from .health import ClockSample, Evidence, HealthPolicy, HealthSample, evaluate, number
from .state import RuntimeState, RuntimeLease


class SimulationRuntimeGuard:
    def __init__(self, state, lease, clock, *, policy=None):
        require(type(state) is RuntimeState and type(lease) is RuntimeLease
                and lease.engine == 'V2_SIMULATION' and callable(clock), 'SIMULATION_RUNTIME_REQUIRED')
        self.state, self.lease, self.clock = state, lease, clock
        self.poisoned = False
        self.policy = policy or HealthPolicy()
        require(type(self.policy) is HealthPolicy, 'INVALID_HEALTH_POLICY')

    def scope(self, account):
        require(account == self.lease.account, 'RUNTIME_ACCOUNT_MISMATCH')
        return self.state.scope(self.lease)

    def note_reconciled(self, now):
        require(self.state._scope == self.lease, 'RUNTIME_ADMISSION_SCOPE_REQUIRED')
        row = self.state._lease(self.lease)
        if row['phase'] == 'ACTIVE_SIMULATION':
            self.state.conn.execute('UPDATE runtime SET reconciled_at=? WHERE account=?',
                                    (now.timestamp(), self.lease.account))

    def check(self, now, *, require_active=True, control_revision=None):
        require(self.state._scope == self.lease, 'RUNTIME_ADMISSION_SCOPE_REQUIRED')
        try:
            require(not self.poisoned, 'RUNTIME_STORAGE_UNCONFIRMED')
            aware(now)
            row = self.state._lease(self.lease)
            require(row['phase'] == ('ACTIVE_SIMULATION' if require_active else 'AWAITING_OWNER'),
                    'RUNTIME_OWNER_ACTIVATION_REQUIRED')
            if require_active:
                require(type(control_revision) is int and control_revision == row['activation_revision'],
                        'RUNTIME_CONTROL_REVISION_CHANGED')
            require(row['healthy'] == 1 and row['health'] is not None
                    and row['reconstructed'] is not None and number(row['reconciled_at']),
                    'RUNTIME_RECONCILIATION_REQUIRED')
            raw = json.loads(row['health'])
            sample = HealthSample(**{**raw, 'clock': ClockSample(**raw['clock']),
                'database': Evidence(**raw['database']), 'broker_read': Evidence(**raw['broker_read'])})
            clock = self.clock()
            require(type(clock) is ClockSample and clock.valid(), 'CLOCK_INVALID')
            require(abs(now.timestamp()-clock.utc) <= self.policy.max_clock_disagreement_seconds,
                    'RUNTIME_CORE_CLOCK_DISAGREEMENT')
            result = evaluate(replace(sample, clock=clock), sample.clock, self.policy)
            require(result.operationally_healthy, result.reasons[0] if result.reasons else 'RUNTIME_UNHEALTHY')
            require(0 <= clock.utc-row['reconciled_at'] <= self.policy.max_dependency_age_seconds,
                    'RUNTIME_RECONCILIATION_STALE_OR_FUTURE')
            return {'epoch': self.lease.epoch, 'engine': self.lease.engine,
                    'health_at': sample.clock.utc, 'checked_at': clock.utc,
                    'reconstruction_digest': row['reconstructed'], 'live_capable': False}
        except Exception as exc:
            reason = str(exc) if type(exc) is Blocked else 'RUNTIME_EVIDENCE_INVALID'
            try:
                self.state.trip(self.lease, reason)
            except Exception:
                self.poisoned = True
                raise
            raise Blocked(reason) from exc


class SimulationRuntime:
    """Only accepted worker: strict PolicyAdmission + exact SimulatedBroker.

    No fixture-only mode, subprocess, network, login, registration, core resume or
    live enablement. The caller supplies an already-created isolated core store.
    """
    def __init__(self, state, worker, account, clock, *, health_policy=None):
        require(type(state) is RuntimeState and type(worker) is Worker,
                'SIMULATION_RUNTIME_REQUIRED')
        require(worker.core_fixture_only is False and type(worker.policy_source) is SimulationPolicySource,
                'STRICT_POLICY_WORKER_REQUIRED')
        require(worker.runtime_guard is None, 'RUNTIME_ALREADY_BOUND')
        self.state, self.worker, self.account, self.clock = state, worker, account, clock
        self.health_policy = health_policy or HealthPolicy()
        self.guard = None

    def start(self, *, owner_request=None):
        require(self.guard is None, 'RUNTIME_ALREADY_STARTED')
        clock = self.clock()
        require(type(clock) is ClockSample and clock.valid(), 'CLOCK_INVALID')
        lease = self.state.start(self.account, now=clock.utc, owner_request=owner_request)
        return self.attach_supervised(lease)

    def attach_supervised(self, lease):
        """Bind a trusted supervisor-created fresh lease; never release it here.

        The process host must verify its durable launch receipt and hold its child
        instance lock. This method alone is not process identity/authentication.
        """
        require(self.guard is None and type(lease) is RuntimeLease
                and lease.account == self.account and lease.engine == 'V2_SIMULATION',
                'FRESH_SUPERVISED_LEASE_REQUIRED')
        row = self.state._lease(lease)
        require(row['phase'] == 'RECONCILING' and row['health'] is None
                and row['reconciled_at'] is None and row['activation_revision'] is None,
                'FRESH_SUPERVISED_LEASE_REQUIRED')
        self.guard = SimulationRuntimeGuard(self.state, lease, self.clock, policy=self.health_policy)
        self.worker.runtime_guard = self.guard
        self.worker.store.bind_runtime_guard(self.guard)
        # Discard in-memory registrations, even if construction reused a source.
        self.worker.policy_source = SimulationPolicySource()
        try:
            self.worker.store.fail_closed(self.account, 'RUNTIME_STARTUP_REQUIRES_RECONCILIATION')
        except Exception:
            self.state.trip(lease, 'CORE_HALT_OUTCOME_UNCONFIRMED')
            raise
        return lease

    def register_intent(self, request_id, admission, *, scope_lease=None):
        require(self.guard is not None, 'RUNTIME_NOT_STARTED')
        require(scope_lease is None or scope_lease == self.guard.lease,
                'RUNTIME_OWNERSHIP_LOST')
        row = self.worker.store.get(self.account, request_id)
        require(row['account'] == self.account and row['attempts'] == 0
                and row['state'] in ('QUEUED','VALIDATING')
                and Order.from_payload(row['payload']) == admission.proposal.order,
                'POLICY_ORDER_MISMATCH')
        self.state.save_intent(str(request_id), admission, lease=scope_lease)
        self.worker.policy_source.register(request_id, admission)

    def restore_pending(self, current_contexts):
        """All-or-nothing policy source rebuild. Contexts are explicit fresh inputs.

        This restores intent, never approval, request state, attempts, fills,
        core controls, simulator balance or a previous ACTIVE lifecycle phase.
        """
        require(self.guard is not None and type(current_contexts) is dict, 'RUNTIME_NOT_STARTED')
        try:
            rows = self.worker.store.recovery_requests(self.account)
            ids = {str(r['request_id']) for r in rows}
            require(len(ids) == len(rows) and set(current_contexts) == ids, 'EXPLICIT_PENDING_CONTEXTS_REQUIRED')
            source = SimulationPolicySource()
            identities = []
            for row in rows:
                order = Order.from_payload(row['payload'])
                require(row['account'] == self.account and order.account == self.account
                        and row['attempts'] == 0 and row['state'] in ('QUEUED','VALIDATING'),
                        'RESTART_REQUEST_NOT_PENDING')
                key = str(row['request_id'])
                admission = self.state.load_intent(key, account=self.account, order_digest=order.digest,
                                                   current_context=current_contexts[key])
                require(admission.proposal.order == order, 'POLICY_ORDER_MISMATCH')
                source.register(key, admission)
                identities.append((key, order.digest))
            digest = hashlib.sha256(json.dumps(sorted(identities), separators=(',', ':')).encode()).hexdigest()
            self.state.reconstructed(self.guard.lease, digest)
            self.worker.policy_source = source
            return len(rows)
        except Exception:
            self.worker.policy_source = SimulationPolicySource()
            self._halt('POLICY_RECONSTRUCTION_FAILED')
            raise

    def _halt(self, reason):
        # A failed runtime journal must not prevent an independent core halt.
        # If either persistence result is uncertain, this object cannot admit again.
        errors = []
        try:
            self.state.trip(self.guard.lease, reason)
        except Exception as exc:
            errors.append(exc)
        try:
            self.worker.store.fail_closed(self.account, reason)
        except Exception as exc:
            errors.append(exc)
        if errors:
            self.guard.poisoned = True
            raise errors[0]

    def step(self, sample):
        require(self.guard is not None, 'RUNTIME_NOT_STARTED')
        try:
            result = self.state.observe(self.guard.lease, sample, self.health_policy)
            if not result.operationally_healthy:
                self.worker.store.fail_closed(self.account, 'RUNTIME_HEALTH_FAILED')
            row = self.state.status(self.account)
            reconcile_only = row['phase'] != 'ACTIVE_SIMULATION'
            outcome = self.worker.tick(self.account, reconciliation_only=reconcile_only)
            if outcome in ('RECONCILIATION_BLOCKED','SUBMISSION_UNKNOWN','UNRESOLVED_ORDER','HALTED'):
                self.state.trip(self.guard.lease, 'RUNTIME_'+outcome)
            elif outcome == 'RECONCILED_ONLY' and row['healthy'] == 1 and row['reconstructed'] is not None:
                self.state.reconciled(self.guard.lease, self.worker.store.now().timestamp())
            return outcome
        except Exception:
            self._halt('RUNTIME_STEP_FAILED')
            raise

    def activate_simulation(self, owner, *, expected_control_revision):
        """Trusted offline owner action only; no browser endpoint, no core resume."""
        require(self.guard is not None and type(expected_control_revision) is int
                and expected_control_revision > 0, 'INVALID_RUNTIME_ACTIVATION')
        self.state._owner(owner, self.account)
        with self.guard.scope(self.account):
            with self.worker.store.account_lock(self.account) as acquired:
                require(acquired, 'ACCOUNT_BUSY')
                control = self.worker.store.control(self.account)
                require(control['mode'] == 'SIMULATION' and control['halted'] is False
                        and control['revision'] == expected_control_revision, 'CORE_OWNER_RESUME_REQUIRED')
                require(not self.worker.store.unresolved(self.account), 'UNRESOLVED_ORDER')
                self.guard.check(self.worker.store.now(), require_active=False)
                self.state.activate_simulation(self.guard.lease, owner, expected_control_revision)

    def quiesce(self, reason='OWNER_STOPPED_RUNTIME'):
        """Halt work but leave exact process-exit accounting to its supervisor."""
        require(self.guard is not None, 'RUNTIME_NOT_STARTED')
        self._halt(reason)

    def stop(self):
        self.quiesce()
        return self.state.record_exit(self.guard.lease, exit_code=0, now=self.clock().utc, explicit_stop=True)

    def run(self, stop_event, sample_provider, *, poll_seconds=1.0):
        """Bounded polling, no retry of an effect and no subprocess auto-restart."""
        require(self.guard is not None and callable(sample_provider)
                and type(poll_seconds) in (int,float) and 0.1 <= poll_seconds <= 10, 'INVALID_RUNTIME_LOOP')
        try:
            while not stop_event.is_set():
                self.step(sample_provider())
                stop_event.wait(poll_seconds)
        except KeyboardInterrupt:
            self.stop()
            raise
        except Exception:
            self._halt('RUNTIME_LOOP_FAILED')
            # The in-process loop has stopped. Persist one restart budget debit.
            # If persistence is uncertain, retain the ownership claim; do not launch.
            self.state.record_exit(self.guard.lease, exit_code=1, now=self.clock().utc)
            raise
        return self.stop()

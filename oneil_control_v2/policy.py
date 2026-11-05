"""Server-owned, socket-free proposal to strict-worker simulation coordination.

No browser policy, auto-confirmation, listener, runtime activation or live broker.
The host supplies explicit current evidence and a dedicated strict Worker. An
optional explicitly bound runtime adds its independent activation, ownership,
health and original-intent archive gates. Confirmation never activates it.
"""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict, replace
import hashlib
import json
import secrets

from oneil_execution_v2.api import AuthenticatedOwner
from oneil_execution_v2.broker import SimulatedBroker
from oneil_execution_v2.domain import require
from oneil_execution_v2.worker import Worker
from oneil_policy_v2.admission import PolicyAdmission, PolicyContext, SimulationPolicySource, json_value
from oneil_policy_v2.planner import plan_automatic, plan_manual_add
from oneil_runtime_v2.intent import encode_intent, decode_intent
from .state import ControlState


class SimulationPolicyCoordinator:
    def __init__(self, *, state, backend, worker, runtime=None):
        from .bridge import CoreSimulationBackend
        require(type(state) is ControlState and type(backend) is CoreSimulationBackend,
                'SIMULATION_COORDINATOR_REQUIRED')
        self.state, self.backend, self.worker = state, backend, worker
        self.runtime = runtime
        if runtime is not None:
            from oneil_runtime_v2.orchestrator import SimulationRuntime
            require(type(runtime) is SimulationRuntime and runtime.guard is not None,
                    'EXPLICIT_STARTED_RUNTIME_REQUIRED')
        self._guard = runtime.guard if runtime is not None else None
        self._runtime_state = self._guard.state if self._guard is not None else None
        self._lease = self._guard.lease if self._guard is not None else None
        self._contexts = {}
        self._worker()
        require(backend.policy_coordinator is None, 'POLICY_COORDINATOR_ALREADY_BOUND')
        backend.policy_coordinator = self

    def _worker(self):
        require(type(self.worker) is Worker and self.worker.store is self.backend.store
                and type(self.worker.broker) is SimulatedBroker
                and self.worker.core_fixture_only is False
                and type(self.worker.policy_source) is SimulationPolicySource,
                'STRICT_POLICY_WORKER_REQUIRED')
        if self.runtime is None:
            require(self.worker.runtime_guard is None and getattr(self.backend.store, 'runtime_guard', None) is None,
                    'EXPLICIT_STARTED_RUNTIME_REQUIRED')
        else:
            from oneil_runtime_v2.orchestrator import SimulationRuntime, SimulationRuntimeGuard
            require(type(self.runtime) is SimulationRuntime and type(self._guard) is SimulationRuntimeGuard
                    and self.runtime.worker is self.worker and self.runtime.guard is self._guard
                    and self.worker.runtime_guard is self._guard and self.backend.store.runtime_guard is self._guard
                    and self.runtime.state is self._guard.state is self._runtime_state
                    and self._guard.lease is self._lease
                    and self.runtime.account == self._lease.account
                    and self._lease.engine == 'V2_SIMULATION', 'RUNTIME_BINDING_CHANGED')

    def _halt_runtime(self, reason):
        failures = []
        for operation in (lambda: self._runtime_state.trip(self._lease, reason),
                          lambda: self.backend.store.fail_closed(self._lease.account, reason)):
            try:
                operation()
            except Exception as exc:
                failures.append(exc)
        if failures:
            self._guard.poisoned = True

    def _poison_runtime(self):
        if self._guard is not None:
            # Poison the originally bound guard before any possibly failing write.
            self._guard.poisoned = True
            self._halt_runtime('COORDINATOR_OUTCOME_UNCONFIRMED')

    def outcome_unknown(self, account):
        """Called if the command journal cannot complete a successful dispatch."""
        if self._guard is not None:
            require(account == self._lease.account, 'RUNTIME_ACCOUNT_MISMATCH')
            self._poison_runtime()

    @contextmanager
    def _scope(self, account):
        self._worker()
        if self._guard is None:
            yield
            return
        require(account == self._guard.lease.account, 'RUNTIME_ACCOUNT_MISMATCH')
        body_error = None
        try:
            with self._guard.scope(account):
                try:
                    yield
                except BaseException as exc:
                    body_error = exc
                    raise
        except BaseException as exc:
            # A distinct scope-entry/exit exception can mean a lost COMMIT even
            # when the body also failed before enqueue. Never leave that guard
            # reusable merely because no core effect began in this invocation.
            if exc is not body_error:
                self._poison_runtime()
            raise

    def _check_runtime(self, account, expected):
        if self._guard is not None:
            require(account == self._guard.lease.account, 'RUNTIME_ACCOUNT_MISMATCH')
            try:
                self._guard.check(self.backend.store.now(), control_revision=expected)
            except Exception:
                self._halt_runtime('COORDINATOR_RUNTIME_BLOCKED')
                raise

    @staticmethod
    def _plan(context, mode, manual, now):
        require(type(context) is PolicyContext, 'EXPLICIT_CURRENT_POLICY_CONTEXT_REQUIRED')
        if mode == 'AUTOMATIC':
            require(manual is None, 'POLICY_INTENT_MISMATCH')
            proposals = plan_automatic(context.policy, context.account, context.controls, context.candidates,
                context.markets, context.sessions, now=now, technical=context.technical)
            require(len(proposals) == 1, 'POLICY_NO_ELIGIBLE_PROPOSAL')
            return proposals[0]
        require(mode == 'MANUAL_ADD', 'POLICY_INTENT_MODE_INVALID')
        return plan_manual_add(context.policy, context.account, context.controls, manual,
            context.markets, context.sessions, now=now, technical=context.technical)

    def _control(self, account, context, expected):
        self._worker()
        control = self.backend.control(account)
        require(control['mode'] == 'SIMULATION', 'ACCOUNT_NOT_SIMULATION')
        require(control['revision'] == expected == context.control_revision, 'CONTROL_REVISION_CONFLICT')
        require(not control['halted'], 'HALTED')
        require(context.policy.account == account and context.account.account == account
                and control['policy'] == asdict(context.policy.core_risk), 'POLICY_CHANGED')
        require(not self.backend.unresolved(account), 'UNRESOLVED_ORDER')
        self._check_runtime(account, expected)

    def propose(self, owner, context, *, mode='AUTOMATIC', manual=None):
        """Trusted host input only. Creates a proposal, never approval or a queue row."""
        require(type(owner) is AuthenticatedOwner and bool(owner.principal), 'AUTHENTICATED_OWNER_REQUIRED')
        context, manual = deepcopy(context), deepcopy(manual)
        require(type(context) is PolicyContext, 'EXPLICIT_CURRENT_POLICY_CONTEXT_REQUIRED')
        with self._scope(owner.account):
            self._control(owner.account, context, context.control_revision)
            proposal = self._plan(context, mode, manual, self.backend.store.now())
            admission = PolicyAdmission(proposal, context, mode, manual, context)
            text, intent_digest = encode_intent(admission)
            key = 'policy-manual-' + manual.request_id if mode == 'MANUAL_ADD' else 'policy-auto-' + intent_digest
            proposal_id = self.state.save_policy_proposal(secrets.token_urlsafe(32), owner.principal,
                owner.account, key, text, intent_digest)
            self._contexts[proposal_id] = context
            return {'proposal_id': proposal_id, 'mode': 'SIMULATION', 'live_capable': False}

    def refresh(self, owner, proposal_id, context):
        """Explicit fresh provider observations only; original intent stays immutable."""
        require(type(owner) is AuthenticatedOwner, 'AUTHENTICATED_OWNER_REQUIRED')
        with self._scope(owner.account):
            row = self.state.policy_proposal(owner.principal, owner.account, proposal_id)
            context = deepcopy(context)
            admission = decode_intent(row['intent'], row['intent_digest'], current_context=context)
            self._validate(owner.account, admission, admission.original_context.control_revision)
            self._contexts[proposal_id] = context

    def _validate(self, account, admission, expected):
        self._control(account, admission.context, expected)
        now = self.backend.store.now()
        original = admission.proposal
        require(now < original.order.expires_at, 'REQUEST_EXPIRED')
        current = self._plan(admission.context, admission.mode, admission.manual, now)
        require(replace(current.order, expires_at=original.order.expires_at) == original.order
                and original.order.expires_at <= current.order.expires_at
                and current.reason == original.reason
                and current.buffered_cost_krw == original.buffered_cost_krw,
                'POLICY_PROPOSAL_CHANGED')

    def _read(self, principal, account, proposal_id, expected):
        row = self.state.policy_proposal(principal, account, proposal_id)
        context = deepcopy(self._contexts.get(proposal_id))
        # A disk archive is original evidence, never a fresh-context fallback.
        admission = decode_intent(row['intent'], row['intent_digest'], current_context=context)
        require(admission.original_context.control_revision == expected, 'CONTROL_REVISION_CONFLICT')
        self._validate(account, admission, expected)
        proposal = admission.proposal
        binding = {'proposal_id': proposal_id, 'intent_digest': row['intent_digest'],
                   'order': proposal.order.payload(), 'policy_mode': admission.mode,
                   'reason': proposal.reason, 'buffered_cost_krw': str(proposal.buffered_cost_krw),
                   'manual_request_id': proposal.manual_request_id,
                   'manual_intent': ({k: v for k, v in json_value(admission.manual).items() if k != 'account'}
                                     if admission.manual is not None else None)}
        return row, admission, binding

    def preview(self, principal, account, proposal_id, expected):
        with self._scope(account):
            return self._read(principal, account, proposal_id, expected)[2]

    def _activation_binding(self, account, expected):
        require(self.runtime is not None and account == self._lease.account,
                'EXPLICIT_STARTED_RUNTIME_REQUIRED')
        control = self.backend.control(account)
        require(control['mode'] == 'SIMULATION' and control['revision'] == expected
                and control['halted'] is False, 'CORE_OWNER_RESUME_REQUIRED')
        require(not self.backend.unresolved(account), 'UNRESOLVED_ORDER')
        row = self._runtime_state._lease(self._lease)
        require(row['phase'] == 'AWAITING_OWNER', 'RUNTIME_OWNER_ACTIVATION_REQUIRED')
        self._guard.check(self.backend.store.now(), require_active=False)
        binding = hashlib.sha256(json.dumps(asdict(self._lease), sort_keys=True).encode()).hexdigest()
        return {'runtime_epoch': self._lease.epoch, 'runtime_binding': binding}

    def preview_activation(self, principal, account, expected):
        with self._scope(account):
            return self._activation_binding(account, expected)

    def confirm_activation(self, session, content, confirmation_id, *, clock):
        changed = False
        try:
            with self._scope(session.account):
                with self.backend.store.account_lock(session.account) as acquired:
                    require(acquired, 'ACCOUNT_BUSY')
                    with self.backend._revision_fence(session.account, content['expected_revision']):
                        binding = self._activation_binding(session.account, content['expected_revision'])
                        require(content == {'action': 'activate_runtime', 'expected_revision': content['expected_revision'],
                            'mode': 'SIMULATION', **binding}, 'RUNTIME_BINDING_CHANGED')
                        self.state.validate_policy_confirmation(session, confirmation_id, content, clock)
                        changed = True
                        self._runtime_state.activate_simulation(self._lease,
                            AuthenticatedOwner(session.principal, session.account), content['expected_revision'])
            return {'runtime_phase': 'ACTIVE_SIMULATION', 'mode': 'SIMULATION', 'live_capable': False}
        except BaseException as exc:
            if changed:
                self._poison_runtime()
                if isinstance(exc, Exception):
                    raise RuntimeError('RUNTIME_ACTIVATION_OUTCOME_UNKNOWN') from exc
            raise

    def confirm(self, session, content, confirmation_id, *, clock):
        self._worker()
        # Runtime ownership transaction, then Worker account advisory lock,
        # then account row lock. The command journal is not held while waiting.
        effect_started = False
        try:
            with self._scope(session.account):
                with self.backend.store.account_lock(session.account) as acquired:
                    require(acquired, 'ACCOUNT_BUSY')
                    with self.backend._revision_fence(session.account, content['expected_revision']):
                        row, admission, binding = self._read(session.principal, session.account,
                            content['proposal_id'], content['expected_revision'])
                        require(content == {'action': 'policy_order', 'expected_revision': content['expected_revision'],
                                            'mode': 'SIMULATION', **binding}, 'POLICY_INTENT_CONFLICT')
                        self.state.validate_policy_confirmation(session, confirmation_id, content, clock)
                        # Everything after enqueue begins, including COMMIT and lock
                        # release, is conservatively uncertain on any exception.
                        effect_started = True
                        result = self.backend.api.confirm_order(AuthenticatedOwner(session.principal, session.account),
                            admission.proposal.order, idempotency_key=row['intent_key'])
                        request = self.backend.store.get(session.account, result['request_id'])
                        require(request['attempts'] == 0 and request['state'] in ('QUEUED', 'VALIDATING')
                                and request['digest'] == admission.proposal.order.digest
                                and request['principal'] == session.principal
                                and request['idempotency_key'] == row['intent_key'], 'POLICY_REQUEST_MISMATCH')
                        self.worker.broker.install_policy_context(admission.context)
                        # Resolve the current source, never a cached pointer that a
                        # lifecycle operation may have replaced.
                        if self.runtime is None:
                            self.worker.policy_source.register(result['request_id'], admission)
                        else:
                            self.runtime.register_intent(result['request_id'], admission,
                                scope_lease=self._guard.lease)
                            # Re-sample health, revision, policy, order and owner
                            # validity after archive/source work, before core commit.
                            self._validate(session.account, admission, content['expected_revision'])
                            self.state.validate_policy_confirmation(session, confirmation_id, content, clock)
                    # Outer core COMMIT precedes releasing the account execution lock.
            return result
        except BaseException as exc:
            if effect_started:
                self._poison_runtime()
                if not isinstance(exc, Exception):
                    raise
                raise RuntimeError('POLICY_DISPATCH_OUTCOME_UNKNOWN') from exc
            raise

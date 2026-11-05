"""Explicit adapter to the existing simulation-only PostgreSQL core.

Construction does not connect. Caller supplies an already-created isolated core
store on the calling thread. The WSGI host must provide one dedicated connection
per request thread (not reuse this object across threads or transaction pools).
"""
from contextlib import contextmanager
from oneil_execution_v2.api import AuthenticatedOwner, SimulationAPI
from oneil_execution_v2.domain import require
from oneil_execution_v2.store import PostgresStore, S


class CoreSimulationBackend:
    def __init__(self, store):
        from oneil_runtime_v2.simulation_store import SQLiteSimulationStore
        require(type(store) in (PostgresStore, SQLiteSimulationStore), 'ISOLATED_CORE_STORE_REQUIRED')
        self.store, self.api = store, SimulationAPI(store)
        self.policy_coordinator = None

    def status(self, account):
        return self.store.read_model(account)

    def control(self, account):
        return self.store.control(account)

    def unresolved(self, account):
        return self.store.unresolved(account)

    @contextmanager
    def _revision_fence(self, account, expected):
        self.store.assert_owner()
        from oneil_runtime_v2.simulation_store import SQLiteSimulationStore
        if type(self.store) is SQLiteSimulationStore:
            with self.store.revision_fence(account, expected):
                yield
            return
        # Keep the account row lock until the nested core transaction commits.
        # This closes the preview/revision race without modifying the core.
        with self.store.conn.transaction():
            row = self.store.conn.execute(f'SELECT * FROM {S}accounts WHERE account=%s FOR UPDATE', (account,)).fetchone()
            require(row is not None and row['mode'] == 'SIMULATION', 'ACCOUNT_NOT_SIMULATION')
            require(row['revision'] == expected, 'CONTROL_REVISION_CONFLICT')
            yield

    def confirm_order(self, principal, account, order, expected, confirmation_id):
        # Raw browser economics never create a stranded/unvalidated queue row.
        require(False, 'SERVER_POLICY_PROPOSAL_REQUIRED')

    def validate_raw_order(self):
        require(False, 'SERVER_POLICY_PROPOSAL_REQUIRED')

    def preview_policy_order(self, principal, account, proposal_id, expected):
        require(self.policy_coordinator is not None, 'SERVER_POLICY_PROPOSAL_REQUIRED')
        return self.policy_coordinator.preview(principal, account, proposal_id, expected)

    def confirm_policy_order(self, session, content, confirmation_id, *, clock):
        require(self.policy_coordinator is not None, 'SERVER_POLICY_PROPOSAL_REQUIRED')
        return self.policy_coordinator.confirm(session, content, confirmation_id, clock=clock)

    def policy_command_outcome_unknown(self, account):
        if self.policy_coordinator is not None:
            self.policy_coordinator.outcome_unknown(account)

    def preview_runtime_activation(self, principal, account, expected):
        require(self.policy_coordinator is not None, 'EXPLICIT_STARTED_RUNTIME_REQUIRED')
        return self.policy_coordinator.preview_activation(principal, account, expected)

    def confirm_runtime_activation(self, session, content, confirmation_id, *, clock):
        require(self.policy_coordinator is not None, 'EXPLICIT_STARTED_RUNTIME_REQUIRED')
        return self.policy_coordinator.confirm_activation(session, content, confirmation_id, clock=clock)

    def resume_simulation(self, principal, account, expected):
        with self._revision_fence(account, expected):
            return self.api.resume_simulation(AuthenticatedOwner(principal, account), expected)

    def halt(self, principal, account, expected):
        with self._revision_fence(account, expected):
            return self.api.halt(AuthenticatedOwner(principal, account), expected)

    def cancel_pending(self, principal, account, request_id, expected):
        with self._revision_fence(account, expected):
            return self.api.cancel_pending(AuthenticatedOwner(principal, account), request_id)

"""Server-neutral simulation control facade. No HTTP listener or production login.

A future web adapter authenticates the owner, enforces CSRF/origin, maps an allowed
account on the server, validates input sizes, and signs/binds an approval receipt.
Never deserialize an AuthenticatedOwner or Approval directly from browser JSON.
"""
from dataclasses import dataclass
from datetime import timedelta
import uuid
from .domain import Approval, Order, require


@dataclass(frozen=True)
class AuthenticatedOwner:
    principal: str
    account: str


class SimulationAPI:
    def __init__(self, store):
        self.store = store

    def _owner(self, owner):
        require(type(owner) is AuthenticatedOwner and bool(owner.principal), 'AUTHENTICATED_OWNER_REQUIRED')
        self.store.control(owner.account)

    def confirm_order(self, owner, order, *, idempotency_key):
        """Called only after the human confirms this exact simulated order.

        Durable acceptance is not broker acknowledgment or a fill. Return 202 with
        request_id and read-model URL in a future HTTP transport.
        """
        self._owner(owner)
        require(order.account==owner.account, 'ACCOUNT_ACCESS_DENIED')
        approval = Approval(uuid.uuid4().hex, owner.principal, order.digest,
                            min(order.expires_at, self.store.now()+timedelta(minutes=2)))
        row = self.store.enqueue(order, approval, idempotency_key)
        return {'request_id':str(row['request_id']), 'state':row['state'], 'mode':'SIMULATION', 'live_capable':False}

    def cancel_pending(self, owner, request_id):
        self._owner(owner)
        row = self.store.cancel_pending(owner.account, request_id)
        return {'request_id':str(row['request_id']), 'state':row['state']}

    def halt(self, owner, expected_revision):
        self._owner(owner)
        return self.store.set_halt(owner.account, halted=True, expected_revision=expected_revision)

    def resume_simulation(self, owner, expected_revision):
        self._owner(owner)
        require(not self.store.unresolved(owner.account), 'UNRESOLVED_ORDER')
        return self.store.set_halt(owner.account, halted=False, expected_revision=expected_revision)

    def status(self, owner):
        self._owner(owner)
        return self.store.read_model(owner.account)

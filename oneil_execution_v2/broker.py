"""Network-free simulator and explicit future broker contract.

There is intentionally no requests/httpx import, URL, API key, or live adapter.
KIS has no client idempotency key guarantee. A real adapter MUST NOT invent the
request_id linkage used by this simulator; unprovable correlation stays UNKNOWN.
"""
from dataclasses import replace
from copy import deepcopy
from .domain import require
from datetime import timedelta
from .domain import AccountSnapshot, BrokerOrder, History, Quote, SessionPermit


class SimulatedBroker:
    def __init__(self, now, *, cash_krw=10_000_000, positions=None, behavior='ack'):
        self.now = now
        self.cash_krw = cash_krw
        self.positions = positions or {}
        self.behavior = behavior
        self.orders = {}
        self.specs = {}
        self.calls = []
        self.history_complete = True
        self.quote_price = 10_000
        self.daily_loss_krw = 0
        self.quote_offset = timedelta(0)
        self.account_offset = timedelta(0)
        self.history_offset = timedelta(0)
        self.submit_hook = None
        self.balances = {}
        self._policy_contexts = {}

    def history(self, account):
        return History(account, self.now()+self.history_offset, self.history_complete,
                       tuple(x for x in self.orders.values() if x.account == account))

    def install_policy_context(self, context):
        """Install explicitly supplied market/calendar fixtures, never infer them.

        This does not reset balances, fill watermarks, intent or ownership. A new
        observation must reconcile those separately before policy admission.
        """
        from oneil_policy_v2.admission import PolicyContext
        require(type(context) is PolicyContext, 'EXPLICIT_POLICY_EVIDENCE_REQUIRED')
        context.policy.validate()
        self._policy_contexts[context.policy.account] = deepcopy(context)

    def _policy_market(self, order):
        context = self._policy_contexts.get(order.account)
        require(context is not None and context.policy.version == order.policy_version,
                'SIMULATOR_POLICY_EVIDENCE_REQUIRED')
        market = context.markets.get((order.ticker, order.side))
        require(market is not None, 'MARKET_EVIDENCE_REQUIRED')
        market.validate(context.policy, self.now(), order.ticker, order.side)
        return context, market

    def quote(self, order):
        if order.policy_version.startswith('policy-v2-'):
            _, market = self._policy_market(order)
            return Quote(market.ticker, market.venue, market.price_krw, market.evidence.observed_at,
                         market.executable)
        return Quote(order.ticker, order.venue, self.quote_price, self.now()+self.quote_offset, True)

    def _balance(self, account):
        if account not in self.balances:
            self.balances[account] = {'cash':self.cash_krw, 'positions':deepcopy(self.positions), 'fills':{}}
        return self.balances[account]

    def account(self, account):
        balance = self._balance(account)
        exposure = sum(x['market_value_krw'] for x in balance['positions'].values())
        return AccountSnapshot(account, self.now()+self.account_offset, balance['cash']+exposure,
                               balance['cash'], exposure, self.daily_loss_krw,
                               deepcopy(balance['positions']), True, balance['fills'].copy())

    def _reflect_fill(self, observed):
        balance = self._balance(observed.account)
        previous = balance['fills'].get(observed.request_id, 0)
        delta = observed.filled_quantity - previous
        require(delta >= 0, 'SIMULATOR_FILL_REGRESSION')
        if delta == 0:
            return
        position = balance['positions'].setdefault(observed.ticker, {'quantity':0, 'market_value_krw':0})
        if observed.side == 'BUY':
            require(balance['cash'] >= delta*observed.limit_price, 'SIMULATOR_INSUFFICIENT_CASH')
            balance['cash'] -= delta*observed.limit_price
            position['quantity'] += delta
            position['market_value_krw'] += delta*observed.limit_price
        else:
            require(position['quantity'] >= delta, 'SIMULATOR_NAKED_SELL')
            position['market_value_krw'] = position['market_value_krw']*(position['quantity']-delta)//position['quantity']
            position['quantity'] -= delta
            balance['cash'] += delta*observed.limit_price
        if position['quantity'] == 0:
            del balance['positions'][observed.ticker]
        balance['fills'][observed.request_id] = observed.filled_quantity

    def session(self, order):
        if order.policy_version.startswith('policy-v2-'):
            context, market = self._policy_market(order)
            session = context.sessions.get(order.venue)
            require(session is not None, 'SESSION_EVIDENCE_REQUIRED')
            return session.permit(context.policy, self.now(), market)
        # A fixture permit, NOT a live exchange-hours rule or holiday calendar.
        from .domain import KST
        now = self.now()
        return SessionPermit(order.venue, now.astimezone(KST).date().isoformat(),
                             now-timedelta(seconds=1), now+timedelta(seconds=60),
                             'SIMULATED_LIMIT', 'simulation-fixture-only', now)

    def submit(self, request_id, order, permit):
        self.calls.append(request_id)
        require(request_id not in self.specs, 'SIMULATOR_DUPLICATE_ATTEMPT')
        self.specs[request_id] = (order, permit)
        if self.submit_hook:
            self.submit_hook()
        if self.behavior == 'timeout_before_accept':
            raise TimeoutError('SIMULATED_TRANSPORT_TIMEOUT')
        status = {'ack':'ACKNOWLEDGED', 'fill':'FILLED', 'partial':'PART_FILLED',
                  'reject':'REJECTED', 'timeout_after_accept':'ACKNOWLEDGED',
                  'crash_after_accept':'ACKNOWLEDGED', 'fill_timeout_after_accept':'FILLED'}.get(self.behavior)
        if status is None:
            raise ValueError('INVALID_SIMULATION_BEHAVIOR')
        if status == 'PART_FILLED' and order.quantity == 1:
            status = 'FILLED'
        quantity = order.quantity if status=='FILLED' else max(1, order.quantity//2) if status=='PART_FILLED' else 0
        result = BrokerOrder(request_id, order.digest, f'sim_{len(self.orders)+1}', order.account,
                             permit.trading_day, order.ticker, order.side, order.venue,
                             order.quantity, order.limit_price, status, quantity)
        result.validate(request_id, order, permit.trading_day)
        self._reflect_fill(result)
        self.orders[request_id] = result
        if self.behavior in {'timeout_after_accept', 'fill_timeout_after_accept'}:
            raise TimeoutError('SIMULATED_RESPONSE_LOST')
        if self.behavior == 'crash_after_accept':
            raise SystemExit('SIMULATED_PROCESS_CRASH')
        return result

    def advance(self, request_id, *, status, filled_quantity):
        updated = replace(self.orders[request_id], status=status, filled_quantity=filled_quantity)
        order, permit = self.specs[request_id]
        updated.validate(request_id, order, permit.trading_day)
        previous = self.orders[request_id]
        allowed = {'ACKNOWLEDGED': {'ACKNOWLEDGED','PART_FILLED','FILLED','CANCELLED','REJECTED'},
                   'PART_FILLED': {'PART_FILLED','FILLED','CANCELLED'},
                   'FILLED': {'FILLED'}, 'CANCELLED': {'CANCELLED'}, 'REJECTED': {'REJECTED'}}
        require(updated.status in allowed[previous.status], 'SIMULATOR_STATE_REGRESSION')
        self._reflect_fill(updated)
        self.orders[request_id] = updated

"""Strict, network-free policy revalidation at the durable admission boundary.

These are trusted server-side simulation fixtures, never browser JSON or owner
approval. Missing registrations after a restart block; nothing reconstructs an
intent, portfolio ownership, calendar or setting from a permissive default.
"""
from copy import deepcopy
from dataclasses import asdict, dataclass, fields, is_dataclass, replace
from datetime import datetime
import hashlib
import json

from oneil_execution_v2.domain import (AccountSnapshot, History, Order, Quote,
    SessionPermit, check_risk, fresh, integer, require)
from .evidence import AccountEvidence, CandidateBatch, Controls
from .planner import ManualAdd, Proposal, charged, plan_automatic, plan_manual_add
from .policy import TradingPolicy


def json_value(value):
    """Canonical, credential-free typed evidence, for immutable admission audit."""
    if is_dataclass(value):
        return {f.name: json_value(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        # Market keys are (ticker, side), so serialize entries without key loss.
        return [{'key': json_value(k), 'value': json_value(v)}
                for k, v in sorted(value.items(), key=lambda pair: str(pair[0]))]
    if isinstance(value, (tuple, list)):
        return [json_value(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted(json_value(v) for v in value)
    require(value is None or type(value) in (str, int, bool), 'POLICY_EVIDENCE_INVALID')
    return value


def history_digest(history):
    require(type(history) is History and history.complete is True, 'HISTORY_INCOMPLETE')
    # The observation timestamp may advance without economic changes. Completeness,
    # identities and cumulative outcomes must match the evidence provider exactly.
    data = {'account': history.account, 'complete': history.complete,
            'orders': sorted((asdict(o) for o in history.orders), key=lambda o: o['request_id'])}
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


@dataclass(frozen=True)
class PolicyContext:
    policy: TradingPolicy
    account: AccountEvidence
    controls: Controls
    control_revision: int
    broker_history_digest: str
    fill_watermarks: dict
    candidates: CandidateBatch | None
    markets: dict
    sessions: dict
    technical: dict


@dataclass(frozen=True)
class PolicyAdmission:
    proposal: Proposal  # original exact proposal, not an approval receipt
    context: PolicyContext  # current independent observations, refreshed explicitly
    mode: str  # AUTOMATIC or MANUAL_ADD
    manual: ManualAdd | None
    original_context: PolicyContext  # immutable evidence used to create the exact proposal


class SimulationPolicySource:
    """Explicit fixtures only. No network, policy inference or persistence fallback.

    The server attaches a proposal to its committed request ID. Durable owner
    confirmation remains mandatory in the store. Copies isolate all nested maps.
    Reinstall reviewed fixtures after restart; otherwise admission stays blocked.
    """
    def __init__(self):
        self._requests = {}

    def register(self, request_id, admission):
        require(type(admission) is PolicyAdmission and type(admission.context) is PolicyContext
                and type(admission.original_context) is PolicyContext,
                'POLICY_ADMISSION_REQUIRED')
        key = str(request_id)
        require(key and len(key) <= 128, 'POLICY_REQUEST_ID_INVALID')
        existing = self._requests.get(key)
        if existing is not None:
            require(existing.proposal == admission.proposal and existing.mode == admission.mode
                    and existing.manual == admission.manual and existing.original_context == admission.original_context, 'POLICY_INTENT_IMMUTABLE')
        self._requests[key] = deepcopy(admission)

    def read(self, request_id):
        admission = self._requests.get(str(request_id))
        require(admission is not None, 'POLICY_ADMISSION_REQUIRED')
        return deepcopy(admission)


@dataclass(frozen=True)
class PolicyAdmissionGate:
    """The store accepts this exact validator for a policy-v2 attempt.

    Keeping validation inside the typed gate prevents an ordinary no-op callback
    from admitting policy requests. This is still trusted application code, not
    a Python sandbox or a substitute for database runtime-role permissions.
    """
    store: object
    source: SimulationPolicySource
    row: dict
    order: Order
    policy: object
    quote: Quote
    snapshot: AccountSnapshot
    permit: SessionPermit
    history: History
    fill_requirements: dict
    runtime_guard: object = None

    def __post_init__(self):
        require(type(self.source) is SimulationPolicySource, 'POLICY_ADMISSION_REQUIRED')
        if self.runtime_guard is not None:
            from oneil_runtime_v2.orchestrator import SimulationRuntimeGuard
            require(type(self.runtime_guard) is SimulationRuntimeGuard, 'STRICT_RUNTIME_GUARD_REQUIRED')
        for name in ('row', 'order', 'policy', 'quote', 'snapshot', 'permit', 'history', 'fill_requirements'):
            object.__setattr__(self, name, deepcopy(getattr(self, name)))

    def __call__(self, now, daily):
        if self.runtime_guard is not None:
            require(self.runtime_guard.lease.account == self.order.account, 'RUNTIME_ACCOUNT_MISMATCH')
            self.runtime_guard.check(now, control_revision=self.store.control(self.order.account)['revision'])
        require(fresh(self.history.observed_at, now, self.policy.max_history_age_seconds), 'HISTORY_STALE')
        require(type(self.snapshot.fill_watermarks) is dict and all(
            type(self.snapshot.fill_watermarks.get(k)) is int and self.snapshot.fill_watermarks[k] >= v
            for k, v in self.fill_requirements.items()), 'ACCOUNT_NOT_CAUSALLY_RECONCILED')
        check_risk(self.order, self.policy, self.quote, self.snapshot, self.permit, now, daily)
        current = self.store.control(self.order.account)
        require(current['halted'] is False and current['policy'] == asdict(self.policy), 'CONTROL_CHANGED')
        audit = validate_admission(self.source.read(self.row['request_id']), row=self.row,
            order=self.order, policy=self.policy, quote=self.quote, snapshot=self.snapshot,
            permit=self.permit, history=self.history, fill_requirements=self.fill_requirements,
            ledger=self.store.admission_ledger(self.order.account, self.permit.trading_day),
            control_revision=current['revision'], now=now)
        if self.runtime_guard is not None:
            # Re-sample clocks after policy validation, still inside final DB locks.
            audit['runtime'] = self.runtime_guard.check(self.store.now(),
                control_revision=self.store.control(self.order.account)['revision'])
        return audit


def validate_admission(admission, *, row, order, policy, quote, snapshot, permit,
                       history, fill_requirements, ledger, control_revision, now):
    """Called under the store's account/control/request locks, before one attempt."""
    require(type(admission) is PolicyAdmission and type(admission.context) is PolicyContext,
            'POLICY_ADMISSION_REQUIRED')
    context = admission.context
    trading = context.policy
    require(type(trading) is TradingPolicy and type(context.account) is AccountEvidence
            and type(context.controls) is Controls, 'EXPLICIT_POLICY_EVIDENCE_REQUIRED')
    trading.validate(); context.account.validate(trading, now); context.controls.validate(trading, now)
    require(trading.account == order.account and trading.core_risk == policy
            and order.policy_version == trading.version, 'POLICY_CHANGED')
    require(integer(context.control_revision, 1) and context.control_revision == control_revision
            and row['control_revision'] == control_revision, 'CONTROL_CHANGED')
    require(type(admission.proposal) is Proposal and admission.proposal.order == order,
            'POLICY_ORDER_MISMATCH')
    require(type(admission.original_context) is PolicyContext
            and admission.original_context.policy == trading, 'POLICY_ORIGINAL_EVIDENCE_CHANGED')
    require(type(snapshot) is AccountSnapshot and snapshot.complete is True
            and snapshot.account == trading.account, 'ACCOUNT_UNVERIFIED')
    expected = {p.ticker: {'quantity': p.quantity, 'market_value_krw': p.market_value_krw}
                for p in context.account.positions}
    require(snapshot.positions == expected, 'POLICY_POSITION_MISMATCH')
    account = context.account
    require(account.equity_krw == snapshot.equity_krw
            and account.orderable_cash_krw <= snapshot.cash_available_krw
            and account.invested_krw >= snapshot.gross_exposure_krw
            and account.daily_loss_krw >= snapshot.daily_loss_krw, 'POLICY_ACCOUNT_MISMATCH')
    require(context.broker_history_digest == history_digest(history)
            and history.account == order.account
            and account.history_evidence.observed_at <= history.observed_at,
            'POLICY_HISTORY_MISMATCH')
    require(type(context.fill_watermarks) is dict and context.fill_watermarks == snapshot.fill_watermarks
            and all(type(context.fill_watermarks.get(k)) is int and context.fill_watermarks[k] >= v
                    for k, v in fill_requirements.items()), 'ACCOUNT_NOT_CAUSALLY_RECONCILED')
    require(type(ledger) is dict and type(ledger.get('orders')) is tuple,
            'POLICY_DURABLE_LEDGER_REQUIRED')
    durable_buys = []
    for committed in ledger['orders']:
        require(type(committed) is Order and committed.account == order.account,
                'POLICY_DURABLE_LEDGER_INVALID')
        committed.validate()
        if committed.side == 'BUY':
            durable_buys.append(committed)
    # Reserve full buffered amounts even for rejection/cancellation; never refund
    # daily risk capacity or permit a second same-day buy after a terminal attempt.
    daily = max(account.daily_buy_krw, sum(charged(o.quantity, o.limit_price) for o in durable_buys))
    bought = account.bought_today | frozenset(o.ticker for o in durable_buys)
    # Cross-check actual broker history too, so a missing/incomplete ledger fails
    # conservatively rather than silently increasing capacity.
    broker_buys = tuple(o for o in history.orders if o.trading_day == account.trading_day and o.side == 'BUY')
    daily = max(daily, sum(charged(o.quantity, o.limit_price) for o in broker_buys))
    bought |= frozenset(o.ticker for o in broker_buys)
    effective_account = replace(account, daily_buy_krw=daily, bought_today=bought)
    if admission.mode == 'AUTOMATIC':
        require(admission.manual is None and admission.proposal.manual_request_id is None,
                'POLICY_INTENT_MISMATCH')
        proposals = plan_automatic(trading, effective_account, context.controls,
            context.candidates, context.markets, context.sessions, now=now, technical=context.technical)
        require(len(proposals) == 1, 'POLICY_NO_ELIGIBLE_PROPOSAL')
        replanned = proposals[0]
    elif admission.mode == 'MANUAL_ADD':
        require(type(admission.manual) is ManualAdd
                and row['idempotency_key'] == 'policy-manual-' + admission.manual.request_id
                and admission.proposal.manual_request_id == admission.manual.request_id,
                'MANUAL_IDEMPOTENCY_REQUIRED')
        replanned = plan_manual_add(trading, effective_account, context.controls, admission.manual,
            context.markets, context.sessions, now=now, technical=context.technical)
    else:
        require(False, 'POLICY_INTENT_MODE_INVALID')
    # Fresh evidence may shorten validity but may never change exact confirmed
    # economics, shrink quantity or extend the original order/approval lifetime.
    require(replace(replanned.order, expires_at=order.expires_at) == order
            and order.expires_at <= replanned.order.expires_at
            and replanned.reason == admission.proposal.reason
            and replanned.buffered_cost_krw == admission.proposal.buffered_cost_krw,
            'POLICY_PROPOSAL_CHANGED')
    market = context.markets[(order.ticker, order.side)]
    expected_quote = Quote(market.ticker, market.venue, market.price_krw, market.evidence.observed_at,
                           market.executable)
    require(quote == expected_quote, 'POLICY_QUOTE_MISMATCH')
    expected_permit = context.sessions[order.venue].permit(trading, now, market)
    require(permit == expected_permit, 'POLICY_SESSION_MISMATCH')
    # Re-use the core risk checker with conservative whole-account exposures and
    # sellable quantities, not the simulator's simpler market-value accounting.
    effective_snapshot = AccountSnapshot(account.account, account.evidence.observed_at,
        account.equity_krw, min(account.deposit_krw, account.orderable_cash_krw), account.invested_krw,
        account.daily_loss_krw,
        {p.ticker: {'quantity': p.sellable_quantity, 'market_value_krw': p.exposure} for p in account.positions},
        True, context.fill_watermarks)
    check_risk(order, policy, quote, effective_snapshot, permit, now, daily)
    return {'schema_version': 1, 'mode': 'SIMULATION', 'live_capable': False,
            'order_digest': order.digest, 'request_id': str(row['request_id']), 'control_revision': control_revision,
            'validated_at': now.isoformat(), 'admission': json_value(admission),
            'effective_daily_buffered_buy_krw': daily, 'effective_bought_today': sorted(bought),
            'broker_snapshot': json_value(snapshot), 'broker_history': json_value(history),
            'durable_orders': json_value(ledger['orders'])}

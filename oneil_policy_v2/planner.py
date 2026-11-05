"""Pure proposal generation. No DB, broker, execution, approval, or activation API."""
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
import re

from oneil_execution_v2.domain import AccountSnapshot, KST, Order, Quote, aware, check_risk, integer, require
from .evidence import AccountEvidence, CandidateBatch, Controls, MarketEvidence, SessionEvidence, TechnicalExit, ticker
from .policy import TradingPolicy, reference


@dataclass(frozen=True)
class Budget:
    deposit_krw: int
    reserve_krw: int
    trading_krw: int
    per_stock_krw: int
    remaining_krw: int


def budget(policy, account, now):
    policy.validate(); account.validate(policy, now)
    trading = account.deposit_krw * (100 - policy.reserve_pct) // 100
    return Budget(account.deposit_krw, account.deposit_krw - trading, trading,
                  trading * policy.per_stock_pct // 100,
                  min(max(0, trading - account.invested_krw), account.orderable_cash_krw))


def charged(quantity, price):
    # Preserve verified legacy 0.5% buffer and exact integer rounding.
    return (quantity * price * 1005 + 999) // 1000


@dataclass(frozen=True)
class Proposal:
    order: Order
    reason: str
    buffered_cost_krw: int
    evidence_references: tuple[str, ...]
    manual_request_id: str | None = None

    @property
    def authorized(self):
        return False

    @property
    def orders_sent(self):
        return 0

    @property
    def gate(self):
        return 'DENY'


@dataclass(frozen=True)
class ManualAdd:
    request_id: str
    account: str
    ticker: str
    quantity: int
    limit_price: int
    expected_holding_quantity: int
    policy_version: str
    created_at: datetime
    expires_at: datetime
    owner_confirmation_reference: str

    def validate(self, policy, now, session):
        require(isinstance(self.request_id, str) and re.fullmatch(
            '[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}', self.request_id),
            'MANUAL_REQUEST_INVALID')
        ticker(self.ticker)
        require(self.account == policy.account and self.policy_version == policy.version, 'MANUAL_POLICY_CHANGED')
        require(all(integer(x, 1) and x <= 2147483647 for x in
                    (self.quantity, self.limit_price, self.expected_holding_quantity)), 'MANUAL_REQUEST_INVALID')
        aware(self.created_at); aware(self.expires_at)
        require(session.opens_at <= self.created_at <= now < self.expires_at <= session.closes_at and
                timedelta(0) < self.expires_at - self.created_at <= timedelta(seconds=2400), 'MANUAL_REQUEST_EXPIRED')
        require(self.created_at.astimezone(KST).date() == now.astimezone(KST).date() ==
                self.expires_at.astimezone(KST).date(), 'MANUAL_REQUEST_DAY_CHANGED')
        reference(self.owner_confirmation_reference)


def _context(policy, account, controls, now):
    require(type(policy) is TradingPolicy and type(account) is AccountEvidence and type(controls) is Controls,
            'EXPLICIT_POLICY_EVIDENCE_REQUIRED')
    aware(now); policy.validate(); controls.validate(policy, now); account.validate(policy, now)


def _market(policy, markets, sessions, now, code, side):
    require(type(markets) is dict and type(sessions) is dict, 'MARKET_EVIDENCE_REQUIRED')
    market = markets.get((code, side))
    require(type(market) is MarketEvidence, 'MARKET_EVIDENCE_REQUIRED')
    market.validate(policy, now, code, side)
    session = sessions.get(market.venue)
    require(type(session) is SessionEvidence, 'SESSION_EVIDENCE_REQUIRED')
    permit = session.permit(policy, now, market)
    return market, session, permit


def _proposal(policy, account, controls, now, market, session, permit, quantity, reason, *,
              extra_evidence=(), manual=None):
    expiry = min(
        permit.closes_at,
        controls.evidence.observed_at + timedelta(seconds=policy.max_control_age_seconds),
        account.evidence.observed_at + timedelta(seconds=policy.risk.max_account_age_seconds),
        account.history_evidence.observed_at + timedelta(seconds=policy.risk.max_history_age_seconds),
        market.evidence.observed_at + timedelta(seconds=policy.risk.max_quote_age_seconds),
        session.calendar_evidence.observed_at + timedelta(seconds=policy.max_session_age_seconds),
        session.capability_evidence.observed_at + timedelta(seconds=policy.max_session_age_seconds),
        *(stamp + timedelta(seconds=ttl) for _, stamp, ttl in extra_evidence),
        *([manual.expires_at] if manual else []))
    require(now < expiry, 'PROPOSAL_EVIDENCE_EXPIRED')
    require(quantity <= market.available_quantity, 'BOOK_SIZE_EXCEEDED')
    order = Order(policy.account, market.ticker, market.side, market.venue,
                  quantity, market.price_krw, expiry, policy.version).validate()
    snapshot = AccountSnapshot(account.account, account.evidence.observed_at,
        account.equity_krw, min(account.deposit_krw, account.orderable_cash_krw),
        account.invested_krw, account.daily_loss_krw,
        {p.ticker: {'quantity': p.sellable_quantity, 'market_value_krw': p.exposure} for p in account.positions}, True)
    check_risk(order, policy.core_risk,
               Quote(market.ticker, market.venue, market.price_krw, market.evidence.observed_at, True),
               snapshot, permit, now, account.daily_buy_krw)
    refs = (policy.source_reference, policy.migration_reference, controls.evidence.reference,
            account.evidence.reference, account.history_evidence.reference, market.evidence.reference,
            session.calendar_evidence.reference, session.capability_evidence.reference)
    refs += tuple(ref for ref, _, _ in extra_evidence)
    if manual:
        refs += (manual.owner_confirmation_reference,)
    return Proposal(order, reason, charged(quantity, market.price_krw) if market.side == 'BUY' else 0,
                    refs, manual.request_id if manual else None)


def _exits(policy, account, controls, markets, sessions, now, technical):
    if not (policy.automatic_sell_enabled or policy.stop_loss_enabled):
        return ()
    require(type(technical) is dict, 'TECHNICAL_EVIDENCE_INVALID')
    for position in sorted(account.positions, key=lambda p: p.ticker):
        quantity = min(position.quantity, position.sellable_quantity, position.managed_quantity)
        if not quantity:
            continue
        market, session, permit = _market(policy, markets, sessions, now, position.ticker, 'SELL')
        price, average = market.price_krw, Decimal(position.average_price)
        extra = ()
        if price * 100 <= average * (100 - policy.stop_loss_pct):
            reason = 'STOP_LOSS'
        elif price * 100 >= average * (100 + policy.take_profit_pct):
            reason = 'TAKE_PROFIT'
        else:
            signal = technical.get(position.ticker)
            if signal is None:
                continue  # Missing signal never invents an exit or disables percentage exits.
            require(type(signal) is TechnicalExit, 'TECHNICAL_SIGNAL_UNVERIFIED')
            signal.validate(policy, now, position.ticker)
            if not signal.exit:
                continue
            reason = 'TAKE_PROFIT_SIGNAL' if price > average else 'STOP_LOSS_SIGNAL'
            extra = ((signal.evidence.reference, signal.evidence.observed_at, policy.max_candidate_age_seconds),)
        enabled = policy.stop_loss_enabled if reason.startswith('STOP_LOSS') else policy.automatic_sell_enabled
        if enabled:
            # Preserve exact managed/sellable exit quantity; do not silently clip.
            # Emit the first fully evidenced exit immediately. Missing evidence
            # for a later holding must not discard this protective proposal.
            # When no valid exit is found, every managed holding was checked,
            # so any unknown earlier exit still prevents a discretionary buy.
            return (_proposal(policy, account, controls, now, market, session, permit, quantity,
                              reason, extra_evidence=extra),)
    return ()


def plan_automatic(policy, account, controls, candidates, markets, sessions, *, now, technical):
    """One simulation proposal per fresh observation. Exits have strict priority.

    No projected fills are used to finance buys. Re-evaluate with fresh account
    and reconciled history after every proposal. None is an approval receipt.
    """
    _context(policy, account, controls, now)
    exits = _exits(policy, account, controls, markets, sessions, now, technical)
    if exits:
        return exits[:1]
    if not policy.automatic_buy_enabled:
        return ()
    require(type(candidates) is CandidateBatch, 'CANDIDATE_EVIDENCE_REQUIRED')
    ranked = candidates.ranked(policy, now)
    allocation = budget(policy, account, now)
    if len(account.positions) >= policy.max_stock_count:
        return ()
    for candidate in ranked:
        if account.position(candidate.ticker) or candidate.ticker in account.bought_today:
            continue
        market, session, permit = _market(policy, markets, sessions, now, candidate.ticker, 'BUY')
        risk = policy.core_risk
        if account.daily_loss_krw >= risk.max_daily_loss_krw:
            require(False, 'DAILY_LOSS_LIMIT')
        # Existing core limits remain additional ceilings; fees are conservatively
        # included in every monetary cap, not only current-cash sizing.
        cap = min(allocation.remaining_krw, allocation.per_stock_krw, market.broker_orderable_krw,
                  risk.max_order_krw, max(0, risk.max_daily_buy_krw - account.daily_buy_krw),
                  max(0, risk.max_gross_exposure_krw - account.invested_krw), risk.max_position_krw)
        quantity = min(market.broker_orderable_quantity, market.available_quantity,
                       cap * 1000 // (market.price_krw * 1005))
        if not quantity:
            continue
        return (_proposal(policy, account, controls, now, market, session, permit, quantity, 'RS_SELECTION',
            extra_evidence=((candidates.evidence.reference, candidates.evidence.observed_at,
                             policy.max_candidate_age_seconds),
                            (candidates.session_evidence.reference, candidates.session_evidence.observed_at,
                             policy.max_session_age_seconds))),)
    return ()


def plan_manual_add(policy, account, controls, request, markets, sessions, *, now, technical):
    _context(policy, account, controls, now)
    require(policy.automatic_buy_enabled, 'BUY_DISABLED')
    require(type(request) is ManualAdd, 'MANUAL_REQUEST_INVALID')
    market, session, permit = _market(policy, markets, sessions, now, request.ticker, 'BUY')
    request.validate(policy, now, session)
    require(not _exits(policy, account, controls, markets, sessions, now, technical), 'EXIT_PRIORITY')
    position = account.position(request.ticker)
    require(position is not None and position.quantity == request.expected_holding_quantity, 'HOLDING_CHANGED')
    require(request.ticker not in account.bought_today, 'ALREADY_BOUGHT_TODAY')
    require(market.price_krw <= request.limit_price, 'OWNER_LIMIT_PRICE_EXCEEDED')
    allocation = budget(policy, account, now)
    risk = policy.core_risk
    cap = min(allocation.remaining_krw, max(0, allocation.per_stock_krw - position.exposure),
              market.broker_orderable_krw, risk.max_order_krw,
              max(0, risk.max_position_krw - position.exposure),
              max(0, risk.max_daily_buy_krw - account.daily_buy_krw),
              max(0, risk.max_gross_exposure_krw - account.invested_krw))
    require(request.quantity <= market.broker_orderable_quantity and
            charged(request.quantity, market.price_krw) <= cap, 'MANUAL_BUY_LIMIT_EXCEEDED')
    # Exact quantity preserved, including rejecting insufficient book depth.
    return _proposal(policy, account, controls, now, market, session, permit,
                     request.quantity, 'OWNER_AVERAGING', manual=request)


def plan_manual_exit(*args, **kwargs):
    # No verified legacy manual-exit semantics or approval contract was found.
    require(False, 'MANUAL_EXIT_UNVERIFIED_NOT_IMPLEMENTED')

"""Enriched evidence missing from the core snapshot, supplied explicitly.

All types are data contracts, NOT authentication, broker verification, or live
capability. A production adapter must establish provenance outside this package.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
import re

from oneil_execution_v2.domain import KST, SessionPermit, aware, fresh, integer, require
from .policy import reference


def ticker(value):
    require(isinstance(value, str) and re.fullmatch('[0-9]{6}', value), 'BAD_TICKER')
    return value


def day(value):
    from datetime import date
    require(isinstance(value, str), 'INVALID_TRADING_DAY')
    try:
        require(date.fromisoformat(value).isoformat() == value, 'INVALID_TRADING_DAY')
    except ValueError:
        require(False, 'INVALID_TRADING_DAY')
    return value


@dataclass(frozen=True)
class Evidence:
    reference: str
    observed_at: datetime
    verified: bool

    def validate(self, now, ttl):
        reference(self.reference)
        require(self.verified is True, 'EVIDENCE_UNVERIFIED')
        require(fresh(self.observed_at, now, ttl), 'EVIDENCE_STALE')


@dataclass(frozen=True)
class Controls:
    account: str
    policy_version: str
    halted: bool
    evidence: Evidence

    def validate(self, policy, now):
        self.evidence.validate(now, policy.max_control_age_seconds)
        require(self.account == policy.account and self.policy_version == policy.version, 'POLICY_CHANGED')
        require(self.halted is False, 'HALTED_OR_UNKNOWN')


@dataclass(frozen=True)
class Position:
    ticker: str
    quantity: int
    sellable_quantity: int
    managed_quantity: int
    cost_krw: int
    market_value_krw: int
    average_price: str
    managed_reference: str

    def validate(self):
        ticker(self.ticker)
        require(all(integer(x) for x in (self.quantity, self.sellable_quantity, self.managed_quantity,
                                         self.cost_krw, self.market_value_krw)) and self.quantity > 0,
                'POSITION_INVALID')
        require(max(self.sellable_quantity, self.managed_quantity) <= self.quantity, 'POSITION_INVALID')
        require(isinstance(self.average_price, str) and len(self.average_price) <= 40, 'COST_INVALID')
        try:
            average = Decimal(self.average_price)
        except InvalidOperation:
            require(False, 'COST_INVALID')
        require(average.is_finite() and average > 0, 'COST_INVALID')
        # The imported cost must not understate average acquisition cost.
        require(self.cost_krw >= average * self.quantity, 'COST_INCONSISTENT')
        reference(self.managed_reference)
        return self

    @property
    def exposure(self):
        return max(self.cost_krw, self.market_value_krw)


@dataclass(frozen=True)
class AccountEvidence:
    account: str
    trading_day: str
    deposit_krw: int
    orderable_cash_krw: int
    equity_krw: int
    reported_invested_krw: int
    daily_loss_krw: int
    daily_buy_krw: int
    positions: tuple[Position, ...]
    bought_today: frozenset[str]
    unresolved_orders: tuple[str, ...]
    complete: bool
    history_complete: bool
    reflected_history_reference: str
    evidence: Evidence
    history_evidence: Evidence

    def validate(self, policy, now):
        self.evidence.validate(now, policy.risk.max_account_age_seconds)
        self.history_evidence.validate(now, policy.risk.max_history_age_seconds)
        require(self.account == policy.account, 'ACCOUNT_MISMATCH')
        require(self.trading_day == aware(now).astimezone(KST).date().isoformat(), 'ACCOUNT_DAY_MISMATCH')
        require(self.complete is True and self.history_complete is True, 'ACCOUNT_OR_HISTORY_INCOMPLETE')
        require(self.reflected_history_reference == self.history_evidence.reference, 'ACCOUNT_NOT_CAUSALLY_RECONCILED')
        require(all(integer(x) for x in (self.deposit_krw, self.orderable_cash_krw, self.equity_krw,
                    self.reported_invested_krw, self.daily_loss_krw, self.daily_buy_krw)) and self.equity_krw > 0,
                'ACCOUNT_INVALID')
        require(type(self.positions) is tuple and type(self.bought_today) is frozenset
                and type(self.unresolved_orders) is tuple, 'ACCOUNT_INVALID')
        require(not self.unresolved_orders, 'UNRESOLVED_ORDERS')
        seen = set()
        for position in self.positions:
            require(type(position) is Position, 'POSITION_INVALID')
            position.validate()
            require(position.ticker not in seen, 'DUPLICATE_POSITION')
            seen.add(position.ticker)
        for code in self.bought_today:
            ticker(code)
        return self

    @property
    def invested_krw(self):
        return max(self.reported_invested_krw, sum(p.exposure for p in self.positions))

    def position(self, code):
        return next((p for p in self.positions if p.ticker == code), None)


@dataclass(frozen=True)
class MarketEvidence:
    account: str
    ticker: str
    venue: str
    side: str
    price_krw: int
    available_quantity: int
    broker_orderable_krw: int
    broker_orderable_quantity: int
    executable: bool
    evidence: Evidence

    def validate(self, policy, now, code, side):
        self.evidence.validate(now, policy.risk.max_quote_age_seconds)
        require(self.account == policy.account and (self.ticker, self.side) == (code, side)
                and self.venue in ('KRX', 'NXT'), 'QUOTE_IDENTITY_MISMATCH')
        require(integer(self.price_krw, 1) and all(integer(x) for x in (self.available_quantity,
                    self.broker_orderable_krw, self.broker_orderable_quantity)), 'QUOTE_INVALID')
        require(self.executable is True, 'QUOTE_NOT_EXECUTABLE')


@dataclass(frozen=True)
class SessionEvidence:
    venue: str
    trading_day: str
    business_day: bool
    opens_at: datetime
    closes_at: datetime
    order_code: str
    eligible_tickers: frozenset[str]
    allowed_sides: frozenset[str]
    calendar_evidence: Evidence
    capability_evidence: Evidence

    def permit(self, policy, now, market):
        self.calendar_evidence.validate(now, policy.max_session_age_seconds)
        self.capability_evidence.validate(now, policy.max_session_age_seconds)
        require(self.business_day is True, 'HOLIDAY_OR_UNVERIFIED_CALENDAR')
        require(self.venue == market.venue and self.trading_day == aware(now).astimezone(KST).date().isoformat(),
                'SESSION_IDENTITY_MISMATCH')
        require(type(self.eligible_tickers) is frozenset and type(self.allowed_sides) is frozenset,
                'SESSION_CAPABILITY_INVALID')
        require(self.allowed_sides and self.allowed_sides <= {'BUY', 'SELL'}, 'SESSION_CAPABILITY_INVALID')
        for code in self.eligible_tickers:
            ticker(code)
        require(market.ticker in self.eligible_tickers and market.side in self.allowed_sides,
                'SESSION_INSTRUMENT_NOT_ELIGIBLE')
        require(isinstance(self.order_code, str) and re.fullmatch('[0-9]{2}', self.order_code), 'ORDER_CODE_UNVERIFIED')
        aware(self.opens_at); aware(self.closes_at)
        require(self.opens_at <= now < self.closes_at and
                timedelta(0) < self.closes_at - self.opens_at <= timedelta(days=1), 'OUTSIDE_VERIFIED_SESSION')
        require(self.opens_at.astimezone(KST).date().isoformat() == self.trading_day and
                self.closes_at.astimezone(KST).date().isoformat() == self.trading_day, 'SESSION_DAY_MISMATCH')
        return SessionPermit(self.venue, self.trading_day, self.opens_at, self.closes_at, self.order_code,
                             self.calendar_evidence.reference + '|' + self.capability_evidence.reference,
                             min(self.calendar_evidence.observed_at, self.capability_evidence.observed_at))


@dataclass(frozen=True)
class Candidate:
    ticker: str
    rs: int


@dataclass(frozen=True)
class CandidateBatch:
    selection_policy: str
    trading_day: str
    source_session: str
    accepted_source_session: str  # explicitly verified previous/current exchange session
    candidates: tuple[Candidate, ...]
    evidence: Evidence
    session_evidence: Evidence

    def ranked(self, policy, now):
        self.evidence.validate(now, policy.max_candidate_age_seconds)
        self.session_evidence.validate(now, policy.max_session_age_seconds)
        require(self.selection_policy == 'RS_ONLY_V1', 'UNVERIFIED_STRATEGY_VARIANT')
        day(self.source_session); day(self.accepted_source_session)
        require(self.trading_day == aware(now).astimezone(KST).date().isoformat() and
                self.source_session == self.accepted_source_session and self.source_session <= self.trading_day,
                'CANDIDATE_SESSION_UNVERIFIED')
        require(type(self.candidates) is tuple and len(self.candidates) <= 10000, 'CANDIDATES_INVALID')
        seen = set()
        for candidate in self.candidates:
            require(type(candidate) is Candidate, 'CANDIDATE_INVALID')
            ticker(candidate.ticker)
            require(candidate.ticker not in seen and integer(candidate.rs, 1) and candidate.rs <= 99,
                    'CANDIDATE_INVALID')
            seen.add(candidate.ticker)
        return tuple(sorted((c for c in self.candidates if c.rs >= 90), key=lambda c: (-c.rs, c.ticker)))


@dataclass(frozen=True)
class TechnicalExit:
    ticker: str
    source_session: str
    accepted_source_session: str
    policy: str
    exit: bool
    evidence: Evidence

    def validate(self, policy, now, code):
        self.evidence.validate(now, policy.max_candidate_age_seconds)
        day(self.source_session); day(self.accepted_source_session)
        require(self.ticker == code and self.policy == 'BB_ICHI_TREND_V1' and type(self.exit) is bool and
                self.source_session == self.accepted_source_session and
                self.source_session <= aware(now).astimezone(KST).date().isoformat(), 'TECHNICAL_SIGNAL_UNVERIFIED')

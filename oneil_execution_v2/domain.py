"""Immutable intent, evidence, and policy types. Money is integer KRW, never float."""
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta
import hashlib
import json
import re
from zoneinfo import ZoneInfo

KST = ZoneInfo('Asia/Seoul')
TERMINAL = frozenset({'FILLED', 'CANCELLED', 'REJECTED', 'EXPIRED', 'BLOCKED'})
UNRESOLVED = frozenset({'SUBMISSION_UNKNOWN', 'ACKNOWLEDGED', 'PART_FILLED'})

class Blocked(ValueError):
    pass


def require(condition, code):
    if not condition:
        raise Blocked(code)


def aware(value):
    require(isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None,
            'AWARE_CLOCK_REQUIRED')
    return value


def integer(value, minimum=0):
    return type(value) is int and value >= minimum


def fresh(stamp, now, seconds):
    return 0 <= (aware(now) - aware(stamp)).total_seconds() <= seconds


@dataclass(frozen=True)
class Order:
    account: str  # SHA-256 fingerprint only, never broker credentials/account number.
    ticker: str
    side: str
    venue: str
    quantity: int
    limit_price: int
    expires_at: datetime
    policy_version: str

    def validate(self):
        require(isinstance(self.account, str) and re.fullmatch('[0-9a-f]{64}', self.account), 'BAD_ACCOUNT')
        require(isinstance(self.ticker, str) and re.fullmatch('[0-9]{6}', self.ticker), 'BAD_TICKER')
        require(self.side in ('BUY', 'SELL'), 'BAD_SIDE')
        require(self.venue in ('KRX', 'NXT'), 'BAD_VENUE')
        require(integer(self.quantity, 1) and self.quantity <= 1_000_000_000, 'BAD_QUANTITY')
        require(integer(self.limit_price, 1) and self.limit_price <= 1_000_000_000_000, 'BAD_PRICE')
        require(isinstance(self.policy_version, str) and 1 <= len(self.policy_version) <= 64, 'BAD_POLICY_VERSION')
        aware(self.expires_at)
        return self

    def payload(self):
        self.validate()
        return {**asdict(self), 'expires_at': self.expires_at.isoformat()}

    @classmethod
    def from_payload(cls, payload):
        return cls(**{**payload, 'expires_at': datetime.fromisoformat(payload['expires_at'])}).validate()

    @property
    def digest(self):
        return hashlib.sha256(json.dumps(self.payload(), sort_keys=True, separators=(',', ':')).encode()).hexdigest()

    @property
    def notional(self):
        return self.quantity * self.limit_price


@dataclass(frozen=True)
class Approval:
    """Server-owned receipt after authenticated owner confirmation, NOT a bool.

    This prototype accepts these receipts only inside the simulated account store.
    A future live implementation must verify an actual identity/session and nonce.
    A JSON object sent by a browser is not sufficient authorization.
    """
    approval_id: str
    principal: str
    order_digest: str
    expires_at: datetime

    def validate(self, order, now):
        require(isinstance(self.approval_id, str) and re.fullmatch('[A-Za-z0-9_-]{16,128}', self.approval_id), 'BAD_APPROVAL_ID')
        require(isinstance(self.principal, str) and 1 <= len(self.principal) <= 128, 'BAD_PRINCIPAL')
        require(self.order_digest == order.digest, 'APPROVAL_CONTENT_MISMATCH')
        require(aware(now) < aware(self.expires_at) <= order.expires_at, 'APPROVAL_EXPIRED')


@dataclass(frozen=True)
class RiskPolicy:
    version: str
    max_order_krw: int
    max_daily_buy_krw: int
    max_position_krw: int
    max_gross_exposure_krw: int
    max_daily_loss_krw: int
    max_quote_age_seconds: int = 5
    max_account_age_seconds: int = 10
    max_history_age_seconds: int = 10
    max_price_deviation_bps: int = 100

    def validate(self):
        require(isinstance(self.version, str) and 1 <= len(self.version) <= 64, 'BAD_POLICY_VERSION')
        for key, value in asdict(self).items():
            if key != 'version':
                require(integer(value, 1), 'INVALID_RISK_POLICY')
        require(self.max_price_deviation_bps <= 10000, 'INVALID_RISK_POLICY')
        return self


@dataclass(frozen=True)
class Quote:
    ticker: str
    venue: str
    price: int
    observed_at: datetime
    executable: bool


@dataclass(frozen=True)
class AccountSnapshot:
    account: str
    observed_at: datetime
    equity_krw: int
    cash_available_krw: int
    gross_exposure_krw: int
    daily_loss_krw: int
    positions: dict  # ticker -> {quantity, market_value_krw}; all nonnegative integers
    complete: bool
    fill_watermarks: dict = field(default_factory=dict)  # request_id -> reflected cumulative fill


@dataclass(frozen=True)
class SessionPermit:
    """Supplied by verified venue/calendar adapter; time-of-day alone is insufficient.

    Includes holidays, extraordinary closures and broker-specific order capability.
    Prototype fixtures are explicitly simulated. No default inferred calendar.
    """
    venue: str
    trading_day: str
    opens_at: datetime
    closes_at: datetime
    order_code: str
    reference: str
    verified_at: datetime

    def validate(self, order, now):
        require(self.venue == order.venue and self.trading_day == now.astimezone(KST).date().isoformat(), 'SESSION_IDENTITY_MISMATCH')
        require(aware(self.opens_at) <= now < aware(self.closes_at), 'OUTSIDE_VERIFIED_SESSION')
        require(self.opens_at < self.closes_at and (self.closes_at-self.opens_at) <= timedelta(hours=24), 'INVALID_SESSION')
        require(bool(self.reference) and bool(self.order_code) and fresh(self.verified_at, now, 86400), 'SESSION_NOT_VERIFIED')


@dataclass(frozen=True)
class BrokerOrder:
    request_id: str
    order_digest: str
    broker_order_id: str
    account: str
    trading_day: str
    ticker: str
    side: str
    venue: str
    quantity: int
    limit_price: int
    status: str
    filled_quantity: int

    def validate(self, request_id, order, trading_day):
        require((self.request_id, self.order_digest, self.account, self.trading_day, self.ticker,
                 self.side, self.venue, self.quantity, self.limit_price) ==
                (request_id, order.digest, order.account, trading_day, order.ticker,
                 order.side, order.venue, order.quantity, order.limit_price), 'BROKER_ORDER_IDENTITY_MISMATCH')
        require(isinstance(self.broker_order_id, str) and re.fullmatch('[A-Za-z0-9_-]{1,64}', self.broker_order_id), 'BAD_BROKER_ID')
        require(self.status in {'ACKNOWLEDGED', 'PART_FILLED', 'FILLED', 'CANCELLED', 'REJECTED'}, 'BAD_BROKER_STATUS')
        require(integer(self.filled_quantity) and self.filled_quantity <= order.quantity, 'BAD_FILL')
        require(self.status != 'FILLED' or self.filled_quantity == order.quantity, 'BAD_FILL')
        require(self.status != 'PART_FILLED' or 0 < self.filled_quantity < order.quantity, 'BAD_FILL')
        require(self.status not in {'ACKNOWLEDGED', 'REJECTED'} or self.filled_quantity == 0, 'BAD_FILL')


@dataclass(frozen=True)
class History:
    account: str
    observed_at: datetime
    complete: bool
    orders: tuple[BrokerOrder, ...]


def check_risk(order, policy, quote, account, permit, now, daily_buy_krw):
    order.validate(); policy.validate(); permit.validate(order, now)
    require(now < order.expires_at, 'REQUEST_EXPIRED')
    require(order.policy_version == policy.version, 'POLICY_CHANGED')
    require(quote.ticker == order.ticker and quote.venue == order.venue and quote.executable is True,
            'QUOTE_NOT_EXECUTABLE')
    require(integer(quote.price, 1) and fresh(quote.observed_at, now, policy.max_quote_age_seconds), 'QUOTE_STALE')
    require(abs(order.limit_price - quote.price) * 10000 <= quote.price * policy.max_price_deviation_bps,
            'PRICE_DEVIATION')
    require(account.account == order.account and account.complete is True and fresh(account.observed_at, now, policy.max_account_age_seconds), 'ACCOUNT_UNVERIFIED')
    require(all(integer(x) for x in [account.equity_krw, account.cash_available_krw, account.gross_exposure_krw, account.daily_loss_krw, daily_buy_krw]) and account.equity_krw > 0, 'ACCOUNT_INVALID')
    require(isinstance(account.positions, dict), 'POSITIONS_INVALID')
    for ticker, position in account.positions.items():
        require(re.fullmatch('[0-9]{6}', ticker) and isinstance(position, dict) and
                integer(position.get('quantity')) and integer(position.get('market_value_krw')), 'POSITIONS_INVALID')
    require(order.notional <= policy.max_order_krw, 'ORDER_LIMIT')
    # A protective sell remains possible at the loss threshold if separately approved.
    if order.side == 'BUY':
        require(account.daily_loss_krw < policy.max_daily_loss_krw, 'DAILY_LOSS_LIMIT')
        require(order.notional <= account.cash_available_krw, 'INSUFFICIENT_CASH')
        require(daily_buy_krw + order.notional <= policy.max_daily_buy_krw, 'DAILY_BUY_LIMIT')
        require(account.gross_exposure_krw + order.notional <= policy.max_gross_exposure_krw, 'GROSS_EXPOSURE_LIMIT')
        require(account.positions.get(order.ticker, {}).get('market_value_krw', 0) + order.notional <= policy.max_position_krw, 'POSITION_LIMIT')
    else:
        require(order.quantity <= account.positions.get(order.ticker, {}).get('quantity', 0), 'SELL_EXCEEDS_POSITION')

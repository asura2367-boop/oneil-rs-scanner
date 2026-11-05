"""Compose supplied KIS transcripts without promoting observations into authority.

Every current KIS observation is unauthenticated. This boundary therefore produces
an actionable diagnostic, never PolicyContext, Evidence(verified=True), an owner
approval, or a submission. Its sole worker integration is a fail-closed block in
SimulationPolicySource. See KIS_PIPELINE.md for the deliberately open gates.
"""
from dataclasses import dataclass, field
from datetime import datetime

from oneil_execution_v2.domain import Blocked, KST, aware, require
from oneil_execution_v2.kis_adapter import (
    AccountBinding, HistoryQuery, KisReadOnlyAdapter, KisResponse, RequestRecipe,
)
from oneil_execution_v2.kis_evidence import (
    BalanceQuery, BuyingPowerQuery, CaptureWindow, EvidenceOutcome, HolidayQuery,
    KisOfflineEvidenceAdapter, MarketQuery, OfflineRead, SellableQuery,
)

POLICY_BLOCK = 'KIS_POLICY_EVIDENCE_NOT_ESTABLISHED'
# These are missing facts, not switches callers can assert to True. A production
# implementation must establish each from reviewed authoritative providers.
MISSING_FACTS = (
    'AUTHENTICATED_ACCOUNT_AND_RESPONSE_PROVENANCE',
    'COMPLETE_ACCOUNT_SNAPSHOT_AND_CROSS_ENDPOINT_CAUSALITY',
    'COMPLETE_NATIVE_HISTORY_AND_DURABLE_REQUEST_IDENTITY',
    'BALANCE_REFLECTS_ALL_CONFIRMED_FILLS_AND_EXTERNAL_RESERVATIONS',
    'WHOLE_ACCOUNT_DAILY_REALIZED_AND_UNREALIZED_LOSS',
    'DAILY_BUY_COMMITMENTS_AND_BOUGHT_TODAY',
    'MANAGED_LOT_OWNERSHIP_AND_ACQUISITION_COST',
    'COMPLETE_SELLABLE_SHARES_FOR_ALL_HELD_INSTRUMENTS',
    'NO_RECEIVABLES_BUYING_POWER_AND_FINAL_CAPACITY_FOR_EXACT_ORDER',
    'PORTAL_VS_SAMPLE_SELLABLE_AND_HOLIDAY_CONTRACT_CONFLICTS',
    'EXECUTABLE_SIDE_SPECIFIC_QUOTE_AND_DEPTH',
    'AUTHORITATIVE_CALENDAR_AND_EXCEPTIONAL_CLOSURES',
    'VERIFIED_SESSION_INTERVAL_AND_ORDER_CODE',
    'VENUE_INSTRUMENT_AND_SIDE_ELIGIBILITY',
    'CURRENT_POLICY_CONTROLS_CANDIDATES_AND_TECHNICAL_SIGNALS',
)


@dataclass(frozen=True)
class KisOfflineInputSet:
    """One explicit scope; raw identifiers/transcripts are never report fields.

    Empty tuples/None are absent input, never evidence of zero cash/positions or
    empty history. Reads must be supplied by the caller; no transport is invoked.
    """
    binding: AccountBinding = field(repr=False)
    trading_day: str
    ticker: str
    venue: str
    # Mode 01's portal contract requires a blank wire price. None is the only
    # supported value; positive order prices must not be claimed as a binding.
    inquiry_price_krw: None = None
    balance_reads: tuple = field(default=(), repr=False)
    buying_power_read: OfflineRead | None = field(default=None, repr=False)
    sellable_read: OfflineRead | None = field(default=None, repr=False)
    price_read: OfflineRead | None = field(default=None, repr=False)
    session_read: OfflineRead | None = field(default=None, repr=False)
    holiday_reads: tuple = field(default=(), repr=False)
    history_reads: tuple = field(default=(), repr=False)


@dataclass(frozen=True)
class PipelineStep:
    endpoint: str
    outcome: EvidenceOutcome = field(repr=False)

    @property
    def parsed(self):
        return self.outcome.valid


@dataclass(frozen=True)
class OfflineHistoryObservation:
    """Native records only. No core request association or terminal-state proof."""
    history: object = field(repr=False)
    window: CaptureWindow

    @property
    def provenance_authenticated(self):
        return False

    @property
    def live_eligible(self):
        return False


@dataclass(frozen=True)
class KisPipelineReport:
    steps: tuple[PipelineStep, ...]
    inconsistencies: tuple[str, ...]

    @property
    def shape_complete(self):
        return len(self.steps) == 7 and all(step.parsed for step in self.steps)

    @property
    def missing_facts(self):
        return MISSING_FACTS

    @property
    def policy_ready(self):
        # Successful parsing or a caller-constructed report is never authority.
        return False

    @property
    def live_capable(self):
        return False

    def summary(self):
        """Fixed codes only. No recipes, body, account numbers or economics."""
        return {'mode': 'OFFLINE_DIAGNOSTIC_ONLY', 'policy_ready': False,
                'live_capable': False, 'shape_complete': self.shape_complete,
                'steps': [{'endpoint': step.endpoint, 'parsed': step.parsed,
                           'reason': step.outcome.reason} for step in self.steps],
                'inconsistencies': list(self.inconsistencies),
                'missing_facts': list(self.missing_facts)}

    def policy_context(self):
        raise Blocked(POLICY_BLOCK)


class _SuppliedHistory:
    """Strict in-memory transcript consumer, never an injected transport callback."""
    def __init__(self, reads, now, day, max_age_seconds, max_pages):
        require(type(reads) is tuple and 1 <= len(reads) <= max_pages,
                'KIS_HISTORY_INPUT_INVALID')
        self.reads = reads
        self.index = 0
        previous = None
        for read in reads:
            require(type(read) is OfflineRead and type(read.recipe) is RequestRecipe
                    and type(read.response) is KisResponse, 'KIS_HISTORY_INPUT_INVALID')
            start = aware(read.started_at); end = aware(read.received_at)
            require(start <= end <= now and (now-start).total_seconds() <= max_age_seconds,
                    'KIS_HISTORY_CAPTURE_INVALID')
            require(previous is None or previous <= start, 'KIS_HISTORY_CAPTURE_INVALID')
            require(all(t.astimezone(KST).date().isoformat() == day for t in (start, end, now)),
                    'KIS_HISTORY_CAPTURE_INVALID')
            previous = end

    def read_history(self, recipe):
        require(self.index < len(self.reads), 'KIS_HISTORY_INPUT_INVALID')
        read = self.reads[self.index]
        require(read.recipe == recipe and recipe.method == 'GET', 'KIS_HISTORY_INPUT_INVALID')
        self.index += 1
        return read.response


def _history(inputs, now, max_age_seconds, max_pages, max_rows):
    try:
        transport = _SuppliedHistory(inputs.history_reads, now, inputs.trading_day,
                                     max_age_seconds, max_pages)
        value = KisReadOnlyAdapter(transport, lambda: now, max_pages=max_pages,
                                  max_rows=max_rows).history(
            HistoryQuery(inputs.binding, inputs.trading_day, inputs.venue))
        # Never expose partial history as a coherent account input. The older
        # parser may retain partial records, so the composition boundary drops it.
        require(value.complete is True and transport.index == len(transport.reads),
                'KIS_HISTORY_INPUT_INVALID')
        window = CaptureWindow(transport.reads[0].started_at, transport.reads[-1].received_at)
        return EvidenceOutcome(OfflineHistoryObservation(value, window), 'KIS_OFFLINE_OBSERVATION_ONLY')
    except Exception:
        # An arbitrary Mapping/transport exception can contain private values.
        return EvidenceOutcome(None, 'KIS_HISTORY_INPUT_INVALID')


def inspect_kis_inputs(inputs, *, now: datetime, max_age_seconds: int,
                       max_pages=20, max_rows=2000) -> KisPipelineReport:
    """Parse all seven offline streams and compare reported facts conservatively.

    These are observations, not policy inputs. Even a coherent transcript leaves
    all missing facts open; there is deliberately no override/verified flag.
    """
    require(type(inputs) is KisOfflineInputSet and type(inputs.binding) is AccountBinding,
            'KIS_PIPELINE_INPUT_REQUIRED')
    inputs.binding.validate()
    adapter = KisOfflineEvidenceAdapter(now, max_age_seconds=max_age_seconds,
                                        max_pages=max_pages, max_rows=max_rows)
    # A single scope builds all expected recipes. A response from another account,
    # environment, venue, day or ticker cannot be mixed in silently. Mode-01
    # buying power does not establish an exact limit-price capacity binding.
    balance = BalanceQuery(inputs.binding, inputs.trading_day)
    power = BuyingPowerQuery(inputs.binding, inputs.trading_day, inputs.ticker, inputs.inquiry_price_krw)
    sellable = SellableQuery(inputs.binding, inputs.trading_day, inputs.ticker)
    market = MarketQuery(inputs.binding.environment, inputs.trading_day, inputs.ticker, inputs.venue)
    holiday = HolidayQuery(inputs.trading_day)
    steps = (
        PipelineStep('balance', adapter.balance(balance, inputs.balance_reads)),
        PipelineStep('buying_power', adapter.buying_power(power, inputs.buying_power_read)),
        PipelineStep('sellable', adapter.sellable(sellable, inputs.sellable_read)),
        PipelineStep('price', adapter.price(market, inputs.price_read)),
        PipelineStep('session', adapter.session(market, inputs.session_read)),
        PipelineStep('holidays', adapter.holidays(holiday, inputs.holiday_reads)),
        PipelineStep('history', _history(inputs, adapter.now, max_age_seconds, max_pages, max_rows)),
    )
    values = {step.endpoint: step.outcome.value for step in steps}
    issues = []
    b = values['balance']; s = values['sellable']; p = values['price']; h = values['holidays']
    if b is not None:
        if sum(row.purchase_amount_krw for row in b.positions) != b.totals.purchase_amount_sum_krw:
            issues.append('KIS_REPORTED_PURCHASE_TOTAL_MISMATCH')
        if sum(row.market_value_krw for row in b.positions) != b.totals.valuation_amount_sum_krw:
            issues.append('KIS_REPORTED_VALUATION_TOTAL_MISMATCH')
        if s is not None:
            position = next((row for row in b.positions if row.ticker == inputs.ticker), None)
            held = position.held_quantity if position is not None else 0
            if held != s.balance_quantity:
                issues.append('KIS_CROSS_ENDPOINT_HOLDING_MISMATCH')
    if p is not None:
        if p.current_price_krw <= 0:
            issues.append('KIS_REPORTED_PRICE_NONPOSITIVE')
        if p.temporary_stop:
            issues.append('KIS_REPORTED_INSTRUMENT_STOPPED')
    if h is not None and not (h.target.business_day and h.target.trading_day and h.target.opening_day):
        issues.append('KIS_CALENDAR_REPORTED_CLOSED')
    return KisPipelineReport(steps, tuple(issues))

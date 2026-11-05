"""Pure, offline KIS account/market response normalization.

This module has no transport, credentials, clock lookup, database or broker
registration. Supplied request/response transcripts are UNAUTHENTICATED data.
Parsing one never creates policy AccountEvidence, MarketEvidence or SessionPermit.
See KIS_EVIDENCE.md for the pinned official sources and deliberately missing facts.
"""
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Generic, TypeVar

from .domain import Blocked, KST, aware, integer, require
from .kis_adapter import AccountBinding, KisResponse, RequestRecipe, _day, _fields, _number, _text

BALANCE_PATH = '/uapi/domestic-stock/v1/trading/inquire-balance'
BUYING_POWER_PATH = '/uapi/domestic-stock/v1/trading/inquire-psbl-order'
SELLABLE_PATH = '/uapi/domestic-stock/v1/trading/inquire-psbl-sell'
PRICE_PATH = '/uapi/domestic-stock/v1/quotations/inquire-price'
BOOK_PATH = '/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn'
HOLIDAY_PATH = '/uapi/domestic-stock/v1/quotations/chk-holiday'
SOURCE_COMMIT = 'd8c7f7936793f85dc1e9c364ba5dc66832353f8c'
PORTAL_PROFILE = 'KIS_PUBLIC_PORTAL_2026_11_05'
MAX_MONEY = 9_999_999_999_999_999
T = TypeVar('T')
# Only fixed codes may leave the parser, including if a caller-supplied Mapping
# implementation raises Blocked with private data instead of behaving like JSON.
_ERROR_CODES = frozenset('''
AWARE_CLOCK_REQUIRED KIS_INVALID_ACCOUNT KIS_INVALID_ACCOUNT_NUMBER
KIS_INVALID_PRODUCT_CODE KIS_INVALID_NUMBER KIS_NUMBER_OUT_OF_RANGE
KIS_INVALID_OBJECT KIS_AMBIGUOUS_FIELD
KIS_BALANCE_DUPLICATE_TICKER KIS_BALANCE_PAGE_SIZE_INVALID
KIS_BALANCE_TOTALS_CHANGED KIS_BALANCE_TOTALS_MISSING_OR_AMBIGUOUS
KIS_BUYING_POWER_MODE_UNVERIFIED KIS_CAPTURE_DAY_MISMATCH
KIS_CAPTURE_ORDER_INVALID KIS_CAPTURE_STALE_OR_INVALID
KIS_CONTINUATION_UNVERIFIED KIS_CURSOR_LOOP KIS_EVIDENCE_API_ERROR
KIS_EVIDENCE_HTTP_ERROR KIS_EVIDENCE_REQUEST_MISMATCH
KIS_HOLIDAY_DUPLICATE_DAY KIS_HOLIDAY_ROWS_MISSING KIS_HOLIDAY_TARGET_MISSING
KIS_INVALID_CURSOR KIS_INVALID_DAY KIS_INVALID_DECIMAL_PRICE
KIS_INVALID_ENVIRONMENT KIS_INVALID_FLAG KIS_INVALID_PRICE KIS_INVALID_QUERY
KIS_INVALID_SIGNED_MONEY KIS_INVALID_TICKER KIS_MARKET_VENUE_UNVERIFIED
KIS_OFFLINE_READ_REQUIRED KIS_PAGES_AFTER_TERMINAL KIS_PAGES_TRUNCATED
KIS_PAGE_LIMIT_OR_EMPTY KIS_POSITION_QUANTITY_INCONSISTENT KIS_ROW_LIMIT
KIS_SELLABLE_CONTINUATION_UNSUPPORTED KIS_SELLABLE_DEMO_UNVERIFIED
KIS_SELLABLE_QUANTITY_INCONSISTENT KIS_SELLABLE_ROWS_AMBIGUOUS
KIS_SESSION_FIELD_INVALID KIS_SESSION_FIELD_MISSING_OR_AMBIGUOUS
KIS_STATUS_CODE_MISSING KIS_TICKER_MISMATCH KIS_UNEXPECTED_CONTINUATION
KIS_WEEKDAY_CODE_UNVERIFIED
KIS_MARKET_INQUIRY_PRICE_MUST_BE_ABSENT KIS_PORTAL_CONTINUATION_UNSUPPORTED
KIS_SELLABLE_SCHEMA_CONFLICT KIS_HOLIDAY_SCHEMA_CONFLICT
'''.split())


def _signed_money(value):
    _text(value, r'-?[0-9]{1,16}(?:\.0{1,8})?', 'KIS_INVALID_SIGNED_MONEY')
    return int(value.split('.')[0])


def _decimal_price(value):
    _text(value, r'[0-9]{1,16}(?:\.[0-9]{1,8})?', 'KIS_INVALID_DECIMAL_PRICE')
    return Decimal(value)


def _flag(value):
    require(value in ('Y', 'N'), 'KIS_INVALID_FLAG')
    return value == 'Y'


def _ticker(value):
    return _text(value, r'[0-9]{6}', 'KIS_INVALID_TICKER')


def _wire_day(value):
    _text(value, r'[0-9]{8}', 'KIS_INVALID_DAY')
    return _day(f'{value[:4]}-{value[4:6]}-{value[6:]}')


def _cursor(cursor):
    require(type(cursor) is tuple and len(cursor) == 2, 'KIS_INVALID_CURSOR')
    for value in cursor:
        _text(value, r'[ -~]{0,100}', 'KIS_INVALID_CURSOR')
    require(cursor == ('', '') or any(value.strip() for value in cursor), 'KIS_INVALID_CURSOR')
    return cursor


def _recipe(path, tr_id, parameters, cursor=('', '')):
    _cursor(cursor)
    return RequestRecipe('GET', path, tr_id, tuple(parameters.items()),
                         '' if cursor == ('', '') else 'N')


@dataclass(frozen=True)
class BalanceQuery:
    binding: AccountBinding
    trading_day_context: str

    def validate(self):
        self.binding.validate(); _day(self.trading_day_context)
        return self


def prepare_balance_query(query: BalanceQuery, *, cursor=('', '')) -> RequestRecipe:
    query.validate(); _cursor(cursor)
    return _recipe(BALANCE_PATH,
                   'TTTC8434R' if query.binding.environment == 'real' else 'VTTC8434R', {
        'CANO': query.binding.cano, 'ACNT_PRDT_CD': query.binding.product_code,
        'AFHR_FLPR_YN': 'N', 'OFL_YN': '', 'INQR_DVSN': '02', 'UNPR_DVSN': '01',
        'FUND_STTL_ICLD_YN': 'N', 'FNCG_AMT_AUTO_RDPT_YN': 'N', 'PRCS_DVSN': '00',
        'CTX_AREA_FK100': cursor[0], 'CTX_AREA_NK100': cursor[1],
    }, cursor)


@dataclass(frozen=True)
class BuyingPowerQuery:
    binding: AccountBinding
    trading_day_context: str
    ticker: str
    # Market-mode inquiry has no price on the wire. A positive local order price
    # cannot be bound to this response or silently serialized into ORD_UNPR.
    price_krw: None = None
    # This inquiry mode is not an instruction to place a market order. KIS warns
    # that 00 does not reflect the stock's margin rate when calculating quantity.
    inquiry_order_code: str = '01'

    def validate(self):
        self.binding.validate(); _day(self.trading_day_context); _ticker(self.ticker)
        require(self.price_krw is None, 'KIS_MARKET_INQUIRY_PRICE_MUST_BE_ABSENT')
        require(self.inquiry_order_code == '01', 'KIS_BUYING_POWER_MODE_UNVERIFIED')
        return self


def prepare_buying_power_query(query: BuyingPowerQuery) -> RequestRecipe:
    query.validate()
    return _recipe(BUYING_POWER_PATH,
                   'TTTC8908R' if query.binding.environment == 'real' else 'VTTC8908R', {
        'CANO': query.binding.cano, 'ACNT_PRDT_CD': query.binding.product_code,
        'PDNO': query.ticker, 'ORD_UNPR': '',
        'ORD_DVSN': query.inquiry_order_code, 'CMA_EVLU_AMT_ICLD_YN': 'N', 'OVRS_ICLD_YN': 'N',
    })


@dataclass(frozen=True)
class SellableQuery:
    binding: AccountBinding
    trading_day_context: str
    ticker: str

    def validate(self):
        self.binding.validate(); _day(self.trading_day_context); _ticker(self.ticker)
        # Official sample supplies only TTTC8408R; do not invent a demo TR ID.
        require(self.binding.environment == 'real', 'KIS_SELLABLE_DEMO_UNVERIFIED')
        return self


def prepare_sellable_query(query: SellableQuery) -> RequestRecipe:
    query.validate()
    return _recipe(SELLABLE_PATH, 'TTTC8408R', {
        'CANO': query.binding.cano, 'ACNT_PRDT_CD': query.binding.product_code,
        'PDNO': query.ticker,
    })


@dataclass(frozen=True)
class MarketQuery:
    environment: str
    trading_day_context: str
    ticker: str
    requested_venue: str

    def validate(self):
        require(self.environment in ('real', 'demo'), 'KIS_INVALID_ENVIRONMENT')
        _day(self.trading_day_context); _ticker(self.ticker)
        require(self.requested_venue in ('KRX', 'NXT'), 'KIS_MARKET_VENUE_UNVERIFIED')
        return self


def prepare_market_query(query: MarketQuery, *, book=False) -> RequestRecipe:
    query.validate(); require(type(book) is bool, 'KIS_INVALID_QUERY')
    return _recipe(BOOK_PATH if book else PRICE_PATH,
                   'FHKST01010200' if book else 'FHKST01010100', {
        'FID_COND_MRKT_DIV_CODE': {'KRX': 'J', 'NXT': 'NX'}[query.requested_venue],
        'FID_INPUT_ISCD': query.ticker,
    })


@dataclass(frozen=True)
class HolidayQuery:
    target_day: str

    def validate(self):
        _day(self.target_day)
        return self


def prepare_holiday_query(query: HolidayQuery, *, cursor=('', '')) -> RequestRecipe:
    query.validate(); _cursor(cursor)
    # Portal contract: both 20-character-capacity cursor fields must be blank;
    # tr_cont is unsupported. Recursive GitHub sample behavior is not adopted.
    require(cursor == ('', ''), 'KIS_PORTAL_CONTINUATION_UNSUPPORTED')
    return _recipe(HOLIDAY_PATH, 'CTCA0903R', {
        'BASS_DT': query.target_day.replace('-', ''),
        'CTX_AREA_FK': cursor[0], 'CTX_AREA_NK': cursor[1],
    }, cursor)


@dataclass(frozen=True)
class OfflineRead:
    """Supplied transcript, not proof a request was sent or a response is genuine."""
    recipe: RequestRecipe
    response: KisResponse = field(repr=False)
    started_at: datetime
    received_at: datetime


@dataclass(frozen=True)
class CaptureWindow:
    started_at: datetime
    received_at: datetime


class OfflineObservation:
    @property
    def live_eligible(self):
        return False

    @property
    def provenance_authenticated(self):
        return False


@dataclass(frozen=True)
class EvidenceOutcome(Generic[T]):
    value: T | None
    reason: str

    @property
    def valid(self):
        """The supported offline shape parsed; NOT financial/session readiness."""
        return self.value is not None


@dataclass(frozen=True)
class ReportedPosition:
    ticker: str
    held_quantity: int
    balance_orderable_quantity: int
    average_price: Decimal
    purchase_amount_krw: int
    market_value_krw: int
    valuation_profit_loss_krw: int
    loan_amount_krw: int


@dataclass(frozen=True)
class ReportedBalanceTotals:
    deposit_total_krw: int
    next_day_settlement_krw: int
    provisional_settlement_krw: int
    total_valuation_krw: int
    net_assets_krw: int
    purchase_amount_sum_krw: int
    valuation_amount_sum_krw: int
    valuation_profit_loss_sum_krw: int


@dataclass(frozen=True)
class BalanceObservation(OfflineObservation):
    account: str
    environment: str
    trading_day_context: str
    window: CaptureWindow
    positions: tuple[ReportedPosition, ...]
    totals: ReportedBalanceTotals
    pages: int

    @property
    def traversal_complete(self):
        return True

    @property
    def snapshot_consistent(self):
        # Equal summaries do not prove snapshot isolation or a fill watermark.
        return False


@dataclass(frozen=True)
class BuyingPowerObservation(OfflineObservation):
    account: str
    environment: str
    trading_day_context: str
    ticker: str
    inquiry_price_krw: None
    inquiry_order_code: str
    window: CaptureWindow
    orderable_cash_krw: int
    no_receivables_buy_amount_krw: int
    no_receivables_buy_quantity: int
    maximum_buy_amount_krw: int
    maximum_buy_quantity: int
    quantity_calculation_price: Decimal

    @property
    def contract_profile(self):
        return PORTAL_PROFILE


@dataclass(frozen=True)
class SellableObservation(OfflineObservation):
    account: str
    environment: str
    trading_day_context: str
    ticker: str
    window: CaptureWindow
    balance_quantity: int
    reported_sellable_quantity: int
    response_continuation: str

    @property
    def traversal_complete(self):
        # Portal is nonpaginated, but output1 vs output remains contradictory.
        return False

    @property
    def contract_profile(self):
        return PORTAL_PROFILE

    @property
    def unresolved_contracts(self):
        return ('SELLABLE_OUTPUT1_VS_OUTPUT', 'PORTAL_VS_SAMPLE_CONTINUATION')


@dataclass(frozen=True)
class PriceObservation(OfflineObservation):
    environment: str
    trading_day_context: str
    ticker: str
    requested_venue: str
    window: CaptureWindow
    current_price_krw: int
    temporary_stop: bool
    instrument_status_code: str

    @property
    def executable(self):
        return False

    @property
    def exchange_timestamp(self):
        return None


@dataclass(frozen=True)
class ReportedSessionField:
    object_path: str
    name: str
    raw_value: str


@dataclass(frozen=True)
class SessionObservation(OfflineObservation):
    environment: str
    trading_day_context: str
    ticker: str
    requested_venue: str
    window: CaptureWindow
    fields: tuple[ReportedSessionField, ...]

    @property
    def session_permitted(self):
        return False


@dataclass(frozen=True)
class ReportedCalendarDay:
    day: str
    business_day: bool
    trading_day: bool
    opening_day: bool
    settlement_day: bool
    weekday_code: str


@dataclass(frozen=True)
class HolidayObservation(OfflineObservation):
    target_day: str
    window: CaptureWindow
    days: tuple[ReportedCalendarDay, ...]
    response_continuation: str

    @property
    def target(self):
        return next(day for day in self.days if day.day == self.target_day)

    @property
    def traversal_complete(self):
        # Portal is nonpaginated; sample array/horizon behavior remains unproven.
        return False

    @property
    def contract_profile(self):
        return PORTAL_PROFILE

    @property
    def unresolved_contracts(self):
        return ('HOLIDAY_OBJECT_VS_ARRAY', 'PORTAL_VS_SAMPLE_CONTINUATION', 'CALENDAR_HORIZON')

    @property
    def session_permitted(self):
        return False


def _position(raw):
    row = _fields(raw)
    held = _number(row.get('hldg_qty')); orderable = _number(row.get('ord_psbl_qty'))
    require(orderable <= held, 'KIS_POSITION_QUANTITY_INCONSISTENT')
    # Zero holdings can legitimately remain after a same-day full sale.
    return ReportedPosition(_ticker(row.get('pdno')), held, orderable,
        _decimal_price(row.get('pchs_avg_pric')), _number(row.get('pchs_amt'), MAX_MONEY),
        _number(row.get('evlu_amt'), MAX_MONEY), _signed_money(row.get('evlu_pfls_amt')),
        _number(row.get('loan_amt'), MAX_MONEY))


def _totals(raw):
    require(type(raw) is list and len(raw) == 1, 'KIS_BALANCE_TOTALS_MISSING_OR_AMBIGUOUS')
    row = _fields(raw[0])
    # Retain signed reported values, including negative deposits. They are NOT
    # converted into settled/available cash, equity policy or today's loss.
    return ReportedBalanceTotals(*(_signed_money(row.get(name)) for name in (
        'dnca_tot_amt', 'nxdy_excc_amt', 'prvs_rcdl_excc_amt', 'tot_evlu_amt',
        'nass_amt', 'pchs_amt_smtl_amt', 'evlu_amt_smtl_amt', 'evlu_pfls_smtl_amt')))


def _calendar_day(raw):
    row = _fields(raw)
    return ReportedCalendarDay(_wire_day(row.get('bass_dt')), _flag(row.get('bzdy_yn')),
        _flag(row.get('tr_day_yn')), _flag(row.get('opnd_yn')), _flag(row.get('sttl_day_yn')),
        _text(row.get('wday_dvsn_cd'), r'0[1-7]', 'KIS_WEEKDAY_CODE_UNVERIFIED'))


def _run(parser):
    try:
        return EvidenceOutcome(parser(), 'KIS_OFFLINE_OBSERVATION_ONLY')
    except Blocked as exc:
        reason = str(exc)
        return EvidenceOutcome(None, reason if reason in _ERROR_CODES else 'KIS_OFFLINE_PAYLOAD_INVALID')
    except Exception:
        return EvidenceOutcome(None, 'KIS_OFFLINE_PAYLOAD_INVALID')


class KisOfflineEvidenceAdapter:
    """Bounded pure transcript validator. There is deliberately no read/send API.

    max_age_seconds bounds caller-supplied capture timestamps, not broker as-of
    freshness. The value must be explicit; no production policy is assumed.
    """
    def __init__(self, now: datetime, *, max_age_seconds: int, max_pages=20, max_rows=2000):
        self.now = aware(now)
        require(integer(max_age_seconds, 1) and max_age_seconds <= 86400, 'KIS_INVALID_AGE_LIMIT')
        require(integer(max_pages, 1) and max_pages <= 100, 'KIS_INVALID_PAGE_LIMIT')
        require(integer(max_rows, 1) and max_rows <= 10000, 'KIS_INVALID_ROW_LIMIT')
        self.max_age_seconds = max_age_seconds; self.max_pages = max_pages; self.max_rows = max_rows

    def _read(self, read, expected, day=None, *, previous=None):
        require(type(read) is OfflineRead and type(read.recipe) is RequestRecipe,
                'KIS_OFFLINE_READ_REQUIRED')
        require(read.recipe == expected and read.recipe.method == 'GET', 'KIS_EVIDENCE_REQUEST_MISMATCH')
        start = aware(read.started_at); end = aware(read.received_at)
        require(start <= end <= self.now and
                (self.now - start).total_seconds() <= self.max_age_seconds, 'KIS_CAPTURE_STALE_OR_INVALID')
        require(previous is None or start >= previous, 'KIS_CAPTURE_ORDER_INVALID')
        if day is not None:
            require(all(stamp.astimezone(KST).date().isoformat() == day
                        for stamp in (start, end, self.now)), 'KIS_CAPTURE_DAY_MISMATCH')
        require(type(read.response) is KisResponse and type(read.response.status_code) is int
                and read.response.status_code == 200, 'KIS_EVIDENCE_HTTP_ERROR')
        body = _fields(read.response.body); headers = _fields(read.response.headers)
        require(body.get('rt_cd') == '0', 'KIS_EVIDENCE_API_ERROR')
        return body, headers

    def _single(self, read, expected, day=None):
        body, headers = self._read(read, expected, day)
        # These source-described single-object endpoints do not paginate. If a
        # server starts requesting continuation, this supported shape is obsolete.
        require(headers.get('tr_cont', '') in ('', 'D', 'E'), 'KIS_UNEXPECTED_CONTINUATION')
        return body, CaptureWindow(read.started_at, read.received_at)

    def _portal_single(self, read, expected, day=None):
        body, headers = self._read(read, expected, day)
        require(headers.get('tr_cont', '') == '', 'KIS_PORTAL_CONTINUATION_UNSUPPORTED')
        return body, CaptureWindow(read.started_at, read.received_at)

    def _pages(self, reads, recipe_factory, *, day=None, cursor_suffix='100'):
        require(type(reads) is tuple and 1 <= len(reads) <= self.max_pages, 'KIS_PAGE_LIMIT_OR_EMPTY')
        pages = []; cursor = ('', ''); seen = {cursor}; previous = None
        for index, read in enumerate(reads):
            body, headers = self._read(read, recipe_factory(cursor=cursor), day, previous=previous)
            previous = read.received_at
            marker = headers.get('tr_cont')
            require(marker in ('M', 'F', 'D', 'E'), 'KIS_CONTINUATION_UNVERIFIED')
            pages.append(body)
            if marker in ('D', 'E'):
                require(index == len(reads)-1, 'KIS_PAGES_AFTER_TERMINAL')
                return tuple(pages), CaptureWindow(reads[0].started_at, read.received_at), marker
            require(index < len(reads)-1, 'KIS_PAGES_TRUNCATED')
            cursor = (body.get('ctx_area_fk'+cursor_suffix), body.get('ctx_area_nk'+cursor_suffix))
            _cursor(cursor)
            key = tuple(value.strip() for value in cursor)
            require(key not in seen, 'KIS_CURSOR_LOOP')
            seen.add(key)
        raise Blocked('KIS_PAGES_TRUNCATED')

    def balance(self, query: BalanceQuery, reads: tuple[OfflineRead, ...]) -> EvidenceOutcome[BalanceObservation]:
        def parse():
            query.validate()
            pages, window, _ = self._pages(reads, lambda **kw: prepare_balance_query(query, **kw),
                                            day=query.trading_day_context)
            positions = []; totals = None; seen = set()
            for body in pages:
                rows = body.get('output1')
                require(type(rows) is list and len(rows) <= (50 if query.binding.environment == 'real' else 20),
                        'KIS_BALANCE_PAGE_SIZE_INVALID')
                require(len(positions)+len(rows) <= self.max_rows, 'KIS_ROW_LIMIT')
                current = _totals(body.get('output2'))
                require(totals is None or current == totals, 'KIS_BALANCE_TOTALS_CHANGED')
                totals = current
                for raw in rows:
                    position = _position(raw)
                    require(position.ticker not in seen, 'KIS_BALANCE_DUPLICATE_TICKER')
                    seen.add(position.ticker); positions.append(position)
            return BalanceObservation(query.binding.account, query.binding.environment,
                query.trading_day_context, window, tuple(positions), totals, len(pages))
        return _run(parse)

    def buying_power(self, query: BuyingPowerQuery, read: OfflineRead) -> EvidenceOutcome[BuyingPowerObservation]:
        def parse():
            body, window = self._portal_single(read, prepare_buying_power_query(query), query.trading_day_context)
            row = _fields(body.get('output'))
            return BuyingPowerObservation(query.binding.account, query.binding.environment,
                query.trading_day_context, query.ticker, query.price_krw, query.inquiry_order_code, window,
                _number(row.get('ord_psbl_cash'), MAX_MONEY), _number(row.get('nrcvb_buy_amt'), MAX_MONEY),
                _number(row.get('nrcvb_buy_qty')), _number(row.get('max_buy_amt'), MAX_MONEY),
                _number(row.get('max_buy_qty')), _decimal_price(row.get('psbl_qty_calc_unpr')))
        return _run(parse)

    def sellable(self, query: SellableQuery, read: OfflineRead) -> EvidenceOutcome[SellableObservation]:
        def parse():
            body, window = self._portal_single(read, prepare_sellable_query(query), query.trading_day_context)
            # Explicit portal-schema fixture profile, not automatic aliasing to
            # the contradictory prose/GitHub output key or array response.
            require('output' not in body and type(body.get('output1')) is dict, 'KIS_SELLABLE_SCHEMA_CONFLICT')
            raw = body['output1']
            row = _fields(raw)
            require(_ticker(row.get('pdno')) == query.ticker, 'KIS_TICKER_MISMATCH')
            balance = _number(row.get('cblc_qty')); sellable = _number(row.get('ord_psbl_qty'))
            require(sellable <= balance, 'KIS_SELLABLE_QUANTITY_INCONSISTENT')
            return SellableObservation(query.binding.account, query.binding.environment,
                query.trading_day_context, query.ticker, window, balance, sellable, '')
        return _run(parse)

    def price(self, query: MarketQuery, read: OfflineRead) -> EvidenceOutcome[PriceObservation]:
        def parse():
            body, window = self._single(read, prepare_market_query(query), query.trading_day_context)
            row = _fields(body.get('output'))
            require(_ticker(row.get('stck_shrn_iscd')) == query.ticker, 'KIS_TICKER_MISMATCH')
            return PriceObservation(query.environment, query.trading_day_context, query.ticker,
                query.requested_venue, window, _number(row.get('stck_prpr'), 1_000_000_000_000),
                _flag(row.get('temp_stop_yn')),
                _text(row.get('iscd_stat_cls_code'), r'[A-Za-z0-9_-]{1,32}', 'KIS_STATUS_CODE_MISSING'))
        return _run(parse)

    def session(self, query: MarketQuery, read: OfflineRead) -> EvidenceOutcome[SessionObservation]:
        def parse():
            body, window = self._single(read, prepare_market_query(query, book=True), query.trading_day_context)
            found = []; symbols = []
            # Source uses a merged display map for both objects. Preserve actual
            # paths instead of inventing object membership or OPEN code values.
            for path in ('output1', 'output2'):
                row = _fields(body.get(path))
                if 'stck_shrn_iscd' in row:
                    symbols.append(_ticker(row['stck_shrn_iscd']))
                for name in ('new_mkop_cls_code', 'antc_mkop_cls_code', 'aspr_acpt_hour'):
                    if name in row:
                        value = _text(row[name], r'[A-Za-z0-9_:.+-]{1,32}', 'KIS_SESSION_FIELD_INVALID')
                        found.append(ReportedSessionField(path, name, value))
            require(symbols and all(symbol == query.ticker for symbol in symbols), 'KIS_TICKER_MISMATCH')
            for name in ('new_mkop_cls_code', 'aspr_acpt_hour'):
                require(sum(item.name == name for item in found) == 1, 'KIS_SESSION_FIELD_MISSING_OR_AMBIGUOUS')
            require(sum(item.name == 'antc_mkop_cls_code' for item in found) <= 1,
                    'KIS_SESSION_FIELD_MISSING_OR_AMBIGUOUS')
            return SessionObservation(query.environment, query.trading_day_context, query.ticker,
                                      query.requested_venue, window, tuple(found))
        return _run(parse)

    def holidays(self, query: HolidayQuery, reads: tuple[OfflineRead, ...]) -> EvidenceOutcome[HolidayObservation]:
        def parse():
            query.validate()
            require(type(reads) is tuple and len(reads) == 1, 'KIS_PORTAL_CONTINUATION_UNSUPPORTED')
            body, window = self._portal_single(reads[0], prepare_holiday_query(query))
            require('output1' not in body and type(body.get('output')) is dict, 'KIS_HOLIDAY_SCHEMA_CONFLICT')
            # Unexpected nonblank response cursors contradict this supported
            # nonpaginated profile, even if its header was blank.
            require(all(body.get(key, '') == '' for key in ('ctx_area_fk', 'ctx_area_nk')),
                    'KIS_PORTAL_CONTINUATION_UNSUPPORTED')
            day = _calendar_day(body['output'])
            require(day.day == query.target_day, 'KIS_HOLIDAY_TARGET_MISSING')
            return HolidayObservation(query.target_day, window, (day,), '')
        return _run(parse)

    def policy_evidence(self, *args, **kwargs):
        raise Blocked('KIS_LIVE_EVIDENCE_NOT_ESTABLISHED')

    def submit(self, *args, **kwargs):
        raise Blocked('KIS_LIVE_SUBMISSION_NOT_IMPLEMENTED')

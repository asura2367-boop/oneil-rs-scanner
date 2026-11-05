"""Network-free KIS boundary: offline request recipes and strict read normalization.

No credentials, token handling, HTTP client, live submission, cancellation, or
worker registration exists here. Only an injected read-only history transport is
called. Recipes do not authorize transmission. See KIS_ADAPTER.md for the verified
source snapshot, unsupported cases, and the remaining production acceptance gates.
"""
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
import re
from typing import Protocol

from .domain import Blocked, KST, Order, SessionPermit, aware, require

CASH_PATH = '/uapi/domestic-stock/v1/trading/order-cash'
HISTORY_PATH = '/uapi/domestic-stock/v1/trading/inquire-daily-ccld'


def _text(value, pattern, code):
    require(isinstance(value, str) and re.fullmatch(pattern, value, flags=re.ASCII), code)
    return value


def _day(value):
    _text(value, r'[0-9]{4}-[0-9]{2}-[0-9]{2}', 'KIS_INVALID_DAY')
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise Blocked('KIS_INVALID_DAY') from exc
    return value


def _fields(value):
    require(isinstance(value, Mapping), 'KIS_INVALID_OBJECT')
    result = {}
    for key, item in value.items():
        require(isinstance(key, str) and key.lower() not in result, 'KIS_AMBIGUOUS_FIELD')
        result[key.lower()] = item
    return result


def _number(value, maximum=1_000_000_000):
    # KIS returns decimal strings. Reject float, exponent, sign, commas, NaN,
    # rounding, bool, and fractional KRW/share quantities instead of coercing them.
    _text(value, r'[0-9]{1,16}(?:\.0{1,8})?', 'KIS_INVALID_NUMBER')
    result = int(value.split('.')[0])
    require(result <= maximum, 'KIS_NUMBER_OUT_OF_RANGE')
    return result


@dataclass(frozen=True)
class AccountBinding:
    """Trusted account+product fingerprint mapping, never taken from browser JSON."""
    account: str
    environment: str
    cano: str = field(repr=False)
    product_code: str = field(repr=False)

    def validate(self):
        _text(self.account, r'[0-9a-f]{64}', 'KIS_INVALID_ACCOUNT')
        require(self.environment in ('real', 'demo'), 'KIS_INVALID_ENVIRONMENT')
        _text(self.cano, r'[0-9]{8}', 'KIS_INVALID_ACCOUNT_NUMBER')
        _text(self.product_code, r'[0-9]{2}', 'KIS_INVALID_PRODUCT_CODE')
        return self


@dataclass(frozen=True)
class RequestRecipe:
    """Offline data only; deliberately omits host, authentication, and executor.

    Parameters include account identifiers, so repr excludes them. Do not put
    recipes in logs/audit events or treat repr protection as secure serialization.
    """
    method: str
    path: str
    tr_id: str
    parameters: tuple[tuple[str, str], ...] = field(repr=False)
    tr_cont: str = ''

    def wire_parameters(self):
        return dict(self.parameters)  # independent copy, preserving string values


@dataclass(frozen=True)
class PreparedCashOrder:
    recipe: RequestRecipe
    request_id: str
    order: Order
    binding: AccountBinding
    trading_day: str

    @property
    def environment(self):
        return self.binding.environment


def _cash_recipe(binding, order):
    code = '0012U' if order.side == 'BUY' else '0011U'
    params = {
        'CANO': binding.cano, 'ACNT_PRDT_CD': binding.product_code,
        'PDNO': order.ticker, 'ORD_DVSN': '00', 'ORD_QTY': str(order.quantity),
        'ORD_UNPR': str(order.limit_price), 'EXCG_ID_DVSN_CD': 'KRX',
        'SLL_TYPE': '01' if order.side == 'SELL' else '', 'CNDT_PRIC': '',
    }
    return RequestRecipe('POST', CASH_PATH,
                         ('TTTC' if binding.environment == 'real' else 'VTTC') + code,
                         tuple(params.items()))


def prepare_cash_order(binding: AccountBinding, request_id: str, order: Order,
                       permit: SessionPermit, now: datetime) -> PreparedCashOrder:
    """Build a KRX cash LIMIT recipe, without sending or authorizing it.

    NXT/SOR, market, credit, amend/cancel and session-specific orders remain blocked
    until their current official capabilities are reviewed. A permit is evidence
    supplied by a trusted caller, not a market-calendar implementation here.
    """
    binding.validate(); order.validate(); aware(now); permit.validate(order, now)
    _text(request_id, r'[A-Za-z0-9_-]{1,128}', 'KIS_INVALID_REQUEST_ID')
    require(binding.account == order.account, 'KIS_ACCOUNT_MISMATCH')
    require(now < order.expires_at, 'REQUEST_EXPIRED')
    require(order.venue == 'KRX', 'KIS_VENUE_NOT_VERIFIED')
    require(permit.order_code == '00', 'KIS_ORDER_CODE_NOT_VERIFIED')
    recipe = _cash_recipe(binding, order)
    # Local request ID is intentionally not inserted as a fictitious KIS field.
    return PreparedCashOrder(recipe, request_id, order, binding, permit.trading_day)


@dataclass(frozen=True)
class NativeOrderId:
    account: str
    environment: str
    trading_day: str
    venue: str
    branch: str
    number: str

    def validate(self):
        _text(self.account, r'[0-9a-f]{64}', 'KIS_INVALID_ACCOUNT')
        require(self.environment in ('real', 'demo'), 'KIS_INVALID_ENVIRONMENT')
        _day(self.trading_day)
        require(self.venue == 'KRX', 'KIS_VENUE_NOT_VERIFIED')
        _text(self.branch, r'[0-9]{1,10}', 'KIS_INVALID_BRANCH')
        _text(self.number, r'[0-9]{1,10}', 'KIS_INVALID_ORDER_NUMBER')
        require(int(self.number) > 0, 'KIS_INVALID_ORDER_NUMBER')
        return self



@dataclass(frozen=True)
class CashAcknowledgement:
    """Receipt for one exact attempt, NOT an order-history identity or fill.

    KRX_FWDG_ORD_ORGNO is a forwarding organization. Its equality with history's
    ORD_GNO_BRNO is unproven. Keep them separate; do not derive a NativeOrderId.
    The response has a time but no date, so trading_day_context is only the
    attempt's verified KST context, not a date asserted by the response.
    """
    request_id: str
    order_digest: str
    account: str
    environment: str
    trading_day_context: str
    forwarding_organization: str
    order_number: str
    order_time: str


@dataclass(frozen=True)
class SubmissionObservation:
    state: str
    reason: str
    acknowledgement: CashAcknowledgement | None = None


@dataclass(frozen=True)
class KisResponse:
    status_code: int
    body: object = field(repr=False)
    headers: Mapping = field(default_factory=dict, repr=False)


def unknown_submission() -> SubmissionObservation:
    """Timeout/disconnect/crash/invalid response never authorizes retry."""
    return SubmissionObservation('SUBMISSION_UNKNOWN', 'KIS_SUBMISSION_OUTCOME_UNKNOWN')


def normalize_submission(prepared: PreparedCashOrder, response: KisResponse) -> SubmissionObservation:
    """A successful cash-order response acknowledges receipt, never execution.

    No error-code taxonomy is proven here. Even HTTP/API errors conservatively
    remain UNKNOWN rather than guessing that the request could not be accepted.
    """
    try:
        prepared.order.validate(); _day(prepared.trading_day)
        _text(prepared.request_id, r'[A-Za-z0-9_-]{1,128}', 'KIS_INVALID_REQUEST_ID')
        require(prepared.environment in ('real', 'demo'), 'KIS_INVALID_ENVIRONMENT')
        prepared.binding.validate()
        require(prepared.binding.account == prepared.order.account and prepared.order.venue == 'KRX',
                'KIS_PREPARED_CONTENT_MISMATCH')
        require(prepared.recipe == _cash_recipe(prepared.binding, prepared.order),
                'KIS_PREPARED_CONTENT_MISMATCH')
        require(type(response.status_code) is int and response.status_code == 200, 'KIS_HTTP_UNCERTAIN')
        body = _fields(response.body)
        require(body.get('rt_cd') == '0', 'KIS_API_UNCERTAIN')
        output = _fields(body.get('output'))
        forwarding = _text(output.get('krx_fwdg_ord_orgno'), r'[0-9]{1,10}', 'KIS_INVALID_FORWARDING_ORG')
        number = _text(output.get('odno'), r'[0-9]{1,10}', 'KIS_INVALID_ORDER_NUMBER')
        require(int(number) > 0, 'KIS_INVALID_ORDER_NUMBER')
        stamp = _text(output.get('ord_tmd'), r'[0-9]{6}', 'KIS_INVALID_ORDER_TIME')
        require(int(stamp[:2]) < 24 and int(stamp[2:4]) < 60 and int(stamp[4:]) < 60,
                'KIS_INVALID_ORDER_TIME')
        acknowledgement = CashAcknowledgement(
            prepared.request_id, prepared.order.digest, prepared.order.account,
            prepared.environment, prepared.trading_day, forwarding, number, stamp)
        return SubmissionObservation('ACKNOWLEDGED', 'KIS_RECEIPT_NOT_FILL', acknowledgement)
    except (Blocked, AttributeError, TypeError):
        return unknown_submission()


@dataclass(frozen=True)
class HistoryQuery:
    binding: AccountBinding
    trading_day: str
    venue: str = 'KRX'

    def validate(self):
        self.binding.validate(); _day(self.trading_day)
        require(self.venue == 'KRX', 'KIS_VENUE_NOT_VERIFIED')
        return self


def prepare_history_query(query: HistoryQuery, *, cursor=('', '')) -> RequestRecipe:
    query.validate()
    require(isinstance(cursor, tuple) and len(cursor) == 2 and all(
        isinstance(x, str) and re.fullmatch(r'[ -~]{0,100}', x) for x in cursor),
        'KIS_INVALID_CURSOR')
    require(cursor == ('', '') or any(x.strip() for x in cursor), 'KIS_INVALID_CURSOR')
    params = {
        'CANO': query.binding.cano, 'ACNT_PRDT_CD': query.binding.product_code,
        'INQR_STRT_DT': query.trading_day.replace('-', ''),
        'INQR_END_DT': query.trading_day.replace('-', ''),
        'SLL_BUY_DVSN_CD': '00', 'PDNO': '', 'CCLD_DVSN': '00',
        'INQR_DVSN': '01', 'INQR_DVSN_3': '00', 'ORD_GNO_BRNO': '',
        'ODNO': '', 'INQR_DVSN_1': '', 'CTX_AREA_FK100': cursor[0],
        'CTX_AREA_NK100': cursor[1], 'EXCG_ID_DVSN_CD': query.venue,
    }
    return RequestRecipe('GET', HISTORY_PATH,
                         'TTTC0081R' if query.binding.environment == 'real' else 'VTTC0081R',
                         tuple(params.items()), '' if cursor == ('', '') else 'N')


@dataclass(frozen=True)
class NativeHistoryOrder:
    native_id: NativeOrderId
    ticker: str
    side: str
    venue: str
    quantity: int
    limit_price: int
    filled_quantity: int
    remaining_quantity: int
    cancelled_quantity: int
    rejected_quantity: int
    original_order_number: str
    order_code: str
    cancellation_flag: str


def normalize_history_row(raw, query: HistoryQuery) -> NativeHistoryOrder:
    """Strict normalizer of broker-reported fields, with no terminal-state inference.

    Amendment/cancellation semantics and missing venue proof cannot release an
    unresolved request. Reported quantities are preserved independently. No name strings
    or local-clock heuristics are used to infer order status or venue.
    """
    query.validate(); row = _fields(raw)
    day = _text(row.get('ord_dt'), r'[0-9]{8}', 'KIS_INVALID_DAY')
    day = f'{day[:4]}-{day[4:6]}-{day[6:]}'
    require(day == query.trading_day, 'KIS_HISTORY_DAY_MISMATCH')
    native = NativeOrderId(query.binding.account, query.binding.environment, day, query.venue,
                           row.get('ord_gno_brno'), row.get('odno')).validate()
    ticker = _text(row.get('pdno'), r'[0-9]{6}', 'KIS_INVALID_TICKER')
    side = {'01': 'SELL', '02': 'BUY'}.get(row.get('sll_buy_dvsn_cd'))
    require(side is not None, 'KIS_INVALID_SIDE')
    venue = row.get('excg_id_dvsn_cd')
    require(venue == query.venue, 'KIS_HISTORY_VENUE_UNVERIFIED')
    quantity = _number(row.get('ord_qty')); price = _number(row.get('ord_unpr'), 1_000_000_000_000)
    filled = _number(row.get('tot_ccld_qty')); remaining = _number(row.get('rmn_qty'))
    cancelled = _number(row.get('cnc_cfrm_qty')); rejected = _number(row.get('rjct_qty'))
    require(quantity > 0, 'KIS_INVALID_ORDER_QUANTITY')
    require(row.get('cncl_yn') in ('Y', 'N'), 'KIS_CANCEL_FLAG_UNVERIFIED')
    original = row.get('orgn_odno')
    require(isinstance(original, str) and (original == '' or re.fullmatch(r'[0-9]{1,10}', original, flags=re.ASCII)),
            'KIS_ORIGINAL_ORDER_UNVERIFIED')
    order_code = _text(row.get('ord_dvsn_cd'), r'[0-9]{2}', 'KIS_INVALID_ORDER_CODE')
    # Preserve quantities independently. The official display mapping is NOT a
    # proof of a conservation equation across partial cancels/amendments. These
    # observations cannot be promoted into core terminal states without that proof.
    return NativeHistoryOrder(native, ticker, side, venue, quantity, price, filled,
                              remaining, cancelled, rejected, original, order_code, row['cncl_yn'])


@dataclass(frozen=True)
class NativeHistory:
    account: str
    environment: str
    trading_day: str
    venue: str
    observed_at: datetime
    complete: bool
    orders: tuple[NativeHistoryOrder, ...]
    reason: str


class HistoryReadTransport(Protocol):
    """Future transport supplies GET responses only. There is no write method."""
    def read_history(self, recipe: RequestRecipe) -> KisResponse: ...


class KisReadOnlyAdapter:
    """Bounded pagination over an injected transport, with no retry policy.

    A complete traversal is not proof the broker has exposed every just-sent order.
    Negative evidence never settles an UNKNOWN. Rate limiting, trusted response
    provenance, token refresh and broker-side freshness are future requirements.
    """
    def __init__(self, transport: HistoryReadTransport, now, *, max_pages=20, max_rows=2000):
        require(type(max_pages) is int and 1 <= max_pages <= 100, 'KIS_INVALID_PAGE_LIMIT')
        require(type(max_rows) is int and 1 <= max_rows <= 10_000, 'KIS_INVALID_ROW_LIMIT')
        self.transport = transport; self.now = now
        self.max_pages = max_pages; self.max_rows = max_rows

    def history(self, query: HistoryQuery) -> NativeHistory:
        query.validate(); started = aware(self.now())
        # This recipe deliberately uses the <=3-month endpoint only. Older queries
        # require a separately verified historical endpoint, never a silent switch.
        age = (started.astimezone(KST).date() - date.fromisoformat(query.trading_day)).days
        require(0 <= age <= 89, 'KIS_HISTORY_DAY_OUT_OF_RANGE')
        orders = []; cursor = ('', ''); seen_cursors = {cursor}; seen_ids = set()
        def result(complete, reason):
            return NativeHistory(query.binding.account, query.binding.environment,
                                 query.trading_day, query.venue, started, complete, tuple(orders), reason)
        for _ in range(self.max_pages):
            try:
                recipe = prepare_history_query(query, cursor=cursor)
                try:
                    response = self.transport.read_history(recipe)
                except Exception:
                    # The transport may itself raise Blocked with PII/credentials.
                    # Never confuse its error text with our fixed validation codes.
                    return result(False, 'KIS_HISTORY_TRANSPORT_OR_PARSE_ERROR')
                require(type(response.status_code) is int and response.status_code == 200, 'KIS_HISTORY_HTTP_ERROR')
                body = _fields(response.body); headers = _fields(response.headers)
                require(body.get('rt_cd') == '0', 'KIS_HISTORY_API_ERROR')
                rows = body.get('output1')
                require(isinstance(rows, list), 'KIS_HISTORY_ROWS_MISSING')
                require(len(rows) <= (100 if query.binding.environment == 'real' else 15),
                        'KIS_HISTORY_PAGE_SIZE_UNEXPECTED')
                require(len(orders) + len(rows) <= self.max_rows, 'KIS_HISTORY_ROW_LIMIT')
                continuation = headers.get('tr_cont')
                require(continuation in ('M', 'F', 'D', 'E'), 'KIS_HISTORY_CONTINUATION_UNVERIFIED')
                # Validate a whole page before exposing any of its records.
                page = tuple(normalize_history_row(row, query) for row in rows)
                page_ids = [row.native_id for row in page]
                require(len(set(page_ids)) == len(page_ids) and not set(page_ids) & seen_ids,
                        'KIS_HISTORY_DUPLICATE_ID')
                orders.extend(page); seen_ids.update(page_ids)
                if continuation in ('D', 'E'):
                    return result(True, 'KIS_HISTORY_TRAVERSAL_COMPLETE')
                cursor = (body.get('ctx_area_fk100'), body.get('ctx_area_nk100'))
                # Validate tokens before the next call and reject loops/empty tokens.
                prepare_history_query(query, cursor=cursor)
                cursor_key = tuple(x.strip() for x in cursor)
                require(cursor_key != ('', '') and cursor_key not in seen_cursors, 'KIS_HISTORY_CURSOR_LOOP')
                seen_cursors.add(cursor_key)
            except Blocked as exc:
                return result(False, str(exc))
            except Exception:
                # Do not expose potentially secret/PII-bearing transport messages.
                return result(False, 'KIS_HISTORY_TRANSPORT_OR_PARSE_ERROR')
        return result(False, 'KIS_HISTORY_PAGE_LIMIT')

    def submit(self, *args, **kwargs):
        raise Blocked('KIS_LIVE_SUBMISSION_NOT_IMPLEMENTED')


def find_history_order(native_id: NativeOrderId, history: NativeHistory) -> NativeHistoryOrder | None:
    """Exact native-history lookup only; returns data, never a core state change.

    The native ID must come from separately verified history identity, not an
    assumed mapping of an ACK forwarding organization to a history branch.
    None means unresolved, NOT not-sent/rejected. No request ID, shape or time
    matching exists; an ID-less uncertain submission remains blocked.
    """
    native_id.validate()
    require((history.account, history.environment, history.trading_day, history.venue) ==
            (native_id.account, native_id.environment, native_id.trading_day, native_id.venue),
            'KIS_HISTORY_SCOPE_MISMATCH')
    if history.complete is not True:
        return None
    matches = [row for row in history.orders if row.native_id == native_id]
    return matches[0] if len(matches) == 1 else None

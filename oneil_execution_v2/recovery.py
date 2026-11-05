"""Authenticated *synthetic* recovery model. No KIS transport or live adapter.

The only issuer is an in-memory fixture authority. Its signatures demonstrate
content binding, not KIS provenance or production authentication. NativeHistory
and cash ACKs are deliberately not accepted as recovery evidence.
"""
from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import hmac
import json
import re
import secrets

from .domain import KST, Order, aware, integer, require
from .kis_adapter import NativeOrderId

CONTRACT = 'ONEIL_OFFLINE_RECOVERY_V1'
UNRESOLVED = {'SUBMISSION_UNKNOWN', 'ACKNOWLEDGED', 'PART_FILLED'}
TERMINAL = {'FILLED', 'CANCELLED', 'REJECTED'}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def token(value):
    require(type(value) is str and re.fullmatch('[A-Za-z0-9_-]{1,128}', value), 'RECOVERY_INVALID_ID')
    return value


def fields(value, expected):
    require(type(value) is dict and set(value) == set(expected.split()), 'RECOVERY_INVALID_FIELDS')
    return value


@dataclass(frozen=True)
class FixtureReceipt:
    content: str
    signature: str


class FixtureEvidenceAuthority:
    """Synthetic test authority, never an account identity/KIS authentication API.

    Keys stay in memory. A restart requires the trusted test issuer to be retained
    or a new coordinator; there is no key export, credential load or key recovery.
    Arbitrary code running in this process is outside this model's trust boundary.
    """
    def __init__(self):
        self._key = secrets.token_bytes(32)
        self.issuer = secrets.token_hex(16)

    def seal(self, *, audience, kind, payload, now, valid_seconds=10):
        aware(now); token(audience); token(kind)
        require(type(valid_seconds) is int and 1 <= valid_seconds <= 30, 'RECOVERY_INVALID_TTL')
        content = canonical({'contract': CONTRACT, 'issuer': self.issuer,
            'audience': audience, 'kind': kind, 'issued_at': now.timestamp(),
            'expires_at': now.timestamp()+valid_seconds, 'payload': payload})
        require(len(content.encode()) <= 65536, 'RECOVERY_EVIDENCE_TOO_LARGE')
        return FixtureReceipt(content, hmac.new(self._key, content.encode(), 'sha256').hexdigest())

    def open(self, receipt, *, audience, kind, now):
        aware(now)
        require(type(receipt) is FixtureReceipt and type(receipt.content) is str
            and len(receipt.content.encode()) <= 65536 and type(receipt.signature) is str,
            'AUTHENTICATED_FIXTURE_EVIDENCE_REQUIRED')
        require(hmac.compare_digest(receipt.signature,
            hmac.new(self._key, receipt.content.encode(), 'sha256').hexdigest()),
            'RECOVERY_EVIDENCE_AUTHENTICATION_FAILED')
        value = json.loads(receipt.content)
        fields(value, 'contract issuer audience kind issued_at expires_at payload')
        require((value['contract'],value['issuer'],value['audience'],value['kind']) ==
            (CONTRACT,self.issuer,audience,kind), 'RECOVERY_EVIDENCE_SCOPE_MISMATCH')
        require(type(value['issued_at']) in (int,float) and type(value['expires_at']) in (int,float)
            and 0 < value['expires_at']-value['issued_at'] <= 30
            and value['issued_at'] <= now.timestamp() < value['expires_at'], 'RECOVERY_EVIDENCE_STALE')
        return value['payload']


@dataclass(frozen=True)
class Attempt:
    request_id: str
    attempt_id: str
    order: Order
    environment: str
    trading_day: str
    attempted_at: datetime
    state: str = 'SUBMISSION_UNKNOWN'
    filled_quantity: int = 0
    native_id: NativeOrderId | None = None
    sequence: int = 0
    observation_digest: str | None = None
    observed_at: datetime | None = None

    def validate(self):
        token(self.request_id); token(self.attempt_id); self.order.validate(); aware(self.attempted_at)
        require(self.environment == 'fixture', 'OFFLINE_RECOVERY_ONLY')
        # Native identity uses the existing exact demo/real vocabulary, but this
        # model only consumes signed synthetic demo evidence, never real accounts.
        probe = NativeOrderId(self.order.account, 'demo', self.trading_day, self.order.venue, '0', '1')
        probe.validate()
        require(self.attempted_at.astimezone(KST).date().isoformat()==self.trading_day, 'RECOVERY_ATTEMPT_DAY_MISMATCH')
        require(self.state in UNRESOLVED | TERMINAL and integer(self.sequence)
            and integer(self.filled_quantity) and self.filled_quantity <= self.order.quantity,
            'RECOVERY_INVALID_STATE')
        _quantity(self.state, self.filled_quantity, self.order.quantity)
        if self.native_id is not None:
            self.native_id.validate()
            require((self.native_id.account,self.native_id.environment,self.native_id.trading_day,self.native_id.venue) ==
                (self.order.account,'demo',self.trading_day,self.order.venue), 'RECOVERY_NATIVE_SCOPE_MISMATCH')
        require((self.sequence == 0) == (self.observation_digest is None and self.observed_at is None),
                'RECOVERY_INVALID_STATE')
        require(self.sequence>0 or self.state=='SUBMISSION_UNKNOWN', 'RECOVERY_INVALID_STATE')
        if self.sequence:
            require(self.state!='SUBMISSION_UNKNOWN', 'RECOVERY_INVALID_STATE')
            require(self.native_id is not None and type(self.observation_digest) is str
                and re.fullmatch('[0-9a-f]{64}',self.observation_digest), 'RECOVERY_INVALID_STATE')
            aware(self.observed_at)
        return self

    def identity(self):
        return {'request_id':self.request_id, 'attempt_id':self.attempt_id,
            'order_digest':self.order.digest, 'account':self.order.account,
            'environment':self.environment, 'trading_day':self.trading_day}


def _quantity(state, filled, quantity):
    require(state not in ('SUBMISSION_UNKNOWN','ACKNOWLEDGED','REJECTED') or filled == 0, 'RECOVERY_INVALID_FILL')
    require(state != 'PART_FILLED' or 0 < filled < quantity, 'RECOVERY_INVALID_FILL')
    require(state != 'FILLED' or filled == quantity, 'RECOVERY_INVALID_FILL')
    require(state != 'CANCELLED' or filled < quantity, 'RECOVERY_INVALID_FILL')


def _identity(payload, attempt):
    require(payload == attempt.identity(), 'RECOVERY_ATTEMPT_MISMATCH')


def bind_identity(attempt, receipt, authority, audience, now):
    """Require separate positive association evidence. ACK shape is never enough."""
    attempt.validate()
    require(type(authority) is FixtureEvidenceAuthority, 'OFFLINE_FIXTURE_AUTHORITY_REQUIRED')
    value = authority.open(receipt,audience=audience,kind='native_binding',now=now)
    fields(value, 'identity native_id basis')
    _identity(value['identity'], attempt)
    require(value['basis'] == 'SYNTHETIC_EXACT_ATTEMPT_ASSOCIATION', 'RECOVERY_IDENTITY_UNPROVEN')
    fields(value['native_id'], 'account environment trading_day venue branch number')
    native = NativeOrderId(**value['native_id']).validate()
    require(attempt.native_id is None or attempt.native_id == native, 'RECOVERY_IDENTITY_IMMUTABLE')
    from dataclasses import replace
    return replace(attempt,native_id=native).validate()


def reconcile(attempt, receipt, authority, audience, now):
    """Positive exact evidence only. No absence, retry, timeout-to-terminal or guess.

    Terminal states and their cumulative fills are immutable. A later correction
    must be independently reviewed; this reducer halts rather than rewriting it.
    """
    attempt.validate()
    require(type(authority) is FixtureEvidenceAuthority, 'OFFLINE_FIXTURE_AUTHORITY_REQUIRED')
    require(attempt.native_id is not None, 'RECOVERY_EXACT_NATIVE_BINDING_REQUIRED')
    value = authority.open(receipt,audience=audience,kind='order_observation',now=now)
    fields(value, 'identity native_id sequence observed_at status filled_quantity traversal semantics')
    _identity(value['identity'], attempt)
    require(value['native_id'] == asdict(attempt.native_id), 'RECOVERY_NATIVE_ID_MISMATCH')
    require(value['traversal'] == 'COMPLETE' and value['semantics'] == CONTRACT,
            'RECOVERY_BROKER_PROOF_INCOMPLETE')
    require(type(value['observed_at']) is str, 'RECOVERY_INVALID_TIMESTAMP')
    observed_at = aware(datetime.fromisoformat(value['observed_at']))
    require(attempt.attempted_at <= observed_at <= now and (now-observed_at).total_seconds() <= 10,
            'RECOVERY_OBSERVATION_STALE')
    require(attempt.observed_at is None or observed_at >= attempt.observed_at, 'RECOVERY_TIME_REGRESSION')
    sequence = value['sequence']; filled = value['filled_quantity']; state = value['status']
    require(integer(sequence,1) and integer(filled) and filled <= attempt.order.quantity
        and state in (UNRESOLVED | TERMINAL)-{'SUBMISSION_UNKNOWN'}, 'RECOVERY_INVALID_OBSERVATION')
    _quantity(state,filled,attempt.order.quantity)
    value_digest = digest(value)
    if sequence == attempt.sequence:
        require(value_digest == attempt.observation_digest, 'RECOVERY_SEQUENCE_CONFLICT')
        return attempt
    require(sequence > attempt.sequence, 'RECOVERY_SEQUENCE_REGRESSION')
    require(filled >= attempt.filled_quantity, 'RECOVERY_FILL_REGRESSION')
    require(attempt.state not in TERMINAL or (state,filled) == (attempt.state,attempt.filled_quantity),
            'RECOVERY_TERMINAL_CONFLICT')
    require(attempt.state != 'PART_FILLED' or state in {'PART_FILLED','FILLED','CANCELLED'},
            'RECOVERY_STATE_REGRESSION')
    from dataclasses import replace
    return replace(attempt,state=state,filled_quantity=filled,sequence=sequence,
        observation_digest=value_digest,observed_at=observed_at).validate()

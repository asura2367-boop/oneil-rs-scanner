"""Import a supplied export. Never read defaults, environment, files, or services."""
from dataclasses import asdict, dataclass, replace
from datetime import datetime
import hashlib
import json
import re

from oneil_execution_v2.domain import RiskPolicy, aware, integer, require


def reference(value):
    require(isinstance(value, str) and 1 <= len(value) <= 512 and
            not any(ord(c) < 32 for c in value), 'EVIDENCE_REFERENCE_REQUIRED')
    return value


def percentage(value):
    require(integer(value) and value <= 100, 'INVALID_PERCENTAGE')
    return value


def revision(value):
    require(integer(value) and value < 2**53 - 1, 'INVALID_REVISION')
    return value


def exact_keys(value, keys, code):
    require(type(value) is dict and set(value) == set(keys), code)


@dataclass(frozen=True)
class TradingPolicy:
    account: str
    reserve_pct: int
    per_stock_pct: int
    automatic_buy_enabled: bool
    automatic_sell_enabled: bool  # profit exits, independent of stop-loss
    stop_loss_enabled: bool
    buy_revision: int
    sell_revision: int
    risk_revision: int
    stop_loss_pct: int
    take_profit_pct: int
    max_stock_count: int
    max_control_age_seconds: int
    max_candidate_age_seconds: int
    max_session_age_seconds: int
    risk: RiskPolicy
    source_reference: str
    source_observed_at: datetime
    legacy_digest: str
    migration_reference: str

    def validate(self):
        require(isinstance(self.account, str) and re.fullmatch('[0-9a-f]{64}', self.account), 'BAD_ACCOUNT')
        percentage(self.reserve_pct); percentage(self.per_stock_pct)
        require(all(type(x) is bool for x in (self.automatic_buy_enabled, self.automatic_sell_enabled,
                                              self.stop_loss_enabled)), 'INVALID_CONTROL')
        for x in (self.buy_revision, self.sell_revision, self.risk_revision):
            revision(x)
        require(all(integer(x, 1) and x <= 50 for x in (self.stop_loss_pct, self.take_profit_pct)),
                'INVALID_EXIT_THRESHOLD')
        require(integer(self.max_stock_count) and self.max_stock_count <= 10000, 'INVALID_STOCK_LIMIT')
        require(all(integer(x, 1) and x <= 86400 for x in (self.max_control_age_seconds,
                    self.max_candidate_age_seconds, self.max_session_age_seconds)), 'INVALID_EVIDENCE_TTL')
        self.risk.validate()
        reference(self.source_reference); reference(self.migration_reference)
        aware(self.source_observed_at)
        require(isinstance(self.legacy_digest, str) and re.fullmatch('[0-9a-f]{64}', self.legacy_digest),
                'INVALID_IMPORT_DIGEST')
        return self

    @property
    def version(self):
        data = asdict(self)
        data['risk'].pop('version')
        data['source_observed_at'] = self.source_observed_at.isoformat()
        digest = hashlib.sha256(json.dumps(data, sort_keys=True, separators=(',', ':'),
                                          allow_nan=False).encode()).hexdigest()
        return 'policy-v2-' + digest[:40]

    @property
    def core_risk(self):
        self.validate()
        return replace(self.risk, version=self.version)


def import_legacy_policy(export, *, runtime, constraints, source_reference,
                         migration_reference):
    """Strict export subset plus explicit reviewed new constraints, no fallback.

    `automatic_sell_enabled` must explicitly be True to preserve the verified
    legacy behavior: its sell switch affects stop-loss only. A later independent
    sell setting is a new policy, represented with dataclasses.replace().
    References are provenance, not proof of owner authentication or approval.
    No production settings are fetched or asserted to be current here.
    """
    exact_keys(export, ('schema_version', 'account', 'observed_at', 'enabled', 'revision', 'risk', 'sell'),
               'EXPLICIT_LEGACY_EXPORT_REQUIRED')
    require(type(export['schema_version']) is int and export['schema_version'] == 1,
            'UNSUPPORTED_LEGACY_SCHEMA')
    exact_keys(export['risk'], ('reserve_pct', 'per_stock_pct', 'revision'), 'EXPLICIT_RISK_EXPORT_REQUIRED')
    exact_keys(export['sell'], ('enabled', 'revision'), 'EXPLICIT_SELL_EXPORT_REQUIRED')
    exact_keys(runtime, ('stop_loss_pct', 'take_profit_pct', 'selection_policy', 'min_rs',
                         'automatic_sell_enabled'), 'EXPLICIT_RUNTIME_SETTINGS_REQUIRED')
    require(runtime['selection_policy'] == 'RS_ONLY_V1' and type(runtime['min_rs']) is int
            and runtime['min_rs'] == 90, 'UNVERIFIED_STRATEGY_VARIANT')
    require(runtime['automatic_sell_enabled'] is True, 'LEGACY_SELL_SEMANTICS_CHANGED')
    exact_keys(constraints, ('max_stock_count', 'max_control_age_seconds', 'max_candidate_age_seconds',
                            'max_session_age_seconds', 'risk'), 'EXPLICIT_V2_CONSTRAINTS_REQUIRED')
    risk_keys = set(RiskPolicy.__dataclass_fields__) - {'version'}
    exact_keys(constraints['risk'], risk_keys, 'EXPLICIT_CORE_RISK_LIMITS_REQUIRED')
    require(isinstance(export['observed_at'], str), 'INVALID_IMPORT_TIME')
    try:
        observed_at = datetime.fromisoformat(export['observed_at'])
    except ValueError:
        require(False, 'INVALID_IMPORT_TIME')
    # Only the small, explicit whitelist is hashed; never persist a raw runtime
    # response containing account credentials, queued intents, or release state.
    legacy_digest = hashlib.sha256(json.dumps(export, sort_keys=True, separators=(',', ':'),
                                             allow_nan=False).encode()).hexdigest()
    return TradingPolicy(
        account=export['account'], reserve_pct=export['risk']['reserve_pct'],
        per_stock_pct=export['risk']['per_stock_pct'], automatic_buy_enabled=export['enabled'],
        automatic_sell_enabled=True, stop_loss_enabled=export['sell']['enabled'],
        buy_revision=export['revision'], sell_revision=export['sell']['revision'],
        risk_revision=export['risk']['revision'], stop_loss_pct=runtime['stop_loss_pct'],
        take_profit_pct=runtime['take_profit_pct'], max_stock_count=constraints['max_stock_count'],
        max_control_age_seconds=constraints['max_control_age_seconds'],
        max_candidate_age_seconds=constraints['max_candidate_age_seconds'],
        max_session_age_seconds=constraints['max_session_age_seconds'],
        risk=RiskPolicy(version='pending-import', **constraints['risk']),
        source_reference=source_reference, source_observed_at=observed_at,
        legacy_digest=legacy_digest, migration_reference=migration_reference).validate()

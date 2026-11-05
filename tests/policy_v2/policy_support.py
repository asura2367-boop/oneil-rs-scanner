from dataclasses import replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from oneil_policy_v2.policy import import_legacy_policy
from oneil_policy_v2.evidence import (Evidence, Controls, Position, AccountEvidence, MarketEvidence,
                                      SessionEvidence, Candidate, CandidateBatch)
from oneil_policy_v2.planner import ManualAdd
NOW = datetime(2026, 11, 5, 10, 0, tzinfo=ZoneInfo('Asia/Seoul'))
ACCOUNT = 'a' * 64

def evidence(name='fixture', at=NOW, verified=True):
    return Evidence('simulation:' + name, at, verified)

def export():
    return {'schema_version': 1, 'account': ACCOUNT, 'observed_at': NOW.isoformat(),
            'enabled': True, 'revision': 9, 'risk': {'reserve_pct': 37, 'per_stock_pct': 28, 'revision': 8},
            'sell': {'enabled': False, 'revision': 7}}

def runtime():
    return {'stop_loss_pct': 6, 'take_profit_pct': 12, 'selection_policy': 'RS_ONLY_V1',
            'min_rs': 90, 'automatic_sell_enabled': True}

def constraints():
    return {'max_stock_count': 4, 'max_control_age_seconds': 15, 'max_candidate_age_seconds': 600,
            'max_session_age_seconds': 3600,
            'risk': {'max_order_krw': 10_000_000, 'max_daily_buy_krw': 20_000_000,
                     'max_position_krw': 10_000_000, 'max_gross_exposure_krw': 30_000_000,
                     'max_daily_loss_krw': 1_000_000, 'max_quote_age_seconds': 5,
                     'max_account_age_seconds': 10, 'max_history_age_seconds': 10,
                     'max_price_deviation_bps': 100}}

def policy(**changes):
    p = import_legacy_policy(export(), runtime=runtime(), constraints=constraints(),
                             source_reference='simulation:settings-export', migration_reference='simulation:review')
    return replace(p, **changes)

def controls(p):
    return Controls(ACCOUNT, p.version, False, evidence('controls'))

def position(code='005930', **changes):
    return replace(Position(code, 10, 10, 10, 10_000, 10_000, '1000', 'simulation:managed-lots'), **changes)

def account(positions=(), **changes):
    return replace(AccountEvidence(ACCOUNT, NOW.date().isoformat(), 1_000_000, 1_000_000,
        1_100_000, sum(p.exposure for p in positions), 0, 0, positions, frozenset(), (), True, True,
        'simulation:history', evidence('balance'), evidence('history')), **changes)

def market(code='005930', side='BUY', price=1000, **changes):
    return replace(MarketEvidence(ACCOUNT, code, 'KRX', side, price, 10000,
                                  1_000_000, 10000, True, evidence('quote')), **changes)

def sessions(**changes):
    return {'KRX': replace(SessionEvidence('KRX', NOW.date().isoformat(), True,
        NOW.replace(hour=9), NOW.replace(hour=15), '00',
        frozenset({'005930', '000660', '035420', '051910'}), frozenset({'BUY', 'SELL'}),
        evidence('calendar'), evidence('capability')), **changes)}

def candidates(*items, **changes):
    return replace(CandidateBatch('RS_ONLY_V1', NOW.date().isoformat(), '2026-11-04', '2026-11-04',
                                 tuple(items or (Candidate('005930', 99),)), evidence('candidates'),
                                 evidence('candidate-session')), **changes)

def manual(p, **changes):
    return replace(ManualAdd('01234567-0123-4123-8123-0123456789ab', ACCOUNT, '005930', 10, 1000,
                            10, p.version, NOW, NOW + timedelta(minutes=20), 'simulation:owner-review'), **changes)

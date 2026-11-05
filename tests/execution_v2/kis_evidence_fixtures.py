"""Invented test values, NOT captured KIS/account data or verified wire schemas.

Field names are mapped against official source linked in KIS_EVIDENCE.md. Values,
status codes, cursor strings, amounts and times here are entirely synthetic.
Account 00000000 / product 00 is a placeholder and must never be transmitted.
"""
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from oneil_execution_v2.kis_adapter import AccountBinding, KisResponse
from oneil_execution_v2.kis_evidence import (
    BalanceQuery, BuyingPowerQuery, HolidayQuery, MarketQuery, OfflineRead, SellableQuery, PORTAL_PROFILE,
    prepare_balance_query, prepare_buying_power_query, prepare_holiday_query,
    prepare_market_query, prepare_sellable_query,
)

NOW = datetime(2026, 11, 5, 1, 0, 0, tzinfo=timezone.utc)
ACCOUNT = 'a' * 64
BINDING = AccountBinding(ACCOUNT, 'real', '00000000', '00')
DAY = '2026-11-05'
BALANCE = BalanceQuery(BINDING, DAY)
BUYING_POWER = BuyingPowerQuery(BINDING, DAY, '005930')
SELLABLE = SellableQuery(BINDING, DAY, '005930')
MARKET = MarketQuery('real', DAY, '005930', 'KRX')
HOLIDAY = HolidayQuery(DAY)

POSITION = {
    'pdno': '005930', 'hldg_qty': '10', 'ord_psbl_qty': '8',
    'pchs_avg_pric': '9999.125', 'pchs_amt': '100000', 'evlu_amt': '101000',
    'evlu_pfls_amt': '1000', 'loan_amt': '0',
}
TOTALS = {
    'dnca_tot_amt': '900000', 'nxdy_excc_amt': '850000', 'prvs_rcdl_excc_amt': '800000',
    'tot_evlu_amt': '1001000', 'nass_amt': '1001000', 'pchs_amt_smtl_amt': '100000',
    'evlu_amt_smtl_amt': '101000', 'evlu_pfls_smtl_amt': '1000',
}
POWER = {
    'ord_psbl_cash': '700000', 'nrcvb_buy_amt': '600000', 'nrcvb_buy_qty': '60',
    'max_buy_amt': '1500000', 'max_buy_qty': '150', 'psbl_qty_calc_unpr': '10000',
}
SELL = {'pdno': '005930', 'cblc_qty': '10', 'ord_psbl_qty': '7'}
PRICE = {'stck_shrn_iscd': '005930', 'stck_prpr': '10100',
         'temp_stop_yn': 'N', 'iscd_stat_cls_code': 'SYNTHETIC'}
BOOK = {
    'output1': {'stck_shrn_iscd': '005930', 'aspr_acpt_hour': '095958',
                'new_mkop_cls_code': 'SYNTHETIC_UNKNOWN'},
    'output2': {'antc_mkop_cls_code': 'SYNTHETIC_UNKNOWN'},
}
CALENDAR = {'bass_dt': '20261105', 'bzdy_yn': 'Y', 'tr_day_yn': 'Y',
            'opnd_yn': 'Y', 'sttl_day_yn': 'Y', 'wday_dvsn_cd': '05'}


def read(recipe, body, *, marker='', started_at=None, received_at=None, status=200):
    return OfflineRead(recipe, KisResponse(status, deepcopy({'rt_cd': '0', **body}), {'tr_cont': marker}),
                       started_at or NOW-timedelta(seconds=2), received_at or NOW-timedelta(seconds=1))


def balance_read(*, positions=None, totals=None, marker='D', cursor=('', ''), next_cursor=('', ''), **kw):
    return read(prepare_balance_query(BALANCE, cursor=cursor), {
        'output1': [POSITION] if positions is None else positions,
        'output2': [TOTALS] if totals is None else totals,
        'ctx_area_fk100': next_cursor[0], 'ctx_area_nk100': next_cursor[1],
    }, marker=marker, **kw)


@dataclass(frozen=True)
class SyntheticCase:
    name: str
    query: object
    reads: tuple[OfflineRead, ...]
    synthetic: bool = True
    source_commit: str | None = 'd8c7f7936793f85dc1e9c364ba5dc66832353f8c'
    contract_profile: str = 'KIS_GITHUB_SAMPLE_D8C7F793'


def cases() -> tuple[SyntheticCase, ...]:
    return (
        SyntheticCase('balance', BALANCE, (balance_read(),)),
        SyntheticCase('buying_power', BUYING_POWER, (read(prepare_buying_power_query(BUYING_POWER), {'output': POWER}),), source_commit=None, contract_profile=PORTAL_PROFILE),
        SyntheticCase('sellable', SELLABLE, (read(prepare_sellable_query(SELLABLE), {'output1': SELL}),), source_commit=None, contract_profile=PORTAL_PROFILE),
        SyntheticCase('price', MARKET, (read(prepare_market_query(MARKET), {'output': PRICE}),)),
        SyntheticCase('session', MARKET, (read(prepare_market_query(MARKET, book=True), BOOK),)),
        SyntheticCase('holidays', HOLIDAY, (read(prepare_holiday_query(HOLIDAY), {'output': CALENDAR}),), source_commit=None, contract_profile=PORTAL_PROFILE),
    )

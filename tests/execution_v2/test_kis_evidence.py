"""Offline-only contract/adversarial tests; no socket, credentials or DB required."""
import ast
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
import unittest
from unittest.mock import patch

from oneil_execution_v2.domain import Blocked, KST
from oneil_execution_v2.kis_adapter import KisResponse
from oneil_execution_v2.kis_evidence import (
    BalanceObservation, BuyingPowerObservation, HolidayObservation, KisOfflineEvidenceAdapter,
    MarketQuery, PriceObservation, SellableObservation, SessionObservation,
    prepare_balance_query, prepare_buying_power_query, prepare_holiday_query,
    prepare_market_query, prepare_sellable_query,
)
from kis_evidence_fixtures import (
    ACCOUNT, BALANCE, BINDING, BOOK, BUYING_POWER, CALENDAR, DAY, HOLIDAY, MARKET,
    NOW, POSITION, POWER, PRICE, SELL, SELLABLE, TOTALS, balance_read, cases, read,
)


class KisEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.adapter = KisOfflineEvidenceAdapter(NOW, max_age_seconds=10)

    def invalid(self, outcome, reason=None):
        self.assertFalse(outcome.valid)
        self.assertIsNone(outcome.value)
        if reason:
            self.assertEqual(outcome.reason, reason)

    def run_case(self, case):
        arg = case.reads if case.name in ('balance', 'holidays') else case.reads[0]
        return getattr(self.adapter, case.name)(case.query, arg)

    def test_all_typed_synthetic_fixtures_are_non_executable(self):
        expected = (BalanceObservation, BuyingPowerObservation, SellableObservation,
                    PriceObservation, SessionObservation, HolidayObservation)
        for case, kind in zip(cases(), expected):
            with self.subTest(case=case.name):
                result = self.run_case(case)
                self.assertTrue(result.valid, result.reason)
                self.assertIsInstance(result.value, kind)
                self.assertTrue(case.synthetic)
                self.assertFalse(result.value.live_eligible)
                self.assertFalse(result.value.provenance_authenticated)

    def test_all_flows_work_with_socket_creation_forbidden(self):
        with patch('socket.socket', side_effect=AssertionError('No network allowed')):
            for case in cases():
                self.assertTrue(self.run_case(case).valid)

    def test_no_transport_clock_credentials_database_or_legacy_import(self):
        source = Path(__file__).resolve().parents[2]/'oneil_execution_v2'/'kis_evidence.py'
        tree = ast.parse(source.read_text())
        imports = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        self.assertEqual(imports, {'collections.abc', 'dataclasses', 'datetime', 'decimal', 'typing',
                                   'domain', 'kis_adapter'})
        self.assertFalse(any(isinstance(node, ast.Import) for node in ast.walk(tree)))
        for case in cases():
            self.assertTrue(all(r.recipe.method == 'GET' for r in case.reads))

    def test_no_policy_conversion_or_submission(self):
        with self.assertRaisesRegex(Blocked, 'KIS_LIVE_EVIDENCE_NOT_ESTABLISHED'):
            self.adapter.policy_evidence()
        with self.assertRaisesRegex(Blocked, 'KIS_LIVE_SUBMISSION_NOT_IMPLEMENTED'):
            self.adapter.submit()

    def test_identifiers_and_raw_payload_are_not_in_repr(self):
        sample = replace(cases()[1].reads[0], response=KisResponse(200, {'private': 'NEVER_LOG_ME'}))
        for value in (BALANCE, sample, prepare_balance_query(BALANCE)):
            self.assertNotIn('00000000', repr(value))
            self.assertNotIn('NEVER_LOG_ME', repr(value))

    def test_bounds_reject_bool_zero_and_extreme_values(self):
        for key, values in (('max_age_seconds', (True, 0, 86401)),
                            ('max_pages', (True, 0, 101)), ('max_rows', (True, 0, 10001))):
            for value in values:
                args = {'max_age_seconds': 10, key: value}
                with self.subTest(key=key, value=value), self.assertRaises(Blocked):
                    KisOfflineEvidenceAdapter(NOW, **args)

    def test_naive_now_rejected(self):
        with self.assertRaises(Blocked):
            KisOfflineEvidenceAdapter(NOW.replace(tzinfo=None), max_age_seconds=10)

    def test_balance_exact_wire_contract_both_environments(self):
        for environment, tr_id in (('real', 'TTTC8434R'), ('demo', 'VTTC8434R')):
            query = replace(BALANCE, binding=replace(BINDING, environment=environment))
            recipe = prepare_balance_query(query)
            self.assertEqual(recipe.tr_id, tr_id)
            self.assertEqual(recipe.wire_parameters(), {
                'CANO': '00000000', 'ACNT_PRDT_CD': '00', 'AFHR_FLPR_YN': 'N', 'OFL_YN': '',
                'INQR_DVSN': '02', 'UNPR_DVSN': '01', 'FUND_STTL_ICLD_YN': 'N',
                'FNCG_AMT_AUTO_RDPT_YN': 'N', 'PRCS_DVSN': '00',
                'CTX_AREA_FK100': '', 'CTX_AREA_NK100': '',
            })
            self.assertEqual(recipe.tr_cont, '')

    def test_balance_keeps_deposit_settlement_valuation_separate(self):
        value = self.adapter.balance(BALANCE, (balance_read(),)).value
        self.assertEqual(value.totals.deposit_total_krw, 900000)
        self.assertEqual(value.totals.next_day_settlement_krw, 850000)
        self.assertEqual(value.totals.provisional_settlement_krw, 800000)
        self.assertEqual(value.positions[0].balance_orderable_quantity, 8)
        self.assertEqual(value.positions[0].average_price, Decimal('9999.125'))
        self.assertTrue(value.traversal_complete)
        self.assertFalse(value.snapshot_consistent)
        for name in ('daily_loss_krw', 'cash_available_krw', 'fill_watermarks', 'managed_quantity'):
            self.assertFalse(hasattr(value, name))

    def test_signed_deposit_and_loss_are_retained_not_clamped(self):
        totals = {**TOTALS, 'dnca_tot_amt': '-100', 'evlu_pfls_smtl_amt': '-1000'}
        result = self.adapter.balance(BALANCE, (balance_read(totals=[totals]),))
        self.assertEqual(result.value.totals.deposit_total_krw, -100)
        self.assertEqual(result.value.totals.valuation_profit_loss_sum_krw, -1000)

    def test_balance_zero_holdings_and_empty_complete_list_are_valid(self):
        row = {**POSITION, 'hldg_qty': '0', 'ord_psbl_qty': '0', 'pchs_avg_pric': '0'}
        self.assertTrue(self.adapter.balance(BALANCE, (balance_read(positions=[row]),)).valid)
        self.assertEqual(self.adapter.balance(BALANCE, (balance_read(positions=[]),)).value.positions, ())

    def test_every_required_balance_number_missing_blocks(self):
        for key in POSITION:
            row = dict(POSITION); del row[key]
            with self.subTest(position=key):
                self.invalid(self.adapter.balance(BALANCE, (balance_read(positions=[row]),)))
        for key in TOTALS:
            totals = dict(TOTALS); del totals[key]
            with self.subTest(total=key):
                self.invalid(self.adapter.balance(BALANCE, (balance_read(totals=[totals]),)))

    def test_no_float_rounding_exponent_boolean_comma_or_unicode_number(self):
        for value in (None, True, 1, 1.0, '1.1', '1e3', '+1', '1,000', 'NaN', '１２', ' 1', '1 ', '-1'):
            with self.subTest(value=value):
                self.invalid(self.adapter.balance(BALANCE, (balance_read(positions=[{**POSITION, 'hldg_qty': value}]),)))
        self.assertTrue(self.adapter.balance(BALANCE, (balance_read(positions=[{**POSITION, 'hldg_qty': '10.000'}]),)).valid)

    def test_fractional_average_is_exact_and_bounded(self):
        for value in ('1e3', 'Infinity', 'NaN', '-1', '1.123456789', 1.1, True):
            with self.subTest(value=value):
                self.invalid(self.adapter.balance(BALANCE, (balance_read(positions=[{**POSITION, 'pchs_avg_pric': value}]),)))

    def test_inconsistent_position_and_duplicate_ticker_block(self):
        self.invalid(self.adapter.balance(BALANCE, (balance_read(positions=[{**POSITION, 'ord_psbl_qty': '11'}]),)),
                     'KIS_POSITION_QUANTITY_INCONSISTENT')
        self.invalid(self.adapter.balance(BALANCE, (balance_read(positions=[POSITION, POSITION]),)),
                     'KIS_BALANCE_DUPLICATE_TICKER')

    def test_multipage_preserves_exact_filters_and_first_timestamp(self):
        first = balance_read(marker='M', next_cursor=('a ', ' b'), started_at=NOW-timedelta(seconds=4),
                             received_at=NOW-timedelta(seconds=3))
        second = balance_read(positions=[{**POSITION, 'pdno': '000660'}], cursor=('a ', ' b'))
        result = self.adapter.balance(BALANCE, (first, second))
        self.assertTrue(result.valid, result.reason)
        self.assertEqual(result.value.pages, 2)
        self.assertEqual(result.value.window.started_at, first.started_at)
        self.assertEqual(second.recipe.tr_cont, 'N')
        a = first.recipe.wire_parameters(); b = second.recipe.wire_parameters()
        for key in a.keys() - {'CTX_AREA_FK100', 'CTX_AREA_NK100'}:
            self.assertEqual(a[key], b[key])

    def test_truncated_traversal_discards_all_partial_observations(self):
        self.invalid(self.adapter.balance(BALANCE, (balance_read(marker='M', next_cursor=('a', 'b')),)),
                     'KIS_PAGES_TRUNCATED')

    def test_extra_page_after_terminal_blocks(self):
        self.invalid(self.adapter.balance(BALANCE, (balance_read(), balance_read())), 'KIS_PAGES_AFTER_TERMINAL')

    def test_balance_unknown_blank_missing_continuation_block(self):
        for marker in ('', None, 'Z', 'd', True):
            with self.subTest(marker=marker):
                self.invalid(self.adapter.balance(BALANCE, (balance_read(marker=marker),)), 'KIS_CONTINUATION_UNVERIFIED')

    def test_changed_summary_across_pages_blocks(self):
        first = balance_read(marker='F', next_cursor=('a', 'b'), started_at=NOW-timedelta(seconds=4),
                             received_at=NOW-timedelta(seconds=3))
        second = balance_read(positions=[], totals=[{**TOTALS, 'dnca_tot_amt': '900001'}], cursor=('a', 'b'))
        self.invalid(self.adapter.balance(BALANCE, (first, second)), 'KIS_BALANCE_TOTALS_CHANGED')

    def test_duplicate_across_pages_blocks(self):
        first = balance_read(marker='F', next_cursor=('a', 'b'), started_at=NOW-timedelta(seconds=4),
                             received_at=NOW-timedelta(seconds=3))
        self.invalid(self.adapter.balance(BALANCE, (first, balance_read(cursor=('a', 'b')))),
                     'KIS_BALANCE_DUPLICATE_TICKER')

    def test_empty_overlong_control_and_padded_loop_cursors_block(self):
        for cursor in (('', ''), (' ', ' '), ('a'*101, 'b'), ('a\n', 'b')):
            with self.subTest(cursor=cursor):
                first = balance_read(marker='M', next_cursor=cursor, started_at=NOW-timedelta(seconds=4),
                                     received_at=NOW-timedelta(seconds=3))
                self.invalid(self.adapter.balance(BALANCE, (first, balance_read())))
        first = balance_read(marker='M', next_cursor=('a', 'b'), started_at=NOW-timedelta(seconds=6), received_at=NOW-timedelta(seconds=5))
        second = balance_read(marker='F', cursor=('a', 'b'), next_cursor=(' a ', 'b '),
                              started_at=NOW-timedelta(seconds=4), received_at=NOW-timedelta(seconds=3))
        self.invalid(self.adapter.balance(BALANCE, (first, second, balance_read())), 'KIS_CURSOR_LOOP')

    def test_changed_account_or_request_content_block(self):
        sample = balance_read()
        for recipe in (replace(sample.recipe, method='POST'), replace(sample.recipe, tr_id='VTTC8434R'),
                       replace(sample.recipe, path='/elsewhere'),
                       replace(sample.recipe, parameters=sample.recipe.parameters+(('EXTRA', 'Y'),))):
            with self.subTest(recipe=recipe):
                self.invalid(self.adapter.balance(BALANCE, (replace(sample, recipe=recipe),)), 'KIS_EVIDENCE_REQUEST_MISMATCH')
        altered = replace(BALANCE, binding=replace(BINDING, cano='99999999'))
        self.invalid(self.adapter.balance(altered, (sample,)), 'KIS_EVIDENCE_REQUEST_MISMATCH')

    def test_page_and_row_limits_block(self):
        self.invalid(KisOfflineEvidenceAdapter(NOW, max_age_seconds=10, max_pages=1).balance(BALANCE, (balance_read(), balance_read())))
        self.invalid(KisOfflineEvidenceAdapter(NOW, max_age_seconds=10, max_rows=1).balance(BALANCE,
                     (balance_read(positions=[POSITION, {**POSITION, 'pdno': '000660'}]),)), 'KIS_ROW_LIMIT')
        for env, count in (('real', 51), ('demo', 21)):
            query = replace(BALANCE, binding=replace(BINDING, environment=env))
            sample = replace(balance_read(positions=[POSITION]*count), recipe=prepare_balance_query(query))
            self.invalid(self.adapter.balance(query, (sample,)), 'KIS_BALANCE_PAGE_SIZE_INVALID')

    def test_missing_or_ambiguous_summary_blocks(self):
        for totals in ([], {}, [TOTALS, TOTALS], None):
            sample = balance_read()
            body = {**sample.response.body, 'output2': totals}
            self.invalid(self.adapter.balance(BALANCE, (replace(sample, response=KisResponse(200, body, {'tr_cont': 'D'})),)))

    def test_stale_future_reverse_naive_and_midnight_captures_block(self):
        sample = balance_read()
        changes = (
            {'started_at': NOW-timedelta(seconds=11)}, {'received_at': NOW+timedelta(microseconds=1)},
            {'started_at': NOW, 'received_at': NOW-timedelta(seconds=1)},
            {'started_at': NOW.replace(tzinfo=None)},
        )
        for update in changes:
            with self.subTest(update=update):
                self.invalid(self.adapter.balance(BALANCE, (replace(sample, **update),)))
        midnight = NOW.replace(hour=15)  # following KST date
        adapter = KisOfflineEvidenceAdapter(midnight, max_age_seconds=10)
        self.invalid(adapter.balance(BALANCE, (replace(sample, started_at=midnight-timedelta(seconds=2),
                                                       received_at=midnight),)), 'KIS_CAPTURE_DAY_MISMATCH')

    def test_slow_last_page_does_not_refresh_first_page(self):
        first = balance_read(marker='F', next_cursor=('a', 'b'), started_at=NOW-timedelta(seconds=11),
                             received_at=NOW-timedelta(seconds=3))
        self.invalid(self.adapter.balance(BALANCE, (first, balance_read(cursor=('a', 'b')))), 'KIS_CAPTURE_STALE_OR_INVALID')

    def test_overlapping_capture_pages_block(self):
        first = balance_read(marker='F', next_cursor=('a', 'b'))
        self.invalid(self.adapter.balance(BALANCE, (first, balance_read(cursor=('a', 'b')))), 'KIS_CAPTURE_ORDER_INVALID')

    def test_http_api_and_case_collision_fail_closed_for_every_endpoint(self):
        for case in cases():
            original = case.reads[0]
            for response in (
                replace(original.response, status_code=500), replace(original.response, status_code=True),
                replace(original.response, body={**original.response.body, 'rt_cd': '1'}),
                replace(original.response, body={**original.response.body, 'RT_CD': '0'}),
                replace(original.response, headers={'tr_cont': 'D', 'TR_CONT': 'D'}),
            ):
                with self.subTest(case=case.name, response=response.status_code):
                    self.invalid(self.run_case(replace(case, reads=(replace(original, response=response),))))

    def test_uppercase_payload_keys_are_supported_without_collisions(self):
        sample = balance_read()
        body = {key.upper(): value for key, value in sample.response.body.items()}
        body['OUTPUT1'] = [{key.upper(): value for key, value in POSITION.items()}]
        body['OUTPUT2'] = [{key.upper(): value for key, value in TOTALS.items()}]
        self.assertTrue(self.adapter.balance(BALANCE, (replace(sample,
                            response=KisResponse(200, body, {'TR_CONT': 'E'})),)).valid)

    def test_buying_power_uses_no_receivables_fields_without_substitution(self):
        value = self.run_case(cases()[1]).value
        self.assertEqual(value.orderable_cash_krw, 700000)
        self.assertEqual(value.no_receivables_buy_amount_krw, 600000)
        self.assertEqual(value.maximum_buy_amount_krw, 1500000)
        self.assertEqual(value.no_receivables_buy_quantity, 60)
        self.assertEqual(value.maximum_buy_quantity, 150)
        for field in POWER:
            row = dict(POWER); del row[field]
            self.invalid(self.adapter.buying_power(BUYING_POWER, read(prepare_buying_power_query(BUYING_POWER), {'output': row})))

    def test_buying_power_modes_and_real_demo_ids(self):
        with self.assertRaisesRegex(Blocked, 'KIS_BUYING_POWER_MODE_UNVERIFIED'):
            prepare_buying_power_query(replace(BUYING_POWER, inquiry_order_code='00'))
        self.assertEqual(prepare_buying_power_query(BUYING_POWER).tr_id, 'TTTC8908R')
        self.assertEqual(prepare_buying_power_query(replace(BUYING_POWER,
            binding=replace(BINDING, environment='demo'))).tr_id, 'VTTC8908R')
        params = prepare_buying_power_query(BUYING_POWER).wire_parameters()
        self.assertEqual(params['CMA_EVLU_AMT_ICLD_YN'], 'N')
        self.assertEqual(params['OVRS_ICLD_YN'], 'N')
        self.assertEqual(params['ORD_DVSN'], '01')

    def test_sellable_not_invented_from_holdings_and_terminal_unverified(self):
        value = self.run_case(cases()[2]).value
        self.assertEqual(value.reported_sellable_quantity, 7)
        self.assertEqual(value.balance_quantity, 10)
        self.assertFalse(value.traversal_complete)
        with self.assertRaisesRegex(Blocked, 'KIS_SELLABLE_DEMO_UNVERIFIED'):
            prepare_sellable_query(replace(SELLABLE, binding=replace(BINDING, environment='demo')))

    def test_sellable_wrong_ticker_multiple_rows_and_pagination_block(self):
        for output in ({**SELL, 'pdno': '000660'}, {**SELL, 'ord_psbl_qty': '11'}, [SELL, SELL], []):
            self.invalid(self.adapter.sellable(SELLABLE, read(prepare_sellable_query(SELLABLE), {'output': output})))
        for marker in ('M', 'F', '', None, 'UNKNOWN'):
            self.invalid(self.adapter.sellable(SELLABLE, read(prepare_sellable_query(SELLABLE), {'output': SELL}, marker=marker)))

    def test_quote_has_no_exchange_timestamp_or_executability(self):
        value = self.run_case(cases()[3]).value
        self.assertEqual(value.current_price_krw, 10100)
        self.assertEqual(value.requested_venue, 'KRX')
        self.assertFalse(value.executable)
        self.assertIsNone(value.exchange_timestamp)
        self.assertEqual(value.instrument_status_code, 'SYNTHETIC')

    def test_requested_venues_are_separate_no_integrated_fallback(self):
        for venue, code in (('KRX', 'J'), ('NXT', 'NX')):
            query = replace(MARKET, requested_venue=venue)
            for book in (False, True):
                self.assertEqual(prepare_market_query(query, book=book).wire_parameters()['FID_COND_MRKT_DIV_CODE'], code)
        for venue in ('UN', 'SOR', '', None):
            with self.assertRaises(Blocked):
                prepare_market_query(replace(MARKET, requested_venue=venue))

    def test_wrong_quote_identity_and_missing_flags_fail_closed(self):
        for output in ({**PRICE, 'stck_shrn_iscd': '000660'}, {**PRICE, 'temp_stop_yn': ''},
                       {**PRICE, 'iscd_stat_cls_code': ''}, {**PRICE, 'stck_prpr': 'NaN'}):
            self.invalid(self.adapter.price(MARKET, read(prepare_market_query(MARKET), {'output': output})))

    def test_halted_or_unknown_status_never_becomes_executable(self):
        for flag in ('Y', 'N'):
            value = self.adapter.price(MARKET, read(prepare_market_query(MARKET),
                {'output': {**PRICE, 'temp_stop_yn': flag, 'iscd_stat_cls_code': '999'}})).value
            self.assertFalse(value.executable)

    def test_session_keeps_raw_paths_codes_and_time_without_inference(self):
        value = self.run_case(cases()[4]).value
        self.assertFalse(value.session_permitted)
        fields = {(item.object_path, item.name): item.raw_value for item in value.fields}
        self.assertEqual(fields[('output1', 'aspr_acpt_hour')], '095958')
        self.assertEqual(fields[('output1', 'new_mkop_cls_code')], 'SYNTHETIC_UNKNOWN')
        self.assertEqual(fields[('output2', 'antc_mkop_cls_code')], 'SYNTHETIC_UNKNOWN')
        self.assertFalse(hasattr(value, 'opens_at'))

    def test_session_accepts_either_observed_field_path_without_guessing(self):
        body = deepcopy(BOOK)
        body['output2']['new_mkop_cls_code'] = body['output1'].pop('new_mkop_cls_code')
        value = self.adapter.session(MARKET, read(prepare_market_query(MARKET, book=True), body)).value
        self.assertTrue(any(f.object_path == 'output2' and f.name == 'new_mkop_cls_code' for f in value.fields))
        self.assertFalse(value.session_permitted)

    def test_session_missing_duplicate_unknown_shape_or_wrong_symbol_blocks(self):
        changes = []
        body = deepcopy(BOOK); del body['output1']['new_mkop_cls_code']; changes.append(body)
        body = deepcopy(BOOK); body['output2']['new_mkop_cls_code'] = '20'; changes.append(body)
        body = deepcopy(BOOK); body['output2'] = []; changes.append(body)
        body = deepcopy(BOOK); body['output2']['stck_shrn_iscd'] = '000660'; changes.append(body)
        for body in changes:
            self.invalid(self.adapter.session(MARKET, read(prepare_market_query(MARKET, book=True), body)))

    def test_holiday_day_flags_are_separate_never_session_or_terminal(self):
        body = {'output': [{**CALENDAR, 'bzdy_yn': 'Y', 'tr_day_yn': 'N', 'opnd_yn': 'N', 'sttl_day_yn': 'Y'}]}
        value = self.adapter.holidays(HOLIDAY, (read(prepare_holiday_query(HOLIDAY), body),)).value
        self.assertTrue(value.target.business_day)
        self.assertFalse(value.target.trading_day)
        self.assertFalse(value.target.opening_day)
        self.assertTrue(value.target.settlement_day)
        self.assertFalse(value.traversal_complete)
        self.assertFalse(value.session_permitted)

    def test_holiday_uses_explicit_rows_not_weekday_or_inferred_dates(self):
        for rows in ([], [{**CALENDAR, 'bass_dt': '20261106'}], [CALENDAR, CALENDAR],
                     [{**CALENDAR, 'bass_dt': '20260230'}], [{**CALENDAR, 'opnd_yn': None}]):
            self.invalid(self.adapter.holidays(HOLIDAY, (read(prepare_holiday_query(HOLIDAY), {'output': rows}),)))

    def test_holiday_cursor_order_stability_and_missing_continuation(self):
        first = read(prepare_holiday_query(HOLIDAY), {'output': [CALENDAR], 'ctx_area_fk': 'FK', 'ctx_area_nk': 'NK'},
                     marker='F', started_at=NOW-timedelta(seconds=4), received_at=NOW-timedelta(seconds=3))
        recipe = prepare_holiday_query(HOLIDAY, cursor=('FK', 'NK'))
        second = read(recipe, {'output': [{**CALENDAR, 'bass_dt': '20261106'}]})
        value = self.adapter.holidays(HOLIDAY, (first, second)).value
        self.assertEqual(len(value.days), 2)
        self.assertEqual(recipe.wire_parameters()['BASS_DT'], '20261105')
        self.assertEqual(recipe.wire_parameters()['CTX_AREA_FK'], 'FK')
        self.assertEqual(recipe.wire_parameters()['CTX_AREA_NK'], 'NK')
        self.assertFalse(value.traversal_complete)
        self.invalid(self.adapter.holidays(HOLIDAY, (first,)), 'KIS_PAGES_TRUNCATED')
        self.invalid(self.adapter.holidays(HOLIDAY, (replace(second, response=replace(second.response, headers={})),)))

    def test_outputs_immutable_and_raw_payload_mutation_does_not_change_them(self):
        sample = balance_read()
        value = self.adapter.balance(BALANCE, (sample,)).value
        sample.response.body['output1'][0]['hldg_qty'] = '999'
        self.assertEqual(value.positions[0].held_quantity, 10)
        with self.assertRaises(AttributeError):
            value.positions[0].held_quantity = 999

    def test_single_object_endpoints_reject_unexpected_continuation(self):
        for case in (cases()[1], cases()[3], cases()[4]):
            for marker in ('M', 'F', 'UNKNOWN', None):
                original = case.reads[0]
                modified = replace(original, response=replace(original.response, headers={'tr_cont': marker}))
                self.invalid(self.run_case(replace(case, reads=(modified,))), 'KIS_UNEXPECTED_CONTINUATION')

    def test_nested_case_collisions_rejected(self):
        sample = balance_read(positions=[{**POSITION, 'PDNO': POSITION['pdno']}])
        self.invalid(self.adapter.balance(BALANCE, (sample,)), 'KIS_AMBIGUOUS_FIELD')
        sample = read(prepare_market_query(MARKET), {'output': {**PRICE, 'STCK_PRPR': PRICE['stck_prpr']}})
        self.invalid(self.adapter.price(MARKET, sample), 'KIS_AMBIGUOUS_FIELD')

    def test_exact_age_boundary_and_equivalent_timezones(self):
        sample = balance_read(started_at=NOW-timedelta(seconds=10), received_at=NOW)
        value = self.adapter.balance(BALANCE, (sample,)).value
        self.assertIsNotNone(value)
        kst_sample = replace(sample, started_at=sample.started_at.astimezone(KST), received_at=NOW.astimezone(KST))
        self.assertEqual(self.adapter.balance(BALANCE, (kst_sample,)).value, value)
        old = replace(sample, started_at=sample.started_at-timedelta(microseconds=1))
        self.invalid(self.adapter.balance(BALANCE, (old,)), 'KIS_CAPTURE_STALE_OR_INVALID')

    def test_untrusted_exception_text_is_not_exposed(self):
        class BadMapping(dict):
            def items(self):
                raise Blocked('PRIVATE_ACCOUNT_OR_CREDENTIAL')
        sample = balance_read()
        sample = replace(sample, response=replace(sample.response, body=BadMapping()))
        result = self.adapter.balance(BALANCE, (sample,))
        self.invalid(result, 'KIS_OFFLINE_PAYLOAD_INVALID')
        self.assertNotIn('PRIVATE_ACCOUNT', repr(result))


if __name__ == '__main__':
    unittest.main()

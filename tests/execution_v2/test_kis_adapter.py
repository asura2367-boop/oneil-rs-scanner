"""Synthetic fixtures only. No credentials, KIS connection, or database required."""
import ast
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from oneil_execution_v2.domain import Blocked, Order, SessionPermit
from oneil_execution_v2.kis_adapter import (
    AccountBinding, CashAcknowledgement, HistoryQuery, KisReadOnlyAdapter,
    KisResponse, NativeOrderId, find_history_order, normalize_history_row,
    normalize_submission, prepare_cash_order, prepare_history_query, unknown_submission,
)
from oneil_execution_v2.worker import Worker

NOW = datetime(2026, 11, 5, 1, 0, tzinfo=timezone.utc)
ACCOUNT = 'a' * 64


def row(**changes):
    result = {
        'ord_dt': '20261105', 'ord_gno_brno': '00001', 'odno': '0000000001',
        'orgn_odno': '0000000000', 'pdno': '005930', 'sll_buy_dvsn_cd': '02',
        'excg_id_dvsn_Cd': 'KRX', 'ord_dvsn_cd': '00', 'ord_qty': '2',
        'ord_unpr': '10000', 'tot_ccld_qty': '0', 'rmn_qty': '2',
        'cnc_cfrm_qty': '0', 'rjct_qty': '0', 'cncl_yn': 'N',
    }
    result.update(changes)
    return result


def page(rows=None, *, marker='D', fk='', nk='', status=200, rt_cd='0'):
    return KisResponse(status, {'rt_cd': rt_cd, 'output1': [] if rows is None else rows,
                                'ctx_area_fk100': fk, 'ctx_area_nk100': nk}, {'tr_cont': marker})


class KisAdapterTests(unittest.TestCase):
    def setUp(self):
        self.binding = AccountBinding(ACCOUNT, 'demo', '12345678', '01')
        self.order = Order(ACCOUNT, '005930', 'BUY', 'KRX', 2, 10000,
                           NOW + timedelta(minutes=1), 'v1')
        self.permit = SessionPermit('KRX', '2026-11-05', NOW-timedelta(minutes=1),
                                    NOW+timedelta(minutes=1), '00', 'fixture-only', NOW)
        self.query = HistoryQuery(self.binding, '2026-11-05')
        self.transport = Mock(spec=['read_history'])
        self.adapter = KisReadOnlyAdapter(self.transport, lambda: NOW)

    def prepared(self, **changes):
        return prepare_cash_order(self.binding, 'request_123', replace(self.order, **changes), self.permit, NOW)

    def test_request_ids_and_string_body_match_official_cash_sample(self):
        for env, prefix in [('demo', 'VTTC'), ('real', 'TTTC')]:
            for side, suffix in [('BUY', '0012U'), ('SELL', '0011U')]:
                with self.subTest(env=env, side=side):
                    prepared = prepare_cash_order(replace(self.binding, environment=env), 'request_123',
                                                  replace(self.order, side=side), self.permit, NOW)
                    recipe = prepared.recipe; params = recipe.wire_parameters()
                    self.assertEqual(recipe.tr_id, prefix+suffix)
                    self.assertEqual(recipe.method, 'POST')
                    self.assertTrue(all(type(x) is str for x in params.values()))
                    self.assertEqual(params['ORD_DVSN'], '00')
                    self.assertEqual(params['SLL_TYPE'], '01' if side == 'SELL' else '')
                    self.assertEqual(params['EXCG_ID_DVSN_CD'], 'KRX')
                    self.assertNotIn('request_123', params.values())
                    self.assertFalse(self.transport.read_history.called)

    def test_recipes_are_immutable_and_parameter_copies_are_independent(self):
        prepared = self.prepared(); copy = prepared.recipe.wire_parameters()
        copy['ORD_QTY'] = '999'
        self.assertEqual(prepared.recipe.wire_parameters()['ORD_QTY'], '2')
        with self.assertRaises(AttributeError):
            prepared.recipe.tr_id = 'bad'

    def test_sensitive_request_parameters_are_not_in_repr(self):
        for value in (self.binding, self.query, self.prepared(), prepare_history_query(self.query)):
            self.assertNotIn('12345678', repr(value))
        self.assertNotIn('private value', repr(KisResponse(200, {'secret': 'private value'})))

    def test_account_mismatch_blocked(self):
        with self.assertRaisesRegex(Blocked, 'KIS_ACCOUNT_MISMATCH'):
            self.prepared(account='b'*64)

    def test_invalid_binding_blocked(self):
        for update in ({'cano': '1234'}, {'environment': 'paper'}, {'product_code': '1'}, {'account': 'bad'}):
            with self.subTest(update=update), self.assertRaises(Blocked):
                prepare_cash_order(replace(self.binding, **update), 'r', self.order, self.permit, NOW)

    def test_nxt_does_not_fall_back_to_krx(self):
        with self.assertRaisesRegex(Blocked, 'KIS_VENUE_NOT_VERIFIED'):
            prepare_cash_order(self.binding, 'r', replace(self.order, venue='NXT'),
                               replace(self.permit, venue='NXT'), NOW)
        with self.assertRaisesRegex(Blocked, 'KIS_VENUE_NOT_VERIFIED'):
            prepare_history_query(replace(self.query, venue='NXT'))

    def test_unsupported_order_code_blocked(self):
        with self.assertRaisesRegex(Blocked, 'KIS_ORDER_CODE_NOT_VERIFIED'):
            prepare_cash_order(self.binding, 'r', self.order, replace(self.permit, order_code='01'), NOW)

    def test_expiry_and_unverified_session_blocked(self):
        with self.assertRaisesRegex(Blocked, 'REQUEST_EXPIRED'):
            self.prepared(expires_at=NOW)
        with self.assertRaisesRegex(Blocked, 'OUTSIDE_VERIFIED_SESSION'):
            prepare_cash_order(self.binding, 'r', self.order, replace(self.permit, closes_at=NOW), NOW)

    def ack(self, **output):
        base = {'KRX_FWDG_ORD_ORGNO': '00001', 'ODNO': '0000000001', 'ORD_TMD': '100000'}
        base.update(output)
        return KisResponse(200, {'rt_cd': '0', 'output': base})

    def test_success_response_is_ack_not_fill_and_preserves_forwarding_org(self):
        result = normalize_submission(self.prepared(), self.ack())
        self.assertEqual(result.state, 'ACKNOWLEDGED')
        self.assertIsInstance(result.acknowledgement, CashAcknowledgement)
        self.assertEqual(result.acknowledgement.forwarding_organization, '00001')
        self.assertEqual(result.acknowledgement.order_number, '0000000001')
        self.assertEqual(result.acknowledgement.trading_day_context, '2026-11-05')
        self.assertFalse(hasattr(result.acknowledgement, 'native_id'))
        self.assertFalse(hasattr(result.acknowledgement, 'filled_quantity'))

    def test_submission_http_and_api_errors_stay_unknown(self):
        for response in (KisResponse(500, {}), KisResponse(429, {}), KisResponse(401, {}),
                         KisResponse(200, {'rt_cd': '1', 'msg_cd': 'UNKNOWN_CODE'}),
                         KisResponse(200, {'rt_cd': 0, 'output': {}})):
            with self.subTest(response=response):
                result = normalize_submission(self.prepared(), response)
                self.assertEqual(result.state, 'SUBMISSION_UNKNOWN')
                self.assertIsNone(result.acknowledgement)

    def test_missing_malformed_or_ambiguous_ack_stays_unknown(self):
        for response in (KisResponse(200, None), KisResponse(200, []),
                         self.ack(ODNO='0'), self.ack(ODNO=1), self.ack(ORD_TMD='246000'),
                         self.ack(KRX_FWDG_ORD_ORGNO=''), self.ack(odno='different')):
            with self.subTest(response=response):
                self.assertEqual(normalize_submission(self.prepared(), response).state, 'SUBMISSION_UNKNOWN')

    def test_prepared_recipe_content_mismatch_cannot_bind_ack(self):
        prepared = self.prepared()
        changed = replace(prepared, order=replace(prepared.order, quantity=20))
        self.assertEqual(normalize_submission(changed, self.ack()).state, 'SUBMISSION_UNKNOWN')

    def test_tampered_account_product_or_sell_type_cannot_bind_ack(self):
        prepared = self.prepared()
        changed_account = replace(prepared, order=replace(prepared.order, account='b'*64))
        self.assertEqual(normalize_submission(changed_account, self.ack()).state, 'SUBMISSION_UNKNOWN')
        for key, value in [('CANO','87654321'), ('ACNT_PRDT_CD','02'), ('SLL_TYPE','05'),
                           ('CNDT_PRIC','10000'), ('client_id','request_123')]:
            with self.subTest(key=key):
                params = prepared.recipe.wire_parameters(); params[key] = value
                changed = replace(prepared, recipe=replace(prepared.recipe, parameters=tuple(params.items())))
                self.assertEqual(normalize_submission(changed, self.ack()).state, 'SUBMISSION_UNKNOWN')

    def test_transport_validation_exception_is_also_redacted(self):
        self.transport.read_history.side_effect = Blocked('secret account 12345678')
        result = self.adapter.history(self.query)
        self.assertFalse(result.complete)
        self.assertEqual(result.reason, 'KIS_HISTORY_TRANSPORT_OR_PARSE_ERROR')
        self.assertNotIn('12345678', repr(result))
        self.transport.read_history.assert_called_once()

    def test_uncertain_transport_never_yields_rejection_or_retry(self):
        self.assertEqual(unknown_submission().state, 'SUBMISSION_UNKNOWN')
        self.assertFalse(self.transport.read_history.called)
        self.assertFalse(hasattr(self.adapter, 'retry_submission'))

    def test_read_only_adapter_submit_is_closed(self):
        with self.assertRaisesRegex(Blocked, 'KIS_LIVE_SUBMISSION_NOT_IMPLEMENTED'):
            self.adapter.submit('r', self.order, self.permit)
        self.assertFalse(self.transport.read_history.called)

    def test_worker_still_refuses_kis_adapter(self):
        with self.assertRaisesRegex(Blocked, 'LIVE_BROKER_NOT_IMPLEMENTED'):
            Worker(None, self.adapter)

    def test_history_query_scope_and_ids(self):
        for environment, tr_id in [('demo', 'VTTC0081R'), ('real', 'TTTC0081R')]:
            query = replace(self.query, binding=replace(self.binding, environment=environment))
            request = prepare_history_query(query); params = request.wire_parameters()
            self.assertEqual(request.tr_id, tr_id)
            self.assertEqual(request.method, 'GET')
            self.assertEqual(params['INQR_STRT_DT'], '20261105')
            self.assertEqual(params['INQR_END_DT'], '20261105')
            self.assertEqual(params['CCLD_DVSN'], '00')
            self.assertEqual(params['SLL_BUY_DVSN_CD'], '00')
            self.assertEqual(params['INQR_DVSN_3'], '00')
            self.assertEqual(params['PDNO'], '')

    def test_pagination_preserves_every_filter_and_uses_both_tokens(self):
        self.transport.read_history.side_effect = [page([row()], marker='M', fk='a', nk='b'),
                                                   page([row(odno='0000000002')], marker='E')]
        history = self.adapter.history(self.query)
        self.assertTrue(history.complete); self.assertEqual(len(history.orders), 2)
        first, second = [x.args[0] for x in self.transport.read_history.call_args_list]
        expected = first.wire_parameters(); expected.update(CTX_AREA_FK100='a', CTX_AREA_NK100='b')
        self.assertEqual(second.wire_parameters(), expected)
        self.assertEqual(second.tr_cont, 'N')
        self.assertEqual(history.observed_at, NOW)

    def test_f_marker_is_not_terminal(self):
        self.transport.read_history.side_effect = [page([], marker='F', fk='a', nk='b'), page([])]
        self.assertTrue(self.adapter.history(self.query).complete)
        self.assertEqual(self.transport.read_history.call_count, 2)

    def test_empty_terminal_history_does_not_prove_no_submission(self):
        self.transport.read_history.return_value = page([])
        history = self.adapter.history(self.query)
        native = NativeOrderId(ACCOUNT, 'demo', '2026-11-05', 'KRX', '00001', '0000000001')
        self.assertTrue(history.complete)
        self.assertIsNone(find_history_order(native, history))
        self.assertEqual(unknown_submission().state, 'SUBMISSION_UNKNOWN')

    def test_missing_unknown_or_blank_continuation_is_incomplete(self):
        for marker in (None, '', 'garbage', 'd'):
            with self.subTest(marker=marker):
                self.transport.read_history.return_value = page([row()], marker=marker)
                history = self.adapter.history(self.query)
                self.assertFalse(history.complete)
                self.assertEqual(history.reason, 'KIS_HISTORY_CONTINUATION_UNVERIFIED')

    def test_repeated_cursor_is_incomplete_without_looping(self):
        self.transport.read_history.side_effect = [page([row()], marker='M', fk='a', nk='b'),
                                                   page([], marker='M', fk=' a ', nk=' b ')]
        history = self.adapter.history(self.query)
        self.assertFalse(history.complete); self.assertEqual(history.reason, 'KIS_HISTORY_CURSOR_LOOP')
        self.assertEqual(self.transport.read_history.call_count, 2)

    def test_empty_or_malformed_next_cursor_is_incomplete(self):
        for fk, nk in [('', ''), (' ', ' '), (None, 'a'), ('a'*101, ''), ('a\n', 'b')]:
            with self.subTest(cursor=(fk, nk)):
                self.transport.read_history.return_value = page([row()], marker='M', fk=fk, nk=nk)
                self.assertFalse(self.adapter.history(self.query).complete)

    def test_page_limit_never_returns_complete(self):
        self.transport.read_history.return_value = page([row()], marker='M', fk='a', nk='b')
        result = KisReadOnlyAdapter(self.transport, lambda: NOW, max_pages=1).history(self.query)
        self.assertFalse(result.complete); self.assertEqual(result.reason, 'KIS_HISTORY_PAGE_LIMIT')

    def test_row_and_broker_page_limits_never_return_complete(self):
        self.transport.read_history.return_value = page([row(), row(odno='2')])
        result = KisReadOnlyAdapter(self.transport, lambda: NOW, max_rows=1).history(self.query)
        self.assertEqual(result.reason, 'KIS_HISTORY_ROW_LIMIT')
        self.transport.read_history.return_value = page([row(odno=str(x)) for x in range(1,17)])
        self.assertEqual(self.adapter.history(self.query).reason, 'KIS_HISTORY_PAGE_SIZE_UNEXPECTED')

    def test_duplicate_native_ids_are_incomplete_even_when_identical(self):
        self.transport.read_history.return_value = page([row(), row()])
        result = self.adapter.history(self.query)
        self.assertFalse(result.complete); self.assertEqual(result.orders, ())
        self.transport.read_history.side_effect = [page([row()], marker='M', fk='a', nk='b'), page([row()])]
        self.assertFalse(self.adapter.history(self.query).complete)

    def test_http_api_parse_and_transport_errors_are_incomplete_without_retry(self):
        for response in (page(status=503), page(rt_cd='1'), KisResponse(200, []),
                         KisResponse(200, {'rt_cd': '0', 'output1': {}})):
            with self.subTest(response=response):
                transport = Mock(spec=['read_history']); transport.read_history.return_value = response
                self.assertFalse(KisReadOnlyAdapter(transport, lambda: NOW).history(self.query).complete)
                transport.read_history.assert_called_once()
        self.transport.read_history.side_effect = TimeoutError('secret account 12345678')
        result = self.adapter.history(self.query)
        self.assertFalse(result.complete); self.assertNotIn('12345678', repr(result))
        self.transport.read_history.assert_called_once()

    def test_missing_venue_or_wrong_day_is_not_inferred_from_query(self):
        for value in (row(excg_id_dvsn_Cd=None), row(excg_id_dvsn_Cd='NXT'), row(ord_dt='20261104')):
            with self.subTest(value=value), self.assertRaises(Blocked):
                normalize_history_row(value, self.query)

    def test_case_normalization_accepts_known_mixed_case_but_rejects_collisions(self):
        self.assertEqual(normalize_history_row(row(), self.query).venue, 'KRX')
        with self.assertRaisesRegex(Blocked, 'KIS_AMBIGUOUS_FIELD'):
            normalize_history_row(row(ODNO='2'), self.query)

    def test_numeric_strings_are_exact_and_never_rounded(self):
        observed = normalize_history_row(row(ord_unpr='10000.0000'), self.query)
        self.assertEqual(observed.limit_price, 10000)
        for bad in ('1.5', '1e2', 'NaN', '-1', '+1', '1,000', ' 1', 1, 1.0, True, '１'):
            with self.subTest(bad=bad), self.assertRaises(Blocked):
                normalize_history_row(row(ord_qty=bad), self.query)

    def test_quantities_and_amendment_identity_preserved_without_status_guess(self):
        # No asserted conservation rule across revisions, no terminal promotion.
        observed = normalize_history_row(row(orgn_odno='0000000009', ord_qty='2',
                                             tot_ccld_qty='1', cnc_cfrm_qty='2', rmn_qty='0',
                                             cncl_yn='Y', rjct_qty='1'), self.query)
        self.assertEqual((observed.filled_quantity, observed.cancelled_quantity,
                          observed.rejected_quantity, observed.remaining_quantity), (1,2,1,0))
        self.assertEqual(observed.original_order_number, '0000000009')
        self.assertFalse(hasattr(observed, 'status'))
        self.assertFalse(hasattr(observed, 'request_id'))

    def test_history_identity_preserves_leading_zeros(self):
        observed = normalize_history_row(row(), self.query)
        self.assertEqual(observed.native_id.branch, '00001')
        self.assertEqual(observed.native_id.number, '0000000001')

    def test_partial_history_cannot_resolve_even_an_exact_native_id(self):
        self.transport.read_history.side_effect = [page([row()], marker='M', fk='a', nk='b'), TimeoutError()]
        result = self.adapter.history(self.query)
        self.assertFalse(result.complete)
        self.assertIsNone(find_history_order(result.orders[0].native_id, result))

    def test_exact_lookup_is_separate_from_ack_and_rejects_cross_scope(self):
        self.transport.read_history.return_value = page([row()]); result = self.adapter.history(self.query)
        native = result.orders[0].native_id
        self.assertEqual(find_history_order(native, result), result.orders[0])
        for update in ({'account':'b'*64}, {'environment':'real'}, {'trading_day':'2026-11-04'}):
            with self.subTest(update=update), self.assertRaisesRegex(Blocked, 'KIS_HISTORY_SCOPE_MISMATCH'):
                find_history_order(replace(native, **update), result)
        self.assertIsNone(find_history_order(replace(native, branch='1'), result))

    def test_out_of_range_or_future_dates_never_call_transport(self):
        for day in ('2025-01-01', '2026-11-06', '2026-02-30', '20261105'):
            with self.subTest(day=day), self.assertRaises(Blocked):
                self.adapter.history(replace(self.query, trading_day=day))
        self.assertFalse(self.transport.read_history.called)

    def test_local_date_is_kst_not_utc(self):
        now = datetime(2026,11,4,16,0,tzinfo=timezone.utc)
        self.transport.read_history.return_value = page([])
        self.assertTrue(KisReadOnlyAdapter(self.transport, lambda: now).history(self.query).complete)

    def test_no_network_imports_or_production_code_dependencies(self):
        source = Path('oneil_execution_v2/kis_adapter.py').read_text()
        modules = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import): modules.update(x.name.split('.')[0] for x in node.names)
            if isinstance(node, ast.ImportFrom): modules.add((node.module or '').split('.')[0])
        self.assertFalse(modules & {'socket','requests','httpx','urllib','kis_auth','subprocess','os'})
        with patch('socket.socket', side_effect=AssertionError('network prohibited')):
            self.transport.read_history.return_value = page([row()])
            self.assertTrue(self.adapter.history(self.query).complete)
            self.assertEqual(normalize_submission(self.prepared(), self.ack()).state, 'ACKNOWLEDGED')


if __name__ == '__main__': unittest.main()

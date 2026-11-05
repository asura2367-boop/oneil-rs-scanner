"""Synthetic authentication, not real KIS provenance or production readiness."""
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
import json
import unittest
from unittest.mock import patch

from oneil_execution_v2.domain import Blocked, Order
from oneil_execution_v2.kis_adapter import NativeOrderId, NativeHistory
from oneil_execution_v2.recovery import (Attempt, CONTRACT, FixtureEvidenceAuthority,
    FixtureReceipt, bind_identity, reconcile)

NOW=datetime(2026,11,5,1,tzinfo=timezone.utc)
ACCOUNT='a'*64


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.authority=FixtureEvidenceAuthority(); self.audience='offline-coordinator'
        self.order=Order(ACCOUNT,'005930','BUY','KRX',10,10000,NOW+timedelta(minutes=1),'fixture-v1')
        self.attempt=Attempt('request-1','attempt-1',self.order,'fixture','2026-11-05',NOW)
        self.native=NativeOrderId(ACCOUNT,'demo','2026-11-05','KRX','00001','000000001')
        self.binding={'identity':self.attempt.identity(),'native_id':asdict(self.native),
            'basis':'SYNTHETIC_EXACT_ATTEMPT_ASSOCIATION'}
        self.payload={'identity':self.attempt.identity(),'native_id':asdict(self.native),
            'sequence':1,'observed_at':NOW.isoformat(),'status':'ACKNOWLEDGED','filled_quantity':0,
            'traversal':'COMPLETE','semantics':CONTRACT}

    def seal(self,kind,value,**kw):
        return self.authority.seal(audience=self.audience,kind=kind,payload=value,now=NOW,**kw)

    def bound(self):
        return bind_identity(self.attempt,self.seal('native_binding',self.binding),self.authority,self.audience,NOW)

    def observe(self,attempt=None,**changes):
        return reconcile(attempt or self.bound(),self.seal('order_observation',dict(self.payload,**changes)),
            self.authority,self.audience,NOW)

    def test_ack_is_not_fill_and_partial_retains_unresolved(self):
        self.assertEqual(self.observe().state,'ACKNOWLEDGED')
        partial=self.observe(status='PART_FILLED',filled_quantity=3)
        self.assertEqual(partial.filled_quantity,3)
        full=self.observe(partial,sequence=2,status='FILLED',filled_quantity=10)
        self.assertEqual(full.state,'FILLED')

    def test_partial_cancel_retains_fill(self):
        partial=self.observe(status='PART_FILLED',filled_quantity=3)
        cancelled=self.observe(partial,sequence=2,status='CANCELLED',filled_quantity=3)
        self.assertEqual((cancelled.state,cancelled.filled_quantity),('CANCELLED',3))

    def test_missing_binding_never_infers_identity(self):
        with self.assertRaisesRegex(Blocked,'EXACT_NATIVE_BINDING'):
            self.observe(self.attempt)

    def test_raw_native_history_is_not_authenticated_evidence(self):
        history=NativeHistory(ACCOUNT,'demo','2026-11-05','KRX',NOW,True,(),'COMPLETE')
        with self.assertRaisesRegex(Blocked,'AUTHENTICATED_FIXTURE'):
            reconcile(self.bound(),history,self.authority,self.audience,NOW)

    def test_other_authority_cannot_authenticate(self):
        with self.assertRaisesRegex(Blocked,'AUTHENTICATION_FAILED'):
            bind_identity(self.attempt,self.seal('native_binding',self.binding),FixtureEvidenceAuthority(),self.audience,NOW)

    def test_signed_content_tampering_rejected(self):
        receipt=self.seal('native_binding',self.binding)
        value=json.loads(receipt.content); value['payload']['native_id']['number']='2'
        with self.assertRaisesRegex(Blocked,'AUTHENTICATION_FAILED'):
            bind_identity(self.attempt,replace(receipt,content=json.dumps(value)),self.authority,self.audience,NOW)

    def test_other_audience_and_kind_rejected(self):
        for audience,kind in [('other','native_binding'),(self.audience,'order_observation')]:
            receipt=self.authority.seal(audience=audience,kind=kind,payload=self.binding,now=NOW)
            with self.assertRaisesRegex(Blocked,'SCOPE_MISMATCH'):
                bind_identity(self.attempt,receipt,self.authority,self.audience,NOW)

    def test_exact_attempt_identity_required(self):
        for key,value in [('request_id','other'),('attempt_id','other'),('order_digest','b'*64),
                          ('account','b'*64),('environment','real'),('trading_day','2026-11-04')]:
            bad=dict(self.binding,identity=dict(self.binding['identity'],**{key:value}))
            with self.subTest(key=key),self.assertRaisesRegex(Blocked,'ATTEMPT_MISMATCH'):
                bind_identity(self.attempt,self.seal('native_binding',bad),self.authority,self.audience,NOW)

    def test_full_native_scope_and_leading_zeros_preserved(self):
        self.assertEqual(self.bound().native_id.number,'000000001')
        for key,value in [('account','b'*64),('environment','real'),('trading_day','2026-11-04'),('venue','NXT')]:
            bad=dict(self.binding,native_id=dict(asdict(self.native),**{key:value}))
            with self.subTest(key=key),self.assertRaises(Blocked):
                bind_identity(self.attempt,self.seal('native_binding',bad),self.authority,self.audience,NOW)
        with self.assertRaisesRegex(Blocked,'NATIVE_ID_MISMATCH'):
            self.observe(native_id=dict(asdict(self.native),number='1'))

    def test_ack_forwarding_or_shape_is_not_binding_basis(self):
        for basis in ['ACK_FORWARDING_ORGANIZATION','SAME_SYMBOL_QTY_TIME','']:
            with self.assertRaisesRegex(Blocked,'IDENTITY_UNPROVEN'):
                bind_identity(self.attempt,self.seal('native_binding',dict(self.binding,basis=basis)),
                              self.authority,self.audience,NOW)

    def test_stale_future_and_expired_receipts_block(self):
        receipt=self.seal('native_binding',self.binding)
        for now in [NOW-timedelta(seconds=1),NOW+timedelta(seconds=10)]:
            with self.assertRaisesRegex(Blocked,'EVIDENCE_STALE'):
                bind_identity(self.attempt,receipt,self.authority,self.audience,now)

    def test_old_future_or_pre_attempt_observation_blocks(self):
        for stamp in [NOW-timedelta(seconds=1),NOW+timedelta(seconds=1)]:
            with self.assertRaisesRegex(Blocked,'OBSERVATION_STALE'):
                self.observe(observed_at=stamp.isoformat())

    def test_complete_traversal_and_semantics_required(self):
        for changes in [dict(traversal='PARTIAL'),dict(semantics='KIS_UNVERIFIED'),dict(status='SUBMISSION_UNKNOWN')]:
            with self.assertRaises(Blocked): self.observe(**changes)

    def test_monotonic_sequence_and_exact_duplicate(self):
        first=self.observe(sequence=5)
        self.assertEqual(self.observe(first,sequence=5),first)
        with self.assertRaisesRegex(Blocked,'SEQUENCE_REGRESSION'): self.observe(first,sequence=4)
        with self.assertRaisesRegex(Blocked,'SEQUENCE_CONFLICT'):
            self.observe(first,sequence=5,status='PART_FILLED',filled_quantity=1)

    def test_fill_and_state_regression_blocked(self):
        partial=self.observe(status='PART_FILLED',filled_quantity=5)
        for state,filled in [('PART_FILLED',4),('ACKNOWLEDGED',0),('REJECTED',0)]:
            with self.assertRaises(Blocked): self.observe(partial,sequence=2,status=state,filled_quantity=filled)

    def test_terminal_corrections_require_review(self):
        cancelled=self.observe(status='CANCELLED',filled_quantity=3)
        for status,filled in [('CANCELLED',4),('FILLED',10),('PART_FILLED',3),('REJECTED',0)]:
            with self.assertRaises(Blocked): self.observe(cancelled,sequence=2,status=status,filled_quantity=filled)
        self.assertEqual(self.observe(cancelled,sequence=2,status='CANCELLED',filled_quantity=3).filled_quantity,3)

    def test_invalid_quantities_and_status_never_coerced(self):
        for value in [True,-1,11,3.5,'3']:
            with self.assertRaises(Blocked): self.observe(status='PART_FILLED',filled_quantity=value)
        for status,filled in [('FILLED',9),('ACKNOWLEDGED',1),('REJECTED',1),('PART_FILLED',0),('CANCELLED',10)]:
            with self.assertRaises(Blocked): self.observe(status=status,filled_quantity=filled)

    def test_identity_binding_is_immutable(self):
        with self.assertRaisesRegex(Blocked,'IDENTITY_IMMUTABLE'):
            bind_identity(self.bound(),self.seal('native_binding',dict(self.binding,
                native_id=dict(asdict(self.native),number='2'))),self.authority,self.audience,NOW)

    def test_real_mode_and_live_authority_not_supported(self):
        with self.assertRaisesRegex(Blocked,'OFFLINE_RECOVERY_ONLY'): replace(self.attempt,environment='real').validate()
        with self.assertRaisesRegex(Blocked,'OFFLINE_FIXTURE_AUTHORITY'):
            bind_identity(self.attempt,None,object(),self.audience,NOW)

    def test_no_sockets_or_live_code_needed(self):
        with patch('socket.socket',side_effect=AssertionError('NETWORK_FORBIDDEN')):
            self.assertEqual(self.observe(status='FILLED',filled_quantity=10).state,'FILLED')

if __name__=='__main__': unittest.main()

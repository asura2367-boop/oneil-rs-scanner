from copy import deepcopy
from datetime import timedelta
import json
import unittest
from datetime import datetime, timezone
from support import CLOCK as EPOCH, FakeStore, ACCOUNT
CLOCK=datetime.fromtimestamp(EPOCH,timezone.utc)
SCENARIOS=('unknown','pending','stale_worker','stale_broker','missing_health')
def sample(scenario='unknown'):
    raw=FakeStore('authenticated' if scenario=='pending' else scenario).read_model(ACCOUNT)
    if scenario=='missing_health':
        raw['health']=None;raw['broker_reconciled_at']=None
    return raw
from oneil_monitor_v2.read_model import SnapshotUnavailable, project_status, json_bytes

class ProjectionTests(unittest.TestCase):
    def project(self, raw=None, now=CLOCK):
        return project_status(raw or sample(), now=now, broker_max_age_seconds=10)

    def test_all_scenarios_are_json_serializable(self):
        for name in SCENARIOS:
            model=self.project(sample(name))
            self.assertEqual(json.loads(json_bytes(model))['mode'],'SIMULATION')
            self.assertFalse(model['live_capable'])
    def test_input_not_mutated(self):
        raw=sample();before=deepcopy(raw);self.project(raw);self.assertEqual(raw,before)
    def test_uuid_and_datetime_serialized(self):
        model=self.project();self.assertEqual(model['requests'][0]['request_id'],'00000000-0000-0000-0000-000000000001');self.assertTrue(model['server_time'].endswith('Z'))
    def test_no_sensitive_or_unrecognized_fields(self):
        raw=sample();raw['secret']='SHOULD_NOT_LEAK';raw['health']['credentials']='SHOULD_NOT_LEAK';raw['requests'][0]['payload']={'secret':'SHOULD_NOT_LEAK'}
        output=json_bytes(self.project(raw));self.assertNotIn(b'SHOULD_NOT_LEAK',output);self.assertNotIn(b'not-for-browser',output)
    def test_heartbeat_not_broker_connectivity(self):
        model=self.project(sample('stale_broker'));self.assertEqual(model['heartbeat']['status'],'fresh');self.assertEqual(model['broker_sync']['status'],'stale')
    def test_recompute_worker_alive(self):
        model=self.project(sample('stale_worker'));self.assertEqual(model['heartbeat']['status'],'stale')
    def test_missing_evidence_not_green(self):
        model=self.project(sample('missing_health'));self.assertEqual(model['heartbeat']['status'],'missing');self.assertEqual(model['broker_sync']['status'],'missing')
    def test_future_evidence_not_green(self):
        raw=sample();raw['health']['heartbeat_at']=CLOCK+timedelta(seconds=1);self.assertEqual(self.project(raw)['heartbeat']['status'],'clock_error')
    def test_stale_snapshot_marked(self):
        self.assertEqual(self.project(now=CLOCK+timedelta(seconds=16))['snapshot']['status'],'stale')
    def test_boundaries_inclusive(self):
        raw=sample();raw['health']['heartbeat_at']=CLOCK-timedelta(seconds=15);raw['broker_reconciled_at']=CLOCK-timedelta(seconds=10)
        self.assertEqual(self.project(raw)['heartbeat']['status'],'fresh');self.assertEqual(self.project(raw)['broker_sync']['status'],'fresh')
    def test_ack_not_filled(self):
        row=self.project()['requests'][2];self.assertEqual(row['state'],'ACKNOWLEDGED');self.assertEqual(row['group'],'pending');self.assertEqual(row['filled_quantity'],0)
    def test_unknown_never_becomes_rejected_on_age(self):
        row=self.project(now=CLOCK+timedelta(days=5))['requests'][0];self.assertEqual(row['state'],'SUBMISSION_UNKNOWN');self.assertFalse(row['can_cancel_before_admission'])
    def test_future_state_is_unknown(self):
        raw=sample();raw['requests'][0]['state']='NEW_UNRECOGNIZED_STATE';row=self.project(raw)['requests'][0];self.assertEqual(row['group'],'unknown')
    def test_pre_admission_cancel_only(self):
        rows=self.project()['requests'];self.assertTrue(rows[1]['can_cancel_before_admission']);self.assertFalse(rows[0]['can_cancel_before_admission']);self.assertFalse(rows[2]['can_cancel_before_admission'])
    def test_controls_never_enabled(self):
        for name in SCENARIOS:self.assertTrue(all(not control['enabled'] for control in self.project(sample(name))['controls'].values()))
    def test_request_window_not_total(self):
        window=self.project()['request_window'];self.assertFalse(window['account_total_known']);self.assertEqual(window['scope'],'LATEST_REQUESTS_ONLY')
    def test_no_order_detail_invention(self):
        row=self.project()['requests'][0];self.assertNotIn('quantity',row);self.assertNotIn('ticker',row);self.assertNotIn('fill_percent',row)
    def test_live_mode_rejected(self):
        for key,value in [('mode','LIVE'),('live_capable',True)]:
            raw=sample();raw[key]=value
            with self.assertRaises(SnapshotUnavailable):self.project(raw)
    def test_naive_timestamp_rejected(self):
        raw=sample();raw['as_of']=CLOCK.replace(tzinfo=None)
        with self.assertRaises(SnapshotUnavailable):self.project(raw)
    def test_invalid_revision_rejected(self):
        for value in [True,0,'7',None]:
            raw=sample();raw['control_revision']=value
            with self.assertRaises(SnapshotUnavailable):self.project(raw)
    def test_no_freshness_defaults(self):
        with self.assertRaises(TypeError):project_status(sample(),now=CLOCK)
    def test_duplicate_id_rejected(self):
        raw=sample();raw['requests'][1]['request_id']=raw['requests'][0]['request_id']
        with self.assertRaises(SnapshotUnavailable):self.project(raw)
    def test_malformed_quantity_rejected(self):
        for value in [-1,True,'2',2.5]:
            raw=sample();raw['requests'][0]['filled_quantity']=value
            with self.assertRaises(SnapshotUnavailable):self.project(raw)
    def test_ack_fill_contradiction_rejected(self):
        raw=sample();raw['requests'][2]['filled_quantity']=3
        with self.assertRaises(SnapshotUnavailable):self.project(raw)
    def test_filled_without_fill_rejected(self):
        raw=sample();raw['requests'][4]['filled_quantity']=0
        with self.assertRaises(SnapshotUnavailable):self.project(raw)
    def test_attempt_contradiction_rejected(self):
        raw=sample();raw['requests'][0]['attempts']=0
        with self.assertRaises(SnapshotUnavailable):self.project(raw)
    def test_invalid_chronology_rejected(self):
        raw=sample();raw['requests'][0]['updated_at']=CLOCK+timedelta(seconds=1)
        with self.assertRaises(SnapshotUnavailable):self.project(raw)
    def test_maximum_window_enforced(self):
        raw=sample();raw['requests']=raw['requests']*17
        with self.assertRaises(SnapshotUnavailable):self.project(raw)

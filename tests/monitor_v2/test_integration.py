from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from oneil_control_v2 import ControlAPI, Settings
from oneil_control_v2.state import digest
from oneil_monitor_v2.host import MonitorHost, compose_monitor, ASSETS
from oneil_monitor_v2 import project_status
from support import create_harness, environment, invoke, TOKEN, ACCOUNT, CLOCK

class MonitorIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.app,self.state,self.store,self.record=create_harness(self.temp.name)
    def test_complete_authenticated_read_path(self):
        status,body,headers=invoke(self.app)
        self.assertEqual(status,200);self.assertEqual(body['source'],'AUTHENTICATED_SIMULATION_CORE')
        self.assertEqual(body['owner_authorization']['state'],'AUTHENTICATED_READ_ONLY')
        self.assertFalse(body['live_capable']);self.assertTrue(body['read_only'])
        self.assertEqual(self.store.calls,[('read',ACCOUNT)])
        self.assertNotIn(ACCOUNT,str(body));self.assertNotIn('private-worker-placeholder',str(body))
        self.assertEqual(headers['Cache-Control'],'no-store')
    def test_read_has_no_command_or_auth_mutations(self):
        with self.state.transaction() as conn:
            before={name:conn.execute(f'SELECT count(*) FROM {name}').fetchone()[0] for name in ('commands','confirmations','nonces','sessions','audit','rate_windows')}
        for _ in range(3):self.assertEqual(invoke(self.app)[0],200)
        with self.state.transaction() as conn:
            after={name:conn.execute(f'SELECT count(*) FROM {name}').fetchone()[0] for name in before}
        self.assertEqual(before,after)
    def test_unauthenticated_never_reads_core(self):
        status,body,_=invoke(self.app,environment(token=None));self.assertEqual(status,401);self.assertEqual(self.store.calls,[])
        self.assertNotIn('requests',body)
    def test_revocation_is_enforced(self):
        invoke(self.app);self.state.revoke_session(self.record.token_digest)
        status,_,_=invoke(self.app);self.assertEqual(status,401);self.assertEqual(len(self.store.calls),1)
    def test_expiration_during_slow_read_does_not_disclose(self):
        now=[CLOCK];self.app.api.clock=lambda:now[0]
        original=self.store.read_model
        def slow(account):
            result=original(account);now[0]=self.record.expires_at;return result
        self.store.read_model=slow
        status,body,_=invoke(self.app);self.assertEqual(status,401);self.assertNotIn('requests',body)
    def test_all_non_get_rejected_before_core(self):
        for method in ('POST','PUT','PATCH','DELETE','HEAD','OPTIONS'):
            for path in ('/api/v2/status','/api/v2/commands','/api/v2/previews','/api/v2/live','/'):
                self.assertEqual(invoke(self.app,environment(method=method,path=path))[0],405)
        self.assertEqual(self.store.calls,[])
    def test_no_live_or_auth_issuer_or_fixture_routes(self):
        for path in ('/api/v2/live','/api/v2/activate','/api/v2/login','/api/v2/commands','/fixtures/authenticated.json','/mock.html','/../state.py','/%2e%2e/state.py'):
            self.assertEqual(invoke(self.app,environment(path=path))[0],404,path)
    def test_exact_https_host_and_no_query(self):
        for change in ({'wsgi.url_scheme':'http'},{'HTTP_HOST':'evil.invalid'},{'QUERY_STRING':'session=public-test'}):
            self.assertEqual(invoke(self.app,environment(**change))[0],400)
        self.assertEqual(self.store.calls,[])
    def test_static_shell_has_no_private_data_or_test_credentials(self):
        for path in ASSETS:
            status,body,headers=invoke(self.app,environment(token=None,path=path))
            self.assertEqual(status,200);self.assertNotIn(TOKEN,body);self.assertNotIn(ACCOUNT,body)
            self.assertEqual(headers['Cache-Control'],'no-store');self.assertIn("frame-ancestors 'none'",headers['Content-Security-Policy'])
        self.assertEqual(self.store.calls,[])
    def test_real_index_cannot_silently_be_mock_mode(self):
        body=invoke(self.app,environment(path='/'))[1]
        self.assertIn('data-monitor-mode="authenticated"',body);self.assertNotIn('data-monitor-mode="mock"',body)
    def test_rejects_enabled_simulation_commands(self):
        self.app.api.settings=Settings('https://monitor.invalid',10,True)
        with self.assertRaisesRegex(ValueError,'COMMANDS_MUST_BE_DISABLED'):MonitorHost(self.app.api)
    def test_rejects_unreviewed_backend_and_projector(self):
        original=self.app.api.backend;self.app.api.backend=object()
        with self.assertRaisesRegex(TypeError,'REVIEWED_SIMULATION_CORE_REQUIRED'):MonitorHost(self.app.api)
        self.app.api.backend=original;self.app.api.projector=lambda *args:None
        with self.assertRaisesRegex(TypeError,'VERSIONED_PROJECTOR_REQUIRED'):MonitorHost(self.app.api)
    def test_source_is_unbound_until_control_adapter_authenticates(self):
        raw=self.store.read_model(ACCOUNT)
        from datetime import datetime,timezone
        result=project_status(raw,now=datetime.fromtimestamp(CLOCK,timezone.utc),broker_max_age_seconds=10)
        self.assertEqual(result['source'],'UNBOUND_SIMULATION_PROJECTION')
    def test_poll_does_not_redate_old_evidence(self):
        first=invoke(self.app)[1];self.app.api.clock=lambda:CLOCK+20;later=invoke(self.app)[1]
        self.assertEqual(first['heartbeat']['observed_at'],later['heartbeat']['observed_at'])
        self.assertEqual(later['heartbeat']['status'],'stale');self.assertEqual(later['broker_sync']['age_seconds'],26)
    def test_core_failure_sanitized(self):
        self.store.scenario='unavailable';status,body,_=invoke(self.app)
        self.assertEqual((status,body),(503,{'error':'SERVICE_UNAVAILABLE'}))
    def test_unknown_never_maps_to_rejected(self):
        self.store.scenario='unknown';body=invoke(self.app)[1]
        self.assertEqual(body['requests'][0]['state'],'SUBMISSION_UNKNOWN')
        self.assertFalse(body['requests'][0]['can_cancel_before_admission'])
    def test_ack_and_partial_not_filled(self):
        body=invoke(self.app)[1];self.assertEqual(body['counts']['filled'],1)
        self.assertEqual(body['requests'][2]['group'],'pending');self.assertEqual(body['requests'][2]['filled_quantity'],0)
    def test_read_controls_always_disabled(self):
        self.assertTrue(all(v['enabled'] is False for v in invoke(self.app)[1]['controls'].values()))

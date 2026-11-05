"""Actual SQLite/control/runtime/dashboard composition; no core/provider doubles.

The WSGI calls are same-process requests, not a listener/TLS/browser claim. A
separate real child-process test exercises the whole lifecycle with local files.
"""
from dataclasses import asdict, replace
from datetime import timedelta
from io import BytesIO
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'policy_v2'))
import policy_support as f
from oneil_control_v2 import SessionRecord
from oneil_control_v2.state import canonical, digest
from oneil_execution_v2.api import AuthenticatedOwner
from oneil_execution_v2.domain import Blocked
from oneil_policy_v2.admission import PolicyContext, history_digest
from oneil_runtime_v2.health import ClockSample
from oneil_runtime_v2.simulation_host import MODE, SupervisedSimulationHost, initialize_simulation_host
from oneil_runtime_v2.state import RuntimeState
from oneil_runtime_v2.supervisor import ACCOUNT, SupervisorState

TOKEN, CSRF = 'public-test-session-token-'+'s'*30, 'public-test-csrf-token-'+'c'*30


class SimulationHostTests(unittest.TestCase):
    def setUp(self):
        guard = patch('socket.socket', side_effect=AssertionError('NO_NETWORK'))
        guard.start(); self.addCleanup(guard.stop)
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)/'host'
        self.policy = f.policy(account=ACCOUNT)
        initialize_simulation_host(self.directory, self.policy, origin='https://simulation.invalid',
                                   cash_krw=1_000_000, positions={})
        self.supervisor = SupervisorState(self.directory); self.addCleanup(self.supervisor.close)
        self.clock = ClockSample(f.NOW.timestamp(), 1000, 900)
        state = RuntimeState(self.directory/'runtime.sqlite')
        self.lease = state.start(ACCOUNT, now=self.clock.utc); state.close()
        self.supervisor.update(phase='STARTING', lease=json.dumps(asdict(self.lease)))
        self.host = SupervisedSimulationHost(self.directory, self.lease, clock=lambda:self.clock)
        self.addCleanup(lambda:self.host.close())
        self.counter = 0
        self.owner = AuthenticatedOwner('simulation:test-owner', ACCOUNT)

    def advance(self, seconds=1):
        c=self.clock; self.clock=ClockSample(c.utc+seconds,c.elapsed+seconds,c.awake+seconds)

    def inject(self, *, scopes=frozenset({'monitor.read','simulation.command'})):
        record = SessionRecord(digest(TOKEN),digest(CSRF),self.owner.principal,ACCOUNT,scopes,
                               int(self.clock.utc)+300,int(self.clock.utc)-30)
        self.host.inject_simulation_session(record,CSRF)
        return record

    def call(self, path, body=None, *, token=TOKEN, key=None, **changes):
        self.counter+=1; raw=canonical(body).encode() if body is not None else b''
        env={'REQUEST_METHOD':'POST' if body is not None else 'GET','PATH_INFO':path,'QUERY_STRING':'',
            'wsgi.url_scheme':'https','HTTP_HOST':'simulation.invalid','HTTP_ORIGIN':'https://simulation.invalid',
            'HTTP_SEC_FETCH_SITE':'same-origin','HTTP_COOKIE':'__Host-oneil_session='+token,
            'HTTP_X_CSRF_TOKEN':CSRF,'HTTP_X_REQUEST_TIMESTAMP':str(int(self.clock.utc)),
            'HTTP_X_REQUEST_NONCE':f'nonce{self.counter:020d}','HTTP_IDEMPOTENCY_KEY':key or f'key{self.counter:020d}',
            'CONTENT_TYPE':'application/json','CONTENT_LENGTH':str(len(raw)),'wsgi.input':BytesIO(raw),**changes}
        response=[]
        body=b''.join(self.host(env,lambda status,headers:response.append((int(status.split()[0]),dict(headers)))))
        data=json.loads(body) if 'application/json' in response[0][1]['Content-Type'] else body.decode()
        return response[0][0],data

    def preview(self, action, **extra):
        revision=self.host.core.control(ACCOUNT)['revision']
        result=self.call('/api/v2/previews',{'action':action,'expected_revision':revision,**extra})
        self.assertEqual(result[0],201,result)
        return result[1]

    def confirm(self, action, preview, *, key=None):
        return self.call('/api/v2/commands',{'action':action,'confirmation_id':preview['confirmation_id'],
            'content_digest':preview['content_digest']},key=key)

    def ready(self):
        self.inject()
        self.assertEqual(self.host.tick(),'RECONCILED_ONLY')
        self.advance()
        self.assertEqual(self.host.tick(),'RECONCILED_ONLY')
        self.assertEqual(self.host.state.status(ACCOUNT)['phase'],'AWAITING_OWNER')
        self.assertEqual(self.confirm('resume_simulation',self.preview('resume_simulation'))[0],200)
        self.assertEqual(self.host.state.status(ACCOUNT)['phase'],'AWAITING_OWNER')
        self.assertEqual(self.confirm('activate_runtime',self.preview('activate_runtime'))[0],200)
        self.assertEqual(self.host.state.status(ACCOUNT)['phase'],'ACTIVE_SIMULATION')

    def context(self):
        return PolicyContext(self.policy,replace(f.account(),account=ACCOUNT,equity_krw=1_000_000),
            replace(f.controls(self.policy),account=ACCOUNT),self.host.core.control(ACCOUNT)['revision'],
            history_digest(self.host.broker.history(ACCOUNT)),{},f.candidates(),
            {('005930','BUY'):replace(f.market(),account=ACCOUNT)},f.sessions(),{})

    def queued(self):
        self.ready()
        proposal=self.host.coordinator.propose(self.owner,self.context())['proposal_id']
        preview=self.preview('policy_order',proposal_id=proposal)
        receipt=self.confirm('policy_order',preview,key='simulation-order-confirmed-01')
        self.assertEqual(receipt[0],202,receipt)
        return receipt[1], preview

    def test_default_has_no_identity_commands_or_production_capability(self):
        self.assertFalse(self.host.api.settings.simulation_commands_enabled)
        for path in ('/api/v2/status','/api/v2/session','/api/v2/host-state'):
            self.assertEqual(self.call(path)[0],401)
        self.assertEqual(self.call('/api/v2/commands',{'action':'halt','expected_revision':1})[0],401)
        code, html=self.call('/')
        self.assertEqual(code,200); self.assertIn('SIMULATION ONLY',html)
        self.assertIn('No real trading or production identity',html)
        self.assertTrue(self.host.core.control(ACCOUNT)['halted'])

    def test_injected_session_gets_only_matching_ephemeral_csrf_bootstrap(self):
        self.inject()
        code,session=self.call('/api/v2/session')
        self.assertEqual(code,200); self.assertEqual(session['csrf_token'],CSRF)
        self.assertEqual(session['mode'],MODE); self.assertFalse(session['production_identity_available'])
        self.assertEqual(self.call('/api/v2/session',HTTP_ORIGIN='https://evil.invalid')[0],403)
        self.assertEqual(self.call('/api/v2/session',token='not-the-token')[0],401)

    def test_production_or_wrong_account_session_injection_refused(self):
        base=SessionRecord(digest(TOKEN),digest(CSRF),'production-owner',ACCOUNT,
            frozenset({'monitor.read'}),int(self.clock.utc)+100,int(self.clock.utc)-10)
        with self.assertRaisesRegex(Blocked,'EXPLICIT_SIMULATION_SESSION_REQUIRED'):
            self.host.inject_simulation_session(base,CSRF)
        with self.assertRaisesRegex(Blocked,'EXPLICIT_SIMULATION_SESSION_REQUIRED'):
            self.host.inject_simulation_session(replace(base,principal=self.owner.principal,account='a'*64),CSRF)
        with self.assertRaisesRegex(Blocked,'EXPLICIT_SIMULATION_SESSION_REQUIRED'):
            self.host.inject_simulation_session(replace(base,principal=self.owner.principal),'wrong-token')

    def test_read_only_session_never_enables_commands(self):
        self.inject(scopes=frozenset({'monitor.read'}))
        self.assertEqual(self.call('/api/v2/host-state')[0],200)
        self.assertFalse(self.host.api.settings.simulation_commands_enabled)
        self.assertEqual(self.call('/api/v2/commands',{'action':'halt','expected_revision':1})[0],403)

    def test_dashboard_core_resume_and_runtime_activation_are_distinct_confirmations(self):
        self.ready()
        code,view=self.call('/api/v2/host-state')
        self.assertEqual(code,200); self.assertEqual(view['runtime_phase'],'ACTIVE_SIMULATION')
        self.assertFalse(view['live_capable']); self.assertFalse(view['production_identity_available'])
        self.assertEqual(view['proposals'],[])
        self.assertEqual(self.host.broker.calls,[])

    def test_actual_sqlite_dashboard_confirmation_archives_and_submits_once(self):
        receipt,preview=self.queued()
        self.assertEqual(self.host.tick(),'ACKNOWLEDGED')
        row=self.host.core.get(ACCOUNT,receipt['request_id'])
        self.assertEqual(row['attempts'],1); self.assertEqual(row['filled_quantity'],0)
        self.assertEqual(self.host.state.conn.execute('SELECT count(*) FROM intent').fetchone()[0],1)
        self.assertEqual(self.confirm('policy_order',preview,key='simulation-order-confirmed-01'),(202,receipt))
        self.assertEqual(len(self.host.broker.calls),1)
        self.assertEqual(self.call('/api/v2/status')[0],200)
        self.assertIn('runtime',row['admission_evidence'])

    def test_order_attempt_is_durable_before_simulator_effect(self):
        receipt,_=self.queued()
        def before_submit():
            conn=sqlite3.connect(self.directory/'simulation.sqlite')
            try:
                row=json.loads(conn.execute('SELECT data FROM requests WHERE request_id=?',(receipt['request_id'],)).fetchone()[0])
                self.assertEqual((row['state'],row['attempts']),('SUBMISSION_UNKNOWN',1))
            finally:conn.close()
            self.assertFalse(self.host.core.conn.in_transaction)
        self.host.broker.submit_hook=before_submit
        self.assertEqual(self.host.tick(),'ACKNOWLEDGED')

    def test_runtime_activation_binds_epoch_and_cannot_be_browser_supplied(self):
        self.ready()
        self.assertEqual(self.call('/api/v2/commands',{'action':'activate_runtime','expected_revision':2,'approved':True})[0],400)
        self.assertEqual(self.call('/api/v2/previews',{'action':'activate_runtime','expected_revision':2})[0],409)
        self.assertEqual(self.host.state.status(ACCOUNT)['phase'],'ACTIVE_SIMULATION')

    def test_activation_commit_receipt_failure_halts_and_preserves_unknown(self):
        self.inject(); self.host.tick(); self.advance(); self.host.tick()
        self.assertEqual(self.confirm('resume_simulation',self.preview('resume_simulation'))[0],200)
        preview=self.preview('activate_runtime')
        with patch.object(self.host.commands,'complete',side_effect=RuntimeError('synthetic receipt write error')):
            self.assertEqual(self.confirm('activate_runtime',preview,key='activation-unknown-key-01')[0],503)
        self.assertTrue(self.host.runtime.guard.poisoned)
        self.assertTrue(self.host.core.control(ACCOUNT)['halted'])
        self.assertEqual(self.confirm('activate_runtime',preview,key='activation-unknown-key-01'),(409,{'error':'COMMAND_OUTCOME_UNKNOWN'}))

    def test_supervisor_stop_blocks_commands_and_quiesces_same_lifecycle(self):
        self.ready(); self.supervisor.request_stop()
        self.assertEqual(self.call('/api/v2/commands',{'action':'halt','expected_revision':2}),
                         (409,{'error':'SUPERVISOR_STOP_REQUESTED'}))
        self.assertEqual(self.host.tick(),'STOPPED')
        self.assertTrue(self.host.core.control(ACCOUNT)['halted'])
        self.assertEqual(self.host.state.status(ACCOUNT)['phase'],'HALTED')
        self.assertEqual(self.host.state.status(ACCOUNT)['owner'],self.lease.owner)

    def test_second_host_cannot_duplicate_worker_ownership(self):
        with self.assertRaisesRegex(RuntimeError,'SUPERVISOR_ALREADY_RUNNING'):
            SupervisedSimulationHost(self.directory,self.lease,clock=lambda:self.clock)
        self.assertFalse(self.host.closed)

    def test_close_revokes_process_sessions_and_never_releases_supervisor_lease(self):
        self.inject(); self.host.close()
        with sqlite3.connect(self.directory/'control.sqlite') as conn:
            self.assertEqual(conn.execute('SELECT revoked FROM sessions').fetchone()[0],1)
        state=RuntimeState(self.directory/'runtime.sqlite')
        try:self.assertEqual(state.status(ACCOUNT)['owner'],self.lease.owner)
        finally:state.close()

    def test_session_cookie_and_csrf_values_are_never_persisted(self):
        self.inject()
        for path in self.directory.iterdir():
            if path.is_file():
                data=path.read_bytes()
                self.assertNotIn(TOKEN.encode(),data,path.name)
                self.assertNotIn(CSRF.encode(),data,path.name)

    def test_supervisor_report_uses_same_simulation_core_and_is_json_safe(self):
        from oneil_runtime_v2.supervisor import report
        self.ready()
        value=report(self.supervisor)
        self.assertEqual(value['execution_dependency'],'ISOLATED_SQLITE_SIMULATION')
        self.assertEqual(value['control_revision'],self.host.core.control(ACCOUNT)['revision'])
        self.assertEqual(value['runtime_phase'],'ACTIVE_SIMULATION')
        self.assertFalse(value['listener_started']);self.assertFalse(value['production_identity_available'])
        json.dumps(value)

    def test_simulator_checkpoint_persists_actual_order_history_not_core_inference(self):
        receipt,_=self.queued(); self.host.tick()
        conn=sqlite3.connect(self.directory/'simulator.sqlite')
        try:
            snapshot=json.loads(conn.execute('SELECT data FROM simulator').fetchone()[0])
            self.assertEqual(snapshot['orders'][receipt['request_id']]['status'],'ACKNOWLEDGED')
            self.assertEqual(snapshot['calls'],[receipt['request_id']])
        finally:conn.close()

    def test_failed_simulator_checkpoint_halts_no_resend(self):
        receipt,_=self.queued()
        with patch.object(self.host,'_checkpoint_simulator',side_effect=RuntimeError('synthetic disk full')):
            with self.assertRaises(RuntimeError):self.host.tick()
        self.assertTrue(self.host.runtime.guard.poisoned)
        self.assertTrue(self.host.core.control(ACCOUNT)['halted'])
        self.assertEqual(self.host.core.get(ACCOUNT,receipt['request_id'])['attempts'],1)
        self.assertEqual(len(self.host.broker.calls),1)

    def test_dependency_read_failure_poisoned_before_any_tick_effect(self):
        self.ready()
        with patch.object(self.host.broker,'history',side_effect=RuntimeError('synthetic dependency failure')):
            with self.assertRaises(RuntimeError):self.host.tick()
        self.assertTrue(self.host.runtime.guard.poisoned)
        self.assertTrue(self.host.core.control(ACCOUNT)['halted'])
        self.assertEqual(self.host.broker.calls,[])

    def test_snapshot_roundtrip_preserves_simulated_fill_causality(self):
        receipt,_=self.queued(); self.host.broker.behavior='fill'
        self.assertEqual(self.host.tick(),'FILLED')
        restored=self.host._load_simulator()
        self.assertEqual(restored.orders,self.host.broker.orders)
        self.assertEqual(restored.balances,self.host.broker.balances)
        self.assertTrue(restored.history_complete)
        self.assertEqual(restored.calls,[receipt['request_id']])

    def test_checkpoint_never_resets_known_loss_or_history_incompleteness(self):
        self.host.broker.daily_loss_krw=1234
        self.host.broker.history_complete=False
        self.host._checkpoint_simulator()
        restored=self.host._load_simulator()
        self.assertEqual(restored.daily_loss_krw,1234)
        self.assertFalse(restored.history_complete)

    def test_missing_checkpoint_after_committed_attempt_remains_incomplete(self):
        receipt,_=self.queued()
        with patch.object(self.host,'_checkpoint_simulator',side_effect=RuntimeError('lost checkpoint')):
            with self.assertRaises(RuntimeError):self.host.tick()
        restored=self.host._load_simulator()
        self.assertFalse(restored.history_complete)
        self.assertEqual(restored.orders,{})

    def test_dashboard_serves_no_login_or_arbitrary_order_execution(self):
        self.inject()
        self.assertEqual(self.call('/api/v2/login',{})[0],404)
        self.assertEqual(self.call('/api/v2/commands',{'action':'activate_live','expected_revision':1})[0],400)
        self.assertEqual(self.call('/',**{'wsgi.url_scheme':'http'})[0],400)

    def test_advancing_clock_observations_are_not_future_evidence(self):
        def advancing():
            self.advance(.001)
            return self.clock
        self.host.clock=advancing
        self.assertEqual(self.host.tick(),'RECONCILED_ONLY')
        self.assertEqual(self.host.tick(),'RECONCILED_ONLY')
        self.assertEqual(self.host.state.status(ACCOUNT)['phase'],'AWAITING_OWNER')
        reasons=[r['reason'] for r in self.host.state.conn.execute('SELECT reason FROM incident')]
        self.assertNotIn('BROKER_READ_STALE_OR_FUTURE',reasons)

    def test_host_read_rechecks_revocation_after_slow_dependency(self):
        record=self.inject()
        original=self.host.core.read_model
        def revoked(account):
            result=original(account)
            self.host.commands.revoke_session(record.token_digest)
            return result
        with patch.object(self.host.core,'read_model',side_effect=revoked):
            self.assertEqual(self.call('/api/v2/host-state')[0],401)

    def restart_host(self):
        self.host.close()
        state=RuntimeState(self.directory/'runtime.sqlite')
        state.record_exit(self.lease,exit_code=0,now=self.clock.utc,explicit_stop=True)
        lease=state.start(ACCOUNT,now=self.clock.utc,owner_request=self.owner)
        state.close()
        self.supervisor.update(phase='STARTING',lease=json.dumps(asdict(lease)))
        self.lease=lease
        self.host=SupervisedSimulationHost(self.directory,lease,clock=lambda:self.clock)

    def test_pending_restart_keeps_host_available_for_explicit_cancel(self):
        receipt,_=self.queued()
        self.restart_host()
        self.assertTrue(self.host.core.control(ACCOUNT)['halted'])
        self.assertIsNone(self.host.state.status(ACCOUNT)['reconstructed'])
        self.assertEqual(self.call('/api/v2/host-state')[0],401)
        # New issuer credentials are necessary. Old session digests cannot revive.
        global TOKEN,CSRF
        old_token,old_csrf=TOKEN,CSRF
        TOKEN,CSRF='new-public-session-'+'n'*40,'new-public-csrf-'+'f'*40
        try:
            self.inject()
            revision=self.host.core.control(ACCOUNT)['revision']
            code,result=self.call('/api/v2/commands',{'action':'cancel_pending','expected_revision':revision,
                'request_id':receipt['request_id']},token=TOKEN)
            self.assertEqual(code,200,result)
            self.assertEqual(self.host.core.get(ACCOUNT,receipt['request_id'])['state'],'CANCELLED')
            self.assertEqual(self.host.core.get(ACCOUNT,receipt['request_id'])['attempts'],0)
            self.assertEqual(self.host.broker.calls,[])
        finally:TOKEN,CSRF=old_token,old_csrf

    def test_real_supervisor_child_runs_authenticated_sqlite_command_lifecycle(self):
        # A separate newly initialized profile uses a real child process. No core
        # mocks, listener, broker connection or IPC request workaround is used.
        from oneil_runtime_v2.supervisor import OfflineSupervisor
        directory=Path(self.tmp.name)/'child-host'
        initialize_simulation_host(directory,self.policy,origin='https://simulation.invalid',cash_krw=1_000_000,positions={})
        root=Path(__file__).resolve().parents[2]
        script=r'''
import sys,json,time,socket
from dataclasses import asdict,replace
from io import BytesIO
from pathlib import Path
socket.socket=lambda *a,**k: (_ for _ in ()).throw(AssertionError('NO NETWORK'))
root=Path(sys.argv[1]);sys.path.insert(0,str(root));sys.path.insert(0,str(root/'tests/policy_v2'))
import policy_support as f
from oneil_runtime_v2.simulation_host import SupervisedSimulationHost
from oneil_runtime_v2.state import RuntimeLease
from oneil_runtime_v2.supervisor import ACCOUNT
from oneil_runtime_v2.health import ClockSample
from oneil_control_v2 import SessionRecord
from oneil_control_v2.state import digest,canonical
from oneil_execution_v2.api import AuthenticatedOwner
from oneil_policy_v2.admission import PolicyContext,history_digest
start=time.monotonic()
def clock():
 d=time.monotonic()-start;return ClockSample(f.NOW.timestamp()+d,1000+d,900+d)
h=SupervisedSimulationHost(sys.argv[2],RuntimeLease(ACCOUNT,'V2_SIMULATION',sys.argv[3],int(sys.argv[4])),clock=clock)
token='public-child-test-token-'+'t'*40;csrf='public-child-csrf-token-'+'c'*40;count=0
def call(path,body):
 global count
 count+=1;raw=canonical(body).encode();reply=[]
 env={'REQUEST_METHOD':'POST','PATH_INFO':path,'QUERY_STRING':'','wsgi.url_scheme':'https','HTTP_HOST':'simulation.invalid',
 'HTTP_ORIGIN':'https://simulation.invalid','HTTP_SEC_FETCH_SITE':'same-origin','HTTP_COOKIE':'__Host-oneil_session='+token,
 'HTTP_X_CSRF_TOKEN':csrf,'HTTP_X_REQUEST_TIMESTAMP':str(int(clock().utc)),'HTTP_X_REQUEST_NONCE':f'nonce{count:020d}',
 'HTTP_IDEMPOTENCY_KEY':f'key{count:020d}','CONTENT_TYPE':'application/json','CONTENT_LENGTH':str(len(raw)),'wsgi.input':BytesIO(raw)}
 data=json.loads(b''.join(h(env,lambda status,headers:reply.append(int(status.split()[0])))))
 assert reply[0] in (200,201,202),(reply,data)
 return data
def command(action,**extra):
 preview=call('/api/v2/previews',{'action':action,'expected_revision':h.core.control(ACCOUNT)['revision'],**extra})
 return call('/api/v2/commands',{'action':action,'confirmation_id':preview['confirmation_id'],'content_digest':preview['content_digest']})
try:
 h.inject_simulation_session(SessionRecord(digest(token),digest(csrf),'simulation:child-owner',ACCOUNT,
  frozenset({'monitor.read','simulation.command'}),int(clock().utc)+300,int(clock().utc)-10),csrf)
 assert h.tick()=='RECONCILED_ONLY';time.sleep(.01);assert h.tick()=='RECONCILED_ONLY'
 command('resume_simulation');command('activate_runtime')
 p=f.policy(account=ACCOUNT)
 context=PolicyContext(p,replace(f.account(),account=ACCOUNT,equity_krw=1_000_000),replace(f.controls(p),account=ACCOUNT),
 h.core.control(ACCOUNT)['revision'],history_digest(h.broker.history(ACCOUNT)),{},f.candidates(),
 {('005930','BUY'):replace(f.market(),account=ACCOUNT)},f.sessions(),{})
 proposal=h.coordinator.propose(AuthenticatedOwner('simulation:child-owner',ACCOUNT),context)['proposal_id']
 receipt=command('policy_order',proposal_id=proposal)
 assert h.tick()=='ACKNOWLEDGED'
 assert h.core.get(ACCOUNT,receipt['request_id'])['attempts']==1 and len(h.broker.calls)==1
 h.supervisor.request_stop();assert h.tick()=='STOPPED'
finally:h.close()
'''
        supervisor=SupervisorState(directory)
        def spawn(path,lease):
            return subprocess.Popen([sys.executable,'-c',script,str(root),str(path),lease.owner,str(lease.epoch)],
                cwd=root,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            runner=OfflineSupervisor(supervisor,spawn=spawn,now=lambda:f.NOW.timestamp())
            self.assertEqual(runner.run(),0)
            self.assertEqual(supervisor.status()['phase'],'STOPPED')
            state=RuntimeState(directory/'runtime.sqlite')
            try:self.assertIsNone(state.status(ACCOUNT)['owner'])
            finally:state.close()
            conn=sqlite3.connect(directory/'simulation.sqlite')
            try:
                rows=[json.loads(r[0]) for r in conn.execute('SELECT data FROM requests')]
                self.assertEqual(len(rows),1);self.assertEqual(rows[0]['attempts'],1)
            finally:conn.close()
        finally:supervisor.close()


if __name__ == '__main__':unittest.main()

"""Public test fixtures only. Never create real sessions or access PostgreSQL."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
from pathlib import Path
from unittest.mock import patch
from uuid import UUID
from oneil_control_v2 import ControlAPI, ControlState, Settings, SessionRecord
from oneil_control_v2.app import COOKIE
from oneil_control_v2.bridge import CoreSimulationBackend
from oneil_control_v2.state import digest
from oneil_monitor_v2 import project_status
from oneil_monitor_v2.host import MonitorHost

CLOCK = int(datetime(2026,11,5,10,30,tzinfo=timezone.utc).timestamp())
ACCOUNT = 'a'*64
TOKEN = 'PUBLIC_TEST_FIXTURE_SESSION_'+'t'*43
CSRF = 'PUBLIC_TEST_FIXTURE_CSRF_'+'c'*43

class FakeStore:
    def __init__(self, scenario='authenticated'):
        self.scenario=scenario
        self.calls=[]
    def read_model(self, account):
        assert account==ACCOUNT
        self.calls.append(('read',account))
        if self.scenario=='unavailable':
            raise RuntimeError('PRIVATE_DIAGNOSTIC_MUST_NOT_LEAK')
        now=datetime.fromtimestamp(CLOCK,timezone.utc)
        heartbeat=now-timedelta(seconds=95 if self.scenario=='stale_worker' else 3)
        broker=now-timedelta(seconds=180 if self.scenario=='stale_broker' else 6)
        states=[('SUBMISSION_UNKNOWN' if self.scenario=='unknown' else 'VALIDATING',1 if self.scenario=='unknown' else 0,0),
                ('QUEUED',0,0),('ACKNOWLEDGED',1,0),('PART_FILLED',1,2),('FILLED',1,5),('CANCELLED',0,0)]
        requests=[{'request_id':UUID(int=i),'state':state,'reason':'TEST_FIXTURE_EVIDENCE','attempts':attempts,
                   'filled_quantity':filled,'created_at':now-timedelta(minutes=i*5),'updated_at':now-timedelta(seconds=i*8)}
                  for i,(state,attempts,filled) in enumerate(states,1)]
        return {'mode':'SIMULATION','live_capable':False,'halted':self.scenario!='authenticated',
                'control_revision':7,'as_of':now,'health':{'heartbeat_at':heartbeat,'status':'TEST_FIXTURE',
                'account':ACCOUNT,'worker_id':'private-worker-placeholder'},'broker_reconciled_at':broker,'requests':requests}


def environment(token=TOKEN, path='/api/v2/status', method='GET', **extra):
    env={'REQUEST_METHOD':method,'PATH_INFO':path,'QUERY_STRING':'','wsgi.url_scheme':'https',
         'HTTP_HOST':'monitor.invalid','HTTP_ORIGIN':'https://monitor.invalid','HTTP_SEC_FETCH_SITE':'same-origin',
         'HTTP_COOKIE':f'{COOKIE}={token}' if token else '', 'wsgi.input':BytesIO(b''), 'CONTENT_LENGTH':'0'}
    return {**env,**extra}


def create_harness(directory, scenario='authenticated', clock=None):
    state=ControlState(Path(directory)/'mock-control.sqlite3');state.initialize()
    scopes=frozenset({'simulation.command'} if scenario=='forbidden' else {'monitor.read'})
    expiry=CLOCK if scenario=='expired' else CLOCK+300
    record=SessionRecord(digest(TOKEN),digest(CSRF),'mock-owner',ACCOUNT,scopes,expiry,CLOCK-60)
    state.install_session_record(record)
    store=FakeStore(scenario)
    # Test double is allowed only here; production constructor guard is unchanged.
    with patch('oneil_control_v2.bridge.PostgresStore',FakeStore):
        backend=CoreSimulationBackend(store)
    api=ControlAPI(state=state,backend=backend,projector=project_status,
                   settings=Settings('https://monitor.invalid',10,False),clock=clock or (lambda:CLOCK))
    return MonitorHost(api),state,store,record


def invoke(app,env=None):
    saved=[]
    raw=b''.join(app(env or environment(),lambda status,headers:saved.append((int(status.split()[0]),dict(headers)))))
    code,headers=saved[0]
    return code, json.loads(raw) if headers['Content-Type'].startswith('application/json') else raw.decode(), headers

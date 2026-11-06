#!/usr/bin/env python3
"""Fixed halted SQLite smoke check. No listener, broker credentials or orders.

Internal child of prepare_offline.py. A Python audit hook blocks socket and
process operations as defense in depth, not a security sandbox for hostile code.
"""
import json
import os
from pathlib import Path
import sys
import tempfile


def limit_resources():
    import resource
    for name, desired in (('RLIMIT_AS', 256 * 1024 * 1024), ('RLIMIT_CPU', 30),
                          ('RLIMIT_FSIZE', 4 * 1024 * 1024), ('RLIMIT_CORE', 0)):
        kind = getattr(resource, name)
        _, hard = resource.getrlimit(kind)
        bound = desired if hard == resource.RLIM_INFINITY else min(desired, hard)
        resource.setrlimit(kind, (bound, bound))


def deny_external_operations(event, args):
    if event.startswith('socket.') or event in ('subprocess.Popen', 'os.system',
            'os.exec', 'os.posix_spawn', 'os.fork', 'os.forkpty'):
        raise RuntimeError('OFFLINE_REHEARSAL_EXTERNAL_OPERATION_BLOCKED')


def run():
    if sys.platform != 'linux' or sys.version_info < (3, 11):
        raise RuntimeError('LINUX_PYTHON_311_REQUIRED')
    import resource
    limit_resources()
    os.umask(0o077)
    sys.addaudithook(deny_external_operations)
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from oneil_execution_v2.domain import RiskPolicy
    from oneil_execution_v2.broker import SimulatedBroker
    from oneil_execution_v2.worker import Worker
    from oneil_runtime_v2.simulation_store import SQLiteSimulationStore
    from oneil_runtime_v2.supervisor import ACCOUNT

    def require(condition):
        if not condition:
            raise RuntimeError('OFFLINE_REHEARSAL_ASSERTION_FAILED')

    policy = RiskPolicy('policy-v2-oracle-offline', 1, 1, 1, 1, 1)
    with tempfile.TemporaryDirectory(prefix='halted-sqlite-') as temporary:
        path = Path(temporary)/'simulation.sqlite'
        store = SQLiteSimulationStore.initialize(path, ACCOUNT, policy)
        try:
            require(store.live_capable is False and store.control(ACCOUNT)['halted'] == 1)
            other = SQLiteSimulationStore(path)
            try:
                with store.account_lock(ACCOUNT) as acquired:
                    require(acquired)
                    with other.account_lock(ACCOUNT) as competing:
                        require(competing is False)
            finally:
                other.close()
            broker = SimulatedBroker(store.now, cash_krw=0, positions={})
            worker = Worker(store, broker)
            require(worker.tick(ACCOUNT, reconciliation_only=True) == 'RECONCILED_ONLY')
            require(worker.tick(ACCOUNT) == 'HALTED')
            require(store.control(ACCOUNT)['halted'] == 1 and broker.calls == [])
        finally:
            store.close()
        reopened = SQLiteSimulationStore(path)
        try:
            require(reopened.control(ACCOUNT)['halted'] == 1)
            require(reopened.unresolved(ACCOUNT) == [] and reopened.recovery_requests(ACCOUNT) == [])
        finally:
            reopened.close()
    require(not Path(temporary).exists())
    return {'status': 'PASSED', 'live_capable': False, 'halted_before': True,
            'halted_after': True, 'simulated_order_calls': 0, 'sqlite_reopened': True,
            'lock_exclusion': True, 'temporary_state_removed': True,
            'peak_rss_kib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}


if __name__ == '__main__':
    try:
        print(json.dumps(run(), sort_keys=True))
    except Exception:
        # Do not print environment, account data, paths or raw exception text.
        print(json.dumps({'status': 'FAILED', 'live_capable': False}))
        raise SystemExit(2)

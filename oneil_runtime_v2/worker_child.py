"""Fixed supervisor child composing the actual strict simulation Worker.

Only durable isolated SQLite + SimulatedBroker dependencies. Default profile is
reconciliation-only; explicit simulation-control profiles compose the same-process
dashboard with no default issuer/listener. No live transport or production identity.
"""
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
import argparse
import json
import shutil
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from oneil_execution_v2.broker import SimulatedBroker
from oneil_execution_v2.domain import History, require
from oneil_execution_v2.worker import Worker
from oneil_policy_v2.admission import SimulationPolicySource
from oneil_runtime_v2.health import Evidence, HealthSample
from oneil_runtime_v2.orchestrator import SimulationRuntime
from oneil_runtime_v2.rehearsal_store import RehearsalStore
from oneil_runtime_v2.state import RuntimeLease, RuntimeState
from oneil_runtime_v2.supervisor import ACCOUNT, InstanceLock, SupervisorState
from oneil_runtime_v2.windows_clock import sample_windows_clock


def run_worker(directory, owner, epoch, *, clock=sample_windows_clock, sleep=time.sleep,
               simulation_setup=None, simulation_dispatch=None):
    from oneil_runtime_v2.simulation_host import CONFIG, SupervisedSimulationHost
    if (Path(directory)/CONFIG).exists():
        # Explicitly initialized fixed-account simulation profile only. No
        # listener or identity issuer is inferred by the supervisor/child.
        host = SupervisedSimulationHost(directory,
            RuntimeLease(ACCOUNT, 'V2_SIMULATION', owner, epoch), clock=clock)
        try:
            require(simulation_setup is None or callable(simulation_setup), 'EXPLICIT_SESSION_ISSUER_REQUIRED')
            if simulation_setup is not None:
                simulation_setup(host)
            return host.run(dispatch=simulation_dispatch, sleep=sleep)
        finally:
            host.close()
    require(simulation_setup is None and simulation_dispatch is None, 'SIMULATION_PROFILE_REQUIRED')
    supervisor = SupervisorState(directory)
    state = core = runtime = None
    try:
        with InstanceLock(supervisor.directory/'worker.lock'):
            lease = RuntimeLease(ACCOUNT, 'V2_SIMULATION', owner, epoch)
            launch = supervisor.status()
            require(launch['phase'] in ('STARTING','RUNNING') and launch['lease'] is not None
                    and json.loads(launch['lease']) == asdict(lease), 'SUPERVISOR_LAUNCH_RECEIPT_REQUIRED')
            state = RuntimeState(supervisor.directory/'runtime.sqlite')
            state._lease(lease)
            if launch['stop_requested']:
                return 0
            core = RehearsalStore(supervisor.directory/'rehearsal.sqlite',
                now=lambda: datetime.fromtimestamp(clock().utc, timezone.utc))
            # Zero initial synthetic cash/positions; the path cannot admit orders.
            broker = SimulatedBroker(core.now, cash_krw=0, positions={})
            worker = Worker(core, broker, policy_source=SimulationPolicySource())
            runtime = SimulationRuntime(state, worker, ACCOUNT, clock)
            runtime.attach_supervised(lease)
            # No inferred fresh contexts or restored owner approvals. Any pending
            # order causes a fail-closed error, not a guessed policy reconstruction.
            runtime.restore_pending({})
            try:
                while not supervisor.status()['stop_requested']:
                    require(state.status(ACCOUNT)['phase'] != 'ACTIVE_SIMULATION',
                            'SUPERVISED_ACTIVATION_DISABLED')
                    complete, orders = core.history_snapshot()
                    broker.history_complete, broker.orders = complete, orders
                    core.control(ACCOUNT)  # Successful real local DB read.
                    history = broker.history(ACCOUNT)
                    require(type(history) is History and history.account == ACCOUNT,
                            'SYNTHETIC_HISTORY_IDENTITY_MISMATCH')
                    stamp = clock()
                    health = core.health()
                    heartbeat = health['heartbeat_at'] if health and health['worker_id'] == worker.worker_id else None
                    try:
                        free = shutil.disk_usage(supervisor.directory).free
                    except OSError:
                        free = -1
                    sample = HealthSample(stamp, True, heartbeat, Evidence(True, stamp.utc),
                        Evidence(history.complete is True, history.observed_at.timestamp()),
                        free, bool(core.unresolved(ACCOUNT)))
                    outcome = runtime.step(sample)
                    if outcome == 'RECONCILED_ONLY' and state.status(ACCOUNT)['reconstructed'] is None:
                        runtime.restore_pending({})
                    # Defense in depth: even a future accidental mode change must
                    # not turn this reconciliation composition into an order path.
                    require(not broker.calls, 'SUPERVISED_ORDER_ATTEMPT_FORBIDDEN')
                    sleep(0.5)
            finally:
                runtime.quiesce('SUPERVISED_CHILD_STOPPED')
            return 0
    finally:
        # Ownership/restart accounting belongs only to the supervisor after its
        # exact process handle signals exit; this child never records its own exit.
        if core is not None:
            core.close()
        if state is not None:
            state.close()
        supervisor.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', required=True)
    parser.add_argument('--owner', required=True)
    parser.add_argument('--epoch', required=True, type=int)
    args = parser.parse_args(argv)
    try:
        return run_worker(args.state_dir, args.owner, args.epoch)
    except KeyboardInterrupt:
        return 0
    except Exception:
        return 2


if __name__ == '__main__':
    raise SystemExit(main())

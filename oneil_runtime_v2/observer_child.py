"""Fixed child: local offline unhealthy observation only, with no execution API."""
from pathlib import Path
import argparse
import shutil
import sys
import time

# Support isolated (-I) absolute-script launch from the verified source tree.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from oneil_runtime_v2.health import Evidence, HealthPolicy, HealthSample
from oneil_runtime_v2.state import RuntimeLease, RuntimeState
from oneil_runtime_v2.supervisor import ACCOUNT, SupervisorState
from oneil_runtime_v2.windows_clock import sample_windows_clock


def observe(directory, owner, epoch, *, clock=sample_windows_clock, sleep=time.sleep):
    supervisor = SupervisorState(directory)
    runtime = RuntimeState(supervisor.directory/'runtime.sqlite')
    lease = RuntimeLease(ACCOUNT, 'V2_SIMULATION', owner, epoch)
    try:
        runtime._lease(lease)
        while not supervisor.status()['stop_requested']:
            sample = clock()
            try:
                free = shutil.disk_usage(supervisor.directory).free
            except OSError:
                free = -1
            # Child liveness is known. Database, broker and unresolved orders
            # are NOT observed, never filled with synthetic healthy evidence.
            runtime.observe(lease, HealthSample(sample, True, sample.utc,
                Evidence(False, None), Evidence(False, None), free, True), HealthPolicy())
            sleep(0.5)
        return 0
    finally:
        runtime.close()
        supervisor.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', required=True)
    parser.add_argument('--owner', required=True)
    parser.add_argument('--epoch', required=True, type=int)
    args = parser.parse_args(argv)
    try:
        return observe(args.state_dir, args.owner, args.epoch)
    except KeyboardInterrupt:
        return 0
    except Exception:
        return 2


if __name__ == '__main__':
    raise SystemExit(main())

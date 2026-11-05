"""Read-only local preflight. No network, credentials, install or service start."""
import importlib.util
import json
import os
import platform
from pathlib import Path
import shutil
import sys
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .windows_clock import sample_windows_clock


def inspect(root):
    root = Path(root).resolve()
    reasons = []
    if os.name != 'nt':
        reasons.append('WINDOWS_NOT_VERIFIED')
    if sys.version_info < (3, 10):
        reasons.append('PYTHON_310_REQUIRED')
    required = ('oneil_execution_v2/schema.sql', 'oneil_control_v2/app.py',
                'oneil_execution_v2/worker.py', 'oneil_execution_v2/store.py',
                'oneil_monitor_v2/host.py', 'oneil_runtime_v2/health.py',
                'oneil_runtime_v2/orchestrator.py', 'oneil_runtime_v2/state.py',
                'oneil_runtime_v2/intent.py', 'oneil_runtime_v2/worker_child.py',
                'oneil_runtime_v2/rehearsal_store.py',
                'oneil_policy_v2/admission.py', 'oneil_policy_v2/policy.py',
                'oneil_policy_v2/planner.py', 'oneil_policy_v2/evidence.py')
    if any(not (root / part).is_file() for part in required):
        reasons.append('SOURCE_FILES_MISSING')
    if importlib.util.find_spec('psycopg') is None:
        reasons.append('PSYCOPG_NOT_INSTALLED')
    try:
        ZoneInfo('Asia/Seoul')
        timezone_available = True
    except (ZoneInfoNotFoundError, OSError, ValueError):
        timezone_available = False
        reasons.append('KST_TIMEZONE_DATA_MISSING')
    try:
        free = shutil.disk_usage(root).free
        if free < 1_073_741_824:
            reasons.append('DISK_SPACE_LOW')
    except OSError:
        free = None
        reasons.append('DISK_SPACE_UNKNOWN')
    try:
        sample_windows_clock()
        clock_available = True
    except (RuntimeError, OSError, AttributeError):
        clock_available = False
        reasons.append('WINDOWS_SUSPEND_CLOCK_UNVERIFIED')
    return {
        'mode': 'OFFLINE_PREFLIGHT_ONLY', 'live_capable': False,
        'admission_enforced': False, 'ready_to_trade': False,
        'windows': os.name == 'nt', 'architecture': platform.machine(),
        'python': platform.python_version(), 'free_bytes': free,
        'kst_timezone_available': timezone_available,
        'suspend_clock_available': clock_available,
        'reasons': reasons,
        'unverified': ['DATABASE', 'BROKER_CONNECTIVITY', 'IDENTITY_TLS', 'WINDOWS_ACL',
                       'SCHEDULER', 'POWER_SETTINGS', 'LEGACY_EXECUTION_FENCE',
                       'HEALTH_ADMISSION_INTEGRATION', 'POLICY_RESTART_RECONSTRUCTION',
                       'PROCESS_CRASH_RECOVERY', 'ACTUAL_WINDOWS_VALIDATION'],
    }


def main():
    result = inspect(Path(__file__).resolve().parents[1])
    print(json.dumps(result, ensure_ascii=False, indent=2))
    # This package is never a deployability certificate, even on a green preflight.
    return 2


if __name__ == '__main__':
    raise SystemExit(main())

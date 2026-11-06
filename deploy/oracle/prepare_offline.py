#!/usr/bin/env python3
"""Oracle Linux 9 source preparation checks, never an installer or live launcher.

The default is read-only. --rehearse runs only the fixed, temporary, halted SQLite
smoke test in a bounded child. It does not install packages, start a service,
open a listener, read credentials, or change host/network/security settings.
This checker deliberately parses on Python 3.9 so an older OS Python can report
why the rehearsal needs a newer interpreter.
"""
import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
MIB = 1024 * 1024
MIN_AVAILABLE_MIB = 384
MIN_DISK_MIB = 128


def os_release(text):
    values = {}
    for line in text.splitlines():
        key, separator, value = line.partition('=')
        if separator and key in ('ID', 'VERSION_ID'):
            values[key] = value.strip().strip('\"\'')
    return values.get('ID') == 'ol' and values.get('VERSION_ID', '').split('.')[0] == '9'


def memory_mib(text):
    values = {}
    for line in text.splitlines():
        key, separator, value = line.partition(':')
        fields = value.split()
        if separator and key in ('MemTotal', 'MemAvailable'):
            if len(fields) != 2 or fields[1] != 'kB' or not fields[0].isdigit():
                raise ValueError('MEMORY_FORMAT_INVALID')
            values[key] = int(fields[0])
    if set(values) != {'MemTotal', 'MemAvailable'} or not 0 <= values['MemAvailable'] <= values['MemTotal']:
        raise ValueError('MEMORY_FORMAT_INVALID')
    return {key: value // 1024 for key, value in values.items()}


def inspect():
    reasons = []
    linux = sys.platform == 'linux'
    if not linux:
        reasons.append('LINUX_REQUIRED')
    if sys.version_info < (3, 11):
        reasons.append('PYTHON_311_OR_NEWER_REQUIRED')
    try:
        oracle = linux and os_release(Path('/etc/os-release').read_text())
    except (OSError, UnicodeError):
        oracle = False
    try:
        memory = memory_mib(Path('/proc/meminfo').read_text()) if linux else {}
    except (OSError, UnicodeError, ValueError):
        memory = {}
    if not memory:
        reasons.append('AVAILABLE_MEMORY_UNKNOWN')
    elif memory['MemAvailable'] < MIN_AVAILABLE_MIB:
        reasons.append('AVAILABLE_MEMORY_BELOW_384_MIB')
    try:
        # A read-only check must not call gettempdir(): its probing creates files.
        free_mib = shutil.disk_usage('/tmp').free // MIB
        if free_mib < MIN_DISK_MIB:
            reasons.append('TEMP_DISK_BELOW_128_MIB')
    except OSError:
        free_mib = None
        reasons.append('TEMP_DISK_UNKNOWN')
    try:
        import sqlite3
        sqlite_available = True
    except ImportError:
        sqlite_available = False
        reasons.append('SQLITE_MODULE_REQUIRED')
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo('Asia/Seoul')
        kst = True
    except (ImportError, KeyError, OSError, ValueError):
        kst = False
        reasons.append('KST_TIMEZONE_DATA_REQUIRED')
    if not all((ROOT / name).is_file() for name in (
            'deploy/oracle/rehearse_offline.py', 'oneil_runtime_v2/simulation_store.py',
            'oneil_execution_v2/worker.py', 'oneil_execution_v2/broker.py')):
        reasons.append('SOURCE_FILES_MISSING')
    return {'mode': 'ORACLE_OFFLINE_PREPARATION_ONLY', 'live_capable': False,
            'installed': False, 'deployed': False, 'ready_to_trade': False,
            'oracle_linux_9': oracle, 'architecture': platform.machine(),
            'python': platform.python_version(), 'memory_mib': memory,
            'temp_free_mib': free_mib, 'sqlite_available': sqlite_available,
            'kst_timezone_available': kst, 'rehearsal_prerequisites_met': not reasons,
            'reasons': reasons, 'rehearsal': {'status': 'NOT_RUN'},
            'unverified': ['ORACLE_VM_ACCEPTANCE', 'LONG_RUNNING_RESOURCE_USAGE',
                'LINUX_RUNTIME_AND_DASHBOARD_PORT', 'CREDENTIAL_AND_BROKER_INTEGRATION',
                'PRODUCTION_IDENTITY_AND_LEGACY_EXCLUSION', 'POWER_LOSS_AND_RECOVERY']}


def rehearse():
    try:
        return _rehearse()
    except OSError:
        # Creation and cleanup can fail even after a read-only disk-space check.
        return {'status': 'TEMP_STORAGE_FAILED', 'live_capable': False}


def _rehearse():
    # Do not pass account credentials, PYTHONPATH, proxy variables or user startup
    # hooks to the child. Both scripts run with isolated Python and no bytecode.
    env = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'TZ': 'UTC'}
    # File-backed output avoids unbounded PIPE capture. The child's file-size
    # limit bounds its log, and no exception/log content is returned to the user.
    with tempfile.TemporaryDirectory(prefix='oneil-oracle-check-', dir='/tmp') as directory:
        env['TMPDIR'] = directory
        with tempfile.TemporaryFile(dir=directory) as output:
            try:
                completed = subprocess.run([sys.executable, '-I', '-B',
                    str(ROOT/'deploy/oracle/rehearse_offline.py')], cwd=ROOT, env=env,
                    stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                    timeout=45, check=False)
            except subprocess.TimeoutExpired:
                return {'status': 'TIMEOUT', 'live_capable': False}
            except OSError:
                return {'status': 'LAUNCH_FAILED', 'live_capable': False}
            if completed.returncode != 0:
                return {'status': 'FAILED', 'live_capable': False}
            output.seek(0)
            try:
                result = json.loads(output.read(8193))
            except (ValueError, UnicodeError):
                return {'status': 'INVALID_RESULT', 'live_capable': False}
            # Validate every boundary flag rather than accepting a zero exit alone.
            if not valid_result(result):
                return {'status': 'INVALID_RESULT', 'live_capable': False}
            return result


def valid_result(value):
    return (type(value) is dict and set(value) == {'status', 'live_capable',
        'halted_before', 'halted_after', 'simulated_order_calls', 'sqlite_reopened',
        'lock_exclusion', 'temporary_state_removed', 'peak_rss_kib'} and
        value['status'] == 'PASSED' and value['live_capable'] is False and
        all(value[k] is True for k in ('halted_before', 'halted_after',
            'sqlite_reopened', 'lock_exclusion', 'temporary_state_removed')) and
        type(value['simulated_order_calls']) is int and value['simulated_order_calls'] == 0 and
        type(value['peak_rss_kib']) is int and 0 < value['peak_rss_kib'] <= 256 * 1024)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rehearse', action='store_true', help='Run one bounded, halted, temporary SQLite smoke check.')
    args = parser.parse_args()
    result = inspect()
    if args.rehearse:
        if result['rehearsal_prerequisites_met']:
            result['rehearsal'] = rehearse()
        else:
            result['rehearsal'] = {'status': 'BLOCKED', 'live_capable': False}
    print(json.dumps(result, indent=2, sort_keys=True))
    # Exit zero means only this preparation step passed, never installation/live.
    return 0 if result['rehearsal_prerequisites_met'] and (
        not args.rehearse or result['rehearsal']['status'] == 'PASSED') else 2


if __name__ == '__main__':
    raise SystemExit(main())

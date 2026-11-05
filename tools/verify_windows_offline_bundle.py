#!/usr/bin/env python3
"""Verify source ZIP integrity without extracting or executing its contents.

Integrity is not publisher authenticity; compare the ZIP SHA-256 with the trusted
release message as well. A compromised source can make a matching manifest.
"""
import argparse
import hashlib
import json
from pathlib import PurePosixPath
import re
import stat
import sys
import zipfile

MAX_BYTES = 16 * 1024 * 1024
MAX_FILES = 1000
PREFIXES = ('oneil_execution_v2/', 'oneil_control_v2/', 'oneil_monitor_v2/',
            'oneil_runtime_v2/', 'oneil_policy_v2/', 'tests/policy_v2/', 'tests/execution_v2/', 'tests/control_v2/',
            'tests/monitor_v2/', 'tests/runtime_v2/', 'deploy/windows/')
TOOLS = {'tools/run_monitor_v2_tests.py', 'tools/monitor_v2_test_response.py',
         'tools/build_monitor_v2_mock.py', 'tools/build_windows_offline_bundle.py',
         'tools/verify_windows_offline_bundle.py', 'tools/run_execution_v2_tests.py',
         'tools/validate_execution_v2_sql.py'}
SUFFIXES = {'.py', '.md', '.sql', '.mjs', '.html', '.css', '.ps1'}
# This combined release must carry all public implementation modules and the
# exact offline checks. A hash-valid but partial policy/runtime archive is not a
# complete release. Extra allowlisted public source may be added separately.
REQUIRED_FILES = TOOLS | {
    'oneil_execution_v2/' + name for name in (
        '__init__.py', 'api.py', 'broker.py', 'domain.py', 'kis_adapter.py',
        'kis_evidence.py', 'KIS_EVIDENCE.md', 'kis_transport.py', 'KIS_TRANSPORT.md',
        'kis_https.py', 'KIS_HTTPS.md', 'KIS_PUBLIC_SPEC.md',
        'kis_authenticated.py', 'KIS_AUTHENTICATED.md',
        'kis_local_authorization.py', 'kis_windows_credentials.py', 'kis_authenticated_https.py',
        'kis_token_issuer.py', 'KIS_WINDOWS_AUTH.md',
        'kis_polling.py', 'KIS_POLLING.md',
        'store.py', 'worker.py', 'schema.sql', 'README.md', 'KIS_ADAPTER.md', 'recovery.py', 'RECOVERY.md')
} | {
    'oneil_control_v2/' + name for name in (
        '__init__.py', 'app.py', 'bridge.py', 'state.py', 'policy.py',
        'POLICY_COORDINATOR.md', 'README.md')
} | {
    'oneil_monitor_v2/' + name for name in (
        '__init__.py', 'host.py', 'read_model.py', 'README.md',
        'web/app.mjs', 'web/client.mjs', 'web/viewmodel.mjs',
        'web/index.html', 'web/styles.css')
} | {
    'oneil_policy_v2/' + name for name in (
        '__init__.py', 'admission.py', 'evidence.py', 'planner.py', 'policy.py',
        'README.md', 'ADMISSION.md', 'kis_pipeline.py', 'KIS_PIPELINE.md')
} | {
    'oneil_runtime_v2/' + name for name in (
        '__init__.py', 'health.py', 'journal.py', 'preflight.py', 'restart.py',
        'windows_clock.py', 'simulation_store.py', 'simulation_host.py', 'SIMULATION_HOST.md',
        'local_dashboard.py', 'windows_storage.py', 'LOCAL_DASHBOARD.md',
        'web/pair.html', 'web/pair.mjs', 'web/logout.html', 'web/logout.mjs',
        'web/index.html', 'web/simulation.mjs', 'web/simulation.css', 'worker_child.py', 'rehearsal_store.py', 'supervisor.py', 'observer_child.py', 'intent.py', 'state.py', 'orchestrator.py', 'ownership.py', 'ORCHESTRATION.md', 'COMPOSITION.md', 'README.md')
} | {
    'tests/execution_v2/' + name for name in (
        'test_domain.py', 'test_kis_adapter.py', 'test_kis_evidence.py', 'test_kis_transport.py',
        'test_kis_https.py', 'test_kis_authenticated.py',
        'test_kis_local_authorization.py', 'test_kis_windows_credentials.py',
        'test_kis_authenticated_https.py', 'test_kis_token_issuer.py',
        'test_kis_polling.py',
        'kis_evidence_fixtures.py', 'test_postgres.py',
        'test_simulator.py', 'test_worker.py', 'test_recovery.py')
} | {
    'tests/control_v2/test_api.py', 'tests/control_v2/test_bridge.py',
    'tests/control_v2/test_policy_flow.py', 'tests/control_v2/test_component_integration.py',
    'tests/control_v2/test_runtime_policy_flow.py',
    'tests/monitor_v2/client.test.mjs', 'tests/monitor_v2/support.py',
    'tests/monitor_v2/test_integration.py', 'tests/monitor_v2/test_projection.py',
    'tests/policy_v2/policy_support.py', 'tests/policy_v2/test_policy_admission.py',
    'tests/policy_v2/test_policy_import.py', 'tests/policy_v2/test_policy_planner.py',
    'tests/policy_v2/test_policy_store_boundary.py', 'tests/policy_v2/test_kis_pipeline.py',
    'tests/runtime_v2/test_simulation_store.py', 'tests/runtime_v2/test_simulation_host.py',
    'tests/runtime_v2/test_local_dashboard.py',
    'tests/monitor_v2/simulation-dashboard.test.mjs',
    'tests/monitor_v2/local-pairing.test.mjs',
    'tests/runtime_v2/test_supervisor.py', 'tests/runtime_v2/test_worker_composition.py', 'tests/runtime_v2/test_bundle.py', 'tests/runtime_v2/test_health.py',
    'tests/runtime_v2/test_windows_io.py', 'tests/runtime_v2/test_orchestrator.py', 'tests/runtime_v2/test_ownership.py',
    'deploy/windows/RUNBOOK.md', 'deploy/windows/VALIDATION.md',
    'deploy/windows/Start-OfflineRehearsal.ps1', 'deploy/windows/run_offline.py',
    'deploy/windows/SUPERVISOR.md',
    'deploy/windows/INTEGRATION.md',
}
DEVICES = {'CON', 'PRN', 'AUX', 'NUL'} | {f'{p}{i}' for p in ('COM', 'LPT') for i in range(1, 10)}


def allowed(path):
    return (path in TOOLS or path.startswith(PREFIXES)) and PurePosixPath(path).suffix in SUFFIXES


def check_names(names):
    normalized = set()
    for name in names:
        parts = name.split('/')
        # Deliberately ASCII source paths for predictable Windows extraction.
        if any(not re.fullmatch(r'[A-Za-z0-9_.-]+', p) or p in ('.', '..') or
               p.endswith('.') or p.split('.')[0].upper() in DEVICES for p in parts):
            raise ValueError('UNSAFE_WINDOWS_PATH')
        key = name.casefold()
        if key in normalized:
            raise ValueError('WINDOWS_PATH_COLLISION')
        normalized.add(key)
    for name in normalized:
        parts = name.split('/')
        if any('/'.join(parts[:i]) in normalized for i in range(1, len(parts))):
            raise ValueError('FILE_PARENT_CONFLICT')


def verify(path):
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        if not infos or len(infos) > MAX_FILES or len(set(names)) != len(names):
            raise ValueError('EMPTY_DUPLICATE_OR_OVERSIZED_ARCHIVE')
        if sum(x.file_size for x in infos) > MAX_BYTES:
            raise ValueError('ARCHIVE_TOO_LARGE')
        for info in infos:
            name = info.filename
            parts = PurePosixPath(name).parts
            if (not parts or name.startswith('/') or '\\' in name or ':' in name or
                any(x in ('.', '..', '') for x in name.split('/')) or
                stat.S_ISLNK(info.external_attr >> 16) or info.is_dir()):
                raise ValueError('UNSAFE_ARCHIVE_PATH')
        check_names(names)
        if any(name != 'MANIFEST.json' and not allowed(name) for name in names):
            raise ValueError('SOURCE_PATH_NOT_ALLOWLISTED')
        if not REQUIRED_FILES.issubset(names):
            raise ValueError('REQUIRED_SOURCE_MISSING')
        if 'MANIFEST.json' not in names:
            raise ValueError('MANIFEST_MISSING')
        manifest = json.loads(archive.read('MANIFEST.json'))
        if (manifest.get('format') != 'oneil.offline.source.v1' or
            manifest.get('mode') != 'OFFLINE_SIMULATION_SOURCE_ONLY' or
            manifest.get('live_capable') is not False or manifest.get('installed') is not False or
            manifest.get('deployed') is not False or manifest.get('dependencies_bundled') is not False or
            re.fullmatch('[0-9a-f]{40,64}', manifest.get('source_commit', '')) is None or
            type(manifest.get('files')) is not dict):
            raise ValueError('MANIFEST_INVALID')
        files = manifest['files']
        if set(files) != set(names) - {'MANIFEST.json'}:
            raise ValueError('FILE_LIST_MISMATCH')
        for name, expected in files.items():
            data = archive.read(name)
            if (type(expected) is not dict or expected.get('bytes') != len(data) or
                expected.get('sha256') != hashlib.sha256(data).hexdigest()):
                raise ValueError('CONTENT_HASH_MISMATCH')
        return {'verified': True, 'source_commit': manifest['source_commit'],
                'files': len(files), 'live_capable': False, 'publisher_authenticity_verified': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive')
    args = parser.parse_args()
    try:
        print(json.dumps(verify(args.archive), indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as exc:
        print(type(exc).__name__ + ': ' + str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())

#!/usr/bin/env python3
"""Build a deterministic source-only ZIP from an exact clean Git commit.

No dependency downloads, network, credentials, installs or deployment. Only fixed
v2 paths are exported; legacy order scripts, reports, state and .env are excluded.
"""
from pathlib import Path
import argparse
import hashlib
import json
import re
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.verify_windows_offline_bundle import allowed, check_names, REQUIRED_FILES


def git(*args):
    return subprocess.check_output(['git', '-C', str(ROOT), *args])


def build(output):
    output = Path(output)
    if output.exists():
        raise ValueError('OUTPUT_EXISTS')
    if git('status', '--porcelain').strip():
        raise ValueError('COMMIT_CLEAN_SOURCE_BEFORE_PACKAGING')
    revision = git('rev-parse', 'HEAD').decode().strip()
    if re.fullmatch('[0-9a-f]{40,64}', revision) is None:
        raise ValueError('INVALID_REVISION')
    blobs = {}
    for entry in git('ls-tree', '-r', '-z', revision).split(b'\0'):
        if not entry:
            continue
        header, raw_path = entry.split(b'\t', 1)
        path = raw_path.decode('utf-8')
        if not allowed(path):
            continue
        mode, kind, oid = header.decode().split()
        if mode not in ('100644', '100755') or kind != 'blob':
            raise ValueError('NON_REGULAR_SOURCE')
        blobs[path] = git('cat-file', 'blob', oid)
    check_names(list(blobs))
    if not REQUIRED_FILES.issubset(blobs):
        raise ValueError('RUNTIME_SOURCE_MISSING')
    manifest = {
        'format': 'oneil.offline.source.v1', 'source_commit': revision,
        'mode': 'OFFLINE_SIMULATION_SOURCE_ONLY', 'live_capable': False,
        'installed': False, 'deployed': False, 'dependencies_bundled': False,
        'files': {path: {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}
                  for path, data in sorted(blobs.items())},
    }
    blobs['MANIFEST.json'] = (json.dumps(manifest, indent=2, sort_keys=True) + '\n').encode()
    # Fixed metadata: same commit produces identical ZIP bytes.
    with output.open('xb') as destination:
        with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for path, data in sorted(blobs.items()):
                info = zipfile.ZipInfo(path, date_time=(2020, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                archive.writestr(info, data)
    return {'source_commit': revision, 'files': len(blobs) - 1,
            'archive_sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
            'archive_bytes': output.stat().st_size}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(build(args.output), indent=2))
        return 0
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(type(exc).__name__ + ': ' + str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())

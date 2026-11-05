#!/usr/bin/env python3
"""Run all network-free v2 checks, including policy, runtime and frontend contract.

Never opens a socket or broker/database connection. PostgreSQL integration is a
separate acceptance gate; it is intentionally not mislabeled as covered here.
"""
from pathlib import Path
import os
import subprocess
import sys
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def build_suite():
    """Discover the reviewed offline suites, never the PostgreSQL gate."""
    suite=unittest.TestSuite()
    for pattern in ('test_domain.py','test_simulator.py','test_worker.py','test_kis_adapter.py','test_kis_evidence.py','test_kis_transport.py','test_kis_https.py','test_recovery.py'):
        suite.addTests(unittest.TestLoader().discover(str(ROOT/'tests/execution_v2'),pattern=pattern))
    # Use independent loaders/top-level paths to avoid inheriting discover state.
    for path in ('tests/control_v2','tests/monitor_v2','tests/policy_v2','tests/runtime_v2'):
        suite.addTests(unittest.TestLoader().discover(str(ROOT/path),pattern='test_*.py'))
    return suite


def main():
    result=unittest.TextTestRunner(verbosity=2).run(build_suite())
    if not result.wasSuccessful() or result.skipped:
        print('Network-free suite must pass without skips.',file=sys.stderr)
        return 1
    env = {**os.environ, 'ONEIL_V2_TEST_PYTHON': sys.executable}
    return subprocess.run(['node','--test',*map(str,sorted((ROOT/'tests/monitor_v2').glob('*.test.mjs')))],cwd=ROOT,env=env).returncode

if __name__=='__main__':raise SystemExit(main())

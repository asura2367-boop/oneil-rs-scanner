#!/usr/bin/env python3
"""Isolated explicit Linux simulation launcher, not an installer or service."""
from pathlib import Path
import sys

if __name__ == '__main__':
    if not sys.flags.isolated or not sys.dont_write_bytecode:
        print('Use python3.11 -I -B deploy/oracle/run_dashboard.py with explicit arguments.', file=sys.stderr)
        raise SystemExit(2)
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from oneil_runtime_v2.linux_dashboard import main
    raise SystemExit(main())

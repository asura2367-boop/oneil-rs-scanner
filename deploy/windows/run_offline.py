"""Fixed isolated-interpreter entrypoint for the manual PowerShell wrapper."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from oneil_runtime_v2.supervisor import main

if __name__ == '__main__':
    raise SystemExit(main())

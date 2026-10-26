#!/usr/bin/env python3
"""V4.5.1: one KIS token in memory for sequential read-only checks.

No order/cancel endpoints; token is passed only to local subprocess environments.
Never put token into GITHUB_OUTPUT, GITHUB_ENV, artifacts, repository, or logs.
"""
import os
import subprocess
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError

from kis_live_v4_3 import guard
from kis_buying_power_readonly import credentials, request


class SessionBlocked(Exception):
    pass


def acquire_token():
    key, secret, _, _ = credentials()
    try:
        response = request('POST', '/oauth2/tokenP',
                           {'grant_type':'client_credentials', 'appkey':key, 'appsecret':secret})
    except HTTPError as exc:
        status = exc.code if type(exc.code) is int and 100 <= exc.code <= 599 else 'unknown'
        raise SessionBlocked('AUTH_HTTP_' + str(status)) from None
    except URLError:
        raise SessionBlocked('AUTH_TRANSPORT') from None
    except Exception:
        raise SessionBlocked('AUTH_UNEXPECTED') from None
    token = response.get('access_token') if isinstance(response,dict) else None
    if not isinstance(token,str) or not token.strip() or any(c.isspace() for c in token):
        raise SessionBlocked('AUTH_TOKEN_MISSING')
    return token


def run_checks(token, run=subprocess.run):
    env = os.environ.copy()
    env['KIS_SESSION_ACCESS_TOKEN'] = token
    # No output capture or debug printing of subprocess arguments or environment.
    steps = [
        [sys.executable, 'kis_live_v4_1.py', '--output', 'data/live/v4_5_preflight.json'],
        [sys.executable, 'kis_live_v4_4.py', '--report', 'data/live/v4_5_reconcile.json'],
        [sys.executable, 'kis_live_v4_5.py', '--preflight', 'data/live/v4_5_preflight.json',
         '--reconcile', 'data/live/v4_5_reconcile.json', '--state', 'data/live/v4_3_state.json',
         '--output', 'data/live/v4_5_gate_report.json',
         '--run-marker', 'data/live/v4_5_run_marker'],
    ]
    for i,cmd in enumerate(steps,1):
        try:
            result=run(cmd, env=env, check=False)
        except Exception:
            raise SessionBlocked('CHILD_EXECUTION_FAILED_' + str(i)) from None
        if result.returncode != 0:
            raise SessionBlocked('CHILD_CHECK_FAILED_' + str(i))
    return True


def main():
    guard()
    if os.getenv('LIVE_ENABLED','false').lower() != 'false' or os.getenv('KIS_ALLOW_ORDER','false').lower() != 'false':
        raise SessionBlocked('LIVE_NOT_DISABLED')
    # Prevent accidental reuse of an externally provided token in the orchestrator.
    if os.environ.get('KIS_SESSION_ACCESS_TOKEN'):
        raise SessionBlocked('EXTERNAL_SESSION_TOKEN_FORBIDDEN')
    # Old reports must not be reused if a check fails.
    for path in ('data/live/v4_5_preflight.json','data/live/v4_5_reconcile.json',
                 'data/live/v4_5_gate_report.json'):
        Path(path).unlink(missing_ok=True)
    token = acquire_token()
    run_checks(token)
    print('V4.5.1 SESSION CHECK COMPLETE; orders_sent=0; LIVE_ENABLED=false')


if __name__ == '__main__':
    try:
        main()
    except SessionBlocked as exc:
        # Codes are locally generated; do not print exception bodies from APIs.
        print('V4.5.1 BLOCKED: ' + str(exc) + '; orders_sent=0; LIVE_ENABLED=false',file=sys.stderr)
        sys.exit(1)
    except Exception:
        print('V4.5.1 BLOCKED: UNEXPECTED; orders_sent=0; LIVE_ENABLED=false',file=sys.stderr)
        sys.exit(1)

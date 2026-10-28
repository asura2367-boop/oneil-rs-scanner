#!/usr/bin/env python3
"""V5.19: acquire/reuse a KIS token for this Actions run only; NO orders or git writes.

Existing kis_live_v4_5_2 code persists encrypted cache locally, not to the repo.
GitHub Actions runner is ephemeral. Never upload or print the token/cache.
"""
import argparse
import datetime as dt
import os
from pathlib import Path

CACHE = Path('data/live/kis_token_cache.enc.json')


def run(*, enabled=False, env=None, key_loader=None, acquirer=None):
    env = os.environ if env is None else env
    if (env.get('LIVE_ENABLED', 'false').strip().lower() != 'false' or
            env.get('KIS_ALLOW_ORDER', 'false').strip().lower() != 'false'):
        return 'BLOCKED_LIVE_FLAGS'
    if not enabled:
        return 'NOT_REQUESTED'
    if any(not env.get(k, '').strip() for k in
           ('KIS_APP_KEY','KIS_APP_SECRET','KIS_ACCOUNT_NO','KIS_ACCOUNT_PRODUCT','KIS_TOKEN_CACHE_KEY')):
        return 'CREDENTIALS_OR_CACHE_KEY_MISSING'
    try:
        if key_loader is None or acquirer is None:
            from kis_live_v4_5_2 import _key, acquire_token
            key_loader, acquirer = _key, acquire_token
        key = key_loader()
        token, refreshed = acquirer(key, dt.datetime.now(dt.timezone.utc))
        if not isinstance(token, str) or not token or not isinstance(refreshed, bool):
            return 'TOKEN_RESPONSE_INVALID'
        # Token is intentionally discarded, never logged or serialized here.
        return 'READY_THIS_RUN_REFRESHED' if refreshed else 'READY_THIS_RUN_REUSED'
    except Exception:
        # Never log exceptions from credentials, KIS, requests, crypto, or filesystem.
        return 'TOKEN_BOOTSTRAP_BLOCKED'


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--broker-read', action='store_true')
    args = p.parse_args()
    status = run(enabled=args.broker_read)
    print('V519_TOKEN_BOOTSTRAP='+status+'; ORDERS_SENT=0; LIVE_ENABLED=false; KIS_ALLOW_ORDER=false')
    # If a requested token cannot be obtained, the workflow must fail closed.
    if args.broker_read and not status.startswith('READY_THIS_RUN_'):
        raise SystemExit(2)
    if status == 'BLOCKED_LIVE_FLAGS':
        raise SystemExit(2)

if __name__ == '__main__':
    main()

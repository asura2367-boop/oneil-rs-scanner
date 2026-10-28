#!/usr/bin/env python3
"""V5.19 local cached-token diagnostics; no network, token output or orders."""
import datetime as dt
import os
from pathlib import Path

CACHE = Path('data/live/kis_token_cache.enc.json')

def diagnose(env=None, cache_path=CACHE, reader=None):
    env = os.environ if env is None else env
    if env.get('LIVE_ENABLED','false').lower() != 'false' or env.get('KIS_ALLOW_ORDER','false').lower() != 'false':
        return 'BLOCKED_LIVE_FLAGS'
    if not all(env.get(k,'').strip() for k in ('KIS_APP_KEY','KIS_APP_SECRET','KIS_ACCOUNT_NO','KIS_ACCOUNT_PRODUCT')):
        return 'BROKER_CREDENTIALS_MISSING'
    if not env.get('KIS_TOKEN_CACHE_KEY','').strip():
        return 'CACHE_KEY_MISSING'
    if not Path(cache_path).is_file():
        return 'ENCRYPTED_CACHE_MISSING'
    try:
        if reader is None:
            from kis_live_v4_5_2 import _read, _key
            # The existing code only reads local encrypted cache; no HTTP requests.
            cached = _read(_key(), dt.datetime.now(dt.timezone.utc))
        else:
            cached = reader()
        return 'CACHE_PRESENT_AND_VALID' if cached else 'CACHE_EXPIRED_OR_UNAVAILABLE'
    except Exception:
        # Do not print exception details: they may contain paths or secrets.
        return 'CACHE_INVALID_OR_UNREADABLE'

if __name__ == '__main__':
    result = diagnose()
    print('KIS_AUTH_DIAGNOSTIC=' + result + '; NO_NETWORK=true; ORDERS_SENT=0')

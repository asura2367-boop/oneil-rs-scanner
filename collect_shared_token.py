#!/usr/bin/env python3
"""One KIS token for both price & investor collectors, fail closed, no order calls."""
import os
import datetime as dt
from kis_live_v4_5_2 import _key, acquire_token, _persist, UTC, CacheBlocked
from kis_live_v4_3 import guard
import build_data
import build_investor


def main():
    guard()
    if os.getenv('LIVE_ENABLED', 'false').lower()!='false' or os.getenv('KIS_ALLOW_ORDER','false').lower()!='false':
        raise RuntimeError('LIVE_DISABLED_GUARD')
    # No session token on disk in plaintext, environment, job output or logs.
    token, refreshed = acquire_token(_key(), dt.datetime.now(UTC))
    if refreshed:
        _persist()  # Persist ciphertext before any data work; fail closed on push errors.
    print('KIS COLLECTOR TOKEN CACHE_'+('REFRESHED' if refreshed else 'REUSED'), flush=True)
    build_data.main(shared_token=token)
    build_investor.main(shared_token=token)
    print('KIS COLLECTORS DONE; token_requests='+str(int(refreshed))+'; orders_sent=0; LIVE_ENABLED=false', flush=True)

if __name__=='__main__':
    main()

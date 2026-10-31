#!/usr/bin/env python3
"""One KIS token for both price & investor collectors, fail closed, no order calls."""
import os
import datetime as dt
from kis_live_v4_5_2 import _key, acquire_token, _persist, UTC, CacheBlocked
from kis_live_v4_3 import guard


def main(prepare_only=False):
    guard()
    if os.getenv('LIVE_ENABLED', 'false').lower()!='false' or os.getenv('KIS_ALLOW_ORDER','false').lower()!='false':
        raise RuntimeError('LIVE_DISABLED_GUARD')
    if prepare_only:
        from zoneinfo import ZoneInfo
        if dt.datetime.now(ZoneInfo('Asia/Seoul')).weekday()>=5:
            if os.getenv('GITHUB_OUTPUT'):
                with open(os.environ['GITHUB_OUTPUT'],'a') as out:out.write('ready=false\n')
            print('KIS SESSION: WEEKEND_CLOSED; token_requests=0');return
        # Serialized short job; long collectors never hold this issuance lock.
        token,refreshed=acquire_token(_key(),dt.datetime.now(UTC),
            app_credentials=(os.getenv('KIS_APP_KEY',''),os.getenv('KIS_APP_SECRET','')),
            min_validity=dt.timedelta(hours=1))
        if refreshed:_persist()
        if os.getenv('GITHUB_OUTPUT'):
            with open(os.environ['GITHUB_OUTPUT'],'a') as out:out.write('ready=true\n')
        print('KIS SESSION CACHE_'+('REFRESHED' if refreshed else 'REUSED')+
              '; token_requests='+str(int(refreshed))+'; orders_sent=0',flush=True)
        return
    from kis_live_v4_5_2 import required_cached_token
    token=required_cached_token();refreshed=False
    print('KIS COLLECTOR TOKEN CACHE_REUSED; token_requests=0',flush=True)
    import build_data,build_investor
    build_data.main(shared_token=token)
    build_investor.main(shared_token=token)
    print('KIS COLLECTORS DONE; token_requests='+str(int(refreshed))+'; orders_sent=0; LIVE_ENABLED=false', flush=True)

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('--prepare-only',action='store_true')
    main(prepare_only=parser.parse_args().prepare_only)

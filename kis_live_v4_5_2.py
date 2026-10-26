#!/usr/bin/env python3
"""V4.5.2: encrypted cross-run KIS token reuse; strictly read-only.

Only ciphertext is committed. The encryption key lives in GitHub Actions Secrets.
If cache cannot be verified, fail closed. Never store token in logs/artifacts.
"""
import base64
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.error import HTTPError, URLError
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from kis_live_v4_3 import guard
from kis_buying_power_readonly import credentials, request
from kis_live_v4_5_1 import run_checks

CACHE = Path('data/live/kis_token_cache.enc.json')
UTC = dt.timezone.utc
MARGIN = dt.timedelta(minutes=5)

class CacheBlocked(Exception):
    pass

def _key():
    s=os.environ.get('KIS_TOKEN_CACHE_KEY','').strip()
    try:
        value=base64.urlsafe_b64decode(s.encode('ascii'))
    except Exception:
        raise CacheBlocked('CACHE_KEY_INVALID') from None
    if len(value)!=32 or len(s)!=44:
        raise CacheBlocked('CACHE_KEY_INVALID')
    return value

def _expiry(api_data, now):
    # KIS tokenP typically supplies access_token_token_expired in KST.
    value=api_data.get('access_token_token_expired')
    if isinstance(value,str) and value.strip():
        try:
            parsed=dt.datetime.strptime(value.strip(),'%Y-%m-%d %H:%M:%S')
            return parsed.replace(tzinfo=dt.timezone(dt.timedelta(hours=9))).astimezone(UTC)
        except ValueError:
            raise CacheBlocked('AUTH_EXPIRY_INVALID') from None
    seconds=api_data.get('expires_in')
    try:
        seconds=int(seconds)
    except (TypeError,ValueError):
        raise CacheBlocked('AUTH_EXPIRY_MISSING') from None
    if not 60<=seconds<=86400*2:
        raise CacheBlocked('AUTH_EXPIRY_INVALID')
    return now+dt.timedelta(seconds=seconds)

def _read(key, now):
    if not CACHE.exists():
        return None
    try:
        meta=json.loads(CACHE.read_text(encoding='utf8'))
        if set(meta)!={'version','nonce','ciphertext'} or meta['version']!=1:
            raise ValueError('bad cache schema')
        nonce=base64.b64decode(meta['nonce'],validate=True)
        ciphertext=base64.b64decode(meta['ciphertext'],validate=True)
        if len(nonce)!=12: raise ValueError('bad nonce')
        plain=AESGCM(key).decrypt(nonce,ciphertext,b'oneil-kis-token-v1')
        obj=json.loads(plain)
        if set(obj)!={'token','expires_at'}: raise ValueError('bad payload')
        expiry=dt.datetime.fromisoformat(obj['expires_at'])
        if expiry.tzinfo is None: raise ValueError('naive expiry')
        token=obj['token']
        if not isinstance(token,str) or not token or any(c.isspace() for c in token):
            raise ValueError('invalid token')
        return (token,expiry) if expiry>now+MARGIN else None
    except Exception:
        # Never fall back to a fresh token if cache is corrupted or key rotated.
        raise CacheBlocked('CACHE_DECRYPT_OR_FORMAT_FAILURE') from None

def _save(key, token, expiry):
    import secrets
    CACHE.parent.mkdir(parents=True,exist_ok=True)
    nonce=secrets.token_bytes(12)
    payload=json.dumps({'token':token,'expires_at':expiry.isoformat()},separators=(',',':')).encode()
    blob=AESGCM(key).encrypt(nonce,payload,b'oneil-kis-token-v1')
    data={'version':1,'nonce':base64.b64encode(nonce).decode(),
          'ciphertext':base64.b64encode(blob).decode()}
    tmp=CACHE.with_suffix('.tmp')
    tmp.write_text(json.dumps(data,sort_keys=True)+'\n',encoding='utf8')
    os.chmod(tmp,0o600)
    tmp.replace(CACHE)

def _persist():
    # Only known encrypted file can be written to Git; no git add -A.
    commands=[['git','config','user.name','github-actions[bot]'],
              ['git','config','user.email','41898282+github-actions[bot]@users.noreply.github.com'],
              ['git','add','--',str(CACHE)],
              ['git','commit','-m','chore: refresh encrypted KIS read-only token cache'],
              ['git','push','origin','HEAD:main']]
    for cmd in commands:
        try:
            proc=subprocess.run(cmd,capture_output=True,text=False,timeout=25,check=False)
        except Exception:
            raise CacheBlocked('CACHE_PERSIST_FAILURE') from None
        if proc.returncode!=0:
            raise CacheBlocked('CACHE_PERSIST_FAILURE')

def acquire_token(key, now):
    cached=_read(key,now)
    if cached:
        return cached[0],False
    app_key,app_secret,_,_=credentials()
    try:
        response=request('POST','/oauth2/tokenP',
                         {'grant_type':'client_credentials','appkey':app_key,'appsecret':app_secret})
    except HTTPError as exc:
        code=exc.code if type(exc.code) is int and 100<=exc.code<=599 else 'unknown'
        raise CacheBlocked('AUTH_HTTP_'+str(code)) from None
    except URLError:
        raise CacheBlocked('AUTH_TRANSPORT') from None
    except Exception:
        raise CacheBlocked('AUTH_FAILURE') from None
    token=response.get('access_token') if isinstance(response,dict) else None
    if not isinstance(token,str) or not token or any(c.isspace() for c in token):
        raise CacheBlocked('AUTH_TOKEN_MISSING')
    expiry=_expiry(response,now)
    if expiry<=now+MARGIN:
        raise CacheBlocked('AUTH_TOO_CLOSE_TO_EXPIRY')
    _save(key,token,expiry)
    return token,True

def main():
    guard()
    if os.getenv('LIVE_ENABLED','false').lower()!='false' or os.getenv('KIS_ALLOW_ORDER','false').lower()!='false':
        raise CacheBlocked('LIVE_NOT_DISABLED')
    if os.environ.get('KIS_SESSION_ACCESS_TOKEN'):
        raise CacheBlocked('EXTERNAL_SESSION_TOKEN_FORBIDDEN')
    if os.getenv('GITHUB_EVENT_NAME') not in ('workflow_dispatch',None):
        raise CacheBlocked('UNSAFE_EVENT')
    # Output files from previous runs must never count as fresh validation.
    for name in ('data/live/v4_5_preflight.json','data/live/v4_5_reconcile.json',
                 'data/live/v4_5_gate_report.json'):
        Path(name).unlink(missing_ok=True)
    key=_key()
    now=dt.datetime.now(UTC)
    token,refreshed=acquire_token(key,now)
    if refreshed:
        _persist()  # Fail closed if cache not committed for future runs.
    run_checks(token)
    print('V4.5.2 CACHE_'+('REFRESHED' if refreshed else 'REUSED')+
          '; V4.5 FINAL GATE: DENY; orders_sent=0; LIVE_ENABLED=false')

if __name__=='__main__':
    try: main()
    except CacheBlocked as exc:
        print('V4.5.2 BLOCKED: '+str(exc)+'; orders_sent=0; LIVE_ENABLED=false',file=sys.stderr)
        sys.exit(1)
    except Exception:
        print('V4.5.2 BLOCKED: UNEXPECTED; orders_sent=0; LIVE_ENABLED=false',file=sys.stderr)
        sys.exit(1)

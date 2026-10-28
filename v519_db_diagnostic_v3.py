"""Read-only, credential-safe PostgreSQL connection diagnosis."""
import os, socket, ssl, sys
from urllib.parse import urlsplit

def emit(stage, code):
    print(f'DIAG_STAGE={stage} DIAG={code}', flush=True)

def main():
    raw=os.environ.get('SUPABASE_DB_URL','')
    if not raw: emit('URL','SECRET_MISSING'); return 2
    try:
        u=urlsplit(raw)
        if u.scheme not in ('postgresql','postgres') or not (u.hostname and u.username and u.password):
            emit('URL','INVALID');return 2
        host=u.hostname;port=u.port or 5432
    except (ValueError,TypeError): emit('URL','PARSE_FAILED');return 2
    try:
        with socket.create_connection((host,port),timeout=7) as s:
            emit('TCP','OK')
            # PostgreSQL SSLRequest: length=8, code=80877103
            s.sendall(bytes.fromhex('0000000804d2162f'))
            reply=s.recv(1)
            if reply==b'S':emit('PG_SSL_REQUEST','SUPPORTED')
            elif reply==b'N':emit('PG_SSL_REQUEST','NOT_SUPPORTED');return 2
            else:emit('PG_SSL_REQUEST','UNEXPECTED_REPLY');return 2
    except socket.timeout:emit('TCP','TIMEOUT');return 2
    except (OSError,ValueError):emit('TCP','FAILED');return 2
    try:
        import psycopg
        with psycopg.connect(raw,connect_timeout=8,sslmode='require') as c:
            with c.cursor() as cur:cur.execute('SELECT 1');assert cur.fetchone()[0]==1
        emit('POSTGRES','CONNECTION_OK');return 0
    except Exception as e:
        # Only use structured SQLSTATE / exception class. Never print exception strings,
        # which can contain the supplied URL, usernames, hosts, or credentials.
        state=getattr(e,'sqlstate',None)
        if state=='28P01':code='PASSWORD_AUTH_FAILED'
        elif state=='28000':code='AUTHORIZATION_FAILED'
        elif state=='3D000':code='DATABASE_NOT_FOUND'
        elif state and state.startswith('08'):code='CONNECTION_SQLSTATE_08'
        elif state:code='OTHER_SQLSTATE_'+state if state.isalnum() and len(state)==5 else 'OTHER_SQLSTATE'
        elif isinstance(e,ssl.SSLError):code='TLS_FAILURE'
        elif isinstance(e,TimeoutError):code='TIMEOUT'
        else:code='UNCLASSIFIED_'+type(e).__name__ if type(e).__name__.isidentifier() else 'UNCLASSIFIED'
        emit('POSTGRES',code);return 2
if __name__=='__main__':sys.exit(main())

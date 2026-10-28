import os, sys, socket
from urllib.parse import urlsplit

url = os.environ.get('SUPABASE_DB_URL', '')
if not url:
    print('DIAG=SECRET_MISSING'); sys.exit(2)
try:
    p = urlsplit(url)
    if p.scheme not in ('postgresql','postgres') or not p.hostname or not p.username or not p.password:
        print('DIAG=URL_INCOMPLETE'); sys.exit(2)
    host, port = p.hostname, p.port or 5432
except (ValueError, TypeError):
    print('DIAG=URL_PARSE_FAILED'); sys.exit(2)
try:
    socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
except OSError:
    print('DIAG=DNS_FAILED'); sys.exit(2)
try:
    import psycopg
    with psycopg.connect(url, connect_timeout=8, sslmode='require') as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT 1')
            assert cur.fetchone()[0] == 1
    print('DIAG=CONNECTION_OK')
except psycopg.OperationalError as e:
    s = str(e).lower()
    if 'password authentication failed' in s or 'invalid password' in s:
        code = 'AUTH_FAILED'
    elif 'tenant or user not found' in s or 'user not found' in s:
        code = 'POOLER_USER_OR_PROJECT_INVALID'
    elif 'timeout' in s or 'timed out' in s:
        code = 'NETWORK_TIMEOUT'
    elif 'name or service not known' in s or 'could not translate host' in s:
        code = 'DNS_FAILED'
    elif 'too many connections' in s:
        code = 'CONNECTION_LIMIT'
    elif 'ssl' in s or 'certificate' in s:
        code = 'TLS_ERROR'
    else:
        code = 'CONNECTION_ERROR_OTHER'
    print('DIAG='+code); sys.exit(2)
except Exception:
    print('DIAG=UNEXPECTED_ERROR'); sys.exit(2)

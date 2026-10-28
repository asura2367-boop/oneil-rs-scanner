"""Safe Supabase connectivity diagnostic: no orders, no SQL writes, no credentials logged."""
import os
import socket
import sys
from urllib.parse import urlsplit


def report(code):
    print('DIAG=' + code)
    return 0 if code == 'CONNECTION_OK' else 2


def main():
    url = os.environ.get('SUPABASE_DB_URL', '')
    if not url:
        return report('SECRET_MISSING')
    try:
        p = urlsplit(url)
        if p.scheme not in ('postgresql', 'postgres') or not all((p.hostname, p.username, p.password)):
            return report('URL_INCOMPLETE')
        port = p.port or 5432
        host = p.hostname
    except (ValueError, TypeError):
        return report('URL_PARSE_FAILED')
    try:
        socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        return report('DNS_FAILED')
    try:
        import psycopg
        with psycopg.connect(url, connect_timeout=10, sslmode='require') as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT 1')
                if cur.fetchone() != (1,):
                    return report('QUERY_FAILED')
        return report('CONNECTION_OK')
    except Exception as exc:
        # Never print exception messages: they can include URLs, hostnames or usernames.
        sqlstate = getattr(exc, 'sqlstate', None)
        if sqlstate == '28P01':
            return report('AUTH_PASSWORD_REJECTED')
        if sqlstate == '28000':
            return report('AUTH_ROLE_OR_TENANT_REJECTED')
        if sqlstate == '3D000':
            return report('DATABASE_NOT_FOUND')
        if sqlstate == '42501':
            return report('PERMISSION_DENIED')
        if sqlstate and sqlstate.startswith('08'):
            return report('SERVER_CONNECTION_EXCEPTION')
        if sqlstate:
            # SQLSTATE is a fixed 5-character PostgreSQL code, never a credential.
            return report('SQLSTATE_' + sqlstate if len(sqlstate) == 5 and sqlstate.isalnum() else 'SQLSTATE_UNKNOWN')
        name = type(exc).__name__
        if name in ('OperationalError', 'InterfaceError'):
            return report('TRANSPORT_OR_TLS_ERROR')
        return report('UNCLASSIFIED_ERROR')


if __name__ == '__main__':
    sys.exit(main())

"""Durable local authentication/command journal; never reads environment secrets.

Only a trusted identity adapter may install pre-issued session digests. There is
no HTTP session creation endpoint. This module creates no login credentials.
SQLite must be on a private, durable local volume, shared by all service workers.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import hmac
import json
import os
from pathlib import Path
import stat
from urllib.parse import quote
import re
import sqlite3


class APIError(Exception):
    def __init__(self, status, code):
        self.status, self.code = status, code
        super().__init__(code)


def check(value, status, code):
    if not value:
        raise APIError(status, code)


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


@dataclass(frozen=True)
class SessionRecord:
    token_digest: str
    csrf_digest: str
    principal: str
    account: str
    scopes: frozenset
    expires_at: int
    authenticated_at: int

    def validate(self):
        check(all(isinstance(x, str) and re.fullmatch('[0-9a-f]{64}', x)
                  for x in (self.token_digest, self.csrf_digest, self.account)), 503, 'INVALID_SESSION_CONFIG')
        check(isinstance(self.principal, str) and re.fullmatch('[A-Za-z0-9:@._-]{1,128}', self.principal), 503, 'INVALID_SESSION_CONFIG')
        check(type(self.scopes) is frozenset and self.scopes and
              self.scopes <= {'monitor.read', 'simulation.command'}, 503, 'INVALID_SESSION_CONFIG')
        check(type(self.expires_at) is int and type(self.authenticated_at) is int and
              0 < self.expires_at - self.authenticated_at <= 3600, 503, 'INVALID_SESSION_CONFIG')
        return self


SCHEMA = '''
CREATE TABLE IF NOT EXISTS sessions (
 token_digest TEXT PRIMARY KEY, csrf_digest TEXT NOT NULL,
 principal TEXT NOT NULL, account TEXT NOT NULL, scopes TEXT NOT NULL,
 expires_at INTEGER NOT NULL, authenticated_at INTEGER NOT NULL,
 revoked INTEGER NOT NULL DEFAULT 0 CHECK(revoked IN (0,1))
);
CREATE TABLE IF NOT EXISTS nonces (
 session_digest TEXT NOT NULL, nonce TEXT NOT NULL, expires_at INTEGER NOT NULL,
 PRIMARY KEY(session_digest, nonce)
);
CREATE TABLE IF NOT EXISTS rate_windows (
 session_digest TEXT NOT NULL, minute INTEGER NOT NULL, count INTEGER NOT NULL,
 PRIMARY KEY(session_digest, minute)
);
CREATE TABLE IF NOT EXISTS commands (
 principal TEXT NOT NULL, account TEXT NOT NULL, idempotency_key TEXT NOT NULL,
 request_digest TEXT NOT NULL, phase TEXT NOT NULL CHECK(phase IN ('STARTED','COMPLETED')),
 response_status INTEGER, response_body TEXT, created_at INTEGER NOT NULL,
 PRIMARY KEY(principal, account, idempotency_key)
);
CREATE TABLE IF NOT EXISTS confirmations (
 confirmation_id TEXT PRIMARY KEY, session_digest TEXT NOT NULL,
 content TEXT NOT NULL, content_digest TEXT NOT NULL, expires_at INTEGER NOT NULL,
 consumed INTEGER NOT NULL DEFAULT 0 CHECK(consumed IN (0,1))
);
CREATE TABLE IF NOT EXISTS audit (
 sequence INTEGER PRIMARY KEY AUTOINCREMENT, at INTEGER NOT NULL,
 principal TEXT NOT NULL, account TEXT NOT NULL,
 idempotency_key TEXT NOT NULL, event TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit
 BEGIN SELECT RAISE(ABORT, 'APPEND_ONLY_AUDIT'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit
 BEGIN SELECT RAISE(ABORT, 'APPEND_ONLY_AUDIT'); END;
'''


class ControlState:
    def __init__(self, path):
        check(str(path) not in ('', ':memory:') and not str(path).startswith('file:'), 503, 'DURABLE_FILE_REQUIRED')
        self.path = str(Path(path).absolute())

    def _connection(self):
        info = os.stat(self.path, follow_symlinks=False)
        check(stat.S_ISREG(info.st_mode) and info.st_mode & 0o077 == 0,
              503, 'PRIVATE_JOURNAL_FILE_REQUIRED')
        # mode=rw prevents an incorrect/missing configured path from creating a
        # second empty journal. Never silently start over after state loss.
        conn = sqlite3.connect('file:' + quote(self.path, safe='/') + '?mode=rw',
                               uri=True, timeout=2, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA synchronous=FULL')
        conn.execute('PRAGMA busy_timeout=2000')
        return conn

    @contextmanager
    def transaction(self):
        conn = self._connection()
        try:
            conn.execute('BEGIN IMMEDIATE')
            yield conn
            conn.execute('COMMIT')
        except BaseException:
            if conn.in_transaction:
                conn.execute('ROLLBACK')
            raise
        finally:
            conn.close()

    def initialize(self):
        """Explicit local setup only; not called by HTTP requests."""
        parent = os.stat(Path(self.path).parent, follow_symlinks=False)
        check(stat.S_ISDIR(parent.st_mode) and parent.st_mode & 0o077 == 0,
              503, 'PRIVATE_JOURNAL_DIRECTORY_REQUIRED')
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(descriptor)
        conn = self._connection()
        try:
            conn.execute('PRAGMA journal_mode=WAL')
            conn.executescript(SCHEMA)
        finally:
            conn.close()

    def install_session_record(self, record):
        """Trusted identity-provider callback only. Digests, never raw credentials.

        The external issuer must prove account ownership and generate high-entropy
        tokens. Refuse overwrite/reuse, including revival of a revoked session.
        """
        record.validate()
        with self.transaction() as conn:
            conn.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?,?,0)',
                         (record.token_digest, record.csrf_digest, record.principal,
                          record.account, canonical(sorted(record.scopes)),
                          record.expires_at, record.authenticated_at))

    def revoke_session(self, token_digest):
        with self.transaction() as conn:
            conn.execute('UPDATE sessions SET revoked=1 WHERE token_digest=?', (token_digest,))

    def _session(self, conn, token_digest, now):
        row = conn.execute('SELECT * FROM sessions WHERE token_digest=?', (token_digest,)).fetchone()
        check(row is not None and row['revoked'] == 0 and
              row['authenticated_at'] <= now < row['expires_at'], 401, 'AUTHENTICATION_REQUIRED')
        return SessionRecord(row['token_digest'], row['csrf_digest'], row['principal'], row['account'],
                             frozenset(json.loads(row['scopes'])), row['expires_at'], row['authenticated_at']).validate()

    def authenticate(self, raw_token, now):
        check(isinstance(raw_token, str) and re.fullmatch('[A-Za-z0-9_-]{43,128}', raw_token), 401, 'AUTHENTICATION_REQUIRED')
        conn = self._connection()
        try:
            return self._session(conn, digest(raw_token), now)
        finally:
            conn.close()

    def _consume_attempt(self, session, nonce, request_timestamp, clock):
        # Commit replay/rate accounting even when later confirmation/idempotency
        # validation fails. An invalid command cannot recycle a captured nonce.
        with self.transaction() as conn:
            # Read time only after BEGIN IMMEDIATE acquires its lock. Body reads
            # and journal contention must not preserve an earlier authorization.
            now = int(clock())
            current = self._session(conn, session.token_digest, now)
            check(current == session, 401, 'SESSION_CHANGED')
            check(abs(now - request_timestamp) <= 30, 409, 'REQUEST_EXPIRED')
            conn.execute('DELETE FROM nonces WHERE expires_at<?', (now,))
            conn.execute('DELETE FROM rate_windows WHERE minute<?', (now // 60 - 2,))
            row = conn.execute('SELECT count FROM rate_windows WHERE session_digest=? AND minute=?',
                               (session.token_digest, now // 60)).fetchone()
            check(row is None or row['count'] < 60, 429, 'RATE_LIMITED')
            check(conn.execute('SELECT 1 FROM nonces WHERE session_digest=? AND nonce=?',
                               (session.token_digest, nonce)).fetchone() is None, 409, 'REPLAY_DETECTED')
            conn.execute('INSERT INTO rate_windows VALUES(?,?,1) ON CONFLICT(session_digest,minute) DO UPDATE SET count=count+1',
                         (session.token_digest, now // 60))
            conn.execute('INSERT INTO nonces VALUES(?,?,?)', (session.token_digest, nonce, now + 61))

    def reserve(self, session, *, key, request_digest, nonce, request_timestamp, clock, confirmation=None):
        """Commit once before any core effect. A crash leaves a permanent UNKNOWN.

        A new network attempt needs a new nonce, while its key and content stay
        identical. Completed retries return the saved receipt, not a fresh effect.
        """
        self._consume_attempt(session, nonce, request_timestamp, clock)
        with self.transaction() as conn:
            # Recheck after this second lock acquisition, including for cached
            # receipts. Never use the request-start timestamp for admission.
            now = int(clock())
            current = self._session(conn, session.token_digest, now)
            check(current == session, 401, 'SESSION_CHANGED')
            check(abs(now - request_timestamp) <= 30, 409, 'REQUEST_EXPIRED')
            args = (session.principal, session.account, key)
            prior = conn.execute('SELECT * FROM commands WHERE principal=? AND account=? AND idempotency_key=?', args).fetchone()
            if prior:
                check(hmac.compare_digest(prior['request_digest'], request_digest), 409, 'IDEMPOTENCY_CONFLICT')
                check(prior['phase'] == 'COMPLETED', 409, 'COMMAND_OUTCOME_UNKNOWN')
                return (prior['response_status'], json.loads(prior['response_body'])), None
            content = None
            if confirmation is not None:
                confirmation_id, content_digest = confirmation
                row = conn.execute('SELECT * FROM confirmations WHERE confirmation_id=?', (confirmation_id,)).fetchone()
                check(row is not None and row['session_digest'] == session.token_digest, 403, 'CONFIRMATION_INVALID')
                check(hmac.compare_digest(row['content_digest'], content_digest), 409, 'CONFIRMATION_CONTENT_CHANGED')
                check(row['consumed'] == 0, 409, 'CONFIRMATION_ALREADY_USED')
                check(now < row['expires_at'], 409, 'CONFIRMATION_EXPIRED')
                content = json.loads(row['content'])
                conn.execute('UPDATE confirmations SET consumed=1 WHERE confirmation_id=?', (confirmation_id,))
            conn.execute("INSERT INTO commands VALUES(?,?,?,?,'STARTED',NULL,NULL,?)", (*args, request_digest, now))
            conn.execute('INSERT INTO audit(at,principal,account,idempotency_key,event) VALUES(?,?,?,?,?)',
                         (now, *args, 'COMMAND_RESERVED'))
            return None, content

    def complete(self, session, key, status, response, now, *, preview=None):
        with self.transaction() as conn:
            if preview:
                token, content, expires = preview
                conn.execute('INSERT INTO confirmations VALUES(?,?,?,?,?,0)',
                             (token, session.token_digest, canonical(content), digest(canonical(content)), expires))
            args = (session.principal, session.account, key)
            cursor = conn.execute("UPDATE commands SET phase='COMPLETED',response_status=?,response_body=? WHERE principal=? AND account=? AND idempotency_key=? AND phase='STARTED'",
                                  (status, canonical(response), *args))
            check(cursor.rowcount == 1, 503, 'COMMAND_OUTCOME_UNKNOWN')
            conn.execute('INSERT INTO audit(at,principal,account,idempotency_key,event) VALUES(?,?,?,?,?)',
                         (now, *args, 'COMMAND_COMPLETED'))

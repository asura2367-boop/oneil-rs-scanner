"""Durable, non-clearing incident latch for offline operations rehearsal.

No order state is stored or altered here. No cross-database atomicity is claimed.
A future host must enforce the real account halt in its authoritative core too.
"""
from pathlib import Path
import re
import sqlite3

from .health import HealthResult


class IncidentJournal:
    @classmethod
    def initialize(cls, path):
        path = Path(path)
        # Exclusive create. Never overwrite a journal or copy a template database.
        with path.open('xb'):
            pass
        connection = None
        try:
            connection = sqlite3.connect(str(path), isolation_level=None)
            connection.execute('PRAGMA synchronous=FULL')
            connection.executescript('''
                BEGIN IMMEDIATE;
                CREATE TABLE metadata (version INTEGER NOT NULL CHECK(version=1));
                INSERT INTO metadata VALUES(1);
                CREATE TABLE incident (reason TEXT PRIMARY KEY,
                    first_seen TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')));
                INSERT INTO incident(reason) VALUES('STARTUP_RECONCILIATION_REQUIRED');
                COMMIT;
            ''')
        finally:
            # An incomplete file is preserved for inspection, not silently reset.
            if connection is not None:
                connection.close()
        return cls(path)

    def __init__(self, path):
        path = Path(path)
        if path.is_symlink() or not path.is_file():
            raise ValueError('JOURNAL_MISSING_OR_UNSAFE')
        # mode=rw is deliberate: no fresh empty state if disk/path is missing.
        self.connection = sqlite3.connect(path.resolve().as_uri() + '?mode=rw', uri=True,
                                          isolation_level=None, timeout=2)
        self.connection.execute('PRAGMA synchronous=FULL')
        try:
            row = self.connection.execute('SELECT version FROM metadata').fetchall()
            if row != [(1,)]:
                raise ValueError('JOURNAL_VERSION_INVALID')
            self.reasons()  # Validate existing records before accepting observations.
        except Exception:
            self.close()
            raise

    def record(self, result):
        if type(result) is not HealthResult or result.operationally_healthy != (not result.reasons):
            raise ValueError('INVALID_HEALTH_RESULT')
        if any(type(reason) is not str or re.fullmatch('[A-Z][A-Z0-9_]{0,95}', reason) is None
               for reason in result.reasons):
            raise ValueError('INVALID_REASON_CODE')
        if result.reasons:
            self.connection.execute('BEGIN IMMEDIATE')
            try:
                self.connection.executemany('INSERT OR IGNORE INTO incident(reason) VALUES(?)',
                                            [(reason,) for reason in result.reasons])
                self.connection.execute('COMMIT')
            except Exception:
                self.connection.execute('ROLLBACK')
                raise
        return self.reasons()

    def reasons(self):
        rows = self.connection.execute('SELECT reason FROM incident ORDER BY reason').fetchall()
        if not rows:
            raise ValueError('JOURNAL_EMPTY_OR_INCONSISTENT')
        for row in rows:
            if type(row[0]) is not str or re.fullmatch('[A-Z][A-Z0-9_]{0,95}', row[0]) is None:
                raise ValueError('JOURNAL_CORRUPT')
        return tuple(row[0] for row in rows)

    def close(self):
        self.connection.close()

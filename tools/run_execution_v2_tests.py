#!/usr/bin/env python3
"""Run unit and real PostgreSQL tests on a newly created private local cluster.

Use an installed PostgreSQL bin directory with --postgres-bin. No download,
production credentials, TCP listener, live broker access, or persistent service.
"""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--postgres-bin',required=True,type=Path)
    args=parser.parse_args()
    import psycopg
    with tempfile.TemporaryDirectory(prefix='oneil-v2-pg-') as temp:
        data=Path(temp)/'data'; socket=Path(temp)/'socket'; socket.mkdir()
        def pg(command,*arguments):

            try:
                subprocess.run([str(args.postgres_bin/command),*map(str,arguments)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
            except subprocess.CalledProcessError as exc:
                print(exc.stderr.decode(), file=sys.stderr)
                log=Path(temp)/'postgres.log'
                if log.exists(): print(log.read_text(), file=sys.stderr)
                raise
        pg('initdb','-D',data,'--auth-local=trust','--auth-host=reject','-U','postgres','--no-locale','--encoding=UTF8')
        started=False
        try:
            pg('pg_ctl','-D',data,'-l',Path(temp)/'postgres.log','-o',f"-h '' -k {socket}",'-w','start')
            started=True
            dsn=f'host={socket} user=postgres dbname=postgres'
            with psycopg.connect(dsn,autocommit=True) as conn:
                conn.execute('CREATE DATABASE oneil_v2_test')
            os.environ['ONEIL_V2_TEST_DSN']=f'host={socket} user=postgres dbname=oneil_v2_test'
            suite=unittest.defaultTestLoader.discover(str(ROOT/'tests'/'execution_v2'),pattern='test_*.py')
            result=unittest.TextTestRunner(verbosity=2).run(suite)
            if result.skipped:
                print('ERROR: integration validation may not skip tests',file=sys.stderr)
                return 1
            return 0 if result.wasSuccessful() else 1
        finally:
            if started: pg('pg_ctl','-D',data,'-m','immediate','-w','stop')

if __name__=='__main__': raise SystemExit(main())

#!/usr/bin/env python3
"""Emit a real authenticated in-process API response using public test doubles.

Only a test utility. Does not start a listener or accept real credentials.
"""
import argparse
import json
from pathlib import Path
import sys
import tempfile
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests/monitor_v2')]
from support import create_harness,invoke

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--scenario',default='authenticated',choices=['authenticated','expired','forbidden','unavailable','stale_worker','stale_broker','unknown'])
args=parser.parse_args()
with tempfile.TemporaryDirectory(prefix='oneil-ui-test-') as temp:
    app,_,_,_=create_harness(temp,args.scenario)
    status,body,_=invoke(app)
# ASCII JSON is also UTF-8 and survives Windows locale-encoded stdout pipes.
print(json.dumps({'status':status,'body':body},ensure_ascii=True))

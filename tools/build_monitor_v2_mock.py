#!/usr/bin/env python3
"""Generate a clearly labeled static UI replay of in-process authenticated tests.

No server, real cookie, login, network, broker or production database. SQLite files
and conspicuous public fixture sessions are temporary and destroyed immediately.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
spec=importlib.util.spec_from_file_location('monitor_test_support',ROOT/'tests/monitor_v2/support.py')
support=importlib.util.module_from_spec(spec);spec.loader.exec_module(support)
SCENARIOS=('authenticated','expired','forbidden','unavailable','stale_worker','stale_broker','unknown')


def build(destination):
    destination=Path(destination).resolve()
    if destination.exists() and any(destination.iterdir()):
        raise ValueError('DESTINATION_MUST_BE_EMPTY')
    destination.mkdir(parents=True,exist_ok=True)
    dist=destination/'dist';shutil.copytree(ROOT/'oneil_monitor_v2/web',dist)
    (dist/'fixtures').mkdir()
    for scenario in SCENARIOS:
        with tempfile.TemporaryDirectory(prefix='oneil-monitor-mock-') as temp:
            app,_,_,_=support.create_harness(temp,scenario)
            status,body,_=support.invoke(app)
        if status==200:
            body['source']='MOCK_AUTHENTICATED_SIMULATION'
            body['owner_authorization']={'state':'MOCK_SESSION_READ_ONLY'}
        output={'http_status':status,'body':body,'test_only':True}
        (dist/'fixtures'/f'{scenario}.json').write_text(json.dumps(output,ensure_ascii=False),encoding='utf-8')
    page=(dist/'index.html').read_text(encoding='utf-8')
    page=page.replace('data-monitor-mode="authenticated"','data-monitor-mode="mock"').replace('SIMULATION / READ ONLY','MOCK / SIMULATION ONLY').replace('인증 확인 전 · 실거래 주문·활성화 기능 없음','인증 동작 모의 검증 · 가상 코어 · 실제 로그인 없음')
    (dist/'index.html').write_text(page,encoding='utf-8')
    (dist/'qa-mobile.html').write_text('<!doctype html><html><meta charset="utf-8"><title>Mock monitor narrow QA</title><style>body{background:#30343b;color:white;font:12px system-ui}iframe{width:390px;height:844px;border:1px solid #667}</style><p>MOCK QA ONLY · 390px iframe, not a device emulator</p><iframe src="index.html" title="Narrow mock monitor"></iframe></html>',encoding='utf-8')
    (destination/'.openai').mkdir()
    (destination/'.openai/hosting.json').write_text('{"static":{"directory":"dist"}}\n',encoding='utf-8')
    (destination/'README.txt').write_text('Static mocked authentication/monitor integration. No credentials, backend, live connection or command capability. Start only with approved local preview tooling.\n',encoding='utf-8')
    return destination

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();print(build(args.output))

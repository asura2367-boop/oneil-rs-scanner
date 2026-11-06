"""Explicit Linux local simulation console; no broker credentials or live orders."""
import argparse
from contextlib import contextmanager
from datetime import datetime
import json
import os
from pathlib import Path
import sys

from oneil_execution_v2.domain import RiskPolicy, require
from oneil_policy_v2.policy import TradingPolicy
from . import local_dashboard
from .linux_clock import elapsed_linux, sample_linux_clock
from .linux_storage import inspect_private_path, inspect_profile, require_linux_user
from .simulation_host import CONFIG, MODE, initialize_simulation_host
from .supervisor import ACCOUNT


@contextmanager
def private_creation():
    # Restricts only files created by this single-threaded command. Never repairs
    # permissions on existing user files. Keep in force for SQLite journal files.
    previous = os.umask(0o077)
    try:
        yield
    finally:
        os.umask(previous)


def read_json(path):
    path = inspect_private_path(path)
    require(path.stat().st_size <= 64 * 1024, 'BOUNDED_LOCAL_JSON_REQUIRED')
    return json.loads(path.read_text(encoding='utf-8'))


def check_profile(directory, cert, key, port):
    require_linux_user()
    require(type(port) is int and 1024 <= port <= 65535, 'EXPLICIT_UNPRIVILEGED_PORT_REQUIRED')
    directory = inspect_profile(directory)
    for path in (cert, key):
        inspect_private_path(path)
    config = read_json(directory / CONFIG)
    require(type(config) is dict and set(config) == {'version', 'mode', 'origin', 'account',
            'policy_version', 'production_identity_available'} and type(config['version']) is int
            and config['version'] == 1 and config['mode'] == MODE and config['account'] == ACCOUNT
            and config['production_identity_available'] is False
            and config['origin'] == f'https://127.0.0.1:{port}', 'EXACT_SIMULATION_ORIGIN_REQUIRED')
    sample_linux_clock()


def initialize(directory, policy_path, positions_path, cash_krw, port):
    require_linux_user()
    require(type(port) is int and 1024 <= port <= 65535, 'EXPLICIT_UNPRIVILEGED_PORT_REQUIRED')
    directory = Path(directory)
    require(directory.is_absolute() and '..' not in directory.parts, 'ABSOLUTE_CANONICAL_PATH_REQUIRED')
    inspect_private_path(directory.parent, directory=True)
    sample_linux_clock()
    raw = read_json(policy_path)
    raw['risk'] = RiskPolicy(**raw['risk'])
    raw['source_observed_at'] = datetime.fromisoformat(raw['source_observed_at'])
    policy = TradingPolicy(**raw).validate()
    positions = read_json(positions_path)
    with private_creation():
        initialize_simulation_host(directory, policy, origin=f'https://127.0.0.1:{port}',
                                   cash_krw=cash_krw, positions=positions)
        inspect_profile(directory)


def run_dashboard(directory, cert, key, port):
    check_profile(directory, cert, key, port)
    with private_creation():
        return local_dashboard._run_dashboard(directory, cert, key, port, clock=sample_linux_clock,
                                              deadline_clock=elapsed_linux)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    init = sub.add_parser('init')
    for arg in ('state-dir', 'policy-json', 'positions-json'):
        init.add_argument('--' + arg, required=True)
    init.add_argument('--cash-krw', required=True, type=int)
    init.add_argument('--port', required=True, type=int)
    for action in ('check', 'run-dashboard'):
        command = sub.add_parser(action)
        for arg in ('state-dir', 'cert', 'key'):
            command.add_argument('--' + arg, required=True)
        command.add_argument('--port', required=True, type=int)
    args = parser.parse_args(argv)
    try:
        if args.action == 'init':
            initialize(args.state_dir, args.policy_json, args.positions_json, args.cash_krw, args.port)
        elif args.action == 'check':
            check_profile(args.state_dir, args.cert, args.key, args.port)
        else:
            run_dashboard(args.state_dir, args.cert, args.key, args.port)
        print(json.dumps({'mode': MODE, 'live_capable': False, 'ready_to_trade': False, 'result': 'completed'}))
        return 0
    except Exception:
        print(json.dumps({'mode': MODE, 'live_capable': False, 'ready_to_trade': False,
                          'error': 'LINUX_SIMULATION_REQUIREMENTS_NOT_MET'}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())

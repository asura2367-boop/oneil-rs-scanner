#!/usr/bin/env python3
import ast
from pathlib import Path
p=Path(__file__).parent/'oneil_v519_live_runner.py'
if not p.is_file():raise SystemExit('RUNNER_NOT_FOUND')
s=p.read_text()
if 'def recover_orders_readonly(' in s:raise SystemExit(0)
old='def run(args):'
if s.count(old)!=1:raise SystemExit('RUNNER_LAYOUT_UNEXPECTED')
recovery='''def recover_orders_readonly(state):
    # Uses persisted journal and full-day KIS observation, no order endpoint.
    if flag("LIVE_ENABLED") or flag("KIS_ALLOW_ORDER"):
        raise Blocked("RECOVERY_REQUIRES_ORDER_SWITCHES_OFF")
    from oneil_v519_broker_recovery import observe_and_reconcile
    with open(state / "oneil.lock", "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        journal = OrderJournal(state / "orders.sqlite")
        try:
            result = observe_and_reconcile(journal)
            return {"gate":"DENY","matched":result["matched"],
                    "unresolved":len(journal.pending()),"orders_sent":0}
        finally:
            journal.close()

'''
s=s.replace(old,recovery+old,1)
needle='    if not args.ticker or not CODE.fullmatch(args.ticker):raise Blocked("TICKER_INVALID")'
if s.count(needle)!=1:raise SystemExit('TICKER_CHECK_MISSING')
s=s.replace(needle,'''    if args.recover_only:
        print(json.dumps(recover_orders_readonly(state),ensure_ascii=False))
        return
'''+needle,1)
for old,new in [('    p.add_argument("--ticker",required=True)','    p.add_argument("--ticker")'),('    p.add_argument("--signal-id",required=True)','    p.add_argument("--signal-id")'),('    p.add_argument("--execute",action="store_true")','    p.add_argument("--recover-only",action="store_true")\n    p.add_argument("--execute",action="store_true")')]:
    if s.count(old)!=1:raise SystemExit('CLI_LAYOUT_UNEXPECTED')
    s=s.replace(old,new,1)
if 'LIVE_ORDER_RELEASE_NOT_YET_ATTESTED' not in s:raise SystemExit('ORDER_LOCK_MISSING')
ast.parse(s)
p.write_text(s)
print('RECOVERY_PATCH_APPLIED; ORDER_TRANSMISSION_LOCKED')

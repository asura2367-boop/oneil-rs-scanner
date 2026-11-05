"""A pure restart PLAN, never a subprocess launcher or order retry mechanism."""
from dataclasses import dataclass


@dataclass(frozen=True)
class RestartPlan:
    action: str
    delay_seconds: int | None
    restart_count: int
    commands_enabled: bool = False
    live_capable: bool = False


def plan_restart(*, exit_code, restart_count, explicit_stop=False, max_restarts=3):
    if type(restart_count) is not int or restart_count < 0:
        raise ValueError('INVALID_RESTART_COUNT')
    if type(max_restarts) is not int or not 0 <= max_restarts <= 10:
        raise ValueError('INVALID_RESTART_LIMIT')
    if type(explicit_stop) is not bool:
        raise ValueError('INVALID_STOP_STATE')
    if explicit_stop:
        return RestartPlan('STOPPED_BY_OWNER', None, restart_count)
    if exit_code is None:
        return RestartPlan('PROCESS_STILL_RUNNING_CHECK_HEALTH', None, restart_count)
    if type(exit_code) is not int:
        raise ValueError('INVALID_EXIT_CODE')
    if exit_code == 0:
        return RestartPlan('STOPPED_CLEANLY', None, restart_count)
    if restart_count >= max_restarts:
        return RestartPlan('RESTART_BUDGET_EXHAUSTED_REQUIRE_OPERATOR', None, restart_count)
    return RestartPlan('RESTART_RECONCILIATION_ONLY', min(5 * 2**restart_count, 300), restart_count + 1)

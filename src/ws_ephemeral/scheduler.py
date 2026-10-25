"""Scheduling of the port rotation."""

import contextlib
import logging
import signal
import threading
from collections.abc import Callable
from datetime import datetime, timedelta
from types import FrameType

from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.base import BaseTrigger
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)

SCHEDULED_JOB = "rotate-port"
ON_START_JOB = "rotate-port-on-start"
RETRY_JOB = "rotate-port-retry"

# a run that could not start on time (busy host, suspended VM) still goes ahead if it
# is at most this late, instead of being silently skipped
MISFIRE_GRACE_SECONDS = 3600

# an ephemeral port lives a week, so a failed rotation can not wait for the next
# scheduled run. The retries back off, to stay gentle with Windscribe.
MAX_RETRY_DELAY = timedelta(hours=6)


def retry_delay_after(failures: int, base: timedelta) -> timedelta:
    """How long to wait before the next retry.

    Args:
        failures (int): How many runs in a row failed so far, at least 1.
        base (timedelta): The delay after the first failure, doubled after each one.

    Returns:
        timedelta: The delay, never more than `MAX_RETRY_DELAY`.
    """
    # the exponent is capped so that a long outage can not overflow the delay
    return min(base * 2 ** min(failures - 1, 16), MAX_RETRY_DELAY)


def with_retry(
    job: Callable[[], object],
    scheduler: BlockingScheduler,
    base_delay: timedelta,
) -> Callable[[], object]:
    """Make a job that reports failure by returning False come back until it works.

    A failed run schedules a single retry, replacing any pending one. A successful run
    cancels the pending retry. Runs never overlap, a retry waits for a run in progress.

    Args:
        job (Callable[[], object]): The job, False means that it failed.
        scheduler (BlockingScheduler): Where the retries are scheduled.
        base_delay (timedelta): The delay after the first failure.

    Returns:
        Callable[[], object]: The job with retries.
    """
    lock = threading.Lock()
    failures = 0

    def run() -> object:
        nonlocal failures
        with lock:
            result = job()
            if result is False:
                failures += 1
                delay = retry_delay_after(failures, base_delay)
                run_date = datetime.now(scheduler.timezone) + delay
                scheduler.add_job(
                    run,
                    DateTrigger(run_date=run_date),
                    id=RETRY_JOB,
                    replace_existing=True,
                    max_instances=1,
                    misfire_grace_time=MISFIRE_GRACE_SECONDS,
                )
                logger.warning(
                    "Run failed (%d in a row), retrying at %s", failures, run_date
                )
            else:
                failures = 0
                with contextlib.suppress(JobLookupError):
                    scheduler.remove_job(RETRY_JOB)
            return result

    return run


def parse_time(value: str) -> tuple[int, int]:
    """Parse a time of day.

    Args:
        value (str): The time as HH:MM.

    Returns:
        tuple[int, int]: The hour and the minute.

    Raises:
        ValueError: If the value is not a valid time.
    """
    try:
        parsed = datetime.strptime(value, "%H:%M")
    except ValueError as exc:
        raise ValueError(f"invalid time {value!r}, expected HH:MM like 02:00") from exc
    return parsed.hour, parsed.minute


def build_trigger(
    *, days: int, at: str, cron: str | None, now: datetime
) -> BaseTrigger:
    """Build the trigger for the recurring job.

    Without a cron expression the job runs every `days` days at `at`, the first time
    being `days` days from now. That matches an ephemeral port living for a week and
    a run at startup (see `build_scheduler`) having just renewed it.

    Args:
        days (int): Days between two runs.
        at (str): Time of the day to run at, as HH:MM.
        cron (str | None): A 5 field cron expression, replaces `days` and `at`.
        now (datetime): The current time, its timezone is the timezone of the trigger.

    Returns:
        BaseTrigger: The trigger.

    Raises:
        ValueError: If the time or the cron expression is invalid.
    """
    if cron is not None:
        return CronTrigger.from_crontab(cron, timezone=now.tzinfo)

    hour, minute = parse_time(at)
    first = (now + timedelta(days=days)).replace(
        hour=hour, minute=minute, second=0, microsecond=0
    )
    return IntervalTrigger(days=days, start_date=first, timezone=now.tzinfo)


def build_scheduler(
    job: Callable[[], object],
    *,
    days: int,
    at: str,
    cron: str | None,
    run_on_start: bool,
    retry_delay: timedelta | None = None,
) -> BlockingScheduler:
    """Build the scheduler, without starting it.

    Args:
        job (Callable[[], object]): What to run, False means that it failed.
        days (int): Days between two runs.
        at (str): Time of the day to run at, as HH:MM.
        cron (str | None): A 5 field cron expression, replaces `days` and `at`.
        run_on_start (bool): Also run once as soon as the scheduler starts.
        retry_delay (timedelta | None): Delay before retrying a failed run, doubled
            after each failure. None turns the retries off.

    Returns:
        BlockingScheduler: The scheduler.

    Raises:
        ValueError: If the time or the cron expression is invalid.
    """
    scheduler = BlockingScheduler()
    now = datetime.now(scheduler.timezone)
    trigger = build_trigger(days=days, at=at, cron=cron, now=now)

    if retry_delay is not None:
        job = with_retry(job, scheduler, retry_delay)

    scheduler.add_job(
        job,
        trigger,
        id=SCHEDULED_JOB,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=MISFIRE_GRACE_SECONDS,
    )
    if run_on_start:
        # no trigger means run once, right away
        scheduler.add_job(job, id=ON_START_JOB, max_instances=1)

    if cron is not None:
        logger.info("Schedule is setup with cron %r", cron)
    else:
        logger.info("Schedule is setup to run every %s day(s) at %s", days, at)
    logger.info("Next scheduled run: %s", trigger.get_next_fire_time(None, now))

    return scheduler


def serve(scheduler: BlockingScheduler) -> None:
    """Run the scheduler until it is told to stop (CTRL-C or `docker stop`).

    Args:
        scheduler (BlockingScheduler): The scheduler to run.
    """

    def stop(signum: int, _frame: FrameType | None) -> None:
        logger.info("Received %s, shutting down", signal.Signals(signum).name)
        if scheduler.running:
            scheduler.shutdown(wait=False)

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    scheduler.start()

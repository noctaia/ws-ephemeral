"""Retries of a failed port rotation."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from apscheduler.jobstores.base import JobLookupError

from ws_ephemeral.scheduler import (
    MAX_RETRY_DELAY,
    RETRY_JOB,
    build_scheduler,
    retry_delay_after,
    with_retry,
)

BASE = timedelta(minutes=5)


class FakeScheduler:
    """Only what `with_retry` needs from a scheduler."""

    timezone = UTC

    def __init__(self) -> None:
        self.pending: dict[str, datetime] = {}
        self.added = 0

    def add_job(self, func: Any, trigger: Any, id: str, **_: Any) -> None:
        self.added += 1
        self.pending[id] = trigger.run_date

    def remove_job(self, id: str) -> None:
        if id not in self.pending:
            raise JobLookupError(id)
        del self.pending[id]


def retrying(results: list[object]) -> tuple[Any, FakeScheduler]:
    scheduler = FakeScheduler()
    outcomes = iter(results)
    return with_retry(lambda: next(outcomes), scheduler, BASE), scheduler  # type: ignore[arg-type]


def test_delay_doubles_after_each_failure() -> None:
    assert [retry_delay_after(n, BASE) for n in (1, 2, 3, 4)] == [
        timedelta(minutes=5),
        timedelta(minutes=10),
        timedelta(minutes=20),
        timedelta(minutes=40),
    ]


def test_delay_is_capped() -> None:
    assert retry_delay_after(20, BASE) == MAX_RETRY_DELAY
    assert retry_delay_after(10_000, BASE) == MAX_RETRY_DELAY


def test_a_failed_run_schedules_a_retry() -> None:
    run, scheduler = retrying([False])
    before = datetime.now(UTC)

    assert run() is False

    assert scheduler.pending[RETRY_JOB] >= before + BASE


def test_a_successful_run_schedules_nothing() -> None:
    run, scheduler = retrying([True])

    assert run() is True

    assert scheduler.added == 0


def test_failures_in_a_row_back_off() -> None:
    run, scheduler = retrying([False, False, False])
    gaps = []
    for _ in range(3):
        before = datetime.now(UTC)
        run()
        gaps.append(scheduler.pending[RETRY_JOB] - before)

    assert gaps[0] < gaps[1] < gaps[2]
    assert timedelta(minutes=20) <= gaps[2] < timedelta(minutes=21)


def test_a_success_cancels_the_pending_retry_and_resets_the_backoff() -> None:
    run, scheduler = retrying([False, False, True, False])
    run()
    run()
    run()
    assert RETRY_JOB not in scheduler.pending

    before = datetime.now(UTC)
    run()
    assert scheduler.pending[RETRY_JOB] - before < BASE + timedelta(seconds=5)


def test_the_result_of_the_job_is_passed_on() -> None:
    run, _ = retrying([None])

    assert run() is None


def test_retries_are_wired_into_the_scheduler_only_when_asked() -> None:
    def build(retry_delay: timedelta | None) -> Any:
        scheduler = build_scheduler(
            lambda: None,
            days=6,
            at="02:00",
            cron=None,
            run_on_start=False,
            retry_delay=retry_delay,
        )
        return scheduler.get_jobs()[0].func

    plain, wrapped = build(None), build(BASE)

    assert plain() is None
    assert wrapped.__name__ == "run" and wrapped is not plain


@pytest.mark.parametrize("failures", [1, 3, 8])
def test_delay_never_shrinks(failures: int) -> None:
    assert retry_delay_after(failures + 1, BASE) >= retry_delay_after(failures, BASE)

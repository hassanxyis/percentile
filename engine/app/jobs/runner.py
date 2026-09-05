"""One tick: reap, sweep, claim, dispatch (plan §11, §12).

Called by `POST /tick`, which GitHub Actions hits every five minutes
(`.github/workflows/tick.yml`). Nothing else drives the engine — no user request
ever reaches it synchronously (plan §2, CLAUDE.md "Architecture").

The order of the three phases matters:

1. **Reap** stale `running` jobs first, so a job whose worker died is eligible
   for the claim in step 3 rather than waiting another five minutes.
2. **Sweep** the review backlog, so any reminder it enqueues can be sent by this
   same tick instead of the next one.
3. **Claim and dispatch** until the batch is empty or the deadline is close.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from app.config import Settings
from app.jobs import queue
from app.jobs.handlers import HandlerError, UnknownJobKind, handler_for

log = logging.getLogger(__name__)


@dataclass
class TickResult:
    """What one tick did. Counts only — never a payload.

    A job payload carries a `participant_id` and, for a moment inside the invite
    handler, a live token. This is the value `POST /tick` returns over the wire
    and logs on the way out, so it holds numbers and job ids and nothing else.
    """

    requeued: int = 0
    reminders: int = 0
    claimed: int = 0
    completed: int = 0
    retried: int = 0
    failed: int = 0
    deadline_hit: bool = False
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "requeued": self.requeued,
            "reminders": self.reminders,
            "claimed": self.claimed,
            "completed": self.completed,
            "retried": self.retried,
            "failed": self.failed,
            "deadline_hit": self.deadline_hit,
        }


def run_tick(client, settings: Settings, now: float | None = None) -> TickResult:
    """Run one tick to completion or to the deadline, whichever comes first."""
    started = now if now is not None else time.monotonic()
    result = TickResult()

    # Phase 1 and 2 are best-effort. Neither should be able to stop the tick
    # from doing its actual work: a reaper that fails means jobs wait five more
    # minutes, but a reaper that aborts the tick means nothing runs at all.
    try:
        result.requeued = queue.requeue_stale_jobs(client, settings.job_stale_timeout_seconds)
        if result.requeued:
            log.warning("requeued %d stale job(s)", result.requeued)
    except Exception as exc:
        log.exception("stale-job reaper failed")
        result.errors.append(f"requeue_stale_jobs: {exc}")

    try:
        result.reminders = queue.enqueue_review_reminders(client, settings.review_backlog_days)
        if result.reminders:
            log.info("queued %d review reminder(s)", result.reminders)
    except Exception as exc:
        log.exception("review backlog sweep failed")
        result.errors.append(f"enqueue_review_reminders: {exc}")

    while True:
        if _deadline_reached(started, settings.tick_deadline_seconds):
            result.deadline_hit = True
            log.info("tick deadline reached; leaving the rest for the next tick")
            break

        batch = queue.claim_jobs(client, settings.job_batch_size)
        if not batch:
            break

        result.claimed += len(batch)
        for job in batch:
            _run_one(client, settings, job, result)

    log.info("tick finished: %s", result.as_dict())
    return result


def _run_one(client, settings: Settings, job: queue.Job, result: TickResult) -> None:
    """Dispatch one job and record the outcome.

    Every exception is caught. One bad job must not abort a batch — the other
    nine are already claimed and `running`, so an escaping exception would leave
    them stranded until the stale reaper picks them up half an hour later.
    """
    try:
        handler = handler_for(job.kind)
        handler(client, settings, job)
    except UnknownJobKind as exc:
        # A kind nobody implements yet. Retrying cannot help, but the job is
        # still failed through the normal path rather than dropped, so it
        # exhausts its attempts and alerts instead of sitting `running` forever.
        log.error("job %s: %s", job.id, exc)
        _record_failure(client, settings, job, str(exc), result)
    except HandlerError as exc:
        # An expected failure the handler described. No traceback: it already
        # said what went wrong, and the message is going into `jobs.last_error`.
        log.warning("job %s (%s) failed: %s", job.id, job.kind, exc)
        _record_failure(client, settings, job, str(exc), result)
    except Exception as exc:
        log.exception("job %s (%s) raised", job.id, job.kind)
        # `type(exc).__name__: exc` rather than the traceback. A traceback in
        # `jobs.last_error` can carry local variables, and one of those locals
        # is an invite token inside the email handler.
        _record_failure(client, settings, job, f"{type(exc).__name__}: {exc}", result)
    else:
        try:
            queue.complete_job(client, job.id)
            result.completed += 1
        except Exception as exc:
            # The work happened but the bookkeeping did not. The job stays
            # `running` and the stale reaper will retry it — which is why every
            # handler has to be idempotent, and why record_score upserts.
            log.exception("job %s completed but could not be marked done", job.id)
            result.errors.append(f"complete_job {job.id}: {exc}")


def _record_failure(
    client, settings: Settings, job: queue.Job, error: str, result: TickResult
) -> None:
    try:
        outcome = queue.fail_job(client, job.id, error, settings.job_max_attempts)
    except Exception as exc:
        log.exception("job %s failed and could not be marked failed", job.id)
        result.errors.append(f"fail_job {job.id}: {exc}")
        return

    if outcome == "retry":
        result.retried += 1
    else:
        result.failed += 1


def _deadline_reached(started: float, deadline_seconds: int) -> bool:
    """True when there is not enough time left to claim another batch.

    tick.yml calls curl with `-m 120`. Stopping early lets the tick answer with
    what it did; being killed mid-batch leaves every claimed job `running` and
    invisible until the reaper runs.
    """
    if deadline_seconds <= 0:
        return False
    return (time.monotonic() - started) >= deadline_seconds

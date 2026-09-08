"""One tick's control flow (plan §11, §12, M7).

The SQL is asserted against real Postgres in `tests/db/test_job_runner.py`. What
is asserted here is the part above it: dispatch, and the four ways a tick has to
keep going when something goes wrong.

Each of those is a failure mode that would otherwise be discovered in
production, at five-minute intervals, on a host that has already gone to sleep.
"""

import pytest

from app.config import Settings
from app.jobs import runner
from app.jobs.handlers import HandlerError, UnknownJobKind, handler_for
from app.jobs.queue import Job

SETTINGS = Settings(
    job_batch_size=10,
    job_max_attempts=5,
    job_stale_timeout_seconds=1800,
    tick_deadline_seconds=90,
)


class FakeQueue:
    """Stands in for the six SQL functions, recording what the runner asked for."""

    def __init__(self, batches: list[list[Job]] | None = None) -> None:
        self.batches = list(batches or [])
        self.completed: list[str] = []
        self.failed: list[tuple[str, str]] = []
        self.requeued = 0
        self.reminders = 0
        self.raise_on_requeue = False
        self.raise_on_reminders = False
        self.raise_on_complete = False

    def requeue_stale_jobs(self, client, timeout_seconds):
        if self.raise_on_requeue:
            raise RuntimeError("reaper is down")
        return self.requeued

    def enqueue_review_reminders(self, client, days):
        if self.raise_on_reminders:
            raise RuntimeError("sweep is down")
        return self.reminders

    def claim_jobs(self, client, limit):
        return self.batches.pop(0) if self.batches else []

    def complete_job(self, client, job_id):
        if self.raise_on_complete:
            raise RuntimeError("could not mark done")
        self.completed.append(job_id)

    def fail_job(self, client, job_id, error, max_attempts):
        self.failed.append((job_id, error))
        return "retry"


@pytest.fixture
def fake_queue(monkeypatch):
    queue = FakeQueue()
    monkeypatch.setattr(runner, "queue", queue)
    return queue


def job(kind: str = "score_session", job_id: str = "job-1") -> Job:
    return Job(id=job_id, kind=kind, payload={"session_id": "s-1"}, attempts=1)


def install_handler(monkeypatch, fn):
    monkeypatch.setattr(runner, "handler_for", lambda kind: fn)


# ── the happy path ───────────────────────────────────────────────────────────


def test_a_successful_job_is_completed(fake_queue, monkeypatch):
    fake_queue.batches = [[job()], []]
    install_handler(monkeypatch, lambda client, settings, j: None)

    result = runner.run_tick(object(), SETTINGS)

    assert fake_queue.completed == ["job-1"]
    assert result.completed == 1
    assert result.failed == 0


def test_claims_until_the_batch_comes_back_empty(fake_queue, monkeypatch):
    fake_queue.batches = [[job(job_id="a")], [job(job_id="b")], []]
    install_handler(monkeypatch, lambda client, settings, j: None)

    result = runner.run_tick(object(), SETTINGS)

    assert result.claimed == 2


def test_the_result_carries_no_payloads():
    """A payload holds a participant_id, and for a moment a live token. This
    value is returned over the wire by POST /tick and logged on the way out."""
    result = runner.TickResult(claimed=3, completed=2)

    assert set(result.as_dict()) == {
        "requeued", "reminders", "claimed", "completed", "retried", "failed", "deadline_hit",
    }


# ── one bad job must not take the batch down ─────────────────────────────────


def test_a_raising_handler_fails_only_its_own_job(fake_queue, monkeypatch):
    """The other nine are already claimed and `running`. An escaping exception
    strands them until the stale reaper runs half an hour later."""
    jobs = [job(job_id="a"), job(job_id="b"), job(job_id="c")]
    fake_queue.batches = [jobs, []]

    def handler(client, settings, j):
        if j.id == "b":
            raise RuntimeError("boom")

    install_handler(monkeypatch, handler)

    result = runner.run_tick(object(), SETTINGS)

    assert fake_queue.completed == ["a", "c"]
    assert [job_id for job_id, _ in fake_queue.failed] == ["b"]
    assert result.retried == 1


def test_failure_text_is_the_exception_type_not_a_traceback(fake_queue, monkeypatch):
    """`jobs.last_error` is retried, logged and emailed. A traceback can carry
    local variables, and one of those locals is an invite token."""
    fake_queue.batches = [[job()], []]

    def handler(client, settings, j):
        raise ValueError("something went wrong")

    install_handler(monkeypatch, handler)

    runner.run_tick(object(), SETTINGS)

    _, error = fake_queue.failed[0]
    assert error == "ValueError: something went wrong"
    assert "Traceback" not in error


def test_handler_error_message_reaches_last_error(fake_queue, monkeypatch):
    fake_queue.batches = [[job()], []]
    install_handler(
        monkeypatch,
        lambda client, settings, j: (_ for _ in ()).throw(HandlerError("no email address")),
    )

    runner.run_tick(object(), SETTINGS)

    assert fake_queue.failed[0][1] == "no email address"


def test_unknown_kind_fails_the_job_rather_than_stranding_it(fake_queue, monkeypatch):
    """Retrying cannot help, but a job left `running` sits forever. Failing it
    normally means it exhausts its attempts and alerts.

    `recompute_norms` stands in for the shape. It is the last kind on the
    unimplemented list — M9 took `render_student` off it and M11 took
    `render_cohort` — so M13 is the milestone that will need a new stand-in here.
    """
    fake_queue.batches = [[job(kind="recompute_norms")], []]
    monkeypatch.setattr(runner, "handler_for", handler_for)

    runner.run_tick(object(), SETTINGS)

    assert "M13" in fake_queue.failed[0][1]


# ── the phases that must not be able to abort a tick ─────────────────────────


def test_a_broken_reaper_does_not_stop_the_tick(fake_queue, monkeypatch):
    """Jobs waiting five more minutes is a nuisance. Nothing running at all is
    an outage."""
    fake_queue.raise_on_requeue = True
    fake_queue.batches = [[job()], []]
    install_handler(monkeypatch, lambda client, settings, j: None)

    result = runner.run_tick(object(), SETTINGS)

    assert result.completed == 1
    assert any("requeue_stale_jobs" in e for e in result.errors)


def test_a_broken_backlog_sweep_does_not_stop_the_tick(fake_queue, monkeypatch):
    fake_queue.raise_on_reminders = True
    fake_queue.batches = [[job()], []]
    install_handler(monkeypatch, lambda client, settings, j: None)

    result = runner.run_tick(object(), SETTINGS)

    assert result.completed == 1


def test_a_failed_complete_is_recorded_not_raised(fake_queue, monkeypatch):
    """The work happened but the bookkeeping did not. The job stays `running`
    and the reaper retries it — which is why every handler is idempotent."""
    fake_queue.raise_on_complete = True
    fake_queue.batches = [[job()], []]
    install_handler(monkeypatch, lambda client, settings, j: None)

    result = runner.run_tick(object(), SETTINGS)

    assert result.completed == 0
    assert any("complete_job" in e for e in result.errors)


# ── the deadline ─────────────────────────────────────────────────────────────


def test_the_deadline_stops_claiming(fake_queue, monkeypatch):
    """tick.yml calls curl with -m 120. Being killed mid-batch leaves every
    claimed job `running` and invisible until the reaper runs."""
    fake_queue.batches = [[job()], [job(job_id="never-claimed")]]
    install_handler(monkeypatch, lambda client, settings, j: None)

    # A deadline already in the past when the tick starts.
    import time

    result = runner.run_tick(object(), SETTINGS, now=time.monotonic() - 1000)

    assert result.deadline_hit is True
    assert result.claimed == 0


def test_zero_deadline_means_no_limit(fake_queue, monkeypatch):
    """0 disables the check rather than making every tick expire immediately."""
    settings = Settings(tick_deadline_seconds=0)
    fake_queue.batches = [[job()], []]
    install_handler(monkeypatch, lambda client, settings_, j: None)

    result = runner.run_tick(object(), settings)

    assert result.deadline_hit is False
    assert result.completed == 1


# ── the registry ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "kind",
    [
        "score_session",
        "match_occupations",
        "send_email",
        "notify_psychologist",
        "review_reminder",
        # M9. Enqueued by `save_review()` since M8, so it was the one kind the
        # system queued and could not run; that gap is what M9 closed.
        "render_student",
        # M11. Enqueued by the counsellor dashboard writing a `jobs` row, the
        # same way `resendInvite` enqueues `send_email`.
        "render_cohort",
    ],
)
def test_every_queued_kind_has_a_handler(kind):
    """Every kind anything in this system enqueues must be runnable. If one of
    them lost its handler, the symptom in production would be an alert saying
    "unknown job kind" about work the system itself queued."""
    assert callable(handler_for(kind))


@pytest.mark.parametrize(("kind", "milestone"), [("recompute_norms", "M13")])
def test_unimplemented_kinds_name_their_milestone(kind, milestone):
    """Nothing enqueues these yet, so the alert naming the milestone is the
    right failure. `render_student` left this list in M9, `render_cohort` in
    M11 — leaving `recompute_norms` as the last one."""
    with pytest.raises(UnknownJobKind, match=milestone):
        handler_for(kind)


def test_a_genuinely_unknown_kind_says_so():
    with pytest.raises(UnknownJobKind, match="unknown job kind"):
        handler_for("teleport_student")

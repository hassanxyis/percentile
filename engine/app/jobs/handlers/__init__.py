"""One handler per job kind (plan §12).

A handler is `(client, settings, job) -> None`. It succeeds by returning and
fails by raising; the runner records which. Two rules:

* **Handlers must be idempotent.** A process killed between the work and
  `complete_job` leaves the job `running`, the reaper returns it to `pending`,
  and it runs again. `record_score` upserts and `record_occupation_matches`
  deletes-then-inserts for exactly this reason.
* **Never put a token in an exception message.** Failure text lands in
  `jobs.last_error`, which is retried, logged and emailed. `fail_job` truncates
  as a backstop, but truncation is not redaction.
"""

from __future__ import annotations

from collections.abc import Callable

from app.config import Settings
from app.jobs.queue import Job

# Job kinds this milestone does not implement, and the milestone that will.
# Failing with this is better than a bare KeyError: the alert then says what is
# missing rather than that something went wrong.
NOT_YET_IMPLEMENTED = {
    "render_cohort": "M11",
    "recompute_norms": "M13",
}


class HandlerError(RuntimeError):
    """A failure the handler anticipated and can describe.

    Raised instead of letting an arbitrary exception escape, so the message that
    reaches `jobs.last_error` was written by someone who knew what it meant.
    """


class UnknownJobKind(HandlerError):
    """No handler for this kind. Retrying cannot help, but it still fails properly."""


Handler = Callable[[object, Settings, Job], None]


def handler_for(kind: str) -> Handler:
    # Imported here rather than at module scope: handlers import queue, config
    # and repository, and a circular import between this registry and the
    # handlers that reference it is otherwise easy to create by accident.
    from app.jobs.handlers import email as email_handler
    from app.jobs.handlers import match, notify, render, score

    registry: dict[str, Handler] = {
        "score_session": score.handle,
        "match_occupations": match.handle,
        "send_email": email_handler.handle,
        "notify_psychologist": notify.handle_notify_psychologist,
        "review_reminder": notify.handle_review_reminder,
        # Enqueued only by `save_review()` on the transition into `confirmed`
        # (0009_review_actions.sql). Nothing else may queue it — R9.
        "render_student": render.handle,
    }

    if kind in registry:
        return registry[kind]

    if kind in NOT_YET_IMPLEMENTED:
        raise UnknownJobKind(
            f"job kind {kind!r} is not implemented until {NOT_YET_IMPLEMENTED[kind]}"
        )
    raise UnknownJobKind(f"unknown job kind {kind!r}")

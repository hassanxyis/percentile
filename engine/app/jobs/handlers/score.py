"""`score_session` — the job M6's submit button queues (plan §7.5).

Steps 1-6 of §7.5, split across three layers by CLAUDE.md's rule that scoring
touches no database:

    repository.load_session_for_scoring   items + responses out of Postgres
    scoring.engine.score_session_record   the pure part
    queue.record_score                    one transaction: scores row,
                                          pending_review, two follow-up jobs

Idempotent: `record_score` upserts on `(session_id, engine_version)`, so a
retried job rewrites its own row rather than colliding with it.
"""

from __future__ import annotations

import logging

from app.config import Settings
from app.jobs.handlers import HandlerError
from app.jobs.queue import Job, record_score
from app.repository import load_session_for_scoring
from app.scoring.engine import score_session_record
from app.scoring.types import ScoringError

log = logging.getLogger(__name__)


def handle(client, settings: Settings, job: Job) -> None:
    session_id = job.payload.get("session_id")
    if not session_id:
        raise HandlerError("score_session job has no session_id in its payload")

    try:
        session, participant = load_session_for_scoring(client, session_id)
        scores = score_session_record(session)
    except ScoringError as exc:
        # ScoringError means the input cannot be scored — a missing response, an
        # out-of-range value, an empty items table. Retrying will not fix any of
        # those, but the job is still failed through the normal path so it
        # exhausts its attempts and alerts. Silently dropping it would leave a
        # student submitted and never reviewed, with nothing anywhere to notice.
        raise HandlerError(f"cannot score session {session_id}: {exc}") from exc

    record_score(client, session_id, settings.scoring_engine_version, scores)

    # Logged at info because this is the line that answers "did that student's
    # submission get through". Counts only, no scores — the numbers themselves
    # belong behind the review screen's RLS, not in a log file.
    log.info(
        "scored session %s (%d responses, %d flags) for participant %s",
        session_id,
        len(session.responses),
        len(scores["flags"]),
        participant.participant_id,
    )

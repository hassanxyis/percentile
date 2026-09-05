"""`match_occupations` — rank O*NET occupations against a scored session (plan §8).

Enqueued by `record_score()`, not run inside it. Two reasons, both in
0008_job_runner.sql's comment: matching loads the whole 923-row catalogue and is
the slowest step, and a catalogue problem should not roll back a score that is
already correct.

The consequence is deliberate. If this job fails, the student is still in
`pending_review` and still in the psychologist's queue — the review screen shows
no occupation list, which is visible and fixable. The alternative, matching
inside the scoring transaction, would take the student out of the queue
entirely on a catalogue error.
"""

from __future__ import annotations

import logging

from app.config import Settings
from app.jobs.handlers import HandlerError
from app.jobs.queue import Job, record_occupation_matches
from app.matching.occupations import match
from app.repository import load_occupations, load_session_for_scoring
from app.scoring.engine import score_session_record
from app.scoring.types import ScoringError

log = logging.getLogger(__name__)


def handle(client, settings: Settings, job: Job) -> None:
    session_id = job.payload.get("session_id")
    if not session_id:
        raise HandlerError("match_occupations job has no session_id in its payload")

    try:
        session, participant = load_session_for_scoring(client, session_id)
        # Rescored rather than read back from the `scores` row. Reading would
        # need the stored jsonb parsed back into the exact shape `match()`
        # expects, and a shape change in `score_interests` would then be a
        # silent mismatch here. Scoring is pure and cheap; the expensive part of
        # this job is the catalogue below.
        scores = score_session_record(session)
        occupations = load_occupations(client)
    except ScoringError as exc:
        raise HandlerError(f"cannot match session {session_id}: {exc}") from exc

    get2 = scores["get2"]
    result = match(
        scores["interests"]["raw"],
        occupations,
        participant.education_level,
        # False when GET2 was not administered — the `module_not_administered`
        # shape has no such key, and `.get` is what makes R10's promise hold
        # here without a branch on whether the module exists.
        entrepreneurial_flag=bool(get2.get("entrepreneurial_flag")),
    )

    # The §8 carve-out is kept separate in `match()`'s return so the report can
    # label it ("worth exploring given your GET2 profile") rather than blending
    # it into the ranked list. `occupation_matches` has one `rank` column, so the
    # additions are appended after the ranked matches rather than interleaved —
    # M9 tells them apart by rank order against `matches`.
    rows = [
        {
            "rank": m["rank"],
            "onet_soc_code": m["onet_soc_code"],
            "score": m["score"],
            "local_title": m.get("local_title"),
            "local_pathway": m.get("local_pathway"),
        }
        for m in result["matches"]
    ]
    next_rank = len(rows) + 1
    for addition in result["entrepreneurial_additions"]:
        rows.append(
            {
                "rank": next_rank,
                "onet_soc_code": addition["onet_soc_code"],
                "score": addition["score"],
                "local_title": addition.get("local_title"),
                "local_pathway": addition.get("local_pathway"),
            }
        )
        next_rank += 1

    written = record_occupation_matches(
        client, session_id, settings.scoring_engine_version, rows
    )

    if result["local_match_gap"]:
        # Not a failure. It means pk_occupation_map.csv has thin coverage for
        # this student's Holland code, which is a data-curation task, not a bug
        # — and a report of international-only matches is still a real report.
        log.info("session %s: fewer local matches than target (plan §8)", session_id)

    log.info("matched session %s: %d occupation rows", session_id, written)

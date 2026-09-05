"""Scoring orchestration (plan §7.5).

This module is PURE. It takes items and responses as arguments, calls the four
scoring modules, and returns a dict. It opens no connection and reads no
configuration — `app/repository.py` does the loading and `app/jobs/handlers/`
does the writing.

That split is not tidiness. It is what makes R1's promise executable: every
session must be rescoreable with one command, and `rescore_all.py` (M12) does
that by feeding stored raw responses back through this function. A function that
fetched its own data could only ever score "now", against whatever the database
currently holds.

plan §7.5 sketches this as `score_session(session_id) -> ScoreRecord`, i.e. with
the id and the writes inside. Those steps live in `handlers/score.py` and in
`record_score()` (0008_job_runner.sql) instead, because CLAUDE.md's rule for
this package — "takes raw responses in and returns score dicts, no database
access inside those modules" — is the more load-bearing of the two statements.
The steps all happen; they happen one layer out.
"""

from dataclasses import dataclass
from datetime import datetime

from app.scoring.flags import compute_flags
from app.scoring.get2 import score_get2
from app.scoring.interests import score_interests
from app.scoring.personality import Norms, score_personality
from app.scoring.types import Item, ScoringError

# Instrument codes as loaded by scripts/load_instruments.py. Items are grouped by
# these rather than by scale, because an item's `scale` is instrument-specific
# (R I A S E C vs O C E A N vs the GET2 subscales) while its instrument_code is
# the thing that decides which scorer sees it.
INTERESTS = "interests"
PERSONALITY = "personality"
GET2 = "get2"


@dataclass(frozen=True, slots=True)
class SessionInput:
    """Everything needed to score one session, and nothing that identifies it.

    No `session_id` field on purpose. This is the boundary between "what the
    scoring rules operate on" and "which row it came from"; keeping the id out
    means nothing downstream of here can quietly start behaving differently for
    one participant.
    """

    items: list[Item]
    responses: dict[str, int]
    ms_elapsed: dict[str, int] | None = None
    started_at: datetime | None = None
    submitted_at: datetime | None = None
    # None until a `norms` row for the population reaches n >= 300 (R4). It
    # cannot be non-None before a pilot has run, and `score_personality` refuses
    # a partial table rather than mixing band systems within one report.
    personality_norms: Norms | None = None
    # Parsed data/instruments/get2_scoring.json, or None when GET2 was not
    # administered — which is every session today (R10, plan §20 item 2).
    get2_scoring: dict | None = None


def score_session_record(session: SessionInput) -> dict:
    """Score one session's raw responses.

    Returns `{interests, personality, get2, flags}`, matching the four jsonb
    columns `record_score()` writes. Raises `ScoringError` on anything it cannot
    score — never a partial or zero-filled result, per that exception's own
    docstring. A zero-filled score reaches a seventeen-year-old's report and
    nobody ever finds out it was wrong.
    """
    by_instrument = group_by_instrument(session.items)

    interest_items = by_instrument.get(INTERESTS, [])
    personality_items = by_instrument.get(PERSONALITY, [])

    # These two are required. `score_interests`/`score_personality` raise on an
    # empty list anyway, but their message ("no interest items supplied") reads
    # as a scoring bug; at this level the real cause is almost always that
    # load_instruments.py has not been run against this database.
    if not interest_items or not personality_items:
        missing = [
            name
            for name, loaded in ((INTERESTS, interest_items), (PERSONALITY, personality_items))
            if not loaded
        ]
        raise ScoringError(
            f"no items loaded for {', '.join(missing)} — "
            "run scripts/load_instruments.py against this database"
        )

    interests = score_interests(session.responses, interest_items)
    personality = score_personality(
        session.responses, personality_items, norms=session.personality_norms
    )

    # Two independent reasons GET2 can be absent, and they must produce one
    # shape: the items were never loaded (today's case — the CSV does not exist,
    # plan §20 item 2), or the scoring file is missing. `score_get2(_, None)`
    # returns `{"instrument": None, "status": "module_not_administered"}` for
    # both, so no downstream template needs two absence checks.
    get2_items = by_instrument.get(GET2, [])
    get2_scoring = session.get2_scoring if get2_items else None
    get2 = score_get2(session.responses, get2_scoring)

    flags = compute_flags(
        session.responses,
        session.items,
        ms_elapsed=session.ms_elapsed,
        started_at=session.started_at,
        submitted_at=session.submitted_at,
        # compute_flags checks subscale uniformity, which only exists when the
        # module was actually scored. Passing the not-administered shape's
        # missing key would be a KeyError; passing None produces no flag, which
        # is correct — an unadministered module cannot look uniform.
        get2_subscales=get2.get("subscales"),
    )

    return {
        "interests": interests,
        "personality": personality,
        "get2": get2,
        "flags": flags,
    }


def group_by_instrument(items: list[Item]) -> dict[str, list[Item]]:
    """Bucket items by instrument code, preserving ordinal order within each.

    Sorting here rather than trusting the caller: `flags.py`'s straightlining
    check walks items in order, and a run of twelve identical answers only means
    anything if "consecutive" matches what the student actually saw.
    """
    grouped: dict[str, list[Item]] = {}
    for item in items:
        grouped.setdefault(item.instrument_code, []).append(item)
    for bucket in grouped.values():
        bucket.sort(key=lambda i: i.ordinal)
    return grouped

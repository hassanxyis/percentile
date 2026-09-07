"""The database-aware half of scoring (plan §7.5).

`app/scoring/*` and `app/matching/*` are pure by rule — they take items,
responses and catalogues as arguments and never query. This module is where that
data comes from, and it is deliberately the only place in the scoring path that
knows Supabase exists.

Everything here loads or saves; nothing here decides anything about a score.
When a bug produces a wrong number, that split tells you which half to read.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.config import TEMPLATE_VERSION
from app.matching.occupations import Occupation
from app.report.student import Direction, Match, Organisation, StudentReportInput
from app.scoring.engine import SessionInput
from app.scoring.types import Item, ScoringError

log = logging.getLogger(__name__)


class ReportNotAllowed(RuntimeError):
    """R9: this session has no confirmed review, so no student report may exist.

    Its own type rather than a `ScoringError`, because the two mean opposite
    things to whoever reads the failed job. A ScoringError is "the data is
    wrong"; this is "the data is fine and a human has not signed it off yet",
    which is the system working.
    """

REPO_ROOT = Path(__file__).resolve().parents[2]
GET2_SCORING_PATH = REPO_ROOT / "data" / "instruments" / "get2_scoring.json"

# Where `organisations.logo_path` points. Public-read, unlike `reports` — a
# school crest is not confidential, and the renderer fetches it with no session.
BRANDING_BUCKET = "branding"

# supabase-js and supabase-py both cap an unbounded select at 1000 rows by
# default. The occupation catalogue is 923 today and O*NET grows every release,
# so a silent truncation here would drop occupations off the end of the
# catalogue and quietly change every student's matches. Page explicitly.
PAGE_SIZE = 1000


@dataclass(frozen=True, slots=True)
class ParticipantContext:
    """The participant fields scoring and matching need, and no others.

    `full_name` and `email` are deliberately absent — nothing in the scoring
    path needs to know who this is, and a struct that carries a minor's name
    into every log line and traceback is how it ends up in `jobs.last_error`.
    """

    participant_id: str
    cohort_id: str
    education_level: str | None


def load_session_for_scoring(client, session_id: str) -> tuple[SessionInput, ParticipantContext]:
    """Assemble everything needed to score one session.

    Raises rather than returning a partial load. A session scored against a
    subset of its answers produces a number that looks fine, lands in a report,
    and is wrong — the exact failure `ScoringError` exists to prevent.
    """
    session = _one(
        client.table("sessions")
        .select("id, participant_id, started_at, submitted_at")
        .eq("id", session_id)
        .execute(),
        f"no session {session_id}",
    )

    if not session.get("submitted_at"):
        # Not a data error — a race. `submit_assessment()` enqueues the job in
        # the same transaction that sets submitted_at, so this should be
        # impossible; if it happens, retrying is right and the backoff handles it.
        raise ScoringError(f"session {session_id} has not been submitted")

    participant = _one(
        client.table("participants")
        .select("id, cohort_id, education_level")
        .eq("id", session["participant_id"])
        .execute(),
        f"no participant for session {session_id}",
    )

    items = load_items(client)
    if not items:
        raise ScoringError(
            "the items table is empty — run scripts/load_instruments.py against this database"
        )

    responses, ms_elapsed = load_responses(client, session_id, items)

    return (
        SessionInput(
            items=items,
            responses=responses,
            ms_elapsed=ms_elapsed,
            started_at=_timestamp(session.get("started_at")),
            submitted_at=_timestamp(session.get("submitted_at")),
            # R4: no `norms` row can reach n >= 300 before a pilot has run, so
            # there is nothing to load yet. When M13 adds it, this is the line
            # that changes — and `score_personality` already refuses a partial
            # table rather than mixing band systems inside one report.
            personality_norms=None,
            get2_scoring=load_get2_scoring(),
        ),
        ParticipantContext(
            participant_id=participant["id"],
            cohort_id=participant["cohort_id"],
            education_level=participant.get("education_level"),
        ),
    )


def load_items(client) -> list[Item]:
    """Every loaded item, as the pure `Item` the scorers take.

    All items, not just the ones this session answered: `submit_assessment()`
    checks completeness against the whole table, so anything short of that is a
    session the database would not have let submit.
    """
    rows = _paged(
        lambda: client.table("items").select(
            "id, instrument_code, ordinal, code, text, scale, "
            "reverse_keyed, response_min, response_max"
        )
    )
    return [
        Item(
            code=row["code"],
            ordinal=row["ordinal"],
            text=row["text"],
            scale=row["scale"],
            reverse_keyed=row["reverse_keyed"],
            response_min=row["response_min"],
            response_max=row["response_max"],
            instrument_code=row["instrument_code"],
        )
        for row in rows
    ]


def load_responses(
    client, session_id: str, items: list[Item]
) -> tuple[dict[str, int], dict[str, int]]:
    """Raw responses keyed by item CODE, plus per-item timings.

    `responses` is stored by `item_id` (a uuid) but every scorer keys on
    `item.code` — the stable instrument identifier like `IP_R_03`. That
    translation happens here, once, and it is why the item list is an argument:
    a response whose item is not in the list means the two are out of step.
    """
    id_to_code = _item_ids(client, items)

    rows = _paged(
        lambda: client.table("responses")
        .select("item_id, value, ms_elapsed")
        .eq("session_id", session_id)
    )

    responses: dict[str, int] = {}
    ms_elapsed: dict[str, int] = {}
    for row in rows:
        code = id_to_code.get(row["item_id"])
        if code is None:
            # An answer to an item that no longer exists. Deleting instrument
            # rows is not something any code path does, so this means someone
            # reloaded a CSV with changed codes — which invalidates the session,
            # not just this answer (R2).
            raise ScoringError(
                f"session {session_id} has a response to unknown item {row['item_id']} — "
                "the instrument data changed after this session was answered"
            )
        responses[code] = row["value"]
        if row.get("ms_elapsed") is not None:
            ms_elapsed[code] = row["ms_elapsed"]

    return responses, ms_elapsed


def load_occupations(client) -> list[Occupation]:
    """The O*NET catalogue, localised, as the pure `Occupation` matching takes.

    Rows without a job zone or a complete RIASEC profile cannot be ranked and are
    skipped by `load_onet.py` at write time; the filter is repeated here because
    a partially loaded catalogue would otherwise raise deep inside `cosine()`.
    """
    rows = _paged(
        lambda: client.table("occupations").select(
            "onet_soc_code, title, job_zone, "
            "interest_r, interest_i, interest_a, interest_s, interest_e, interest_c, "
            "pk_title, pk_pathway, pk_relevant, entrepreneurial_track"
        )
    )

    occupations = []
    for row in rows:
        interests = {
            letter: row[f"interest_{letter.lower()}"]
            for letter in ("R", "I", "A", "S", "E", "C")
        }
        if row["job_zone"] is None or any(v is None for v in interests.values()):
            continue
        occupations.append(
            Occupation(
                onet_soc_code=row["onet_soc_code"],
                title=row["title"],
                job_zone=row["job_zone"],
                interests={k: float(v) for k, v in interests.items()},
                pk_title=row.get("pk_title"),
                pk_pathway=row.get("pk_pathway"),
                pk_relevant=bool(row.get("pk_relevant")),
                entrepreneurial_track=bool(row.get("entrepreneurial_track")),
            )
        )

    if not occupations:
        raise ScoringError(
            "the occupations table is empty — run scripts/load_onet.py against this database"
        )
    return occupations


def load_student_report(client, session_id: str) -> StudentReportInput:
    """Everything the student report renders from (plan §14, M9).

    Reads the CONFIRMED review, and refuses without one. R9 is enforced by the
    database trigger on `reports`, but discovering the violation at insert time
    would mean the PDF was already rendered and uploaded — so the check happens
    here too, before any work. Two enforcement points, neither redundant: this
    one keeps an unreviewed student's data out of a rendered document at all.

    Scores are read at the LATEST engine version present for the session, not at
    `SCORING_ENGINE_VERSION`, for the same reason the review screen does it
    (`web/app/review/[session_id]/queries.ts`): a rescore writes a new row beside
    the old one (R1), and pinning to the current version would fail to render
    every session scored before the last bump.

    Deliberately does NOT load `scores.flags` or `reviews.interview_notes`.
    R8 keeps flags off the student's copy and §16 treats the notes like health
    data; a loader that fetched them would put one template mistake between
    those rules and a student's inbox.
    """
    session = _one(
        client.table("sessions").select("id, participant_id").eq("id", session_id).execute(),
        f"no session {session_id}",
    )
    participant = _one(
        client.table("participants")
        .select("id, full_name, cohort_id")
        .eq("id", session["participant_id"])
        .execute(),
        f"no participant for session {session_id}",
    )
    cohort = _one(
        client.table("cohorts")
        .select("id, name, organisation_id")
        .eq("id", participant["cohort_id"])
        .execute(),
        f"no cohort for session {session_id}",
    )
    organisation = _one(
        client.table("organisations")
        .select("name, brand_hex, logo_path")
        .eq("id", cohort["organisation_id"])
        .execute(),
        f"no organisation for session {session_id}",
    )

    review_rows = (
        client.table("reviews")
        .select("id, status, confirmed_at")
        .eq("session_id", session_id)
        .execute()
    ).data or []
    review = review_rows[0] if review_rows else None

    if not review or review.get("status") != "confirmed":
        # Not a ScoringError — nothing about the scores is wrong. This is the R9
        # gate, and the message says so, because it will be read in
        # `jobs.last_error` by someone deciding whether it is a bug.
        raise ReportNotAllowed(
            f"session {session_id} has no confirmed review — R9 forbids a student report"
        )

    score_rows = (
        client.table("scores")
        .select("engine_version, interests, personality, values_scores, scored_at")
        .eq("session_id", session_id)
        .order("scored_at", desc=True)
        .limit(1)
        .execute()
    ).data or []
    if not score_rows:
        raise ScoringError(f"session {session_id} has no scores row to report on")
    score = score_rows[0]

    match_rows = (
        client.table("occupation_matches")
        .select("rank, onet_soc_code, local_title, local_pathway, occupations(title)")
        .eq("session_id", session_id)
        .eq("engine_version", score["engine_version"])
        .order("rank")
        .execute()
    ).data or []

    direction_rows = (
        client.table("career_directions")
        .select("rank, onet_soc_code, local_title, rationale")
        .eq("review_id", review["id"])
        .order("rank")
        .execute()
    ).data or []

    return StudentReportInput(
        student_name=participant["full_name"],
        organisation=Organisation(
            name=organisation["name"],
            brand_hex=organisation.get("brand_hex"),
            logo_path=organisation.get("logo_path"),
            logo_url=_logo_url(client, organisation.get("logo_path")),
        ),
        cohort_name=cohort["name"],
        interests=score["interests"] or {},
        personality=score["personality"] or {},
        # `values_scores` holds the GET2 shape in v2; the column was not renamed
        # so rescoreable history stays intact (0003_reviews.sql's comment).
        get2=score.get("values_scores") or {},
        matches=[
            Match(
                rank=row["rank"],
                title=(row.get("occupations") or {}).get("title") or row["onet_soc_code"],
                local_title=row.get("local_title"),
                local_pathway=row.get("local_pathway"),
                onet_soc_code=row["onet_soc_code"],
            )
            for row in match_rows
        ],
        directions=[
            Direction(
                rank=row["rank"],
                local_title=row["local_title"],
                rationale=row.get("rationale"),
                onet_soc_code=row.get("onet_soc_code"),
            )
            for row in direction_rows
        ],
        confirmed_on=_report_date(review.get("confirmed_at")),
        engine_version=score["engine_version"],
        template_version=TEMPLATE_VERSION,
    )


def _logo_url(client, logo_path: str | None) -> str | None:
    """A fetchable URL for the school's crest, or None.

    The `branding` bucket is public, unlike `reports`: a school crest is not
    confidential, and WeasyPrint fetches images over HTTP at render time with no
    session of its own. A signed URL would work too and would expire mid-render
    on a slow queue for no benefit.

    Returns None rather than raising on any failure. A missing crest is a cover
    page without a logo; a raised exception here would fail the whole render and
    turn a cosmetic gap into a student with no report.
    """
    if not logo_path:
        return None
    try:
        return client.storage.from_(BRANDING_BUCKET).get_public_url(logo_path)
    except Exception:
        log.warning("could not resolve a public URL for logo %s", logo_path)
        return None


def _report_date(value: str | None) -> str:
    """`confirmed_at` as a student reads it — "12 March 2026".

    Not ISO. This appears on the cover under a sixteen-year-old's name, and
    `2026-03-12T09:14:22.481+00:00` reads as a machine's output rather than a
    date a person signed something on.
    """
    stamp = _timestamp(value)
    if stamp is None:
        return "—"
    return f"{stamp.day} {stamp.strftime('%B %Y')}"


def load_get2_scoring() -> dict | None:
    """Parsed get2_scoring.json, or None when it does not exist.

    None is the normal case today and returns the `module_not_administered`
    shape from `score_get2` (R10, plan §20 item 2). Absence is not an error:
    "adding GET2 is loading a file" only stays true if every layer treats the
    file's absence as a configuration state rather than a fault.
    """
    if not GET2_SCORING_PATH.exists():
        return None
    return json.loads(GET2_SCORING_PATH.read_text(encoding="utf-8"))


def _item_ids(client, items: list[Item]) -> dict[str, str]:
    """{item_id: code} for every loaded item.

    A second query rather than threading the uuid through `Item`: `Item` is the
    pure scoring type and has no id field on purpose — a database key inside it
    would be one more thing tempting a scorer to look something up.
    """
    rows = _paged(lambda: client.table("items").select("id, code"))
    mapping = {row["id"]: row["code"] for row in rows}

    if len(mapping) != len(items):
        raise ScoringError(
            f"item id lookup returned {len(mapping)} rows for {len(items)} items — "
            "the items table changed mid-load"
        )
    return mapping


def _paged(build_query) -> list[dict]:
    """Run a select, following pages until a short one comes back.

    PostgREST caps a request at `db-max-rows` (1000 on Supabase) and returns the
    truncated set with no error. Anything that could exceed that — items today
    at 110, occupations at 923 — must page, or it silently loses the tail.

    Takes a factory, not a query. supabase-py's builder mutates in place, so
    calling `.range()` twice on one instance appends a second Range header
    instead of replacing the first — the second page then comes back identical
    to the first and the loop never ends. Building fresh each time is the fix
    that does not depend on knowing that.
    """
    rows: list[dict] = []
    offset = 0
    while True:
        page = build_query().range(offset, offset + PAGE_SIZE - 1).execute().data or []
        rows.extend(page)
        if len(page) < PAGE_SIZE:
            return rows
        offset += PAGE_SIZE


def _one(result, missing_message: str) -> dict:
    rows = result.data or []
    if not rows:
        raise ScoringError(missing_message)
    return rows[0]


def _timestamp(value: str | None) -> datetime | None:
    """Postgres timestamptz as text -> datetime.

    `flags.py`'s long-gap check subtracts these, so a string that silently
    stayed a string would raise a TypeError deep inside flag computation rather
    than here. Python 3.11+ parses the offset form Postgres emits ("+00:00");
    the trailing-Z form is normalised because some clients send it back that way.
    """
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))

"""Occupation matching (plan §8).

Ranks O*NET occupations against a student's RIASEC interest profile. Pure: the
occupation catalogue arrives as an argument, nothing here reads the database.

**Ipsatize before comparing.** Interest profiles differ in elevation — some
people check almost every box, some check almost none — and that elevation says
more about response style than about fit. Subtracting each profile's own mean
across its six scales leaves only shape, on both sides of the comparison.

**Interests only.** v1 blended a values term (`0.70 * interest_sim + 0.30 *
values_sim`) computed against the Work Importance Locator. v2 retired the WIL
(plan §19) and §7.3/§8 are explicit that GET2 is *not* folded into this
comparison — it measures tendency to act, not interest content, and O*NET has no
equivalent axis. There is no student values vector in v2 and O*NET 31.0 no longer
publishes occupation Work Values at all, so the term has no operand on either
side and is gone rather than silently zeroed.

GET2 reaches this module only as `entrepreneurial_flag`, a boolean that adds a
small, separately labelled set of entrepreneurship-track occupations — it never
reweights the ranking itself.
"""

import logging
import math
from dataclasses import dataclass, field

from app.scoring.interests import RIASEC
from app.scoring.types import ScoringError

log = logging.getLogger(__name__)

# Job Zone is an education/experience band: a matric student should not be told
# to consider occupations that assume a doctorate. O*NET 31.0 merged the old
# Job Zone 1 into a combined "Job Zone 1-2" band coded 2 — there are no zone-1
# rows in the catalogue any more, so plan §8's `matric -> [1,2,3]` is written
# here as [2,3]. Behaviour is unchanged; the dead value is just not carried.
# Plan §20 item 6 flags this whole mapping as provisional, to be confirmed
# against real admissions outcomes rather than assumed.
JOB_ZONES_FOR_EDUCATION = {
    "matric": (2, 3),
    "intermediate": (2, 3, 4),
    "undergraduate": (3, 4, 5),
}
EDUCATION_FALLBACK = "intermediate"

# Without a diversity cap the top 15 is fifteen varieties of the same job and
# the report looks unintelligent. Two per 2-digit SOC major group (plan §8).
MAX_PER_SOC_GROUP = 2

# The localisation split (plan §8): Pakistan-mapped occupations lead, and
# international ones follow under their own heading rather than being mixed in.
LOCAL_SLOTS = 10
INTERNATIONAL_SLOTS = 5
DEFAULT_K = LOCAL_SLOTS + INTERNATIONAL_SLOTS

# Fewer local matches than this means pk_occupation_map.csv has a gap for this
# profile shape. That is a content problem, not a bug — the flag exists so the
# gaps can be reviewed monthly and tell you which occupations to map next.
LOCAL_MATCH_TARGET = 5

# When GET2 says the student has entrepreneurial tendency, up to this many
# entrepreneurship-track occupations are surfaced even though their raw cosine
# score left them outside the ranked list. Returned separately, never blended
# into `matches` — the report labels them "worth exploring given your GET2
# profile" so nobody mistakes them for interest-derived matches (plan §8).
ENTREPRENEURIAL_SLOTS = 3


@dataclass(frozen=True, slots=True)
class Occupation:
    """One row of the O*NET catalogue, already localised.

    `interests` holds the six O*NET Occupational Interest ratings keyed by RIASEC
    letter. Their absolute range does not matter — ipsatizing removes it — but
    all six must be present, which `scripts/load_onet.py` enforces at load time.
    """

    onet_soc_code: str
    title: str
    job_zone: int
    interests: dict[str, float] = field(default_factory=dict)
    pk_title: str | None = None
    pk_pathway: str | None = None
    pk_relevant: bool = False
    entrepreneurial_track: bool = False


def match(
    student_interests: dict[str, int],
    occupations: list[Occupation],
    education_level: str | None,
    entrepreneurial_flag: bool = False,
    k: int = DEFAULT_K,
) -> dict:
    """Rank occupations against a student's interest profile.

    Args:
        student_interests: raw RIASEC scores, as returned in `score_interests()["raw"]`.
        occupations: the catalogue to rank, already loaded from `occupations`.
        education_level: matric | intermediate | undergraduate. An unrecognised
            value falls back to `intermediate` with a warning rather than
            returning nothing — a missing roster field should narrow the report,
            not empty it.
        entrepreneurial_flag: `score_get2()["entrepreneurial_flag"]`. Adds the
            §8 carve-out; never changes the ranking.
        k: how many ranked matches to return.

    Returns `matches` (local first, then international), `entrepreneurial_additions`
    kept deliberately separate, and `local_match_gap`.
    """
    if not occupations:
        raise ScoringError("no occupations supplied")

    student = ipsatize(_student_vector(student_interests))
    pool = [o for o in occupations if o.job_zone in _job_zones_for(education_level)]

    if not pool:
        # Every occupation was filtered out by Job Zone. That is a catalogue or
        # education-level problem, not an empty result to render as "no matches".
        raise ScoringError(
            f"no occupations in job zones {_job_zones_for(education_level)} "
            f"for education level {education_level!r}"
        )

    ranked = sorted(
        ((occ, _score_against(student, occ)) for occ in pool),
        # Ties break on SOC code so the same inputs always produce the same
        # report — occupation order out of Postgres is not guaranteed.
        key=lambda row: (-row[1], row[0].onet_soc_code),
    )

    selected = diversify(ranked, k=k)
    local = [row for row in selected if row[0].pk_relevant]
    international = [row for row in selected if not row[0].pk_relevant]

    matches = local[:LOCAL_SLOTS] + international[:INTERNATIONAL_SLOTS]

    if len(local) < LOCAL_MATCH_TARGET:
        log.info(
            "pk_occupation_map gap: %d local matches for code %s (target %d)",
            len(local),
            "".join(sorted(student_interests, key=lambda s: -student_interests[s])[:3]),
            LOCAL_MATCH_TARGET,
        )

    return {
        "matches": [_as_match(occ, score, rank) for rank, (occ, score) in enumerate(matches, 1)],
        "entrepreneurial_additions": _entrepreneurial_additions(
            ranked, selected, entrepreneurial_flag
        ),
        "local_match_gap": len(local) < LOCAL_MATCH_TARGET,
    }


def ipsatize(vector: dict[str, float]) -> dict[str, float]:
    """Centre a profile on its own mean, leaving shape without elevation."""
    mean = math.fsum(vector.values()) / len(vector)
    return {scale: value - mean for scale, value in vector.items()}


def cosine(a: dict[str, float], b: dict[str, float]) -> float:
    """Cosine similarity over the RIASEC scales, -1..1.

    On mean-centred input this is the Pearson correlation between the two
    profiles, which is what makes it a shape comparison.
    """
    magnitude_a = math.sqrt(math.fsum(v * v for v in a.values()))
    magnitude_b = math.sqrt(math.fsum(v * v for v in b.values()))

    # A profile that scored every scale identically centres to the zero vector,
    # which has no direction and therefore no defined similarity to anything.
    # Ranking it would return whatever order the catalogue happened to be in and
    # present that as a recommendation. score_interests() already labels this
    # profile `undifferentiated` — the caller branches on that and tells the
    # student to explore rather than showing a top 15 (plan §7.1).
    if math.isclose(magnitude_a, 0.0) or math.isclose(magnitude_b, 0.0):
        raise ScoringError(
            "cannot match an undifferentiated profile: every interest scale scored "
            "the same, so there is no profile shape to compare"
        )

    return math.fsum(a[scale] * b.get(scale, 0.0) for scale in a) / (magnitude_a * magnitude_b)


def norm01(x: float) -> float:
    """Map a -1..1 similarity onto 0..1 (plan §8)."""
    return (x + 1) / 2


def soc_major_group(onet_soc_code: str) -> str:
    """The 2-digit SOC major group — '29-1141.00' -> '29'."""
    return onet_soc_code[:2]


def diversify(
    ranked: list[tuple[Occupation, float]],
    k: int,
    max_per_group: int = MAX_PER_SOC_GROUP,
) -> list[tuple[Occupation, float]]:
    """Take the top k, allowing at most `max_per_group` per SOC major group.

    Greedy over an already-sorted list: each occupation is kept unless its group
    is full, so the highest-scoring member of every group always survives.
    """
    kept: list[tuple[Occupation, float]] = []
    per_group: dict[str, int] = {}

    for occ, score in ranked:
        group = soc_major_group(occ.onet_soc_code)
        if per_group.get(group, 0) >= max_per_group:
            continue
        per_group[group] = per_group.get(group, 0) + 1
        kept.append((occ, score))
        if len(kept) == k:
            break

    return kept


def _student_vector(student_interests: dict[str, int]) -> dict[str, float]:
    missing = [scale for scale in RIASEC if scale not in student_interests]
    if missing:
        raise ScoringError(f"student interest profile missing scales: {missing}")
    return {scale: float(student_interests[scale]) for scale in RIASEC}


def _score_against(student: dict[str, float], occ: Occupation) -> float:
    missing = [scale for scale in RIASEC if scale not in occ.interests]
    if missing:
        raise ScoringError(
            f"occupation {occ.onet_soc_code} missing interest scales {missing} — "
            f"re-run scripts/load_onet.py"
        )

    occupation_shape = ipsatize({scale: float(occ.interests[scale]) for scale in RIASEC})
    # occupation_matches.score is numeric(5,4); round here so what gets ranked is
    # what gets stored, rather than a value that shifts on its way to Postgres.
    return round(norm01(cosine(student, occupation_shape)), 4)


def _job_zones_for(education_level: str | None) -> tuple[int, ...]:
    if education_level in JOB_ZONES_FOR_EDUCATION:
        return JOB_ZONES_FOR_EDUCATION[education_level]

    log.warning(
        "unknown education_level %r, falling back to %r",
        education_level,
        EDUCATION_FALLBACK,
    )
    return JOB_ZONES_FOR_EDUCATION[EDUCATION_FALLBACK]


def _entrepreneurial_additions(
    ranked: list[tuple[Occupation, float]],
    selected: list[tuple[Occupation, float]],
    entrepreneurial_flag: bool,
) -> list[dict]:
    """The §8 carve-out: track occupations that the cosine ranking passed over.

    Drawn only from occupations *not* already in `matches`, so a track occupation
    that earned its place on interest alone is never double-counted.
    """
    if not entrepreneurial_flag:
        return []

    already_shown = {occ.onet_soc_code for occ, _ in selected}
    additions = [
        (occ, score)
        for occ, score in ranked
        if occ.entrepreneurial_track and occ.onet_soc_code not in already_shown
    ][:ENTREPRENEURIAL_SLOTS]

    return [_as_match(occ, score, rank) for rank, (occ, score) in enumerate(additions, 1)]


def _as_match(occ: Occupation, score: float, rank: int) -> dict:
    """Shape one result for the `occupation_matches` table and the report."""
    return {
        "rank": rank,
        "onet_soc_code": occ.onet_soc_code,
        "title": occ.title,
        "score": score,
        "local_title": occ.pk_title,
        "local_pathway": occ.pk_pathway,
        "pk_relevant": occ.pk_relevant,
        "entrepreneurial_track": occ.entrepreneurial_track,
    }

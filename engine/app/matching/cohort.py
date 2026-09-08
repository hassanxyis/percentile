"""Cohort analytics (plan §10, M11).

PURE, like everything else in this package: participant rows in, numbers out. No
database, no settings, no file access — `repository.py` loads and
`report/cohort.py` decides what appears on the page. That split is what lets the
statistical rules below be tested arithmetically instead of by rendering a PDF
and reading it.

─────────────────────────────────────────────────────────────────────────────
THREE RULES THIS MODULE ENFORCES, NOT JUST FOLLOWS

* **§9 statistical honesty.** `describe_rate` will not produce a percentage on a
  denominator below 10. Not "should not" — cannot: the string it returns has no
  percentage in it, so no template can print one by accident.
* **§7.4 exclusion.** Two or more `warn` flags removes a participant from every
  aggregate, and the count of who was removed travels with the result so the
  report can state it. An aggregate that quietly dropped people would overstate
  its own n.
* **R8, at the boundary this document draws.** The follow-up list names students,
  because §10 requires it. The exclusion breakdown does NOT — it is a count per
  flag code with no names attached. "Fatima Khan — straightlining" in a document
  that circulates inside a school is a quality judgement about a minor with her
  name on it, and §7.4 asks for how many and why, not for who.
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from app.matching.occupations import cosine, ipsatize, norm01
from app.scoring.interests import RIASEC
from app.scoring.personality import BIG_FIVE, DOMAIN_LABELS
from app.scoring.types import ScoringError

# Congruence classes (plan §10). Provisional thresholds — the method page says so
# in words. Stated as the lower bound of each class, so the boundary value falls
# in the HIGHER class: exactly 0.60 is aligned, exactly 0.35 is partial.
ALIGNED_MIN = 0.60
PARTIAL_MIN = 0.35

ALIGNED = "aligned"
PARTIAL = "partial"
MISALIGNED = "misaligned"

# §9: "never print a percentage on a denominator below 10 without the denominator
# beside it". Enforced by withholding the percentage entirely below this — see
# `describe_rate`.
MIN_DENOMINATOR_FOR_PERCENTAGE = 10

# §7.4: "Two or more `warn` flags exclude a participant from cohort aggregates,
# and the cohort report must then state how many were excluded and why."
EXCLUDE_AT_WARN_FLAGS = 2

# Why a student appears on the §10 follow-up list. Both are facts about a result,
# never about how they answered — a response-quality flag is not a reason to put
# a name on a page (R8).
FOLLOW_UP_UNDIFFERENTIATED = "no clear interest profile emerged"
FOLLOW_UP_MISALIGNED = "interests differ from others heading into the same field"


@dataclass(frozen=True, slots=True)
class Participant:
    """One scored student, as `repository.load_cohort_report` hands them over.

    `full_name` IS carried, unlike `StudentReportInput` — §10 requires "named
    lists of students to follow up" and that page is the most useful one in the
    document. What is deliberately absent is the flag DETAIL: `warn_flags` is a
    count and `flag_codes` are codes, so no phrase like "31 identical
    consecutive answers" can travel next to a name.
    """

    participant_id: str
    full_name: str
    intended_field: str | None
    status: str
    interests: dict[str, int]
    band: str
    personality_bands: dict[str, str]
    warn_flags: int = 0
    flag_codes: tuple[str, ...] = ()

    @property
    def excluded(self) -> bool:
        return self.warn_flags >= EXCLUDE_AT_WARN_FLAGS


@dataclass(frozen=True, slots=True)
class FieldGroup:
    """One `intended_field` and the shape of the students heading into it."""

    field: str
    n: int
    centroid: dict[str, float]
    # None when the group is too small for a leave-one-out comparison — see
    # `_congruence_for`. Absence, not a flattering default.
    aligned: int = 0
    partial: int = 0
    misaligned: int = 0
    uncomputable: int = 0


@dataclass(frozen=True, slots=True)
class FollowUp:
    """One student the counsellor should talk to, and the reason (§10)."""

    participant_id: str
    full_name: str
    intended_field: str | None
    reason: str


@dataclass(frozen=True, slots=True)
class CohortAggregate:
    """Everything the cohort report renders from.

    `total_participants` counts the whole roster; `scored` counts those with a
    score; `included` excludes the §7.4 cases. Three numbers rather than one,
    because a report that said "20" while aggregating 14 of them would be lying
    in the most ordinary way a report can.
    """

    total_participants: int
    scored: int
    included: int
    excluded: int
    excluded_by_code: dict[str, int] = field(default_factory=dict)
    status_counts: dict[str, int] = field(default_factory=dict)
    interest_distribution: dict[str, int] = field(default_factory=dict)
    personality_distribution: dict[str, dict[str, int]] = field(default_factory=dict)
    fields: list[FieldGroup] = field(default_factory=list)
    congruence: dict[str, int] = field(default_factory=dict)
    congruence_denominator: int = 0
    follow_up: list[FollowUp] = field(default_factory=list)


def aggregate(
    participants: list[Participant],
    all_statuses: list[str],
) -> CohortAggregate:
    """Everything §10 asks for, from one cohort's scored participants.

    Args:
        participants: every participant in the cohort who has a `scores` row.
        all_statuses: `participants.status` for EVERY participant in the cohort,
            scored or not. Passed separately because the completion table counts
            the whole roster — an invited student who never started is exactly
            who a counsellor opens this table to find, and they have no score to
            arrive in the list above.

    Raises `ScoringError` when nothing can be aggregated. A cohort report over
    zero students is not a small report, it is a false one.
    """
    if not participants:
        raise ScoringError(
            "no scored participants in this cohort — there is nothing to aggregate"
        )

    included = [p for p in participants if not p.excluded]
    excluded = [p for p in participants if p.excluded]

    if not included:
        raise ScoringError(
            f"all {len(participants)} scored participants were excluded by response-quality "
            f"flags (plan §7.4) — this cohort has no aggregate to report"
        )

    groups = _field_groups(included)

    return CohortAggregate(
        total_participants=len(all_statuses),
        scored=len(participants),
        included=len(included),
        excluded=len(excluded),
        excluded_by_code=_excluded_by_code(excluded),
        status_counts=_status_counts(all_statuses),
        interest_distribution=_interest_distribution(included),
        personality_distribution=_personality_distribution(included),
        fields=groups,
        congruence=_congruence_totals(groups),
        congruence_denominator=sum(
            g.aligned + g.partial + g.misaligned for g in groups
        ),
        follow_up=_follow_up(included),
    )


def describe_rate(count: int, total: int) -> str:
    """"12 of 20 (60%)" — or "3 of 7", with no percentage at all.

    §9's rule is "never print a percentage on a denominator below 10 without the
    denominator beside it". This goes one step further and withholds the
    percentage entirely below 10, for the reason `web/lib/roster.ts`'s
    `progressSentence` gives on the other side of the product: a count with its
    total is honest at any size, and a rule the template cannot break is worth
    more than a rule the template is asked to remember.

    A pilot cohort is 15–25 students (§9.3) and an `intended_field` group inside
    it will routinely be 2 or 3. "67% of this field is misaligned" describing two
    students is the single most misleading sentence this report could print.
    """
    if total <= 0:
        return "none"
    if total < MIN_DENOMINATOR_FOR_PERCENTAGE:
        return f"{count} of {total}"
    return f"{count} of {total} ({round(100 * count / total)}%)"


def congruence_class(score: float) -> str:
    """Which §10 band a congruence score falls in.

    Boundaries are inclusive at the bottom of the higher class: exactly 0.60 is
    `aligned`, exactly 0.35 is `partial`. Stated once here so the report and the
    method page cannot disagree about a student sitting on the line.
    """
    if score >= ALIGNED_MIN:
        return ALIGNED
    if score >= PARTIAL_MIN:
        return PARTIAL
    return MISALIGNED


def centroid(participants: list[Participant]) -> dict[str, float]:
    """The mean RIASEC vector of a group.

    Not ipsatized here. Ipsatizing is what `_congruence_for` does to BOTH sides
    immediately before comparing them, and a centroid that arrived pre-centred
    could not be drawn as a hexagon on the page — the report shows a field's
    average profile, and elevation is part of that picture even though it plays
    no part in the comparison.
    """
    if not participants:
        raise ScoringError("cannot take a centroid of an empty group")

    return {
        scale: math.fsum(float(p.interests.get(scale, 0)) for p in participants)
        / len(participants)
        for scale in RIASEC
    }


# ── field groups and congruence ──────────────────────────────────────────────


def _field_groups(included: list[Participant]) -> list[FieldGroup]:
    """One group per `intended_field`, with its centroid and congruence counts.

    Participants with no `intended_field` are skipped rather than pooled into an
    "unknown" group. A centroid over students who have nothing in common except
    a blank column is not a field profile, and congruence against it would be
    arithmetic with no meaning — §10's congruence rate is "do students heading
    into this field resemble each other", and there is no field here.
    """
    by_field: dict[str, list[Participant]] = {}
    for participant in included:
        if participant.intended_field:
            by_field.setdefault(participant.intended_field, []).append(participant)

    groups = []
    for name in sorted(by_field):
        members = by_field[name]
        counts = {ALIGNED: 0, PARTIAL: 0, MISALIGNED: 0}
        uncomputable = 0

        for member in members:
            score = _congruence_for(member, members)
            if score is None:
                uncomputable += 1
            else:
                counts[congruence_class(score)] += 1

        groups.append(
            FieldGroup(
                field=name,
                n=len(members),
                centroid=centroid(members),
                aligned=counts[ALIGNED],
                partial=counts[PARTIAL],
                misaligned=counts[MISALIGNED],
                uncomputable=uncomputable,
            )
        )
    return groups


def _congruence_for(
    participant: Participant, group: list[Participant]
) -> float | None:
    """How well one student's shape matches the rest of their intended field.

    LEAVE-ONE-OUT, and that is the load-bearing decision in this module.

    Comparing a student against a centroid they are inside is circular: they
    contribute to the thing they are then measured against. In a group of one it
    is not merely circular but degenerate — the student IS the centroid, cosine
    returns exactly 1.0, and the report would announce perfect alignment for a
    field with a single student in it. §9's honesty rules exist to stop precisely
    that kind of number.

    Returns None when the group has one member, because there is then no "rest of
    the field" to compare against. Absence is the honest answer; the report says
    the group is too small rather than printing a flattering figure.

    Returns None too when either side ipsatizes to the zero vector — an
    undifferentiated profile has no shape, which is the same reason
    `occupations.cosine` refuses it. Those students reach the follow-up list via
    their `undifferentiated` band instead.
    """
    others = [p for p in group if p.participant_id != participant.participant_id]
    if not others:
        return None

    student = ipsatize({scale: float(participant.interests.get(scale, 0)) for scale in RIASEC})
    reference = ipsatize(centroid(others))

    try:
        return norm01(cosine(student, reference))
    except ScoringError:
        # A flat profile on either side. Not an error here: one student answering
        # every item the same must not fail the whole cohort's report.
        return None


def _congruence_totals(groups: list[FieldGroup]) -> dict[str, int]:
    """Cohort-wide congruence counts, summed across fields."""
    return {
        ALIGNED: sum(g.aligned for g in groups),
        PARTIAL: sum(g.partial for g in groups),
        MISALIGNED: sum(g.misaligned for g in groups),
    }


# ── distributions and counts ─────────────────────────────────────────────────


def _interest_distribution(included: list[Participant]) -> dict[str, int]:
    """How many students led with each RIASEC letter.

    The FIRST letter of the Holland code, not every letter in it. A student's
    code is three letters and counting all three would produce a distribution
    summing to 3n, which reads as a percentage of something that does not exist.
    """
    counts = dict.fromkeys(RIASEC, 0)
    for participant in included:
        letter = _first_letter(participant.interests)
        if letter:
            counts[letter] += 1
    return counts


def _first_letter(interests: dict[str, int]) -> str | None:
    """The highest-scoring RIASEC scale, ties broken by RIASEC order.

    Same tie-break as `score_interests` — RIASEC order — so a student's leading
    letter here is the first letter of the code on their own report. Two
    different answers to "what is this student's strongest interest" across two
    documents is the kind of inconsistency a counsellor notices and cannot
    explain.
    """
    if not interests:
        return None
    return min(RIASEC, key=lambda scale: (-interests.get(scale, 0), RIASEC.index(scale)))


def _personality_distribution(included: list[Participant]) -> dict[str, dict[str, int]]:
    """Band counts per Big Five domain, keyed by domain then band.

    Every domain appears even when no student landed in a given band, so the
    chart has a stable shape across cohorts and a missing bar means zero rather
    than a template that forgot to render it.
    """
    distribution: dict[str, dict[str, int]] = {}
    for domain in BIG_FIVE:
        counts: dict[str, int] = {}
        for participant in included:
            band = participant.personality_bands.get(domain)
            if band:
                counts[band] = counts.get(band, 0) + 1
        distribution[domain] = counts
    return distribution


def _status_counts(all_statuses: list[str]) -> dict[str, int]:
    """The completion table (§10), over every participant in the cohort.

    §10's v2 addition: `confirmed` and `pending_review` are their own numbers
    rather than being folded into "completion". A school comparing this term to
    next needs to see the review backlog as a figure it can act on — chasing a
    psychologist is something an institution can do, and it cannot do it from a
    single completion percentage.
    """
    counts: dict[str, int] = {}
    for status in all_statuses:
        counts[status] = counts.get(status, 0) + 1
    return counts


def _excluded_by_code(excluded: list[Participant]) -> dict[str, int]:
    """Why the excluded were excluded — codes and counts, never names (R8).

    §7.4 requires the report to state how many were excluded and why. A code with
    a count answers both. A name beside a code would answer a third question
    nobody asked, in a document that circulates inside a school.
    """
    counts: dict[str, int] = {}
    for participant in excluded:
        for code in participant.flag_codes:
            counts[code] = counts.get(code, 0) + 1
    return counts


def _follow_up(included: list[Participant]) -> list[FollowUp]:
    """The named list §10 asks for: who to talk to, and why.

    Two reasons qualify, both facts about a RESULT rather than about how someone
    answered:

    * `undifferentiated` — no clear interest profile emerged, so §7.1 says to
      explore rather than recommend. That conversation is the whole point.
    * `misaligned` — their interests differ from others heading into the same
      field. §9 forbids calling this "in the wrong field", and the wording here
      follows that: it describes what the instrument measured, not a verdict.

    A student who qualifies on both appears once, undifferentiated first — it is
    the more fundamental finding and it explains the other.
    """
    follow_up = []
    by_field: dict[str, list[Participant]] = {}
    for participant in included:
        if participant.intended_field:
            by_field.setdefault(participant.intended_field, []).append(participant)

    for participant in included:
        if participant.band == "undifferentiated":
            follow_up.append(_as_follow_up(participant, FOLLOW_UP_UNDIFFERENTIATED))
            continue

        group = by_field.get(participant.intended_field or "", [])
        score = _congruence_for(participant, group) if group else None
        if score is not None and congruence_class(score) == MISALIGNED:
            follow_up.append(_as_follow_up(participant, FOLLOW_UP_MISALIGNED))

    return sorted(follow_up, key=lambda f: f.full_name)


def _as_follow_up(participant: Participant, reason: str) -> FollowUp:
    return FollowUp(
        participant_id=participant.participant_id,
        full_name=participant.full_name,
        intended_field=participant.intended_field,
        reason=reason,
    )


# `DOMAIN_LABELS` is re-exported so `report/cohort.py` can label the personality
# distribution without importing from two modules — the label a student reads on
# their own report is the label the institution reads on this one.
__all__ = [
    "ALIGNED",
    "ALIGNED_MIN",
    "CohortAggregate",
    "DOMAIN_LABELS",
    "EXCLUDE_AT_WARN_FLAGS",
    "FieldGroup",
    "FollowUp",
    "MISALIGNED",
    "MIN_DENOMINATOR_FOR_PERCENTAGE",
    "PARTIAL",
    "PARTIAL_MIN",
    "Participant",
    "aggregate",
    "centroid",
    "congruence_class",
    "describe_rate",
]

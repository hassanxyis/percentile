"""The cohort report's data assembly (plan §10, §14, M11).

PURE, exactly like `student.py`: takes a loaded `CohortReportInput` and an
`Interpretations` and returns the dict the Jinja2 template renders. No database,
no settings, no file access.

─────────────────────────────────────────────────────────────────────────────
THIS DOCUMENT IS THE INSTITUTION'S COPY, AND THAT CHANGES WHAT MAY BE ON IT

`StudentReportInput` carries no `flags` field and no other student's name,
because R8 keeps quality flags off the student's own report. This struct carries
BOTH names and flag codes. That is not a relaxation of R8 — it is the other side
of it. R8 sends the individual report to the student and the aggregate to the
institution, and §10 requires that aggregate to include "named lists of students
to follow up".

What it still must not carry, and does not:

* **Flag DETAIL strings.** `flags.py` writes sentences like "31 identical
  consecutive answers". A count per code answers §7.4's "how many were excluded
  and why"; the detail sentence beside a named minor, in a document a school
  files and circulates, is the accusation R8 exists to prevent — one step removed
  from putting it on the student's own copy. `CohortParticipant` has codes and a
  count, and no field a detail string could arrive in.
* **`interview_notes`, interview mode, or a reviewer's name.** §16 treats those
  like health data. There is no field for them here either.
* **Any individual's scores.** A student appears by name on exactly one page —
  the follow-up list — with a reason drawn from their interest band or their
  congruence class, both of which are results rather than judgements about how
  they answered.
─────────────────────────────────────────────────────────────────────────────

§9's statistical honesty is enforced by `_rate`, not by asking the template to
remember it: below ten the percentage does not exist in the context, so no
template edit can print one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.content import Interpretations
from app.matching import cohort as analytics
from app.report import charts
from app.report.student import Organisation, ReportError
from app.scoring.interests import RIASEC
from app.scoring.personality import BIG_FIVE, DOMAIN_LABELS

# The interest hexagon's ceiling — 10 checkbox items per RIASEC scale, the same
# constant `student.py` uses. A field centroid is a mean of those, so it is on
# the same scale and must be drawn against the same ceiling: a centroid scaled
# to its own maximum would make every field look equally strong, which is the
# one thing `charts.py`'s header forbids.
INTEREST_MAX = 10

# Personality band names, in order, from `PROVISIONAL_BAND_CUTOFFS`. Listed so
# the distribution chart has a stable row order across cohorts — a band with
# nobody in it draws an empty row rather than vanishing and shifting the others.
BAND_ORDER = ("very low", "low", "average", "high", "very high")

# Display order for the completion table. `participants_status_check`
# (0003_reviews.sql) constrains the column to exactly these nine; listing them
# here in lifecycle order means the table reads as a journey rather than as
# whatever order a dict happened to hold.
STATUS_ORDER = (
    "invited",
    "started",
    "submitted",
    "scored",
    "pending_review",
    "reviewed",
    "needs_more_info",
    "confirmed",
    "failed",
)

# Plain-language status labels for the institution. Deliberately the same
# wording `web/lib/roster.ts` shows the counsellor on screen: a school reading
# "Needs attention" in the PDF and "Needs attention" on the dashboard is reading
# one system, and `failed` must not appear as "Failed" beside a count of
# students in either place — a job broke, not a student.
STATUS_LABEL = {
    "invited": "Invited, not started",
    "started": "In progress",
    "submitted": "Submitted",
    "scored": "Awaiting review",
    "pending_review": "Awaiting review",
    "reviewed": "Being reviewed",
    "needs_more_info": "Follow-up requested",
    "confirmed": "Complete",
    "failed": "Needs attention",
}


@dataclass(frozen=True, slots=True)
class CohortReportInput:
    """Everything the cohort report renders from.

    Note what is NOT here, and see the module header for why: no flag detail
    strings, no `interview_notes`, no reviewer identity, no individual scores.
    """

    cohort_name: str
    organisation: Organisation
    intake_year: int | None
    education_level: str | None
    generated_on: str
    aggregate: analytics.CohortAggregate
    engine_version: str
    template_version: str


@dataclass(frozen=True, slots=True)
class Rate:
    """A proportion, and the only form in which one reaches the page.

    `percent` is None below ten, so §9's rule ("never print a percentage on a
    denominator below 10 without the denominator beside it") is not a thing the
    template is asked to remember — it is a number that does not exist to print.
    `text` always carries the denominator, whatever the size.
    """

    count: int
    total: int
    percent: int | None
    text: str


def build_cohort_context(
    report: CohortReportInput, interpretations: Interpretations
) -> dict[str, Any]:
    """Assemble the template context. Raises if the report cannot honestly render."""
    aggregate = report.aggregate

    if aggregate.included < 1:
        # Should be unreachable — `analytics.aggregate` raises first. Kept
        # because this is the last point before a PDF exists, and eight pages of
        # zeros presented as a cohort profile is worse than a failed job.
        raise ReportError(
            "a cohort report needs at least one included participant (plan §10)"
        )

    brand = report.organisation.brand_hex or charts.DEFAULT_BRAND

    return {
        "cohort_name": report.cohort_name,
        "organisation": report.organisation,
        "intake_year": report.intake_year,
        "education_level": report.education_level,
        "generated_on": report.generated_on,
        "brand": charts._colour(brand),  # noqa: SLF001 — same package, one validator
        "title": interpretations.text("report", "cohort", "title"),
        "subtitle": interpretations.text("report", "cohort", "subtitle"),
        "how_to_read": {
            "title": interpretations.text("report", "cohort", "how_to_read", "title"),
            "body": interpretations.text("report", "cohort", "how_to_read", "body"),
            "limits": interpretations.text("report", "cohort", "how_to_read", "limits"),
        },
        "counts": {
            "total": aggregate.total_participants,
            "scored": aggregate.scored,
            "included": aggregate.included,
            "excluded": aggregate.excluded,
        },
        "completion": _completion(report, interpretations),
        "excluded": _excluded(report, interpretations),
        "interests": _interests(report, interpretations, brand),
        "personality": _personality(report, interpretations, brand),
        "congruence": _congruence(report, interpretations, brand),
        "follow_up": _follow_up(report, interpretations),
        "method": {
            "title": interpretations.text("report", "cohort", "method", "title"),
            "body": interpretations.text("report", "cohort", "method", "body"),
        },
        "attribution": _attribution(interpretations),
        "confidential": interpretations.text("report", "cohort", "confidential"),
        "versions": {
            "engine": report.engine_version,
            "template": report.template_version,
        },
    }


def _rate(count: int, total: int) -> Rate:
    """The only way a proportion reaches this document (§9).

    Delegates the wording to `analytics.describe_rate` so the string on the page
    and the rule in the aggregation module cannot drift apart.
    """
    text = analytics.describe_rate(count, total)
    percent = (
        round(100 * count / total)
        if total >= analytics.MIN_DENOMINATOR_FOR_PERCENTAGE and total > 0
        else None
    )
    return Rate(count=count, total=total, percent=percent, text=text)


def _completion(
    report: CohortReportInput, interpretations: Interpretations
) -> dict[str, Any]:
    """The completion table (§10), over every participant on the roster.

    `pending_review` and `confirmed` are separate rows rather than one
    "completion" figure. §10's v2 addition says so, and the reason is
    actionable: a school that can see its reports are waiting on a review queue
    can chase a psychologist, which is the one part of this delay an institution
    can actually do something about.
    """
    counts = report.aggregate.status_counts
    total = report.aggregate.total_participants

    rows = [
        {
            "status": status,
            "label": STATUS_LABEL.get(status, status),
            "count": counts[status],
            "rate": _rate(counts[status], total),
        }
        for status in STATUS_ORDER
        if counts.get(status)
    ]

    # A status the database gained without this file. Shown under its raw name
    # rather than dropped: a row missing from a completion table is a student
    # missing from a school's count of its own cohort.
    rows.extend(
        {
            "status": status,
            "label": status,
            "count": count,
            "rate": _rate(count, total),
        }
        for status, count in sorted(counts.items())
        if status not in STATUS_ORDER
    )

    awaiting = sum(
        counts.get(status, 0)
        for status in ("submitted", "scored", "pending_review", "reviewed")
    )

    return {
        "title": interpretations.text("report", "cohort", "completion", "title"),
        "body": interpretations.text("report", "cohort", "completion", "body"),
        "backlog_note": interpretations.text(
            "report", "cohort", "completion", "backlog_note"
        ),
        "rows": rows,
        "total": total,
        "complete": _rate(counts.get("confirmed", 0), total),
        "awaiting_review": _rate(awaiting, total),
    }


def _excluded(
    report: CohortReportInput, interpretations: Interpretations
) -> dict[str, Any]:
    """§7.4's disclosure: how many were left out, and why.

    Codes and counts. No names — see the module header. `by_code` is sorted by
    count descending so the most common check leads, with the code as tie-break
    so two runs of the same cohort produce the same page.
    """
    aggregate = report.aggregate
    by_code = sorted(
        aggregate.excluded_by_code.items(), key=lambda item: (-item[1], item[0])
    )

    return {
        "title": interpretations.text("report", "cohort", "excluded", "title"),
        "body": interpretations.text("report", "cohort", "excluded", "body"),
        "none": interpretations.text("report", "cohort", "excluded", "none"),
        "count": aggregate.excluded,
        "rate": _rate(aggregate.excluded, aggregate.scored),
        "by_code": [{"code": code, "count": count} for code, count in by_code],
    }


def _interests(
    report: CohortReportInput, interpretations: Interpretations, brand: str
) -> dict[str, Any]:
    """What the cohort is drawn to — leading RIASEC letter, counted once each."""
    distribution = report.aggregate.interest_distribution
    included = report.aggregate.included

    rows = [
        (letter, float(distribution.get(letter, 0)), analytics.describe_rate(
            distribution.get(letter, 0), included
        ))
        for letter in RIASEC
    ]

    return {
        "title": interpretations.text("report", "cohort", "interests", "title"),
        "body": interpretations.text("report", "cohort", "interests", "body"),
        "letters": [
            {
                "letter": letter,
                "count": distribution.get(letter, 0),
                "rate": _rate(distribution.get(letter, 0), included),
            }
            for letter in RIASEC
        ],
        # Scaled to the cohort size, not to the largest bucket: a letter nobody
        # chose should draw an empty bar, and the tallest bar should mean
        # "most of this cohort" rather than "the most of anything here".
        "chart": charts.bars(rows, 0, max(included, 1), brand),
    }


def _personality(
    report: CohortReportInput, interpretations: Interpretations, brand: str
) -> dict[str, Any]:
    """Band counts per Big Five domain.

    One chart per domain rather than one chart of five stacked bars: a stacked
    bar makes five counts a matter of reading segment widths, which is the
    colour-only signalling `charts.py` exists to refuse.
    """
    distribution = report.aggregate.personality_distribution
    included = report.aggregate.included

    domains = []
    for domain in BIG_FIVE:
        counts = distribution.get(domain, {})
        rows = [
            (band, float(counts.get(band, 0)), analytics.describe_rate(
                counts.get(band, 0), included
            ))
            for band in BAND_ORDER
        ]
        domains.append(
            {
                "key": domain,
                "label": DOMAIN_LABELS[domain],
                "bands": [
                    {"band": band, "count": counts.get(band, 0)} for band in BAND_ORDER
                ],
                "chart": charts.bars(rows, 0, max(included, 1), brand),
            }
        )

    return {
        "title": interpretations.text("report", "cohort", "personality", "title"),
        "body": interpretations.text("report", "cohort", "personality", "body"),
        "domains": domains,
    }


def _congruence(
    report: CohortReportInput, interpretations: Interpretations, brand: str
) -> dict[str, Any]:
    """Interests against intended fields (§10).

    The thresholds printed on the method page come from the same constants
    `congruence_class` branches on, so the rule a reader is shown cannot drift
    from the rule that was applied.
    """
    aggregate = report.aggregate
    denominator = aggregate.congruence_denominator

    fields = [
        {
            "field": group.field,
            "n": group.n,
            "aligned": group.aligned,
            "partial": group.partial,
            "misaligned": group.misaligned,
            "uncomputable": group.uncomputable,
            "comparable": group.n > 1,
            "aligned_rate": _rate(
                group.aligned, group.aligned + group.partial + group.misaligned
            ),
            "chart": charts.hexagon(group.centroid, RIASEC, INTEREST_MAX, brand),
        }
        for group in aggregate.fields
    ]

    return {
        "title": interpretations.text("report", "cohort", "congruence", "title"),
        "body": interpretations.text("report", "cohort", "congruence", "body"),
        "method": interpretations.text("report", "cohort", "congruence", "method"),
        "thresholds": interpretations.text(
            "report", "cohort", "congruence", "thresholds"
        ),
        "too_few": interpretations.text("report", "cohort", "congruence", "too_few"),
        "aligned_min": analytics.ALIGNED_MIN,
        "partial_min": analytics.PARTIAL_MIN,
        "fields": fields,
        "totals": {
            "aligned": _rate(aggregate.congruence[analytics.ALIGNED], denominator),
            "partial": _rate(aggregate.congruence[analytics.PARTIAL], denominator),
            "misaligned": _rate(
                aggregate.congruence[analytics.MISALIGNED], denominator
            ),
        },
        "denominator": denominator,
    }


def _follow_up(
    report: CohortReportInput, interpretations: Interpretations
) -> dict[str, Any]:
    """The named list (§10) — the one page where a student appears by name.

    `students` rather than `items`: Jinja resolves `.items` to dict.items, the
    built-in winning over the key, so the template silently iterates something
    useless. The same trap `student.py`'s `directions.chosen` avoids.
    """
    return {
        "title": interpretations.text("report", "cohort", "follow_up", "title"),
        "body": interpretations.text("report", "cohort", "follow_up", "body"),
        "none": interpretations.text("report", "cohort", "follow_up", "none"),
        "students": list(report.aggregate.follow_up),
    }


def _attribution(interpretations: Interpretations) -> dict[str, Any]:
    """Licence text for the method page and the running footer (R6).

    No GET2 line. This document reports interests and personality only — the
    entrepreneurial module is not administered, and R10 makes crediting a
    licensor for an instrument nobody ran both wrong and unhelpful to a licence
    still being negotiated. When GET2 ships, this grows the same conditional
    `student.py`'s `_attribution` already carries.
    """
    return {
        "onet": interpretations.text("attribution", "onet"),
        "onet_licence_url": interpretations.text("attribution", "onet_licence_url"),
        "ipip": interpretations.text("attribution", "ipip"),
        "short": interpretations.text("attribution", "short"),
    }

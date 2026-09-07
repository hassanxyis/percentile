"""The student report's data assembly (plan §14).

PURE. Takes a loaded `StudentReportInput` and returns the dict the Jinja2
template renders. No database, no settings, no file access — `repository.py`
loads, `handlers/render.py` writes, and this decides what appears on the page.

That split is what makes the R8 test meaningful: `build_context` is called
directly with a session carrying flags, and the assertion is that no flag
appears anywhere in its output. A function that fetched its own data could only
be tested by rendering a PDF and grepping it.

─────────────────────────────────────────────────────────────────────────────
THREE RULES THIS MODULE ENFORCES, NOT JUST FOLLOWS

* **R8 — response-quality flags never reach the student's copy.** `flags` is not
  a field on the context this builds. Not filtered later in the template, not
  hidden with CSS: absent, so a template edit cannot reintroduce it.
* **R3 — every interpretive string comes from `interpretations.yaml`.** Nothing
  in this file writes a sentence about a student. The strings it assembles are
  looked up by band and letter, and a missing one raises rather than defaults.
* **R10 — the GET2 page is omitted entirely, not shown blank.** `get2` is None
  in the context when the module was not administered, and the template has no
  section at all rather than an empty one.
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.content import Interpretations
from app.report import charts
from app.scoring.get2 import GET2_SUBSCALES
from app.scoring.interests import RIASEC
from app.scoring.personality import BIG_FIVE, DOMAIN_LABELS

# Instrument ceilings, for scaling the charts. Read from the scoring modules'
# own documented ranges rather than from the observed data (charts.py's header
# explains why scaling to the observed maximum is wrong).
INTEREST_MAX = 10          # 10 checkbox items per RIASEC scale
PERSONALITY_MIN = 10       # 10 items, 1..5 each
PERSONALITY_MAX = 50
GET2_MIN = 0               # subscales are already percentages of their own max
GET2_MAX = 100

# §14 p.8–9. Fifteen is what §8 ranks; the entrepreneurial additions are
# appended after them by `match_occupations` and labelled separately.
MATCH_LIMIT = 15


class ReportError(RuntimeError):
    """The report cannot be built from this input.

    Distinct from `InterpretationMissing` (text nobody has written) — this is a
    session that should not have reached a render at all, most importantly one
    whose review is not confirmed. Raised rather than rendered around: R9 says
    no report without sign-off, and a report with a blank directions page would
    be a report.
    """


@dataclass(frozen=True, slots=True)
class Organisation:
    name: str
    brand_hex: str | None
    logo_path: str | None


@dataclass(frozen=True, slots=True)
class Direction:
    """One confirmed career direction — the psychologist's own words (§9.1).

    `rationale` is written by a human in the review screen and is the only
    free text in this report that did not come from `interpretations.yaml`. It
    is reproduced verbatim; nothing here rewrites or summarises it (R3).
    """

    rank: int
    local_title: str
    rationale: str | None
    onet_soc_code: str | None


@dataclass(frozen=True, slots=True)
class Match:
    rank: int
    title: str
    local_title: str | None
    local_pathway: str | None
    onet_soc_code: str


@dataclass(frozen=True, slots=True)
class StudentReportInput:
    """Everything the student report renders from.

    Note what is NOT here: `flags`, `interview_notes`, `interview_mode`, and the
    reviewer's name. R8 keeps flags off the student's copy; §16 treats
    interview_notes like health data and permits only the rationale line the
    psychologist explicitly chose to include. A struct that carried them would
    put one template mistake between those rules and a student's inbox.
    """

    student_name: str
    organisation: Organisation
    cohort_name: str
    interests: dict[str, Any]
    personality: dict[str, Any]
    get2: dict[str, Any]
    matches: list[Match]
    directions: list[Direction]
    confirmed_on: str
    engine_version: str
    template_version: str


def build_context(
    report: StudentReportInput, interpretations: Interpretations
) -> dict[str, Any]:
    """Assemble the template context. Raises if the report cannot honestly render."""
    if not report.directions:
        # R9's other half. The trigger stops a `reports` row without a confirmed
        # review; this stops a *rendered* report without the directions that
        # confirmation is defined as producing (`save_review` refuses to confirm
        # with zero). Reaching here means something wrote `confirmed` around it.
        raise ReportError(
            "a student report needs at least one confirmed career direction (R9, §9.1)"
        )

    brand = report.organisation.brand_hex or charts.DEFAULT_BRAND

    return {
        "student_name": report.student_name,
        "organisation": report.organisation,
        "cohort_name": report.cohort_name,
        "confirmed_on": report.confirmed_on,
        "brand": charts._colour(brand),  # noqa: SLF001 — same package, one validator
        "how_to_read": {
            "title": interpretations.text("report", "how_to_read", "title"),
            "body": interpretations.text("report", "how_to_read", "body"),
            "limits": interpretations.text("report", "how_to_read", "limits"),
        },
        "interests": _interests(report, interpretations, brand),
        "personality": _personality(report, interpretations, brand),
        "get2": _get2(report, interpretations, brand),
        "matches": _matches(report, interpretations),
        "directions": _directions(report, interpretations),
        "cross": _cross(report, interpretations),
        "questions": {
            "prompts": interpretations.items("questions", "prompts"),
        },
        "method": {
            "title": interpretations.text("report", "method", "title"),
            "body": interpretations.text("report", "method", "body"),
        },
        "attribution": _attribution(report, interpretations),
        "confidential": interpretations.text("report", "confidential"),
        "versions": {
            "engine": report.engine_version,
            "template": report.template_version,
        },
    }


def _interests(
    report: StudentReportInput, interpretations: Interpretations, brand: str
) -> dict[str, Any]:
    raw = {scale: report.interests.get("raw", {}).get(scale, 0) for scale in RIASEC}
    code = str(report.interests.get("code", ""))
    band = str(report.interests.get("band", ""))

    letters = [
        {"letter": letter, "text": interpretations.text("interests", "letters", letter)}
        for letter in code
        if letter in RIASEC
    ]

    # Caveats, when they apply. Both are honest-uncertainty notes rather than
    # findings: §7.1 says the third letter is unstable when the gap is small,
    # and a student comparing codes with a friend deserves to know that.
    caveats = []
    if report.interests.get("code_provisional"):
        caveats.append(interpretations.text("interests", "code_provisional"))
    if report.interests.get("tie_broken"):
        caveats.append(interpretations.text("interests", "tie_broken"))

    return {
        "code": code,
        "band": band,
        "band_text": interpretations.text("interests", "bands", band),
        "letters": letters,
        "caveats": caveats,
        "raw": raw,
        "chart": charts.hexagon(raw, RIASEC, INTEREST_MAX, brand),
    }


def _personality(
    report: StudentReportInput, interpretations: Interpretations, brand: str
) -> dict[str, Any]:
    raw = report.personality.get("raw", {})
    bands = report.personality.get("bands", {})
    labels = report.personality.get("labels") or DOMAIN_LABELS

    rows: list[tuple[str, float, str]] = []
    domains = []
    for domain in BIG_FIVE:
        band = str(bands.get(domain, ""))
        label = str(labels.get(domain, domain))
        rows.append((label, float(raw.get(domain, PERSONALITY_MIN)), band))
        domains.append(
            {
                "key": domain,
                "label": label,
                "band": band,
                "text": interpretations.text("personality", "domains", domain, band),
            }
        )

    return {
        "domains": domains,
        "provisional_note": interpretations.text("personality", "provisional_note"),
        "chart": charts.bars(rows, PERSONALITY_MIN, PERSONALITY_MAX, brand),
    }


def _get2(
    report: StudentReportInput, interpretations: Interpretations, brand: str
) -> dict[str, Any] | None:
    """The GET2 page, or None — which omits it entirely (R10, §14 p.7).

    None rather than an empty dict so the template's `{% if get2 %}` cannot be
    satisfied by a truthy-but-empty structure. §14 is explicit that the page is
    "omitted entirely (not shown blank)": a heading over three empty bars tells a
    student something is missing from their report, which is not what happened.
    """
    if report.get2.get("instrument") != "GET2":
        return None

    subscales = report.get2.get("subscales", {})
    band = str(report.get2.get("entrepreneurial_band", ""))

    rows = [
        (
            scale.replace("_", " ").capitalize(),
            float(subscales.get(scale, 0)),
            f"{int(subscales.get(scale, 0))}%",
        )
        for scale in GET2_SUBSCALES
    ]

    return {
        "total": report.get2.get("get2_total"),
        "band": band,
        "band_text": interpretations.text("get2", "bands", band),
        "subscales": [
            {
                "key": scale,
                "label": scale.replace("_", " ").capitalize(),
                "percent": subscales.get(scale, 0),
                "text": interpretations.text("get2", "subscales", scale),
            }
            for scale in GET2_SUBSCALES
        ],
        "flag_note": (
            interpretations.text("get2", "flag_note")
            if report.get2.get("entrepreneurial_flag")
            else None
        ),
        "chart": charts.bars(rows, GET2_MIN, GET2_MAX, brand),
    }


def _matches(report: StudentReportInput, interpretations: Interpretations) -> dict[str, Any]:
    """Occupation matches, local first (§8).

    `local_title` being set is what `match_occupations` writes for a
    Pakistan-mapped occupation, so it is the honest signal for the grouping —
    the same one the review screen uses. Splitting rather than sorting keeps the
    two groups visually separate on the page, because §8 says local results are
    "shown first" rather than merely ranked higher.
    """
    ranked = sorted(report.matches, key=lambda m: m.rank)[:MATCH_LIMIT]
    local = [m for m in ranked if m.local_title]
    other = [m for m in ranked if not m.local_title]

    return {
        "title": interpretations.text("report", "matches", "title"),
        "body": interpretations.text("report", "matches", "body"),
        "local": local,
        "other": other,
        "entrepreneurial_note": interpretations.text(
            "report", "matches", "entrepreneurial_note"
        ),
    }


def _directions(report: StudentReportInput, interpretations: Interpretations) -> dict[str, Any]:
    # `chosen`, not `items`. Jinja resolves `directions.items` to dict.items —
    # the built-in method wins over the key, so the template silently iterates
    # nothing useful and raises deep inside the loop. Any dict method name is a
    # trap here: `values`, `keys`, `get`, `copy`.
    return {
        "title": interpretations.text("report", "directions", "title"),
        "body": interpretations.text("report", "directions", "body"),
        "chosen": sorted(report.directions, key=lambda d: d.rank),
    }


def _cross(report: StudentReportInput, interpretations: Interpretations) -> dict[str, Any]:
    """Where the modules agree or pull against each other (§14 p.11).

    The pattern is chosen arithmetically — which of three human-written
    paragraphs to show — and the paragraph itself is written by a psychologist.
    This function must not compose a sentence about the student; if the rule
    below needs nuance, that nuance belongs in the written text, not here.

    The rule: a Social or Enterprising interest code leans on working with
    people, so low Extraversion is a genuine tension worth naming. An
    Investigative or Conventional code with high Conscientiousness is the
    textbook agreement. Anything else is `unclear`, which is the honest default
    rather than a fallback — two instruments not lining up is ordinary.
    """
    code = str(report.interests.get("code", ""))
    bands = report.personality.get("bands", {})
    extraversion = str(bands.get("E", ""))
    conscientiousness = str(bands.get("C", ""))

    people_facing = any(letter in code[:2] for letter in ("S", "E"))
    structured = any(letter in code[:2] for letter in ("I", "C"))

    if people_facing and extraversion in ("low", "very low"):
        pattern = "tension"
    elif structured and conscientiousness in ("high", "very high"):
        pattern = "aligned"
    else:
        pattern = "unclear"

    return {
        "intro": interpretations.text("cross", "intro"),
        "pattern": pattern,
        "text": interpretations.text("cross", pattern),
    }


def _attribution(
    report: StudentReportInput, interpretations: Interpretations
) -> dict[str, Any]:
    """Licence text for the method page and the footer (R6).

    The GET2 line is included only when that module was administered. R10 makes
    the attribution wording provisional until permission is confirmed, and
    crediting a licensor on a report that never used their instrument would be
    both wrong and, for a licence still being negotiated, unhelpful.
    """
    attribution = {
        "onet": interpretations.text("attribution", "onet"),
        "onet_licence_url": interpretations.text("attribution", "onet_licence_url"),
        "ipip": interpretations.text("attribution", "ipip"),
        "short": interpretations.text("attribution", "short"),
        "get2": None,
    }
    if report.get2.get("instrument") == "GET2":
        attribution["get2"] = interpretations.text("attribution", "get2")
    return attribution

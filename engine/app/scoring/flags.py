"""Response-quality flags (plan §7.4).

Flags appear on the psychologist's review screen (§9.1) and the counsellor's
copy of the cohort report, never on the student's own report. Two or more
`warn` flags exclude a participant from cohort aggregates, and the cohort
report must then state how many were excluded and why.

A flag is a signal to talk to the student, never a verdict on them. Keep the
`detail` strings factual and free of judgement — a reviewer may read one out.
"""

from dataclasses import asdict, dataclass
from datetime import datetime
from statistics import median

from app.scoring.get2 import GET2_SUBSCALES
from app.scoring.personality import BIG_FIVE, reverse_if_keyed
from app.scoring.types import Item

# ── thresholds ───────────────────────────────────────────────────────────────
STRAIGHTLINE_RUN = 12          # identical consecutive answers across modules A+B
MIN_ACTIVE_SECONDS = 6 * 60    # total time spent on items
MIN_MEDIAN_MS = 800            # median time per item
INCONSISTENT_PAIR_DELTA = 1.5  # forward vs reverse-adjusted mean, per domain
LONG_GAP_HOURS = 48
GET2_UNIFORM_SPREAD = 5        # every subscale within this many points of each other


@dataclass(frozen=True, slots=True)
class Flag:
    code: str
    severity: str  # "info" | "warn"
    detail: str

    def as_dict(self) -> dict:
        return asdict(self)


def compute_flags(
    responses: dict[str, int],
    items: list[Item],
    ms_elapsed: dict[str, int] | None = None,
    started_at: datetime | None = None,
    submitted_at: datetime | None = None,
    get2_subscales: dict[str, int] | None = None,
) -> list[dict]:
    """Return flags for one session, ordered as listed in plan §7.4.

    Every argument except `responses` and `items` is optional, because a
    rescore of an old session may not have timing data, and `get2_subscales`
    is None whenever the module was not administered (R10 fallback). A
    missing signal produces no flag rather than a false one.
    """
    ordered = sorted(items, key=lambda i: (i.instrument_code, i.ordinal))
    flags: list[Flag] = []

    for check in (
        _straightlining(responses, ordered),
        _too_fast(responses, ms_elapsed),
        _inconsistent_pairs(responses, items),
        _long_gap(started_at, submitted_at),
        _get2_uniform_response(get2_subscales),
    ):
        if check is not None:
            flags.append(check)

    return [f.as_dict() for f in flags]


def warn_count(flags: list[dict]) -> int:
    """Two or more warns exclude a participant from cohort aggregates."""
    return sum(1 for f in flags if f.get("severity") == "warn")


# ── individual checks ────────────────────────────────────────────────────────


def _straightlining(responses: dict[str, int], ordered: list[Item]) -> Flag | None:
    """Longest run of identical values in presentation order.

    Presentation order matters: a run is only meaningful as consecutive taps on
    consecutive screens.
    """
    longest = run = 0
    previous = None

    for item in ordered:
        if item.code not in responses:
            run, previous = 0, None
            continue
        value = responses[item.code]
        run = run + 1 if value == previous else 1
        previous = value
        longest = max(longest, run)

    if longest < STRAIGHTLINE_RUN:
        return None
    return Flag(
        code="straightlining",
        severity="warn",
        detail=f"{longest} identical consecutive answers",
    )


def _too_fast(responses: dict[str, int], ms_elapsed: dict[str, int] | None) -> Flag | None:
    if not ms_elapsed:
        return None

    timings = [ms for code, ms in ms_elapsed.items() if code in responses and ms is not None]
    if not timings:
        return None

    total_seconds = sum(timings) / 1000
    med = median(timings)

    reasons = []
    if total_seconds < MIN_ACTIVE_SECONDS:
        reasons.append(f"{total_seconds / 60:.1f} min total")
    if med < MIN_MEDIAN_MS:
        reasons.append(f"{med:.0f} ms median per item")

    if not reasons:
        return None
    return Flag(code="too_fast", severity="warn", detail=", ".join(reasons))


def _inconsistent_pairs(responses: dict[str, int], items: list[Item]) -> Flag | None:
    """Forward and reverse items in one domain should agree once re-keyed.

    Both means are put on the same scale before comparing — forward items raw,
    reverse items after reverse-keying — so a large gap means the student
    answered the two directions inconsistently rather than simply scoring high.
    """
    offenders = []

    for domain in BIG_FIVE:
        forward = [
            responses[i.code]
            for i in items
            if i.scale == domain and not i.reverse_keyed and i.code in responses
        ]
        reverse = [
            reverse_if_keyed(responses[i.code], i)
            for i in items
            if i.scale == domain and i.reverse_keyed and i.code in responses
        ]
        if not forward or not reverse:
            continue

        delta = abs(sum(forward) / len(forward) - sum(reverse) / len(reverse))
        if delta > INCONSISTENT_PAIR_DELTA:
            offenders.append(f"{domain} ({delta:.1f})")

    if not offenders:
        return None
    return Flag(
        code="inconsistent_pairs",
        severity="warn",
        detail="forward and reverse items disagree: " + ", ".join(offenders),
    )


def _long_gap(started_at: datetime | None, submitted_at: datetime | None) -> Flag | None:
    """A multi-day gap means the student's context changed mid-assessment.

    Informational, not a quality warning — the answers may be perfectly good,
    but the counsellor should know they were not given in one sitting.
    """
    if started_at is None or submitted_at is None:
        return None

    hours = (submitted_at - started_at).total_seconds() / 3600
    if hours <= LONG_GAP_HOURS:
        return None
    return Flag(
        code="long_gap",
        severity="info",
        detail=f"{hours / 24:.1f} days between starting and submitting",
    )


def _get2_uniform_response(get2_subscales: dict[str, int] | None) -> Flag | None:
    """Every GET2 subscale landing within a few points of each other usually
    means the respondent clicked through without engaging, rather than a
    genuinely flat entrepreneurial profile (plan §7.4).

    None when GET2 was not administered — absence of the module is not a
    quality signal.
    """
    if not get2_subscales:
        return None

    values = [get2_subscales[s] for s in GET2_SUBSCALES if s in get2_subscales]
    if len(values) < len(GET2_SUBSCALES):
        return None

    spread = max(values) - min(values)
    if spread > GET2_UNIFORM_SPREAD:
        return None
    return Flag(
        code="get2_uniform_response",
        severity="warn",
        detail=f"all five GET2 subscales within {spread} points of each other",
    )

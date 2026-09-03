"""Big Five personality scoring (plan §7.2).

IPIP 50-item Big-Five factor markers: 10 items per domain, 1..5 each, so every
domain scores 10..50.

The fifth domain is reported as Emotional Stability, not Neuroticism. A report
handed to a seventeen-year-old should not lead with a negatively framed trait
name. Item keying stays exactly as published; the direction is flipped once,
here, and only here.
"""

from app.scoring.types import Item, ScoringError

BIG_FIVE = ("O", "C", "E", "A", "S")

DOMAIN_LABELS = {
    "O": "Openness to Experience",
    "C": "Conscientiousness",
    "E": "Extraversion",
    "A": "Agreeableness",
    "S": "Emotional Stability",
}

# Provisional bands for a 10-item, 1..5 domain (range 10..50). These apply until
# a `norms` row for the population reaches n >= 300, at which point normative
# bands replace them (R4). Upper bound of each band, ascending.
PROVISIONAL_BAND_CUTOFFS = (
    (19, "very low"),
    (27, "low"),
    (34, "average"),
    (42, "high"),
)
HIGHEST_BAND = "very high"

# Normative band boundaries in standard deviations, used once local norms exist.
NORMATIVE_BAND_CUTOFFS = (
    (-1.5, "very low"),
    (-0.5, "low"),
    (0.5, "average"),
    (1.5, "high"),
)

Norms = dict[str, tuple[float, float]]  # {scale: (mean, sd)}


def score_personality(
    responses: dict[str, int],
    items: list[Item],
    norms: Norms | None = None,
) -> dict:
    """Score the personality module.

    Args:
        responses: {item_code: raw value on the item's own response scale}.
        items: personality items, carrying reverse-keying and response range.
        norms: {scale: (mean, sd)} when the population has local norms with
            n >= 300. Omit it and the result uses provisional bands.

    Both paths return the same shape, so the report template does not branch.
    """
    if not items:
        raise ScoringError("no personality items supplied")

    domains = dict.fromkeys(BIG_FIVE, 0)

    for item in items:
        if item.code not in responses:
            raise ScoringError(f"missing response for item {item.code}")

        raw = responses[item.code]
        if not item.response_min <= raw <= item.response_max:
            raise ScoringError(
                f"response {raw} for {item.code} outside "
                f"{item.response_min}..{item.response_max}"
            )
        if item.scale not in domains:
            raise ScoringError(f"unknown personality scale {item.scale!r} on {item.code}")

        domains[item.scale] += reverse_if_keyed(raw, item)

    if norms:
        bands = {s: _normative_band(domains[s], *norms[s]) for s in BIG_FIVE if s in norms}
        # A partial norms table would silently mix band systems within one
        # report. Refuse it: either every domain is normed or none is.
        if len(bands) != len(BIG_FIVE):
            missing = sorted(set(BIG_FIVE) - set(norms))
            raise ScoringError(f"norms supplied but missing scales: {missing}")
        norms_status = "local"
    else:
        bands = {s: _provisional_band(domains[s]) for s in BIG_FIVE}
        norms_status = "pending_local_norms"

    return {
        "raw": domains,
        "bands": bands,
        "labels": DOMAIN_LABELS,
        "percentiles": None,
        "norms_status": norms_status,
    }


def reverse_if_keyed(raw: int, item: Item) -> int:
    """Flip a reverse-keyed response using the item's own response range.

    For a 1..5 scale this is `6 - raw`. The 6 is derived, never hard-coded — an
    instrument added later with a 0..6 scale must score correctly without a code
    change (plan §17.1, test_personality_no_hardcoded_six).
    """
    if not item.reverse_keyed:
        return raw
    return item.response_max + item.response_min - raw


def _provisional_band(total: int) -> str:
    for upper, label in PROVISIONAL_BAND_CUTOFFS:
        if total <= upper:
            return label
    return HIGHEST_BAND


def _normative_band(total: int, mean: float, sd: float) -> str:
    if sd <= 0:
        raise ScoringError(f"norm sd must be positive, got {sd}")
    z = (total - mean) / sd
    for upper, label in NORMATIVE_BAND_CUTOFFS:
        if z < upper:
            return label
    return HIGHEST_BAND

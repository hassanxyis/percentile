"""RIASEC interest scoring (plan §7.1).

O*NET Interest Profiler Short Form: 60 items, 10 per scale, 0..4 each, so every
scale scores 0..40.
"""

from app.scoring.types import Item, ScoringError

# Fixed order for tie-breaking, and the canonical order for display. Holland's
# hexagon runs R-I-A-S-E-C; ties resolve by this order so the same responses
# always produce the same code.
RIASEC = ("R", "I", "A", "S", "E", "C")

# Differentiation bands. Report copy changes on these — they are not decoration.
WELL_DIFFERENTIATED_MIN = 20
MODERATE_MIN = 10

# Below this gap between the third and fourth scale, the third letter of the
# code is not meaningfully distinct from the one that lost.
CODE_PROVISIONAL_GAP = 2


def score_interests(responses: dict[str, int], items: list[Item]) -> dict:
    """Score the interest module.

    Args:
        responses: {item_code: 0..4}. Must contain every item in `items`.
        items: the interest instrument's items, carrying scale and response range.

    Returns a dict with raw scores, the three-letter Holland code, a
    differentiation band, and `norms_status` — never a percentile, until local
    norms exist (R4).
    """
    if not items:
        raise ScoringError("no interest items supplied")

    raw = _raw_scale_scores(responses, items)

    values = sorted(raw.values(), reverse=True)
    differentiation = values[0] - values[-1]

    # Rank scales by score descending, breaking ties by RIASEC order. Sorting on
    # the index makes the tie-break explicit rather than relying on dict order.
    ranked = sorted(raw, key=lambda s: (-raw[s], RIASEC.index(s)))
    code_scales = ranked[:3]

    return {
        "raw": raw,
        "code": "".join(code_scales),
        "differentiation": differentiation,
        "band": _band(differentiation),
        "tie_broken": _has_tie_at_boundary(raw, ranked),
        # The third letter is unstable when the scale that took it barely beat
        # the one that missed. The report says so in words.
        "code_provisional": (raw[ranked[2]] - raw[ranked[3]]) < CODE_PROVISIONAL_GAP,
        "percentiles": None,
        "norms_status": "pending_local_norms",
    }


def _raw_scale_scores(responses: dict[str, int], items: list[Item]) -> dict[str, int]:
    """Sum responses per scale, validating range as we go.

    An out-of-range response means the taker UI sent something the instrument
    does not define. Raise rather than clamp: clamping hides the bug and ships a
    wrong score.
    """
    raw = dict.fromkeys(RIASEC, 0)

    for item in items:
        if item.code not in responses:
            raise ScoringError(f"missing response for item {item.code}")

        value = responses[item.code]
        if not item.response_min <= value <= item.response_max:
            raise ScoringError(
                f"response {value} for {item.code} outside "
                f"{item.response_min}..{item.response_max}"
            )
        if item.scale not in raw:
            raise ScoringError(f"unknown interest scale {item.scale!r} on {item.code}")

        raw[item.scale] += value

    return raw


def _band(differentiation: int) -> str:
    if differentiation >= WELL_DIFFERENTIATED_MIN:
        return "well_differentiated"
    if differentiation >= MODERATE_MIN:
        return "moderate"
    # No clear code emerged. The report must say this in words and tell the
    # counsellor to explore rather than recommend.
    return "undifferentiated"


def _has_tie_at_boundary(raw: dict[str, int], ranked: list[str]) -> bool:
    """True when a tie decided which scale made the three-letter code.

    Only the 3rd/4th boundary changes the code itself, but a tie anywhere in the
    top three changes letter *order*, which is also reported. Both count.
    """
    top_scores = [raw[s] for s in ranked[:4]]
    return len(set(top_scores[:3])) < 3 or top_scores[2] == top_scores[3]

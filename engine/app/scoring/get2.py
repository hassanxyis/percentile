"""Entrepreneurial tendency scoring — GET2 (plan §7.3, replaces v1's values.py).

Caird's General Enterprising Tendency v2: five subscales — achievement,
autonomy, creativity, risk_taking, control — each reported as a percentage of
its own published maximum, matching how GET2 results are conventionally
presented (a counsellor who has seen a GET2 report before should recognise the
shape).

R10: GET2 use is provisional until Open University / Dr. Caird permission is
confirmed in writing. Until then, or when an organisation's plan does not
include the module, callers pass `scoring=None` and get the
`module_not_administered` shape instead of a score — every downstream template
branches on `instrument` rather than assuming the key exists.

GET2 is deliberately not folded into the interest-vector occupation match
(§8) — it measures tendency to act, not interest content, and O*NET occupation
profiles have no equivalent axis to compare it against. It surfaces as its own
report section and as `entrepreneurial_flag`, a boolean that biases which
occupations get shown.
"""

from app.scoring.types import ScoringError

GET2_SUBSCALES = ("achievement", "autonomy", "creativity", "risk_taking", "control")

# Provisional bands for get2_total (mean of the five subscale percentages).
# Upper bound of each band, ascending. These apply until local norms exist (R4).
GET2_BAND_CUTOFFS = (
    (34, "emerging"),
    (64, "moderate"),
)
STRONG_BAND = "strong"

# entrepreneurial_flag drives the §8 occupation carve-out and the psychologist
# review screen's highlight — fires on a high overall score, or on the specific
# risk-taking + autonomy combination even when the total is middling.
ENTREPRENEURIAL_TOTAL_MIN = 65
ENTREPRENEURIAL_RISK_MIN = 70
ENTREPRENEURIAL_AUTONOMY_MIN = 70


def score_get2(responses: dict[str, int], scoring: dict | None) -> dict:
    """Score the GET2 module.

    Args:
        responses: {item_code: raw value on the item's own response scale}.
            Ignored when `scoring` is None.
        scoring: parsed get2_scoring.json — `subscales` (items, max_raw) per
            scale. Pass None when GET2 was not administered for this session
            (org config, or R10 permission not yet cleared) — this is the
            fallback path every downstream template must handle without a
            KeyError (plan §5, §6.3).

    Whichever reason the module was skipped for, the return shape is the same:
    `{"instrument": None, "status": "module_not_administered"}`. Callers never
    need two different absence checks.
    """
    if scoring is None:
        return {"instrument": None, "status": "module_not_administered"}

    spec = scoring.get("subscales") or {}
    missing = [s for s in GET2_SUBSCALES if s not in spec]
    if missing:
        raise ScoringError(f"get2_scoring.json missing scales: {missing}")

    percentages = {}
    for scale in GET2_SUBSCALES:
        rule = spec[scale]
        try:
            raw = sum(responses[code] for code in rule["items"])
        except KeyError as exc:
            raise ScoringError(
                f"scale {scale} references item {exc.args[0]} which has no response"
            ) from exc

        max_raw = rule["max_raw"]
        # A raw score outside 0..max_raw means the transcription is wrong.
        # Raise immediately rather than clamping — you want to know now, not
        # after four hundred students have been scored against a bad file.
        if not 0 <= raw <= max_raw:
            raise ScoringError(
                f"{scale} raw score {raw} outside 0..{max_raw} — "
                f"check the get2_scoring.json transcription"
            )
        percentages[scale] = round(100 * raw / max_raw)

    total = round(sum(percentages.values()) / len(GET2_SUBSCALES))

    entrepreneurial_flag = total >= ENTREPRENEURIAL_TOTAL_MIN or (
        percentages["risk_taking"] >= ENTREPRENEURIAL_RISK_MIN
        and percentages["autonomy"] >= ENTREPRENEURIAL_AUTONOMY_MIN
    )

    return {
        "instrument": "GET2",
        "subscales": percentages,
        "get2_total": total,
        "entrepreneurial_band": _band(total),
        "entrepreneurial_flag": entrepreneurial_flag,
        "percentiles": None,
        "norms_status": "pending_local_norms",
    }


def _band(total: int) -> str:
    for upper, label in GET2_BAND_CUTOFFS:
        if total <= upper:
            return label
    return STRONG_BAND

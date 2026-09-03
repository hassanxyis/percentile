"""Work values scoring (plan §7.3).

O*NET Work Importance Locator: 20 need statements sorted into five columns of
four, where column 5 is most important and column 1 least.

The scoring worksheet — which statements feed which value, and each value's
multiplier — is transcribed by hand from the published WIL user's guide into
`data/instruments/values_scoring.json`, and passed in here as `config`. The
multipliers are not all the same and Working Conditions is handled differently,
so nothing about the worksheet is inferred in code (R2).

Verify any implementation against the worked example in the published score
report before trusting a single result. That comparison is the only independent
check that the transcription is right.
"""

from app.scoring.types import ScoringError

VALUE_SCALES = ("ACH", "IND", "REC", "REL", "SUP", "WCN")

EXPECTED_CARDS = 20
EXPECTED_COLUMNS = 5
CARDS_PER_COLUMN = 4


def score_values(sort: dict[str, int], config: dict) -> dict:
    """Score the card sort.

    Args:
        sort: {item_code: column_no}, column_no in 1..5.
        config: parsed values_scoring.json — `values` (items, multiplier, min,
            max per scale) and `labels`.

    A partial sort is not scoreable. It raises rather than zero-filling: a
    zero-filled value score looks like a real preference and is indistinguishable
    from one in the report.
    """
    validate_sort(sort)

    spec = config.get("values") or {}
    missing = [s for s in VALUE_SCALES if s not in spec]
    if missing:
        raise ScoringError(f"values_scoring.json missing scales: {missing}")

    scores = {}
    for scale in VALUE_SCALES:
        rule = spec[scale]
        try:
            total = sum(sort[code] for code in rule["items"])
        except KeyError as exc:
            raise ScoringError(
                f"scale {scale} references item {exc.args[0]} which is not in the sort"
            ) from exc

        score = rule["multiplier"] * total

        # A score outside the published range means the transcription is wrong.
        # Raise immediately rather than clamping — you want to know now, not
        # after four hundred students have been scored against a bad worksheet.
        if not rule["min"] <= score <= rule["max"]:
            raise ScoringError(
                f"{scale} scored {score}, outside the published range "
                f"{rule['min']}..{rule['max']} — check the worksheet transcription"
            )
        scores[scale] = score

    # Ties break alphabetically so the same sort always yields the same top two.
    ranked = sorted(scores, key=lambda s: (-scores[s], s))
    top2 = ranked[:2]

    return {
        "raw": scores,
        "top2": top2,
        "tie_broken": scores[ranked[1]] == scores[ranked[2]],
        "labels": config.get("labels", {}),
        "percentiles": None,
        "norms_status": "pending_local_norms",
    }


def validate_sort(sort: dict[str, int]) -> None:
    """Reject anything that is not exactly 20 cards in 5 columns of 4."""
    if len(sort) != EXPECTED_CARDS:
        raise ScoringError(f"expected {EXPECTED_CARDS} placements, got {len(sort)}")

    counts: dict[int, int] = {}
    for code, column in sort.items():
        if not 1 <= column <= EXPECTED_COLUMNS:
            raise ScoringError(f"card {code} in column {column}, must be 1..{EXPECTED_COLUMNS}")
        counts[column] = counts.get(column, 0) + 1

    wrong = {col: n for col, n in sorted(counts.items()) if n != CARDS_PER_COLUMN}
    if wrong or len(counts) != EXPECTED_COLUMNS:
        raise ScoringError(
            f"each of {EXPECTED_COLUMNS} columns needs exactly "
            f"{CARDS_PER_COLUMN} cards; got {counts}"
        )

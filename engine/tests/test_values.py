"""Work values scoring (plan §17.1).

The single most important test in this repo is `test_values_worked_example` in
tests/test_values_worked_example.py. It is the only independent check that the
WIL scoring worksheet was transcribed correctly. Everything here tests the
mechanics around it.
"""

import pytest

from app.scoring.types import ScoringError
from app.scoring.values import score_values, validate_sort

# A structurally valid stand-in for values_scoring.json. The real multipliers and
# item groupings are transcribed from the published WIL user's guide (R2); these
# exist only to exercise the code paths.
FAKE_CONFIG = {
    "values": {
        scale: {
            "items": [f"WIL_{n:02d}" for n in items],
            "multiplier": 2,
            "min": 6,
            "max": 30,
        }
        for scale, items in {
            "ACH": (1, 2, 3),
            "IND": (4, 5, 6),
            "REC": (7, 8, 9),
            "REL": (10, 11, 12),
            "SUP": (13, 14, 15),
            "WCN": (16, 17, 18),
        }.items()
    },
    "labels": {
        "ACH": "Achievement",
        "IND": "Independence",
        "REC": "Recognition",
        "REL": "Relationships",
        "SUP": "Support",
        "WCN": "Working Conditions",
    },
}


def valid_sort() -> dict[str, int]:
    """20 cards, 4 in each of 5 columns."""
    return {f"WIL_{n:02d}": (n - 1) // 4 + 1 for n in range(1, 21)}


def _with_ach(**overrides) -> dict:
    """FAKE_CONFIG with the ACH rule amended."""
    return {
        "values": {**FAKE_CONFIG["values"], "ACH": {**FAKE_CONFIG["values"]["ACH"], **overrides}},
        "labels": FAKE_CONFIG["labels"],
    }


# ── sort validation ──────────────────────────────────────────────────────────


def test_valid_sort_passes():
    validate_sort(valid_sort())


def test_partial_sort_raises_and_does_not_zero_fill():
    """A partial sort is not scoreable. Zero-filling would look like a real
    preference and be indistinguishable from one in the report."""
    sort = valid_sort()
    del sort["WIL_20"]

    with pytest.raises(ScoringError, match="expected 20 placements"):
        score_values(sort, FAKE_CONFIG)


def test_three_cards_in_a_column_raises():
    """Move one card so a column holds 3 and another 5."""
    sort = valid_sort()
    sort["WIL_01"] = 2

    with pytest.raises(ScoringError, match="exactly 4 cards"):
        score_values(sort, FAKE_CONFIG)


def test_column_out_of_range_raises():
    sort = valid_sort()
    sort["WIL_01"] = 6

    with pytest.raises(ScoringError, match="must be 1..5"):
        score_values(sort, FAKE_CONFIG)


# ── scoring ──────────────────────────────────────────────────────────────────


def test_scores_are_multiplied_column_sums():
    """ACH holds cards 1-3, all in column 1: (1+1+1) * 2 = 6."""
    result = score_values(valid_sort(), FAKE_CONFIG)

    assert result["raw"]["ACH"] == 6
    # IND holds 4,5,6 → columns 1,2,2 → (1+2+2) * 2 = 10
    assert result["raw"]["IND"] == 10


def test_top2_and_alphabetical_tiebreak():
    """WCN (16,17,18 → columns 4,5,5) scores highest here."""
    result = score_values(valid_sort(), FAKE_CONFIG)

    assert result["top2"][0] == "WCN"
    assert len(result["top2"]) == 2


def test_out_of_range_score_raises_rather_than_clamping():
    """A score outside the published min/max means the transcription is wrong.

    Raise immediately — you want to know now, not after four hundred students
    have been scored against a bad worksheet.
    """
    config = _with_ach(min=99, max=100)

    with pytest.raises(ScoringError, match="check the worksheet transcription"):
        score_values(valid_sort(), config)


def test_missing_scale_in_config_raises():
    config = {"values": {k: v for k, v in FAKE_CONFIG["values"].items() if k != "WCN"}}

    with pytest.raises(ScoringError, match="missing scales"):
        score_values(valid_sort(), config)


def test_config_referencing_an_unsorted_item_raises():
    config = _with_ach(items=["WIL_99"])

    with pytest.raises(ScoringError, match="not in the sort"):
        score_values(valid_sort(), config)


def test_no_percentiles_without_local_norms():
    result = score_values(valid_sort(), FAKE_CONFIG)

    assert result["percentiles"] is None
    assert result["norms_status"] == "pending_local_norms"

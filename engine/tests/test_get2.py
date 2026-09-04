"""Entrepreneurial tendency scoring (plan §7.3, §18.1)."""

import pytest

from app.scoring.get2 import score_get2
from app.scoring.types import ScoringError

from .conftest import get2_scoring


def _responses(per_scale: int = 4, value: int = 2) -> dict[str, int]:
    """Every item answered `value` on a 0..2 scale, 4 items per subscale."""
    scoring = get2_scoring(per_scale=per_scale)
    return {
        code: value
        for rule in scoring["subscales"].values()
        for code in rule["items"]
    }


# ── not administered ────────────────────────────────────────────────────────


def test_not_administered_returns_null_shape():
    """R10 fallback: no scoring config means the module was skipped, not that
    it scored zero. Every downstream template must branch on `instrument`."""
    result = score_get2({}, None)

    assert result == {"instrument": None, "status": "module_not_administered"}


def test_not_administered_ignores_responses():
    """Even if responses happen to be present, scoring=None means skipped."""
    result = score_get2(_responses(), None)

    assert result["instrument"] is None


# ── subscale percentage ──────────────────────────────────────────────────────


def test_get2_subscale_percentage():
    """4 items × 2 (of max 8 raw, 4 items at max response 2 each) → 100%."""
    scoring = get2_scoring(per_scale=4, max_raw=8)
    responses = _responses(per_scale=4, value=2)

    result = score_get2(responses, scoring)

    assert result["instrument"] == "GET2"
    assert result["subscales"] == dict.fromkeys(
        ("achievement", "autonomy", "creativity", "risk_taking", "control"), 100
    )
    assert result["get2_total"] == 100


def test_get2_subscale_percentage_partial():
    """4 items at 1 each = 4 raw of max 8 → 50%."""
    scoring = get2_scoring(per_scale=4, max_raw=8)
    responses = _responses(per_scale=4, value=1)

    result = score_get2(responses, scoring)

    assert result["subscales"]["achievement"] == 50
    assert result["get2_total"] == 50


def test_missing_scale_in_config_raises():
    scoring = get2_scoring()
    del scoring["subscales"]["control"]

    with pytest.raises(ScoringError, match="missing scales"):
        score_get2(_responses(), scoring)


def test_config_referencing_an_unanswered_item_raises():
    scoring = get2_scoring()
    scoring["subscales"]["achievement"]["items"] = ["GET2_ACH_99"]

    with pytest.raises(ScoringError, match="no response"):
        score_get2({}, scoring)


def test_out_of_range_raw_score_raises_rather_than_clamping():
    """A raw score outside 0..max_raw means the transcription is wrong.

    Raise immediately — you want to know now, not after four hundred students
    have been scored against a bad max_raw.
    """
    scoring = get2_scoring(per_scale=4, max_raw=2)  # max lower than achievable raw
    responses = _responses(per_scale=4, value=2)

    with pytest.raises(ScoringError, match="check the get2_scoring.json"):
        score_get2(responses, scoring)


# ── entrepreneurial flag ─────────────────────────────────────────────────────


def test_get2_entrepreneurial_flag_fires_on_high_total():
    scoring = get2_scoring(per_scale=4, max_raw=8)
    responses = _responses(per_scale=4, value=2)  # 100% total

    result = score_get2(responses, scoring)

    assert result["entrepreneurial_flag"] is True


def test_get2_entrepreneurial_flag_does_not_fire_just_below_threshold():
    """total=64, and risk_taking/autonomy below their own 70 threshold, must
    not trip either entrepreneurial_flag path."""
    scoring = get2_scoring(per_scale=1, max_raw=100)
    responses = {
        rule["items"][0]: 64 for rule in scoring["subscales"].values()
    }

    result = score_get2(responses, scoring)

    assert result["get2_total"] == 64
    assert result["entrepreneurial_flag"] is False


def test_get2_entrepreneurial_flag_fires_on_risk_and_autonomy_alone():
    """A middling total with risk_taking and autonomy both >=70 still flags —
    the two-of-five path documented in plan §7.3, independent of the total."""
    scoring = get2_scoring(per_scale=4, max_raw=8)
    responses = _responses(per_scale=4, value=0)

    for scale in ("risk_taking", "autonomy"):
        for code in scoring["subscales"][scale]["items"]:
            responses[code] = 2  # 100% on these two only

    result = score_get2(responses, scoring)

    assert result["get2_total"] < 65
    assert result["subscales"]["risk_taking"] >= 70
    assert result["subscales"]["autonomy"] >= 70
    assert result["entrepreneurial_flag"] is True


# ── band ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("pct", "expected_band"),
    [(0, "emerging"), (34, "emerging"), (35, "moderate"), (64, "moderate"),
     (65, "strong"), (100, "strong")],
)
def test_get2_band_boundaries(pct, expected_band):
    scoring = get2_scoring(per_scale=1, max_raw=100)
    responses = {
        code: pct
        for rule in scoring["subscales"].values()
        for code in rule["items"]
    }

    result = score_get2(responses, scoring)

    assert result["entrepreneurial_band"] == expected_band


# ── statistical honesty ──────────────────────────────────────────────────────


def test_no_percentiles_without_local_norms():
    scoring = get2_scoring()
    result = score_get2(_responses(), scoring)

    assert result["percentiles"] is None
    assert result["norms_status"] == "pending_local_norms"

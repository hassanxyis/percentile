"""Interest scoring (plan §17.1).

O*NET Interest Profiler Short Form is a checkbox sheet: 60 items, 10 per
RIASEC scale, each checked or not. A scale's raw score is its count of
checks, 0..10.
"""

import pytest

from app.scoring.interests import score_interests
from app.scoring.types import ScoringError

from .conftest import uniform


def test_all_checked_is_undifferentiated(interests):
    """Someone who checks everything has no Holland code worth reporting.

    Every scale 10, differentiation 0. The report must say no clear code
    emerged rather than pick three letters arbitrarily.
    """
    result = score_interests(uniform(interests, 1), interests)

    assert result["raw"] == dict.fromkeys("RIASEC", 10)
    assert result["differentiation"] == 0
    assert result["band"] == "undifferentiated"


def test_known_profile(interests):
    """Hand-computed check counts and the resulting three-letter code.

    Each scale below gets `n` of its 10 items checked, so the raw score
    equals `n` directly.
    """
    responses = _checked_counts(interests, {"R": 1, "I": 10, "A": 8, "S": 8, "E": 5, "C": 0})
    result = score_interests(responses, interests)

    assert result["raw"] == {"R": 1, "I": 10, "A": 8, "S": 8, "E": 5, "C": 0}
    assert result["differentiation"] == 10
    assert result["band"] == "well_differentiated"
    # A beats S on the RIASEC tie-break, both at 8.
    assert result["code"] == "IAS"


def test_tiebreak_follows_riasec_order(interests):
    """A and S tie at 8; A precedes S in R,I,A,S,E,C so A takes the letter."""
    responses = _checked_counts(interests, {"R": 0, "I": 10, "A": 8, "S": 8, "E": 8, "C": 0})
    result = score_interests(responses, interests)

    assert result["code"] == "IAS"
    assert result["tie_broken"] is True


def test_code_provisional_when_third_and_fourth_are_close(interests):
    """S takes the third letter by one check, so the letter is not stable.

    I=10, A=8, S=7, E=6. The report must say the third letter is unstable
    rather than present IAS as settled.
    """
    responses = _checked_counts(interests, {"R": 0, "I": 10, "A": 8, "S": 7, "E": 6, "C": 0})

    result = score_interests(responses, interests)

    assert result["raw"]["S"] == 7
    assert result["raw"]["E"] == 6
    assert result["code"] == "IAS"
    assert result["code_provisional"] is True


def test_stable_third_letter_is_not_provisional(interests):
    """I=10, A=8, S=8, E=3 — a five-check gap at the 3rd/4th boundary."""
    responses = _checked_counts(interests, {"R": 0, "I": 10, "A": 8, "S": 8, "E": 3, "C": 0})

    result = score_interests(responses, interests)

    assert result["code"] == "IAS"
    assert result["code_provisional"] is False


@pytest.mark.parametrize(
    ("spread", "expected"),
    [
        (5, "well_differentiated"),  # boundary: 5 is well differentiated
        (4, "moderate"),             # boundary: 4 is not
        (3, "moderate"),             # boundary: 3 is still moderate
        (2, "undifferentiated"),     # boundary: 2 is not
    ],
)
def test_band_boundaries(interests, spread, expected):
    """Report copy changes on these bands, so the boundaries are pinned."""
    responses = uniform(interests, 0)

    # Check `spread` of I's items and leave every other scale at 0, so
    # differentiation is exactly `spread`.
    remaining = spread
    for item in (i for i in interests if i.scale == "I"):
        take = min(item.response_max, remaining)
        responses[item.code] = take
        remaining -= take
    assert remaining == 0, "spread must fit within the I scale"

    result = score_interests(responses, interests)

    assert result["differentiation"] == spread
    assert result["band"] == expected


def test_never_reports_percentiles_without_local_norms(interests):
    """R4: percentiles stay null until a local norm sample exists."""
    result = score_interests(uniform(interests, 1), interests)

    assert result["percentiles"] is None
    assert result["norms_status"] == "pending_local_norms"


def test_missing_response_raises(interests):
    responses = uniform(interests, 1)
    del responses[interests[0].code]

    with pytest.raises(ScoringError, match="missing response"):
        score_interests(responses, interests)


def test_out_of_range_response_raises_rather_than_clamping(interests):
    """A 2 on a checkbox 0..1 item means the UI sent something undefined.
    Clamping would hide the bug and ship a wrong score."""
    responses = uniform(interests, 1)
    responses[interests[0].code] = 2

    with pytest.raises(ScoringError, match="outside"):
        score_interests(responses, interests)


def test_empty_items_raises():
    with pytest.raises(ScoringError, match="no interest items"):
        score_interests({}, [])


def _checked_counts(items, counts: dict[str, int]) -> dict[str, int]:
    """Check the first `counts[scale]` items in each scale, leave the rest
    unchecked — mirrors how a real respondent's ticks translate to a count."""
    responses = uniform(items, 0)
    for scale, n in counts.items():
        scale_items = [i for i in items if i.scale == scale]
        for item in scale_items[:n]:
            responses[item.code] = 1
    return responses

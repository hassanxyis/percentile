"""Interest scoring (plan §17.1)."""

import pytest

from app.scoring.interests import score_interests
from app.scoring.types import ScoringError

from .conftest import by_scale, uniform


def test_all_max_is_undifferentiated(interests):
    """Someone who likes everything has no Holland code worth reporting.

    Every scale 40, differentiation 0. The report must say no clear code
    emerged rather than pick three letters arbitrarily.
    """
    result = score_interests(uniform(interests, 4), interests)

    assert result["raw"] == dict.fromkeys("RIASEC", 40)
    assert result["differentiation"] == 0
    assert result["band"] == "undifferentiated"


def test_known_profile(interests):
    """Hand-computed sums and the resulting three-letter code."""
    responses = by_scale(interests, {"R": 1, "I": 4, "A": 3, "S": 3, "E": 2, "C": 0})
    result = score_interests(responses, interests)

    assert result["raw"] == {"R": 10, "I": 40, "A": 30, "S": 30, "E": 20, "C": 0}
    assert result["differentiation"] == 40
    assert result["band"] == "well_differentiated"
    # A beats S on the RIASEC tie-break, both at 30.
    assert result["code"] == "IAS"


def test_tiebreak_follows_riasec_order(interests):
    """A and S tie at 30; A precedes S in R,I,A,S,E,C so A takes the letter."""
    responses = by_scale(interests, {"R": 0, "I": 4, "A": 3, "S": 3, "E": 3, "C": 0})
    result = score_interests(responses, interests)

    assert result["code"] == "IAS"
    assert result["tie_broken"] is True


def test_code_provisional_when_third_and_fourth_are_close(interests):
    """S takes the third letter by one point, so the letter is not stable.

    I=40, A=30, S=29, E=28. The report must say the third letter is unstable
    rather than present ISA as settled.
    """
    responses = by_scale(interests, {"R": 0, "I": 4, "A": 3, "S": 3, "E": 3, "C": 0})
    # Drop one S item to 2 (29) and one E item to 1 (28).
    responses[next(i.code for i in interests if i.scale == "S")] = 2
    responses[next(i.code for i in interests if i.scale == "E")] = 1

    result = score_interests(responses, interests)

    assert result["raw"]["S"] == 29
    assert result["raw"]["E"] == 28
    assert result["code"] == "IAS"
    assert result["code_provisional"] is True


def test_stable_third_letter_is_not_provisional(interests):
    """I=40, A=30, S=30, E=20 — a ten-point gap at the 3rd/4th boundary."""
    responses = by_scale(interests, {"R": 0, "I": 4, "A": 3, "S": 3, "E": 2, "C": 0})

    result = score_interests(responses, interests)

    assert result["code"] == "IAS"
    assert result["code_provisional"] is False


@pytest.mark.parametrize(
    ("spread", "expected"),
    [
        (20, "well_differentiated"),  # boundary: 20 is well differentiated
        (19, "moderate"),             # boundary: 19 is not
        (10, "moderate"),             # boundary: 10 is still moderate
        (9, "undifferentiated"),      # boundary: 9 is not
    ],
)
def test_band_boundaries(interests, spread, expected):
    """Report copy changes on these bands, so the boundaries are pinned."""
    responses = uniform(interests, 0)

    # Put `spread` points onto I and leave every other scale at 0, so
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
    result = score_interests(uniform(interests, 2), interests)

    assert result["percentiles"] is None
    assert result["norms_status"] == "pending_local_norms"


def test_missing_response_raises(interests):
    responses = uniform(interests, 2)
    del responses[interests[0].code]

    with pytest.raises(ScoringError, match="missing response"):
        score_interests(responses, interests)


def test_out_of_range_response_raises_rather_than_clamping(interests):
    """A 5 on a 0..4 item means the UI sent something undefined. Clamping would
    hide the bug and ship a wrong score."""
    responses = uniform(interests, 2)
    responses[interests[0].code] = 5

    with pytest.raises(ScoringError, match="outside"):
        score_interests(responses, interests)


def test_empty_items_raises():
    with pytest.raises(ScoringError, match="no interest items"):
        score_interests({}, [])

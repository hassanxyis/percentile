"""Personality scoring (plan §17.1)."""

import pytest

from app.scoring.personality import reverse_if_keyed, score_personality
from app.scoring.types import Item, ScoringError

from .conftest import personality_items, uniform


def _item(reverse: bool, lo: int, hi: int) -> Item:
    return Item(
        code="X_01",
        ordinal=1,
        text="placeholder",
        scale="E",
        reverse_keyed=reverse,
        response_min=lo,
        response_max=hi,
        instrument_code="personality",
    )


def test_reverse_item_flips_on_a_1_to_5_scale():
    """A reverse item answered 1 scores 5."""
    assert reverse_if_keyed(1, _item(True, 1, 5)) == 5
    assert reverse_if_keyed(5, _item(True, 1, 5)) == 1
    assert reverse_if_keyed(3, _item(True, 1, 5)) == 3


def test_forward_item_is_untouched():
    assert reverse_if_keyed(1, _item(False, 1, 5)) == 1


def test_no_hardcoded_six():
    """Reverse-keying is derived from the item's own range, not a constant.

    An instrument added later on a 0..6 scale must score correctly with no code
    change: 0 becomes 6, not -1 (plan §7.2).
    """
    assert reverse_if_keyed(0, _item(True, 0, 6)) == 6
    assert reverse_if_keyed(6, _item(True, 0, 6)) == 0
    assert reverse_if_keyed(2, _item(True, 0, 6)) == 4


def test_domain_totals(personality):
    """Every item answered 3 is mid-scale, so reverse-keying leaves it at 3.

    Ten items per domain gives 30 regardless of keying — a clean check that
    reverse items are counted, not skipped.
    """
    result = score_personality(uniform(personality, 3), personality)

    assert result["raw"] == dict.fromkeys("OCEAS", 30)
    assert all(band == "average" for band in result["bands"].values())


def test_reverse_keying_is_actually_applied(personality):
    """All 1s: five forward items score 1, five reverse items score 5 → 30."""
    result = score_personality(uniform(personality, 1), personality)

    assert result["raw"]["E"] == 30


@pytest.mark.parametrize(
    ("total", "expected"),
    [(10, "very low"), (19, "very low"), (20, "low"), (27, "low"),
     (28, "average"), (34, "average"), (35, "high"), (42, "high"),
     (43, "very high"), (50, "very high")],
)
def test_provisional_band_boundaries(total, expected):
    """Bands are reported to students, so every boundary is pinned."""
    from app.scoring.personality import _provisional_band

    assert _provisional_band(total) == expected


def test_stability_not_neuroticism(personality):
    """The fifth domain is reported as Emotional Stability (plan §6)."""
    result = score_personality(uniform(personality, 3), personality)

    assert result["labels"]["S"] == "Emotional Stability"
    assert "Neuroticism" not in result["labels"].values()


def test_provisional_until_norms_exist(personality):
    result = score_personality(uniform(personality, 3), personality)

    assert result["norms_status"] == "pending_local_norms"
    assert result["percentiles"] is None


def test_normative_bands_when_norms_supplied(personality):
    """With local norms the bands become normative and the shape is unchanged,
    so the report template does not branch (plan §7.2)."""
    norms = dict.fromkeys("OCEAS", (30.0, 5.0))
    provisional = score_personality(uniform(personality, 3), personality)
    normed = score_personality(uniform(personality, 3), personality, norms=norms)

    assert normed["norms_status"] == "local"
    assert normed.keys() == provisional.keys()
    # Every domain sits exactly on the mean.
    assert all(band == "average" for band in normed["bands"].values())


def test_partial_norms_rejected(personality):
    """Norming three domains and not the other two would mix band systems
    inside one report without saying so."""
    with pytest.raises(ScoringError, match="missing scales"):
        score_personality(
            uniform(personality, 3), personality, norms={"O": (30.0, 5.0)}
        )


def test_zero_sd_rejected(personality):
    with pytest.raises(ScoringError, match="sd must be positive"):
        score_personality(
            uniform(personality, 3),
            personality,
            norms=dict.fromkeys("OCEAS", (30.0, 0.0)),
        )


def test_out_of_range_raises(personality):
    responses = uniform(personality, 3)
    responses[personality[0].code] = 9

    with pytest.raises(ScoringError, match="outside"):
        score_personality(responses, personality)


def test_missing_response_raises(personality):
    responses = uniform(personality, 3)
    del responses[personality[0].code]

    with pytest.raises(ScoringError, match="missing response"):
        score_personality(responses, personality)


def test_unknown_scale_raises():
    """An item keyed to a scale outside the Big Five is a data-file error."""
    items = personality_items()
    stray = Item(
        code="IPIP_Z_01",
        ordinal=99,
        text="placeholder",
        scale="Z",
        reverse_keyed=False,
        response_min=1,
        response_max=5,
        instrument_code="personality",
    )
    items.append(stray)

    with pytest.raises(ScoringError, match="unknown personality scale"):
        score_personality(uniform(items, 3), items)

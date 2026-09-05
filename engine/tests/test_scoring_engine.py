"""The pure scoring orchestrator (plan §7.5, M7).

`score_session_record` is the seam between the scoring rules and the database.
Everything asserted here holds without a connection, which is the property that
makes `rescore_all.py` (M12) possible at all — R1 requires every session to be
rescoreable from its stored raw responses, and a scorer that fetched its own
data could only ever score against the present.
"""

import pytest

from app.scoring.engine import GET2, SessionInput, group_by_instrument, score_session_record
from app.scoring.get2 import GET2_SUBSCALES
from app.scoring.types import Item, ScoringError

from .conftest import get2_scoring, interest_items, personality_items


def get2_items(per_scale: int = 4) -> list[Item]:
    """GET2 items matching `get2_scoring()`'s synthetic groupings, 0..2.

    The range is the point: 0..2 is neither the interests checkbox nor the
    personality Likert, and nothing in the engine special-cases it.
    """
    items = []
    ordinal = 1
    for scale in GET2_SUBSCALES:
        for n in range(1, per_scale + 1):
            items.append(
                Item(
                    code=f"GET2_{scale.upper()[:3]}_{n:02d}",
                    ordinal=ordinal,
                    text=f"placeholder get2 item {ordinal}",
                    scale=scale,
                    reverse_keyed=False,
                    response_min=0,
                    response_max=2,
                    instrument_code=GET2,
                )
            )
            ordinal += 1
    return items


def complete_responses(items: list[Item], value: int = 1) -> dict[str, int]:
    return {item.code: value for item in items}


def two_module_session(**overrides) -> SessionInput:
    """The shape that ships today: interests + personality, no GET2."""
    items = interest_items() + personality_items()
    defaults = {
        "items": items,
        "responses": {
            **{i.code: 1 for i in interest_items()},
            **{i.code: 3 for i in personality_items()},
        },
    }
    return SessionInput(**{**defaults, **overrides})


# ── the shape it returns ─────────────────────────────────────────────────────


def test_returns_the_four_columns_record_score_writes():
    result = score_session_record(two_module_session())

    assert set(result) == {"interests", "personality", "get2", "flags"}


def test_interests_and_personality_are_scored():
    result = score_session_record(two_module_session())

    assert result["interests"]["code"]
    assert set(result["personality"]["raw"]) == {"O", "C", "E", "A", "S"}


def test_no_percentiles_without_local_norms():
    """R4. Nothing may show a percentile until a population reaches n >= 300."""
    result = score_session_record(two_module_session())

    assert result["interests"]["percentiles"] is None
    assert result["personality"]["percentiles"] is None
    assert result["personality"]["norms_status"] == "pending_local_norms"


# ── R10: GET2 absent is a state, not a failure ───────────────────────────────


def test_get2_absent_returns_the_not_administered_shape():
    """The case that ships today — get2_items.csv does not exist (§20 item 2)."""
    result = score_session_record(two_module_session())

    assert result["get2"] == {"instrument": None, "status": "module_not_administered"}


def test_get2_absent_does_not_raise_on_the_uniform_flag():
    """`compute_flags` must not KeyError on the not-administered shape.

    The flag checks subscale uniformity, and the absent shape has no `subscales`
    key. An unadministered module cannot look uniform, so the right answer is no
    flag rather than a crash — R10's promise is that the absence path is already
    handled, not that it gets handled later under deadline pressure.
    """
    result = score_session_record(two_module_session())

    assert not any(f["code"] == "get2_uniform_response" for f in result["flags"])


def test_get2_scores_when_its_items_are_loaded():
    """Adding GET2 is loading a file (R10). Nothing here branches on the code."""
    items = interest_items() + personality_items() + get2_items()
    session = SessionInput(
        items=items,
        responses={
            **{i.code: 1 for i in interest_items()},
            **{i.code: 3 for i in personality_items()},
            **{i.code: 2 for i in get2_items()},
        },
        get2_scoring=get2_scoring(),
    )

    result = score_session_record(session)

    assert result["get2"]["instrument"] == "GET2"
    assert set(result["get2"]["subscales"]) == set(GET2_SUBSCALES)


def test_get2_scoring_file_without_items_is_still_not_administered():
    """A scoring file present but no items loaded is absence, not a half-score.

    The two independent reasons GET2 can be missing — no items, no scoring
    file — must produce one shape, so no downstream template needs two checks.
    """
    session = two_module_session()
    session = SessionInput(
        items=session.items,
        responses=session.responses,
        get2_scoring=get2_scoring(),
    )

    result = score_session_record(session)

    assert result["get2"]["status"] == "module_not_administered"


# ── refusing to score rather than scoring wrongly ────────────────────────────


def test_empty_items_names_the_loader():
    """The real cause is almost always that load_instruments.py never ran."""
    with pytest.raises(ScoringError, match="load_instruments"):
        score_session_record(SessionInput(items=[], responses={}))


def test_missing_module_is_refused_not_partially_scored():
    """Interests present, personality absent. A score for half an assessment
    would look fine and land in a report."""
    items = interest_items()
    with pytest.raises(ScoringError, match="personality"):
        score_session_record(SessionInput(items=items, responses=complete_responses(items)))


def test_missing_response_raises():
    session = two_module_session()
    incomplete = dict(session.responses)
    incomplete.pop(next(iter(incomplete)))

    with pytest.raises(ScoringError, match="missing response"):
        score_session_record(SessionInput(items=session.items, responses=incomplete))


def test_out_of_range_response_raises():
    """A 4 written against a 0..1 interests item scores silently and wrongly
    unless someone refuses it."""
    session = two_module_session()
    bad = dict(session.responses)
    bad[interest_items()[0].code] = 4

    with pytest.raises(ScoringError, match="outside"):
        score_session_record(SessionInput(items=session.items, responses=bad))


# ── grouping ─────────────────────────────────────────────────────────────────


def test_grouping_sorts_by_ordinal_within_an_instrument():
    """flags.py walks items in order; "twelve consecutive identical answers"
    only means something if consecutive matches what the student saw."""
    items = list(reversed(interest_items(per_scale=2)))

    grouped = group_by_instrument(items)

    ordinals = [i.ordinal for i in grouped["interests"]]
    assert ordinals == sorted(ordinals)


def test_grouping_keys_on_instrument_not_scale():
    items = interest_items() + personality_items()

    grouped = group_by_instrument(items)

    assert set(grouped) == {"interests", "personality"}

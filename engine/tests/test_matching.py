"""Occupation matching (plan §8, §17.1, M3).

The catalogue is synthetic (see conftest.occupation_catalogue) — real O*NET
rows are gitignored, and hand-chosen interest vectors let each test state the
profile shape it is exercising.
"""

import pytest

from app.matching.occupations import (
    ENTREPRENEURIAL_SLOTS,
    LOCAL_MATCH_TARGET,
    MAX_PER_SOC_GROUP,
    cosine,
    ipsatize,
    match,
    norm01,
    soc_major_group,
)
from app.scoring.types import ScoringError

from .conftest import occupation, occupation_catalogue


def _profile(**scales: int) -> dict[str, int]:
    """A RIASEC profile; unnamed scales score 0."""
    return {scale: scales.get(scale, 0) for scale in "RIASEC"}


# ── ipsatize and cosine ──────────────────────────────────────────────────────


def test_ipsatize_centres_on_own_mean():
    centred = ipsatize({"R": 10, "I": 8, "A": 6, "S": 4, "E": 2, "C": 0})

    assert sum(centred.values()) == pytest.approx(0.0)
    assert centred["R"] == pytest.approx(5.0)
    assert centred["C"] == pytest.approx(-5.0)


def test_ipsatize_preserves_shape():
    """Adding a constant to every scale must not change the centred vector.

    That is the whole point: a student who checks every box and a student who
    checks half of them have the same *shape* if the pattern is the same.
    """
    low = ipsatize({"R": 5, "I": 3, "A": 1, "S": 1, "E": 1, "C": 1})
    high = ipsatize({"R": 9, "I": 7, "A": 5, "S": 5, "E": 5, "C": 5})

    assert low == pytest.approx(high)


def test_cosine_ignores_elevation():
    """Two occupations differing only in elevation score identically."""
    student = _profile(R=9, I=7, A=2, S=1, E=1, C=1)
    catalogue = [
        occupation("15-1252.00", {"R": 4, "I": 3, "A": 1, "S": 1, "E": 1, "C": 1}),
        occupation("17-2141.00", {"R": 7, "I": 6, "A": 4, "S": 4, "E": 4, "C": 4}),
    ]

    result = match(student, catalogue, "undergraduate")
    scores = {m["onet_soc_code"]: m["score"] for m in result["matches"]}

    assert scores["15-1252.00"] == pytest.approx(scores["17-2141.00"])


def test_cosine_of_identical_shape_is_one():
    assert cosine(ipsatize({"R": 4, "I": 2}), ipsatize({"R": 8, "I": 4})) == pytest.approx(1.0)


def test_norm01_maps_similarity_onto_zero_to_one():
    assert norm01(-1.0) == 0.0
    assert norm01(0.0) == 0.5
    assert norm01(1.0) == 1.0


def test_soc_major_group_is_first_two_digits():
    assert soc_major_group("29-1141.00") == "29"
    assert soc_major_group("15-1252.00") == "15"


def test_undifferentiated_profile_raises():
    """A flat profile centres to the zero vector, which has no direction.

    Ranking it would return the catalogue's own order dressed up as a
    recommendation. score_interests() already bands this profile
    `undifferentiated`; the caller branches on that instead (plan §7.1).
    """
    with pytest.raises(ScoringError, match="undifferentiated"):
        match(_profile(R=5, I=5, A=5, S=5, E=5, C=5), occupation_catalogue(), "undergraduate")


# ── job zone filter ──────────────────────────────────────────────────────────


def test_matching_job_zone():
    """A matric student receives nothing above Job Zone 3.

    Job Zone is an education/experience band — matching a matric student to
    occupations assuming a bachelor's or a doctorate is worse than useless.
    """
    result = match(_profile(R=9, I=6), occupation_catalogue(), "matric")

    assert result["matches"], "expected at least one matric-appropriate match"
    codes = {m["onet_soc_code"] for m in result["matches"]}
    zones = {o.job_zone for o in occupation_catalogue() if o.onet_soc_code in codes}
    assert zones <= {2, 3}


@pytest.mark.parametrize(
    ("education_level", "allowed"),
    [("matric", {2, 3}), ("intermediate", {2, 3, 4}), ("undergraduate", {3, 4, 5})],
)
def test_job_zone_bands_per_education_level(education_level, allowed):
    result = match(_profile(R=9, I=6, E=3), occupation_catalogue(), education_level)

    codes = {m["onet_soc_code"] for m in result["matches"]}
    zones = {o.job_zone for o in occupation_catalogue() if o.onet_soc_code in codes}
    assert zones <= allowed


def test_unknown_education_level_falls_back_rather_than_returning_nothing():
    """A missing roster field should narrow the report, not empty it."""
    result = match(_profile(R=9, I=6), occupation_catalogue(), None)

    assert result["matches"]


# ── diversity ────────────────────────────────────────────────────────────────


def test_matching_diversity():
    """No more than 2 results share a 2-digit SOC major group.

    The catalogue holds six near-identical SOC-15 roles; without the cap an
    Investigative profile fills the list with them and the report looks
    unintelligent (plan §8).
    """
    result = match(_profile(I=10, C=6), occupation_catalogue(), "undergraduate")

    counts: dict[str, int] = {}
    for m in result["matches"]:
        group = soc_major_group(m["onet_soc_code"])
        counts[group] = counts.get(group, 0) + 1

    assert counts, "expected matches"
    assert max(counts.values()) <= MAX_PER_SOC_GROUP


def test_diversify_keeps_the_best_of_each_group():
    """The cap drops the weaker duplicates, never the group's top scorer."""
    result = match(_profile(I=10, C=6), occupation_catalogue(), "undergraduate")

    soc15 = [m for m in result["matches"] if soc_major_group(m["onet_soc_code"]) == "15"]
    assert len(soc15) == MAX_PER_SOC_GROUP
    assert soc15[0]["score"] >= soc15[1]["score"]


# ── localisation ─────────────────────────────────────────────────────────────


def test_pk_relevant_results_come_first():
    """Local matches lead; international ones follow under their own heading."""
    result = match(_profile(I=9, R=6, C=4), occupation_catalogue(), "undergraduate")

    flags = [m["pk_relevant"] for m in result["matches"]]
    assert True in flags and False in flags, "expected a mix to test the ordering"
    assert flags == sorted(flags, reverse=True)


def test_local_matches_carry_their_local_title_and_pathway():
    result = match(_profile(S=9, I=5), occupation_catalogue(), "undergraduate")

    local = [m for m in result["matches"] if m["pk_relevant"]]
    assert local
    assert all(m["local_title"] and m["local_pathway"] for m in local)


def test_local_match_gap_flagged():
    """Fewer than 5 local matches is a gap in pk_occupation_map.csv (§8)."""
    catalogue = [
        occupation("15-1252.00", {"I": 7}, pk_relevant=True),
        occupation("15-1211.00", {"I": 6}),
        occupation("17-2141.00", {"R": 7, "I": 6}),
        occupation("29-1141.00", {"S": 7}),
    ]

    result = match(_profile(I=9, R=4), catalogue, "undergraduate")

    assert sum(m["pk_relevant"] for m in result["matches"]) < LOCAL_MATCH_TARGET
    assert result["local_match_gap"] is True


def test_local_match_gap_not_flagged_when_map_has_coverage():
    result = match(_profile(S=9, E=6, C=4), occupation_catalogue(), "undergraduate")

    assert result["local_match_gap"] is False


# ── entrepreneurial carve-out (plan §8, M3 done-condition) ───────────────────


def test_entrepreneurial_carve_out_surfaces_track_occupation():
    """M3's definition of done.

    A high-`entrepreneurial_flag` profile must surface at least one
    entrepreneurship-track occupation even when its raw cosine score is
    mediocre. The profile here is Investigative/Realistic, so the
    Enterprising-heavy track occupations rank poorly on interest alone.
    """
    student = _profile(I=10, R=8, C=3)

    without = match(student, occupation_catalogue(), "undergraduate", entrepreneurial_flag=False)
    with_flag = match(student, occupation_catalogue(), "undergraduate", entrepreneurial_flag=True)

    assert not any(m["entrepreneurial_track"] for m in without["matches"]), (
        "this profile should not reach a track occupation on cosine score alone — "
        "otherwise the test proves nothing about the carve-out"
    )
    assert len(with_flag["entrepreneurial_additions"]) >= 1
    assert all(m["entrepreneurial_track"] for m in with_flag["entrepreneurial_additions"])


def test_entrepreneurial_additions_are_labelled_not_blended():
    """§8: labelled 'worth exploring given your GET2 profile', never blended
    anonymously into the ranked list."""
    result = match(
        _profile(I=10, R=8, C=3), occupation_catalogue(), "undergraduate",
        entrepreneurial_flag=True,
    )

    ranked = {m["onet_soc_code"] for m in result["matches"]}
    added = {m["onet_soc_code"] for m in result["entrepreneurial_additions"]}

    assert added
    assert not (ranked & added), "a carve-out occupation must not also appear in matches"


def test_no_carve_out_when_flag_false():
    """GET2 biases which occupations are shown, and only when it fires."""
    result = match(
        _profile(I=10, R=8, C=3), occupation_catalogue(), "undergraduate",
        entrepreneurial_flag=False,
    )

    assert result["entrepreneurial_additions"] == []


def test_carve_out_is_capped():
    result = match(
        _profile(I=10, R=8, C=3), occupation_catalogue(), "undergraduate",
        entrepreneurial_flag=True,
    )

    assert len(result["entrepreneurial_additions"]) <= ENTREPRENEURIAL_SLOTS


def test_carve_out_does_not_reweight_the_ranking():
    """The flag adds a separate section; it must not move the ranked list."""
    student = _profile(I=10, R=8, C=3)

    without = match(student, occupation_catalogue(), "undergraduate", entrepreneurial_flag=False)
    with_flag = match(student, occupation_catalogue(), "undergraduate", entrepreneurial_flag=True)

    assert without["matches"] == with_flag["matches"]


# ── input validation ─────────────────────────────────────────────────────────


def test_empty_catalogue_raises():
    with pytest.raises(ScoringError, match="no occupations supplied"):
        match(_profile(R=9, I=6), [], "undergraduate")


def test_profile_missing_a_scale_raises():
    with pytest.raises(ScoringError, match="missing scales"):
        match({"R": 9, "I": 6}, occupation_catalogue(), "undergraduate")


def test_occupation_missing_an_interest_scale_raises():
    """A half-loaded catalogue row is a loader bug — say so, don't rank it."""
    broken = occupation("15-1252.00", {"I": 7})
    object.__setattr__(broken, "interests", {"R": 1.0, "I": 7.0})

    with pytest.raises(ScoringError, match="load_onet"):
        match(_profile(R=9, I=6), [broken], "undergraduate")


def test_no_occupations_in_job_zone_raises():
    """An empty pool is a catalogue problem, not a 'no matches' result."""
    catalogue = [occupation("15-1252.00", {"I": 7}, job_zone=5)]

    with pytest.raises(ScoringError, match="no occupations in job zones"):
        match(_profile(I=9, R=4), catalogue, "matric")


# ── result shape ─────────────────────────────────────────────────────────────


def test_matches_are_ranked_from_one():
    result = match(_profile(I=9, R=6), occupation_catalogue(), "undergraduate")

    assert [m["rank"] for m in result["matches"]] == list(
        range(1, len(result["matches"]) + 1)
    )


def test_scores_fit_the_occupation_matches_column():
    """`occupation_matches.score` is numeric(5,4) — 0..1 at four decimals."""
    result = match(_profile(I=9, R=6), occupation_catalogue(), "undergraduate")

    for m in result["matches"]:
        assert 0.0 <= m["score"] <= 1.0
        assert m["score"] == round(m["score"], 4)


def test_ranking_is_deterministic_on_ties():
    """Two occupations with identical shape must always order the same way.

    Row order out of Postgres is not guaranteed, so ties break on SOC code.
    """
    catalogue = [
        occupation("29-1141.00", {"I": 7, "C": 5}),
        occupation("15-1252.00", {"I": 7, "C": 5}),
    ]

    forward = match(_profile(I=9, C=6), catalogue, "undergraduate")
    reversed_ = match(_profile(I=9, C=6), list(reversed(catalogue)), "undergraduate")

    assert forward["matches"] == reversed_["matches"]
    assert forward["matches"][0]["onet_soc_code"] == "15-1252.00"

"""Cohort analytics (plan §10, §9, M11).

The arithmetic behind the institution's report, asserted without a database or a
PDF — `app/matching/cohort.py` is pure for exactly that reason.

Four things here carry real weight rather than checking sums:

* **Congruence is leave-one-out.** A student compared against a centroid they are
  inside scores against themselves, and in a group of one that is a guaranteed
  1.0. The report would then announce perfect alignment for a field with a single
  student in it.
* **§9's honesty rule is arithmetic, not a comment.** `describe_rate` cannot
  produce a percentage below n=10, so no template can print one.
* **§7.4's exclusion is counted, not silent.** Dropping two students from an
  aggregate and still calling it "the cohort" overstates the n on every page.
* **R8's boundary for this document.** Names appear on the follow-up list, which
  §10 requires. They do not appear beside a response-quality flag.
"""

from __future__ import annotations

import pytest

from app.matching.cohort import (
    ALIGNED,
    ALIGNED_MIN,
    MISALIGNED,
    PARTIAL,
    PARTIAL_MIN,
    Participant,
    aggregate,
    centroid,
    congruence_class,
    describe_rate,
)
from app.matching.occupations import cosine, ipsatize, norm01
from app.scoring.interests import RIASEC
from app.scoring.types import ScoringError


def participant(
    n: int,
    interests: dict[str, int] | None = None,
    intended_field: str | None = "medicine",
    band: str = "well_differentiated",
    status: str = "confirmed",
    warn_flags: int = 0,
    flag_codes: tuple[str, ...] = (),
    personality_bands: dict[str, str] | None = None,
) -> Participant:
    """One scored student. Unnamed RIASEC scales score 0."""
    return Participant(
        participant_id=f"p-{n:03d}",
        full_name=f"Student {n:03d}",
        intended_field=intended_field,
        status=status,
        interests={scale: (interests or {}).get(scale, 0) for scale in RIASEC},
        band=band,
        personality_bands=personality_bands or dict.fromkeys("OCEAS", "average"),
        warn_flags=warn_flags,
        flag_codes=flag_codes,
    )


def statuses(**counts: int) -> list[str]:
    """A roster's worth of `participants.status` values: `statuses(invited=5)`."""
    return [status for status, n in counts.items() for _ in range(n)]


# ── §9: the honesty rule, as arithmetic ──────────────────────────────────────


def test_describe_rate_never_prints_a_percentage_below_ten():
    """§9: "never print a percentage on a denominator below 10".

    Enforced by withholding the percentage rather than by asking a template to
    remember. A pilot cohort is 15–25 students and an intended-field group inside
    it is routinely 2 or 3 — "67% misaligned" describing two students is the most
    misleading sentence this report could print.
    """
    for total in range(1, 10):
        rendered = describe_rate(1, total)
        assert "%" not in rendered, f"a percentage appeared on a denominator of {total}"
        assert str(total) in rendered, "the denominator must always be shown"


def test_describe_rate_prints_a_percentage_once_the_denominator_supports_one():
    assert describe_rate(12, 20) == "12 of 20 (60%)"
    assert describe_rate(5, 10) == "5 of 10 (50%)"


def test_describe_rate_survives_an_empty_denominator():
    """A field group with nobody in it must not divide by zero mid-render."""
    assert describe_rate(0, 0) == "none"


# ── §10: congruence classes ──────────────────────────────────────────────────


def test_congruence_thresholds_are_not_off_by_one():
    """The boundary value belongs to the HIGHER class (§10).

    A student sitting exactly on 0.60 must read the same on the method page and
    in the table. Off-by-one here moves real students between "aligned" and
    "partial" and nobody would ever notice.
    """
    assert congruence_class(ALIGNED_MIN) == ALIGNED
    assert congruence_class(ALIGNED_MIN - 0.0001) == PARTIAL
    assert congruence_class(PARTIAL_MIN) == PARTIAL
    assert congruence_class(PARTIAL_MIN - 0.0001) == MISALIGNED
    assert congruence_class(1.0) == ALIGNED
    assert congruence_class(0.0) == MISALIGNED


def test_a_field_group_of_one_gets_no_congruence_figure():
    """Leave-one-out has no operand at n=1, and the honest output is absence.

    Comparing a student against a centroid they alone define returns exactly 1.0
    — the report would announce perfect alignment for a field containing one
    person. §9's whole purpose is to stop numbers like that reaching a page.
    """
    result = aggregate(
        [
            participant(1, {"I": 8, "S": 6}, intended_field="medicine"),
            participant(2, {"R": 9}, intended_field="engineering"),
        ],
        statuses(confirmed=2),
    )

    assert [group.n for group in result.fields] == [1, 1]
    for group in result.fields:
        assert group.uncomputable == 1
        assert group.aligned == group.partial == group.misaligned == 0

    assert result.congruence_denominator == 0, (
        "no student can be classed when every field group has one member"
    )


def test_students_with_the_same_shape_are_aligned_with_each_other():
    """The positive case, hand-checkable.

    Three students with an identical profile shape: each one, compared against
    the mean of the other two, is a perfect match. Congruence is 1.0 and every
    one of them is `aligned`.
    """
    result = aggregate(
        [participant(n, {"I": 8, "S": 6, "C": 4}) for n in range(1, 4)],
        statuses(confirmed=3),
    )

    group = result.fields[0]
    assert group.n == 3
    assert group.aligned == 3
    assert group.uncomputable == 0


def test_a_student_whose_shape_opposes_their_field_is_misaligned():
    """The other end of the same arithmetic.

    Two Investigative students and one Enterprising student all heading into
    medicine. The odd one out is compared against the other two — not against a
    centroid they dragged toward themselves — and lands in `misaligned`.
    """
    result = aggregate(
        [
            participant(1, {"I": 9, "C": 7}),
            participant(2, {"I": 9, "C": 7}),
            participant(3, {"E": 9, "A": 7}),
        ],
        statuses(confirmed=3),
    )

    group = result.fields[0]
    assert group.misaligned == 1
    assert group.aligned == 2


def test_a_flat_profile_is_uncomputable_rather_than_failing_the_cohort():
    """An undifferentiated student has no shape to compare (`cosine` refuses it).

    One student answering every item the same must not raise and take the whole
    school's report down with them. They are counted as uncomputable here and
    reach the follow-up list through their band instead.
    """
    result = aggregate(
        [
            participant(1, {"I": 8, "S": 6}),
            participant(2, {"I": 8, "S": 6}),
            participant(3, dict.fromkeys(RIASEC, 5), band="undifferentiated"),
        ],
        statuses(confirmed=3),
    )

    assert result.fields[0].uncomputable == 1


def test_congruence_is_leave_one_out_not_self_inclusive():
    """The decision this module turns on, with its counterfactual computed.

    Two Investigative students and one Enterprising, all heading into medicine.
    The odd one out contributes a third of the group's centroid, so a
    self-inclusive comparison drags the reference toward the very student being
    measured — and on these numbers that is not a rounding difference, it flips
    the class outright: self-inclusive scores the outlier ~0.65 (`aligned`),
    leave-one-out scores them 0.40 (`partial`).

    Both figures are computed here rather than asserted as constants, so this
    keeps testing the decision rather than today's arithmetic.
    """
    members = [
        participant(1, {"I": 9}),
        participant(2, {"I": 9}),
        participant(3, {"E": 9}),
    ]
    outlier = members[-1]
    outlier_shape = ipsatize({scale: float(outlier.interests[scale]) for scale in RIASEC})

    leave_one_out = norm01(
        cosine(outlier_shape, ipsatize(centroid([m for m in members if m is not outlier])))
    )
    self_inclusive = norm01(cosine(outlier_shape, ipsatize(centroid(members))))

    assert leave_one_out < self_inclusive, (
        "a self-inclusive centroid must flatter the student it contains"
    )
    assert congruence_class(self_inclusive) == ALIGNED
    assert congruence_class(leave_one_out) == PARTIAL

    # And that is what the module actually reports.
    result = aggregate(members, statuses(confirmed=3))
    assert result.fields[0].partial == 1
    assert result.fields[0].aligned == 2


# ── centroids ────────────────────────────────────────────────────────────────


def test_a_centroid_is_the_mean_profile_and_keeps_its_elevation():
    """Not ipsatized. The page draws this as a hexagon of a field's average
    profile, and elevation is part of that picture even though the comparison
    removes it."""
    result = centroid(
        [participant(1, {"I": 10, "S": 4}), participant(2, {"I": 6, "S": 8})]
    )

    assert result["I"] == pytest.approx(8.0)
    assert result["S"] == pytest.approx(6.0)
    assert set(result) == set(RIASEC)


def test_a_centroid_of_an_empty_group_raises_rather_than_returning_zeros():
    """A zero vector would silently become "this field has no interests"."""
    with pytest.raises(ScoringError, match="empty group"):
        centroid([])


# ── §7.4: exclusion ──────────────────────────────────────────────────────────


def test_a_participant_with_two_warn_flags_is_excluded_and_counted():
    """§7.4: two or more warns exclude, and the report states how many and why.

    Silently dropping them would overstate the n on every page — the number the
    whole document's honesty rests on.
    """
    result = aggregate(
        [
            participant(1, {"I": 8}),
            participant(2, {"I": 8}),
            participant(
                3, {"I": 8}, warn_flags=2, flag_codes=("straightlining", "too_fast")
            ),
        ],
        statuses(confirmed=3),
    )

    assert result.scored == 3
    assert result.included == 2
    assert result.excluded == 1
    assert result.excluded_by_code == {"straightlining": 1, "too_fast": 1}


def test_one_warn_flag_is_not_enough_to_exclude():
    """The threshold is two (§7.4). One flag is a conversation, not a disqualification."""
    result = aggregate(
        [participant(n, {"I": 8}, warn_flags=1, flag_codes=("long_gap",)) for n in (1, 2)],
        statuses(confirmed=2),
    )

    assert result.included == 2
    assert result.excluded == 0


def test_the_excluded_breakdown_carries_no_names():
    """R8, at the boundary this document draws.

    §7.4 asks for how many were excluded and why. It does not ask for who, and
    "Student 003 — straightlining" in a document that circulates inside a school
    is a quality judgement about a minor with their name attached.
    """
    excluded = participant(
        3, {"I": 8}, warn_flags=2, flag_codes=("straightlining", "too_fast")
    )
    result = aggregate(
        [participant(1, {"I": 8}), participant(2, {"I": 8}), excluded],
        statuses(confirmed=3),
    )

    assert excluded.full_name not in repr(result.excluded_by_code)
    assert excluded.participant_id not in repr(result.excluded_by_code)


def test_a_cohort_where_everyone_is_excluded_refuses_to_aggregate():
    """Zero included students is not a small report, it is a false one."""
    with pytest.raises(ScoringError, match="excluded"):
        aggregate(
            [
                participant(n, {"I": 8}, warn_flags=2, flag_codes=("too_fast",))
                for n in (1, 2)
            ],
            statuses(confirmed=2),
        )


def test_a_cohort_with_no_scored_participants_refuses_to_aggregate():
    with pytest.raises(ScoringError, match="nothing to aggregate"):
        aggregate([], statuses(invited=20))


# ── §10: the completion table ────────────────────────────────────────────────


def test_the_completion_table_counts_every_participant_not_just_the_scored():
    """A cohort of 20 with 3 scored must not report "3 of 3 complete".

    The counsellor opens this table to find who has not started — students who
    have no score and therefore never appear in the aggregate list at all.
    """
    result = aggregate(
        [participant(n, {"I": 8}) for n in range(1, 4)],
        statuses(invited=12, started=3, pending_review=2, confirmed=3),
    )

    assert result.total_participants == 20
    assert result.scored == 3
    assert result.status_counts["invited"] == 12
    assert result.status_counts["started"] == 3


def test_the_review_backlog_is_its_own_number():
    """§10's v2 addition: `pending_review` and `confirmed` are separate figures.

    Folded into a single "completion" percentage, a school cannot see that the
    thing holding up its reports is a review queue — which is the one part of
    this an institution can actually chase.
    """
    result = aggregate(
        [participant(n, {"I": 8}) for n in range(1, 4)],
        statuses(pending_review=8, confirmed=3, invited=1),
    )

    assert result.status_counts["pending_review"] == 8
    assert result.status_counts["confirmed"] == 3


# ── distributions ────────────────────────────────────────────────────────────


def test_the_interest_distribution_counts_leading_letters_once_each():
    """Counting all three code letters would sum to 3n and read as a percentage
    of something that does not exist."""
    result = aggregate(
        [
            participant(1, {"I": 9, "S": 5}),
            participant(2, {"I": 8, "C": 6}),
            participant(3, {"R": 7, "I": 2}),
        ],
        statuses(confirmed=3),
    )

    assert sum(result.interest_distribution.values()) == 3
    assert result.interest_distribution["I"] == 2
    assert result.interest_distribution["R"] == 1


def test_the_leading_letter_breaks_ties_the_way_score_interests_does():
    """RIASEC order, matching `score_interests`.

    Two documents disagreeing about a student's strongest interest is something
    a counsellor notices and cannot explain.
    """
    result = aggregate(
        [participant(1, {"A": 7, "S": 7})], statuses(confirmed=1)
    )
    assert result.interest_distribution["A"] == 1
    assert result.interest_distribution["S"] == 0


def test_every_personality_domain_appears_even_with_no_students_in_a_band():
    """A stable chart shape across cohorts: a missing bar means zero, not a
    template that forgot to render it."""
    result = aggregate(
        [participant(n, {"I": 8}, personality_bands={"O": "high"}) for n in (1, 2)],
        statuses(confirmed=2),
    )

    assert set(result.personality_distribution) == set("OCEAS")
    assert result.personality_distribution["O"] == {"high": 2}
    assert result.personality_distribution["C"] == {}


# ── §10: the named follow-up list ────────────────────────────────────────────


def test_the_follow_up_list_names_students_and_says_why():
    """§10 requires "named lists of students to follow up".

    Asserted positively so that a later over-correction toward R8 — stripping
    names from this page — fails here rather than quietly producing a list
    nobody can act on.
    """
    result = aggregate(
        [
            participant(1, {"I": 8, "S": 6}),
            participant(2, {"I": 8, "S": 6}),
            participant(3, dict.fromkeys(RIASEC, 5), band="undifferentiated"),
        ],
        statuses(confirmed=3),
    )

    assert [f.full_name for f in result.follow_up] == ["Student 003"]
    assert result.follow_up[0].reason
    assert result.follow_up[0].participant_id == "p-003"


def test_a_misaligned_student_reaches_the_follow_up_list():
    result = aggregate(
        [
            participant(1, {"I": 9, "C": 7}),
            participant(2, {"I": 9, "C": 7}),
            participant(3, {"E": 9, "A": 7}),
        ],
        statuses(confirmed=3),
    )

    assert [f.full_name for f in result.follow_up] == ["Student 003"]


def test_a_student_qualifying_twice_is_listed_once():
    """An undifferentiated student in a field they do not match appears once,
    under the more fundamental finding."""
    result = aggregate(
        [
            participant(1, {"I": 9, "C": 7}),
            participant(2, {"I": 9, "C": 7}),
            participant(3, dict.fromkeys(RIASEC, 5), band="undifferentiated"),
        ],
        statuses(confirmed=3),
    )

    names = [f.full_name for f in result.follow_up]
    assert names.count("Student 003") == 1


def test_the_follow_up_reason_never_reads_as_a_verdict():
    """§9: the report says "classed misaligned by this instrument", never "in the
    wrong field". R7 forbids clinical framing anywhere in report copy."""
    result = aggregate(
        [
            participant(1, {"I": 9, "C": 7}),
            participant(2, {"I": 9, "C": 7}),
            participant(3, {"E": 9, "A": 7}),
            participant(4, dict.fromkeys(RIASEC, 5), band="undifferentiated"),
        ],
        statuses(confirmed=4),
    )

    for entry in result.follow_up:
        lowered = entry.reason.lower()
        for word in ("wrong", "unsuitable", "fail", "poor", "bad", "disorder"):
            assert word not in lowered, f"{entry.reason!r} reads as a verdict"


def test_an_excluded_student_never_reaches_the_follow_up_list():
    """They were dropped from the aggregate for response quality (§7.4). Naming
    them on the page a counsellor acts from would put a flag beside a name by the
    back door."""
    result = aggregate(
        [
            participant(1, {"I": 8, "S": 6}),
            participant(2, {"I": 8, "S": 6}),
            participant(
                3,
                dict.fromkeys(RIASEC, 5),
                band="undifferentiated",
                warn_flags=2,
                flag_codes=("straightlining", "too_fast"),
            ),
        ],
        statuses(confirmed=3),
    )

    assert [f.full_name for f in result.follow_up] == []


# ── fields with no intended_field ────────────────────────────────────────────


def test_students_without_an_intended_field_form_no_group():
    """A centroid over students who share only a blank column is not a field
    profile, and congruence against it would be arithmetic with no meaning."""
    result = aggregate(
        [
            participant(1, {"I": 8}, intended_field=None),
            participant(2, {"I": 8}, intended_field=None),
        ],
        statuses(confirmed=2),
    )

    assert result.fields == []
    assert result.congruence_denominator == 0
    assert result.included == 2, "they still count toward the cohort's distributions"


def test_fields_are_reported_in_a_stable_order():
    """Sorted by name, so two runs of the same cohort produce the same document."""
    result = aggregate(
        [
            participant(1, {"I": 8}, intended_field="medicine"),
            participant(2, {"R": 8}, intended_field="engineering"),
            participant(3, {"C": 8}, intended_field="business_commerce"),
        ],
        statuses(confirmed=3),
    )

    assert [g.field for g in result.fields] == [
        "business_commerce",
        "engineering",
        "medicine",
    ]

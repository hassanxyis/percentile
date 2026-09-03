"""Response-quality flags (plan §17.1, §7.4)."""

from datetime import datetime, timedelta

from app.scoring.flags import compute_flags, warn_count

from .conftest import interest_items, personality_items, uniform


def codes(flags: list[dict]) -> set[str]:
    return {f["code"] for f in flags}


def varied(items) -> dict[str, int]:
    """Responses that cycle 0,1,2,3 so no long identical run exists."""
    return {item.code: i % 4 for i, item in enumerate(items)}


def consistent(items) -> dict[str, int]:
    """A coherent responder: someone who genuinely scores high on every domain.

    Forward items get 4/5, reverse items get the mirrored 2/1, so the two
    directions agree once re-keyed. The values still alternate, so this does not
    trip straightlining either. `varied` cannot be used for personality items —
    its 4-cycle lines up with the alternating reverse-keying and manufactures a
    disagreement that no real student produced.
    """
    responses = {}
    for i, item in enumerate(items):
        high = 4 + (i % 2)  # 4, 5, 4, 5, ...
        responses[item.code] = (
            item.response_max + item.response_min - high if item.reverse_keyed else high
        )
    return responses


# ── straightlining ───────────────────────────────────────────────────────────


def test_twelve_identical_flags():
    items = interest_items()
    responses = varied(items)
    for item in items[:12]:
        responses[item.code] = 2

    assert "straightlining" in codes(compute_flags(responses, items))


def test_eleven_identical_does_not_flag():
    """The boundary is exact: 11 is a plausible run of genuine answers."""
    items = interest_items()
    responses = varied(items)
    for item in items[:11]:
        responses[item.code] = 2
    # Ensure the 12th differs so the run stops at 11.
    responses[items[11].code] = 3

    assert "straightlining" not in codes(compute_flags(responses, items))


# ── too_fast ─────────────────────────────────────────────────────────────────


def test_fast_median_flags():
    items = interest_items()
    responses = uniform(items, 2)
    timings = {item.code: 400 for item in items}

    flags = compute_flags(responses, items, ms_elapsed=timings)

    assert "too_fast" in codes(flags)


def test_unhurried_session_does_not_flag():
    items = interest_items()
    responses = varied(items)
    # 60 items × 10 s = 10 minutes, comfortably past both thresholds.
    timings = {item.code: 10_000 for item in items}

    assert "too_fast" not in codes(compute_flags(responses, items, ms_elapsed=timings))


def test_missing_timings_produce_no_flag():
    """A rescored old session may have no timing data. Absence of a signal must
    not become a false accusation."""
    items = interest_items()

    assert "too_fast" not in codes(compute_flags(varied(items), items, ms_elapsed=None))


# ── inconsistent pairs ───────────────────────────────────────────────────────


def test_inconsistent_forward_and_reverse_flags():
    """Answering 5 to every item means forward items average 5 while reverse
    items, re-keyed, average 1 — a four-point disagreement."""
    items = personality_items()

    flags = compute_flags(uniform(items, 5), items)

    assert "inconsistent_pairs" in codes(flags)


def test_consistent_answers_do_not_flag():
    """Mid-scale answers agree once re-keyed."""
    items = personality_items()

    assert "inconsistent_pairs" not in codes(compute_flags(uniform(items, 3), items))


def test_strong_but_coherent_profile_does_not_flag():
    """Scoring high on a domain is not inconsistency.

    The check must fire on students who contradict themselves, not on students
    with a pronounced profile — those are exactly the students the report is
    most useful for.
    """
    items = personality_items()

    assert "inconsistent_pairs" not in codes(compute_flags(consistent(items), items))


# ── long gap ─────────────────────────────────────────────────────────────────


def test_long_gap_is_info_not_warn():
    """The answers may be fine; the counsellor should simply know it was not
    one sitting."""
    items = interest_items()
    start = datetime(2026, 9, 1, 9, 0)

    flags = compute_flags(
        varied(items), items, started_at=start, submitted_at=start + timedelta(hours=72)
    )

    gap = next(f for f in flags if f["code"] == "long_gap")
    assert gap["severity"] == "info"


def test_same_day_submission_does_not_flag():
    items = interest_items()
    start = datetime(2026, 9, 1, 9, 0)

    flags = compute_flags(
        varied(items), items, started_at=start, submitted_at=start + timedelta(hours=1)
    )

    assert "long_gap" not in codes(flags)


# ── fallback card sort ───────────────────────────────────────────────────────


def test_tap_fallback_is_info_only():
    """The tap-to-place fallback is a first-class interface, not a failure —
    it is the real interface for many mid-range Android students (plan §12)."""
    items = interest_items()

    flags = compute_flags(varied(items), items, sort_used_fallback=True)

    sort_flag = next(f for f in flags if f["code"] == "incomplete_sort")
    assert sort_flag["severity"] == "info"


# ── exclusion rule ───────────────────────────────────────────────────────────


def test_two_warns_trigger_exclusion_threshold():
    """Straightlining plus too_fast is two warns — the cohort report excludes
    this participant and must say so (plan §7.4)."""
    items = interest_items()
    responses = uniform(items, 2)
    timings = {item.code: 300 for item in items}

    flags = compute_flags(responses, items, ms_elapsed=timings)

    assert warn_count(flags) >= 2


def test_info_flags_alone_do_not_exclude():
    items = interest_items()
    start = datetime(2026, 9, 1, 9, 0)

    flags = compute_flags(
        varied(items),
        items,
        started_at=start,
        submitted_at=start + timedelta(hours=72),
        sort_used_fallback=True,
    )

    assert len(flags) == 2
    assert warn_count(flags) == 0


def test_clean_session_has_no_flags():
    """A varied, unhurried session produces nothing.

    The 6-minute floor is a whole-assessment threshold, so `compute_flags` must
    be handed every item the student answered. Passing a single module's items
    would put a genuine session under the floor and flag it falsely.
    """
    items = interest_items() + personality_items()
    timings = {item.code: 8_000 for item in items}

    assert compute_flags(consistent(items), items, ms_elapsed=timings) == []

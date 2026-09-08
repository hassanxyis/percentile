"""The cohort report's data assembly (plan §10, §14, M11).

Two tests here carry the weight, and they pull in opposite directions on
purpose:

* `test_no_flag_detail_string_reaches_the_cohort_report` — this is the
  institution's copy, so R8 permits more than the student's own report carries.
  It does not permit a sentence like "31 identical consecutive answers" beside a
  named minor in a document a school files.
* `test_the_follow_up_list_names_students` — §10 requires named lists. Asserted
  positively, so a later over-correction toward R8 that strips the names fails
  here rather than quietly producing a page nobody can act on.

The third is `test_a_small_group_never_produces_a_bare_percentage`: §9's rule is
the easiest one in this document to violate with a `{{ x }}%` in a template, and
the result looks authoritative.

Unlike `test_report_student.py`, this file loads the REAL `interpretations.yaml`
rather than a fixture of marker strings. That is the difference M11 has from M9:
the cohort report's text is page furniture and ships written, so the shipped file
is the honest thing to render against — and a missing key fails here rather than
in production.
"""

from __future__ import annotations

import re

import pytest

from app.content import INTERPRETATIONS_PATH, InterpretationMissing, load_interpretations
from app.matching.cohort import Participant, aggregate
from app.report.cohort import CohortReportInput, build_cohort_context
from app.report.render import COHORT_STYLESHEET, COHORT_TEMPLATE, render_html
from app.report.student import Organisation, ReportError
from app.scoring.interests import RIASEC

# Every flag `flags.py` can emit, with the detail strings it writes beside them.
# Reused from the student report's R8 test — the same strings, asserted against a
# different document for a different reason.
from tests.test_report_student import EVERY_FLAG


def written():
    """The real shipped file. See the module docstring for why."""
    return load_interpretations(INTERPRETATIONS_PATH)


def participant(
    n: int,
    interests: dict[str, int] | None = None,
    intended_field: str | None = "medicine",
    band: str = "well_differentiated",
    warn_flags: int = 0,
    flag_codes: tuple[str, ...] = (),
    personality_bands: dict[str, str] | None = None,
) -> Participant:
    return Participant(
        participant_id=f"p-{n:03d}",
        full_name=f"Student {n:03d}",
        intended_field=intended_field,
        status="confirmed",
        interests={scale: (interests or {}).get(scale, 0) for scale in RIASEC},
        band=band,
        personality_bands=personality_bands or dict.fromkeys("OCEAS", "average"),
        warn_flags=warn_flags,
        flag_codes=flag_codes,
    )


def statuses(**counts: int) -> list[str]:
    return [status for status, n in counts.items() for _ in range(n)]


def report(participants=None, all_statuses=None, **overrides) -> CohortReportInput:
    participants = participants or [
        participant(n, {"I": 8, "S": 6, "C": 4}) for n in range(1, 4)
    ]
    all_statuses = all_statuses or statuses(confirmed=len(participants))

    defaults = dict(
        cohort_name="Class of 2027 — Pre-Medical A",
        organisation=Organisation(name="Demo Academy", brand_hex="#1C6A61", logo_path=None),
        intake_year=2027,
        education_level="intermediate",
        generated_on="12 March 2026",
        aggregate=aggregate(participants, all_statuses),
        engine_version="1.0.0",
        template_version="1.0.0",
    )
    defaults.update(overrides)
    return CohortReportInput(**defaults)


def html(**kwargs) -> str:
    return render_html(
        build_cohort_context(report(**kwargs), written()),
        COHORT_TEMPLATE,
        COHORT_STYLESHEET,
    )


def body(rendered: str) -> str:
    """The document without its inlined stylesheet.

    `render_html` inlines the whole CSS file into a `<style>` block, and that
    CSS is full of `width: 100%` and `max-width: 46%`. A percentage assertion
    over the raw string therefore matches the stylesheet and never reaches the
    question being asked, which is about what a reader sees on the page.

    Stripping rather than loosening the regex: the §9 rule is "no bare
    percentage anywhere in the document", and the honest way to keep that
    assertion absolute is to remove the part of the string that is not the
    document.
    """
    return re.sub(r"<style>.*?</style>", "", rendered, flags=re.DOTALL)


# ── R8: what this document may and may not carry ─────────────────────────────


def test_no_flag_detail_string_reaches_the_cohort_report():
    """R8's boundary for the institution's copy.

    The excluded page states how many students were left out and which checks
    fired (§7.4). It does not reproduce the per-student sentence: "31 identical
    consecutive answers" printed beside a named minor, in a document a school
    files and circulates, is the accusation R8 exists to prevent — one step
    removed from putting it on the student's own report.

    `Participant` has no field a detail string could arrive in, so this asserts
    the consequence of a structural absence rather than a filter.
    """
    excluded = participant(
        9,
        {"I": 8},
        warn_flags=2,
        flag_codes=tuple(flag["code"] for flag in EVERY_FLAG),
    )
    rendered = html(
        participants=[participant(n, {"I": 8, "S": 6}) for n in (1, 2, 3)] + [excluded],
        all_statuses=statuses(confirmed=4),
    )

    for flag in EVERY_FLAG:
        assert flag["detail"] not in rendered, (
            f"the detail string for {flag['code']} reached the institution's copy"
        )

    # The codes and the count DO appear — that is §7.4's disclosure.
    assert "straightlining" in rendered
    assert "1 of 4" in rendered


def test_an_excluded_student_is_never_named():
    """§7.4 asks how many were excluded and why, not who.

    A name beside a response-quality code answers a question nobody asked, about
    a minor, in a document that circulates inside a school.
    """
    excluded = participant(
        9, {"I": 8}, warn_flags=2, flag_codes=("straightlining", "too_fast")
    )
    rendered = html(
        participants=[participant(n, {"I": 8, "S": 6}) for n in (1, 2, 3)] + [excluded],
        all_statuses=statuses(confirmed=4),
    )

    assert "Student 009" not in rendered


def test_the_input_has_no_field_for_notes_or_a_reviewer():
    """§16 treats interview notes like health data; they are not on this
    document in any form, and there is deliberately nowhere to put them."""
    built = report()

    assert not hasattr(built, "interview_notes")
    assert not hasattr(built, "reviewer_name")
    assert not hasattr(built, "interview_mode")


def test_the_follow_up_list_names_students():
    """§10 requires "named lists of students to follow up".

    Asserted positively. A later over-correction toward R8 — stripping names
    from this page — must fail here rather than quietly producing a list a
    counsellor cannot act on.
    """
    rendered = html(
        participants=[
            participant(1, {"I": 8, "S": 6}),
            participant(2, {"I": 8, "S": 6}),
            participant(3, dict.fromkeys(RIASEC, 5), band="undifferentiated"),
        ],
        all_statuses=statuses(confirmed=3),
    )

    assert "Student 003" in rendered
    assert "no clear interest profile" in rendered


# ── §9: statistical honesty, mechanically ────────────────────────────────────


def test_a_small_group_never_produces_a_bare_percentage():
    """§9: no percentage on a denominator below ten.

    Three students is a realistic pilot field group. "100% of this field is
    aligned" describing three people is the most misleading sentence this report
    could print, and it would look authoritative.
    """
    rendered = body(html())

    assert "%" not in rendered, "a percentage appeared on a cohort of three"
    assert "3 of 3" in rendered


def test_every_percentage_carries_its_denominator():
    """Above ten a percentage is allowed — never alone.

    Regex over the whole rendered document rather than over one page: the rule
    holds everywhere or it does not hold.
    """
    participants = [participant(n, {"I": 8, "S": 6, "C": 4}) for n in range(1, 15)]
    rendered = body(
        render_html(
            build_cohort_context(
                report(participants=participants, all_statuses=statuses(confirmed=14)),
                written(),
            ),
            COHORT_TEMPLATE,
            COHORT_STYLESHEET,
        )
    )

    for match in re.finditer(r"(\d+) of (\d+) \((\d+)%\)", rendered):
        count, total, percent = (int(g) for g in match.groups())
        assert total >= 10, "a percentage was printed on a denominator below ten"
        assert percent == round(100 * count / total)

    # And no percentage appears that is not part of an "x of n (p%)" string.
    bare = re.sub(r"\d+ of \d+ \(\d+%\)", "", rendered)
    assert "%" not in bare, "a percentage appeared without its denominator"


def test_a_field_chosen_by_one_student_says_so_rather_than_claiming_alignment():
    """The n=1 case, end to end.

    Leave-one-out leaves nothing to compare against, so the group is reported
    with its size and the explanation — never with a congruence figure that is
    1.0 by construction.
    """
    rendered = body(
        html(
            participants=[
                participant(1, {"I": 8}, intended_field="medicine"),
                participant(2, {"R": 9}, intended_field="engineering"),
            ],
            all_statuses=statuses(confirmed=2),
        )
    )

    assert "1 student" in rendered
    assert "no one to compare them against" in rendered
    assert "100%" not in rendered


# ── §10: the thresholds must be visible ──────────────────────────────────────


def test_the_provisional_thresholds_are_printed_on_the_method_page():
    """§10 requires the thresholds on the method page.

    Printed from the same constants `congruence_class` branches on, so the rule
    a reader is shown cannot drift from the rule that was applied. A threshold
    nobody can see is a number a reader has to take on trust.
    """
    rendered = html()

    assert "0.6" in rendered
    assert "0.35" in rendered
    assert "provisional" in rendered.lower()


def test_the_completion_table_shows_the_review_backlog_separately():
    """§10's v2 addition, on the page.

    A school that can see its reports are waiting on a review queue can chase a
    psychologist. Folded into one completion percentage, it cannot.
    """
    rendered = html(
        participants=[participant(n, {"I": 8, "S": 6}) for n in (1, 2, 3)],
        all_statuses=statuses(pending_review=8, confirmed=3, invited=1),
    )

    assert "Awaiting review" in rendered
    assert "Complete" in rendered
    assert "8 of 12" in rendered


def test_a_failed_job_does_not_read_as_a_failed_student():
    """R7, and the same wording `web/lib/roster.ts` shows on screen.

    `failed` means a job broke. "Failed" printed beside a count of students in a
    document sent to a school reads as something the students did.
    """
    rendered = html(
        participants=[participant(n, {"I": 8, "S": 6}) for n in (1, 2, 3)],
        all_statuses=statuses(confirmed=3, failed=2),
    )

    assert "Needs attention" in rendered
    assert ">Failed<" not in rendered


# ── R3 and R6 ────────────────────────────────────────────────────────────────


def test_every_cohort_string_this_template_asks_for_ships_written():
    """What makes M11 renderable today where M9 is not.

    The cohort report's text is page furniture, method description and threshold
    explanation — written to an institution about a group, never clinical
    judgement about a person — so it ships filled in. This is the test that fails
    if a new `report.cohort.*` key is added blank.
    """
    html()  # raises InterpretationMissing if any key is unwritten


def test_a_blanked_cohort_string_raises_rather_than_rendering_a_gap():
    """R3's mechanism applies here too, even though the text ships written.

    A blank string must stop the render rather than leave a heading over
    nothing — the same rule the student report is held to.
    """
    incomplete = written()
    incomplete._data["report"]["cohort"]["method"]["body"] = ""  # noqa: SLF001

    with pytest.raises(InterpretationMissing, match="report.cohort.method.body"):
        build_cohort_context(report(), incomplete)


def test_attribution_is_in_a_running_footer_on_every_page():
    """R6 is a licence term over pages, and a page count is not knowable from
    HTML — so the assertion is on the mechanism that guarantees it."""
    rendered = html()

    assert "string-set: attribution-short content()" in rendered
    assert "content: string(attribution-short)" in rendered
    assert "O*NET" in rendered
    assert "U.S. Department of Labor" in rendered


def test_no_get2_attribution_appears_on_a_report_that_did_not_administer_it():
    """R10: crediting a licensor for an instrument nobody ran is both wrong and
    unhelpful to a licence still being negotiated."""
    assert "Caird" not in html()


# ── charts and escaping ──────────────────────────────────────────────────────


def test_the_charts_render_as_markup_rather_than_as_escaped_text():
    """This shipped once on the student report and passed every content
    assertion, because the words were all still there."""
    rendered = html()

    assert "&lt;svg" not in rendered, "a chart was escaped instead of rendered"
    assert "<polygon" in rendered, "the field centroid hexagon is missing"
    assert "<rect" in rendered, "the distribution bars are missing"


def test_a_student_name_containing_markup_is_escaped_on_the_follow_up_list():
    """`full_name` is human-typed roster data, and this is the one report that
    prints other people's names."""
    hostile = Participant(
        participant_id="p-666",
        full_name="<script>alert(1)</script>",
        intended_field="medicine",
        status="confirmed",
        interests=dict.fromkeys(RIASEC, 5),
        band="undifferentiated",
        personality_bands=dict.fromkeys("OCEAS", "average"),
    )
    rendered = html(
        participants=[participant(1, {"I": 8}), participant(2, {"I": 8}), hostile],
        all_statuses=statuses(confirmed=3),
    )

    assert "<script>alert(1)</script>" not in rendered
    assert "&lt;script&gt;" in rendered


def test_a_school_name_with_an_ampersand_does_not_break_the_document():
    """Real school names contain `&`, and it reaches the template from a form."""
    rendered = render_html(
        build_cohort_context(
            report(
                organisation=Organisation(
                    name='St. Mary\'s "Convent" & College',
                    brand_hex=None,
                    logo_path=None,
                )
            ),
            written(),
        ),
        COHORT_TEMPLATE,
        COHORT_STYLESHEET,
    )

    assert "&amp;" in rendered


def test_a_hostile_brand_colour_cannot_inject_markup():
    """`brand_hex` is typed into the settings form and lands in an SVG attribute."""
    rendered = render_html(
        build_cohort_context(
            report(
                organisation=Organisation(
                    name="Demo", brand_hex='#fff" onload="alert(1)', logo_path=None
                )
            ),
            written(),
        ),
        COHORT_TEMPLATE,
        COHORT_STYLESHEET,
    )

    assert "onload" not in rendered


def test_the_school_logo_appears_when_one_is_set():
    rendered = render_html(
        build_cohort_context(
            report(
                organisation=Organisation(
                    name="Demo Academy",
                    brand_hex="#1C6A61",
                    logo_path="org-1/logo.png",
                    logo_url="https://storage.test/branding/org-1/logo.png",
                )
            ),
            written(),
        ),
        COHORT_TEMPLATE,
        COHORT_STYLESHEET,
    )

    assert 'src="https://storage.test/branding/org-1/logo.png"' in rendered


# ── refusing to build ────────────────────────────────────────────────────────


def test_a_cohort_with_no_included_participants_refuses_to_build():
    """Eight pages of zeros presented as a cohort profile is worse than a failed
    job. The aggregate refuses first; this is the backstop at the last point
    before a PDF exists."""
    empty = aggregate([participant(1, {"I": 8})], statuses(confirmed=1))
    object.__setattr__(empty, "included", 0)

    with pytest.raises(ReportError, match="at least one included participant"):
        build_cohort_context(report(aggregate=empty), written())


def test_the_student_report_still_renders_unchanged():
    """`render_html` gained template arguments for this milestone.

    They default to the student report's, so M9's callers are untouched — but
    "untouched by construction" is worth one assertion, since a wrong default
    would render every student a cohort report.
    """
    from app.report.student import build_context
    from tests.test_report_student import _report, _written

    rendered = render_html(build_context(_report(), _written()))

    assert "Your interests and how you work" in rendered

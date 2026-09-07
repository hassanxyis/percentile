"""The student report's data assembly (plan §14, M9).

The centre of this file is `test_no_quality_flag_reaches_the_student_report`.
R8 says flags appear on the psychologist's screen and never on the student's
copy, and this is the test that holds that line — it builds a session carrying
every flag `flags.py` can emit and asserts none of them appear anywhere in the
rendered HTML.

The rest divide into three:

  * R3 — the loader raises rather than filling a gap with a default
  * R10 — the GET2 page is omitted entirely, not shown blank
  * R6 — attribution appears, with the wording the licensor requires
"""

from __future__ import annotations

import pytest

from app.content import InterpretationMissing, Interpretations
from app.report.render import render_html
from app.report.student import (
    Direction,
    Match,
    Organisation,
    ReportError,
    StudentReportInput,
    build_context,
)

# ── a fully written interpretations file ─────────────────────────────────────
#
# Marker strings, not prose. Every value is "<key> text" so an assertion can
# name exactly which string reached the page — and so nothing in this file can
# be mistaken for the human-written copy R3 requires. The real file ships empty
# on purpose (app/content/interpretations.yaml).


def _written() -> Interpretations:
    return Interpretations(
        {
            "interests": {
                "letters": {letter: f"letter {letter} text" for letter in "RIASEC"},
                "bands": {
                    "well_differentiated": "well differentiated text",
                    "moderate": "moderate band text",
                    "undifferentiated": "undifferentiated band text",
                },
                "code_provisional": "code provisional text",
                "tie_broken": "tie broken text",
            },
            "personality": {
                "domains": {
                    domain: {
                        band: f"{domain} {band} text"
                        for band in ("very low", "low", "average", "high", "very high")
                    }
                    for domain in ("O", "C", "E", "A", "S")
                },
                "provisional_note": "personality provisional note",
            },
            "get2": {
                "subscales": {
                    scale: f"get2 {scale} text"
                    for scale in (
                        "achievement",
                        "autonomy",
                        "creativity",
                        "risk_taking",
                        "control",
                    )
                },
                "bands": {
                    "emerging": "emerging text",
                    "moderate": "get2 moderate text",
                    "strong": "strong text",
                },
                "flag_note": "get2 flag note",
            },
            "cross": {
                "intro": "cross intro",
                "aligned": "cross aligned text",
                "tension": "cross tension text",
                "unclear": "cross unclear text",
            },
            "questions": {"prompts": ["first question", "second question"]},
            "report": {
                "how_to_read": {
                    "title": "How to read this report",
                    "body": "how to read body",
                    "limits": "how to read limits",
                },
                "directions": {"title": "Your confirmed directions", "body": "directions body"},
                "matches": {
                    "title": "Occupations to look into",
                    "body": "matches body",
                    "entrepreneurial_note": "entrepreneurial note",
                },
                "method": {"title": "Method and attribution", "body": "method body"},
                "confidential": "Private — for the named student and their counsellor.",
            },
            "attribution": {
                "onet": (
                    "This report includes information from O*NET Resource Center by the "
                    "U.S. Department of Labor, Employment and Training Administration "
                    "(USDOL/ETA). Used under the CC BY 4.0 license. O*NET® is a trademark "
                    "of USDOL/ETA. Percentile has modified all or some of this "
                    "information. USDOL/ETA has not approved, endorsed, or tested these "
                    "modifications."
                ),
                "onet_licence_url": "https://creativecommons.org/licenses/by/4.0/",
                "ipip": "IPIP public domain text",
                "get2": "GET2 Caird Open University text",
                "short": "O*NET® USDOL/ETA, CC BY 4.0 · IPIP public domain",
            },
        }
    )


def _report(**overrides) -> StudentReportInput:
    defaults = dict(
        student_name="Fatima Khan",
        organisation=Organisation(name="Demo Academy", brand_hex="#1C6A61", logo_path=None),
        cohort_name="Class of 2027 — Pre-Medical A",
        interests={
            "raw": {"R": 2, "I": 8, "A": 3, "S": 7, "E": 4, "C": 6},
            "code": "ISC",
            "differentiation": 6,
            "band": "well_differentiated",
        },
        personality={
            "raw": {"O": 41, "C": 33, "E": 22, "A": 38, "S": 27},
            "bands": {"O": "high", "C": "average", "E": "low", "A": "high", "S": "low"},
        },
        get2={"instrument": None, "status": "module_not_administered"},
        matches=[
            Match(
                rank=1,
                title="Registered Nurses",
                local_title="Registered Nurse",
                local_pathway="FSc Pre-Medical → BSc Nursing",
                onet_soc_code="29-1141.00",
            ),
            Match(
                rank=2,
                title="Software Developers",
                local_title=None,
                local_pathway=None,
                onet_soc_code="15-1252.00",
            ),
        ],
        directions=[
            Direction(
                rank=1,
                local_title="Nursing",
                rationale="Matches both her stated goal and the Social part of her profile.",
                onet_soc_code="29-1141.00",
            )
        ],
        confirmed_on="12 March 2026",
        engine_version="1.0.0",
        template_version="1.0.0",
    )
    defaults.update(overrides)
    return StudentReportInput(**defaults)


# ── R8: no quality flag reaches the student ──────────────────────────────────

# Every code `flags.py` can emit, with the detail strings it writes beside them.
EVERY_FLAG = [
    {"code": "straightlining", "severity": "warn", "detail": "31 identical consecutive answers"},
    {"code": "too_fast", "severity": "warn", "detail": "2.1 min total, 410 ms median per item"},
    {
        "code": "inconsistent_pairs",
        "severity": "warn",
        "detail": "forward and reverse items disagree: C (2.4)",
    },
    {"code": "long_gap", "severity": "info", "detail": "3.2 days between starting and submitting"},
    {
        "code": "get2_uniform_response",
        "severity": "warn",
        "detail": "all five GET2 subscales within 3 points of each other",
    },
]


def test_no_quality_flag_reaches_the_student_report():
    """R8: flags are for the psychologist's screen, never the student's copy.

    The strongest form of this assertion available: `StudentReportInput` has no
    `flags` field at all, so a session's flags cannot be threaded into the
    context even by mistake. This test proves the consequence — that no flag
    code, and no flag detail string, appears in the rendered document.

    A student reading "31 identical consecutive answers" on their own report is
    being told the system thinks they cheated, in a document they were given as
    help. That is the harm R8 exists to prevent.
    """
    assert not hasattr(_report(), "flags"), (
        "StudentReportInput must not carry flags — R8 keeps them off this document"
    )

    html = render_html(build_context(_report(), _written()))

    for flag in EVERY_FLAG:
        assert flag["code"] not in html, f"flag code {flag['code']} reached the student report"
        assert flag["detail"] not in html, f"flag detail for {flag['code']} reached the report"

    # The words a flag would arrive wrapped in, independent of any code above.
    for word in ("straightlin", "inconsistent", "flagged", "quality flag"):
        assert word not in html.lower(), f"flag vocabulary {word!r} reached the student report"


def test_interview_notes_have_no_field_to_arrive_in():
    """§16: interview_notes are treated like health data — never in this report.

    Only the rationale line the psychologist explicitly chose for a direction
    appears (§9.1). There is deliberately no notes field on the input struct.
    """
    report = _report()
    assert not hasattr(report, "interview_notes")
    assert not hasattr(report, "interview_mode")
    assert not hasattr(report, "reviewer_name")


# ── R3: a missing string raises rather than rendering a gap ──────────────────


def test_a_missing_interpretation_raises_rather_than_rendering_a_gap():
    """The rule that makes R3 mechanical instead of aspirational.

    A blank string in `interpretations.yaml` must stop the render. If it
    rendered as an empty paragraph, a student would read the silence as the
    system having nothing to say about them — and nobody would find out.
    """
    incomplete = _written()
    incomplete._data["interests"]["letters"]["I"] = ""  # noqa: SLF001 — building the failure case

    with pytest.raises(InterpretationMissing, match="interests.letters.I"):
        build_context(_report(), incomplete)


def test_the_shipped_interpretations_file_is_unwritten():
    """The file in the repo must NOT contain generated interpretation text (R3).

    This is a guard against a well-meaning future commit — including one by a
    coding agent — filling the blanks in. A person with psychology training
    writes them; until then the empty file is the correct state, and a render
    failing loudly is the intended behaviour.

    If you are reading this because the test failed: if a psychologist wrote the
    text, delete this test in the same commit. If a model wrote it, revert.
    """
    from app.content import INTERPRETATIONS_PATH, load_interpretations

    missing = load_interpretations(INTERPRETATIONS_PATH).missing_keys()
    interpretive = [key for key in missing if not key.startswith(("report.", "attribution."))]

    assert interpretive, (
        "every interpretive string in interpretations.yaml is filled in — if a "
        "psychologist wrote them, remove this test; if a model did, revert (R3)"
    )


def test_structural_and_attribution_text_is_written():
    """The furniture and the licence text are NOT interpretation, and must exist.

    Page headings, the method description and the O*NET wording are not clinical
    judgement — they are shipped filled in. A blank here is a broken report, not
    work waiting for a psychologist.
    """
    from app.content import INTERPRETATIONS_PATH, load_interpretations

    missing = load_interpretations(INTERPRETATIONS_PATH).missing_keys()
    structural = [key for key in missing if key.startswith(("report.", "attribution."))]

    assert structural == [], f"structural text must ship written, missing: {structural}"


# ── R10: the GET2 page is omitted entirely, not shown blank ──────────────────


def test_get2_page_is_absent_when_the_module_was_not_administered():
    """§14 p.7: "page omitted entirely (not shown blank)".

    `None`, not an empty dict — the template's `{% if get2 %}` must not be
    satisfiable by a truthy-but-empty structure. A heading over three empty bars
    tells a student something is missing from their report, which is not what
    happened: the module was never given to them.
    """
    context = build_context(_report(), _written())
    assert context["get2"] is None

    html = render_html(context)
    assert "Starting and running things" not in html
    assert "not administered" not in html.lower()
    assert "entrepreneur" not in html.lower()


def test_get2_page_renders_when_the_module_was_administered():
    report = _report(
        get2={
            "instrument": "GET2",
            "subscales": {
                "achievement": 72,
                "autonomy": 65,
                "creativity": 58,
                "risk_taking": 71,
                "control": 60,
            },
            "get2_total": 65,
            "entrepreneurial_band": "strong",
            "entrepreneurial_flag": True,
        }
    )
    html = render_html(build_context(report, _written()))

    assert "Starting and running things" in html
    assert "get2 achievement text" in html
    assert "strong text" in html
    assert "get2 flag note" in html


def test_get2_attribution_appears_only_when_the_module_ran():
    """R10 and R6 together: credit a licensor only for an instrument used.

    GET2 permission is still provisional (plan §20 item 1). Printing Caird/OU
    attribution on a report that never administered GET2 would be both wrong and
    unhelpful to a licence still being negotiated.
    """
    without = build_context(_report(), _written())
    assert without["attribution"]["get2"] is None
    assert "Caird" not in render_html(without)

    report = _report(
        get2={
            "instrument": "GET2",
            "subscales": dict.fromkeys(
                ("achievement", "autonomy", "creativity", "risk_taking", "control"), 50
            ),
            "get2_total": 50,
            "entrepreneurial_band": "moderate",
            "entrepreneurial_flag": False,
        }
    )
    assert "Caird" in render_html(build_context(report, _written()))


# ── R6: attribution, in the licensor's own words ─────────────────────────────


def test_onet_attribution_uses_the_modified_content_wording():
    """R6 is a licence term, not a courtesy (onetcenter.org/license.html).

    The MODIFIED-content variant is the correct one: Percentile localises
    occupation titles and adds Pakistani study pathways
    (data/local/pk_occupation_map.csv), so USDOL/ETA requires the modification
    be declared and disclaimed. Quoting the unmodified wording would claim the
    data is untouched, which is not true of any row on the matches page.
    """
    html = render_html(build_context(_report(), _written()))

    assert "O*NET Resource Center" in html
    assert "U.S. Department of Labor" in html
    assert "CC BY 4.0" in html
    assert "https://creativecommons.org/licenses/by/4.0/" in html
    # The two halves that make it the modified-content variant.
    assert "has modified all or some of this information" in html
    assert "has not approved, endorsed, or tested these modifications" in html
    # The trademark notice USDOL/ETA requires when naming the resource.
    assert "trademark of USDOL/ETA" in html


def test_the_charts_render_as_markup_rather_than_as_escaped_text():
    """Autoescape turns an SVG string into `&lt;svg...` visible on the page.

    This shipped once. It passes every content assertion — the words are all
    there — and produces a report where each chart is replaced by a wall of
    angle-bracketed source. `{{ ... |safe }}` is what stops it, and this is the
    test that notices if a future edit drops the filter.

    Caught by rendering the HTML and looking at it, which no other test here
    did: `test_report_pdf.py` asserted PDF drawing operators, and a page border
    satisfies those whether or not a chart exists.
    """
    html = render_html(build_context(_report(), _written()))

    assert "&lt;svg" not in html, "the chart was escaped instead of rendered"
    # Two charts on a report with no GET2 page: the interest hexagon and the
    # personality bars.
    assert html.count("<svg") == 2
    assert "<polygon" in html, "the hexagon is missing"
    assert "<rect" in html, "the personality bars are missing"


def test_a_hostile_label_is_still_escaped_inside_the_chart():
    """`|safe` covers the SVG this project generates, not the values in it.

    The filter is the reason this needs its own assertion: marking the chart
    safe would be a hole if `charts.py` interpolated a label unescaped.
    """
    report = _report(
        personality={
            "raw": {"O": 41, "C": 33, "E": 22, "A": 38, "S": 27},
            "bands": dict.fromkeys("OCEAS", "average"),
            "labels": {"O": "<script>alert(1)</script>", "C": "C", "E": "E",
                       "A": "A", "S": "S"},
        }
    )
    html = render_html(build_context(report, _written()))

    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_attribution_is_in_a_running_footer_on_every_page():
    """R6 puts attribution on every report PAGE, not once at the end.

    A page count is not knowable from HTML, so the assertion is on the mechanism
    that guarantees it: a `@page` rule whose content comes from a `string-set`.
    A per-section footer div would be the version that silently loses a page.
    """
    html = render_html(build_context(_report(), _written()))

    assert "string-set: attribution-short content()" in html
    assert "content: string(attribution-short)" in html


# ── the confirmed-direction page (§14 p.10) ──────────────────────────────────


def test_the_psychologist_rationale_is_reproduced_verbatim():
    """§9.1: the rationale is in the psychologist's own words.

    Nothing rewrites, summarises or truncates it. This page is the one a student
    is most likely to keep, and the sentence on it was written by the person who
    sat with them.
    """
    rationale = "Matches both her stated goal and the Social part of her profile."
    html = render_html(build_context(_report(), _written()))
    assert rationale in html


def test_a_report_without_a_confirmed_direction_refuses_to_build():
    """R9's other half.

    The database trigger stops a `reports` ROW without a confirmed review;
    this stops a rendered DOCUMENT without the directions that confirming is
    defined as producing. `save_review` refuses to confirm with zero directions,
    so reaching here means something wrote `confirmed` around it.
    """
    with pytest.raises(ReportError, match="career direction"):
        build_context(_report(directions=[]), _written())


def test_directions_render_in_rank_order():
    report = _report(
        directions=[
            Direction(rank=2, local_title="Health informatics", rationale=None,
                      onet_soc_code=None),
            Direction(rank=1, local_title="Nursing", rationale=None, onet_soc_code=None),
        ]
    )
    context = build_context(report, _written())
    assert [d.rank for d in context["directions"]["chosen"]] == [1, 2]

    html = render_html(context)
    assert html.index("Nursing") < html.index("Health informatics")


# ── occupation matches (§8) ──────────────────────────────────────────────────


def test_local_matches_are_grouped_separately_from_the_rest():
    """§8: Pakistan-relevant results are shown first, as their own group."""
    context = build_context(_report(), _written())

    assert [m.local_title for m in context["matches"]["local"]] == ["Registered Nurse"]
    assert [m.title for m in context["matches"]["other"]] == ["Software Developers"]


def test_the_match_list_is_capped_and_ordered():
    matches = [
        Match(rank=n, title=f"Occupation {n}", local_title=None, local_pathway=None,
              onet_soc_code=f"00-{n:04d}.00")
        for n in range(30, 0, -1)  # deliberately out of order
    ]
    context = build_context(_report(matches=matches), _written())

    shown = context["matches"]["local"] + context["matches"]["other"]
    assert len(shown) == 15
    assert [m.rank for m in shown] == list(range(1, 16))


# ── cross-instrument page (§14 p.11) ─────────────────────────────────────────


def test_cross_pattern_names_a_tension_between_social_interests_and_low_extraversion():
    report = _report(
        interests={"raw": {}, "code": "SEC", "band": "moderate"},
        personality={"raw": {}, "bands": {"O": "average", "C": "average", "E": "very low",
                                          "A": "average", "S": "average"}},
    )
    context = build_context(report, _written())
    assert context["cross"]["pattern"] == "tension"
    assert context["cross"]["text"] == "cross tension text"


def test_cross_pattern_falls_back_to_unclear_rather_than_inventing_agreement():
    """`unclear` is the honest default, not a failure.

    Two instruments not lining up is ordinary. A rule that forced every student
    into "aligned" or "tension" would be manufacturing a finding.
    """
    report = _report(
        interests={"raw": {}, "code": "ARS", "band": "moderate"},
        personality={"raw": {}, "bands": dict.fromkeys("OCEAS", "average")},
    )
    context = build_context(report, _written())
    assert context["cross"]["pattern"] == "unclear"


# ── uncertainty is carried through, not smoothed away ────────────────────────


def test_a_provisional_code_says_so_on_the_page():
    """§7.1: the third letter is unstable when the gap is small. Say it."""
    report = _report(
        interests={
            "raw": {"R": 2, "I": 8, "A": 3, "S": 7, "E": 4, "C": 6},
            "code": "ISC",
            "band": "moderate",
            "code_provisional": True,
            "tie_broken": True,
        }
    )
    html = render_html(build_context(report, _written()))
    assert "code provisional text" in html
    assert "tie broken text" in html


def test_an_undifferentiated_profile_gets_its_own_band_text():
    """§7.1: "the report must say this in words and tell the counsellor to
    explore rather than recommend". The band must not be silently omitted."""
    report = _report(
        interests={"raw": {}, "code": "ISC", "band": "undifferentiated"}
    )
    html = render_html(build_context(report, _written()))
    assert "undifferentiated band text" in html


# ── escaping ─────────────────────────────────────────────────────────────────


def test_a_name_with_an_ampersand_does_not_break_the_document():
    """Real school names contain `&`. Real rationales contain quotes.

    Both reach the template from a form. Unescaped, an ampersand is invalid
    markup and a quote inside an attribute ends it early.
    """
    report = _report(
        student_name="Ali & Sons",
        organisation=Organisation(name='St. Mary\'s "Convent" & College', brand_hex=None,
                                  logo_path=None),
        directions=[
            Direction(rank=1, local_title="Design & media",
                      rationale='She said "I want to make things".', onet_soc_code=None)
        ],
    )
    html = render_html(build_context(report, _written()))

    assert "Ali &amp; Sons" in html
    assert "Design &amp; media" in html
    assert "&#34;I want to make things&#34;" in html or "&quot;I want to make things&quot;" in html


def test_a_hostile_brand_colour_cannot_inject_markup():
    """`brand_hex` is typed into the settings form and lands in an SVG attribute."""
    report = _report(
        organisation=Organisation(
            name="Demo", brand_hex='#fff" onload="alert(1)', logo_path=None
        )
    )
    html = render_html(build_context(report, _written()))
    assert "onload" not in html

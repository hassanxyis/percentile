"""The one test that actually runs WeasyPrint (plan §14, CLAUDE.md · WeasyPrint).

Everything else in the report suite asserts against HTML, which needs no font
stack and runs on any machine. This file exists because that leaves one thing
unproven: that the HTML and CSS this project writes actually survive WeasyPrint
and come out as a PDF with pages in it.

Skipped when WeasyPrint cannot import — which is every Windows machine, since
the Pango/Cairo libraries are not installable with pip. CI's Linux job installs
them (`ci.yml`, the same list as `engine/Dockerfile`), so this runs there, and
CI is therefore the place that catches "works locally, blank PDF in production"
before a student does.

If you are reading this because it failed in CI and passed for you: that is the
failure mode this file was written to catch, not a flaky test.
"""

from __future__ import annotations

import pytest

from app.report.student import build_context

# Import-time, not call-time. A missing font stack is an environment fact, and
# the whole module should skip rather than each test failing on the same import.
#
# NOT `pytest.importorskip`. WeasyPrint's import failure on a machine without
# Pango is an OSError from cffi's `dlopen`, not an ImportError — importorskip
# only catches the latter, so it would let the error through and abort
# collection for the entire suite. Catching Exception is deliberate: any failure
# to load the font stack means the same thing here, and the reason string says
# where to look.
try:
    import weasyprint  # noqa: F401 — imported for its side effect of loading Pango
except Exception as exc:  # pragma: no cover — environment-dependent
    pytest.skip(
        f"WeasyPrint's system libraries are not available ({type(exc).__name__}) — "
        "CI installs them, see engine/Dockerfile",
        allow_module_level=True,
    )

from tests.test_report_student import _report, _written  # noqa: E402 — after the skip guard

# A PDF's page count is in its trailer rather than anywhere convenient. Counting
# `/Type /Page` markers is crude and sufficient: the assertion is "about the
# right number of pages", not an exact layout, which would break on every
# wording change and teach everyone to ignore it.
PAGE_MARKER = b"/Type /Page"


@pytest.fixture(scope="module")
def pdf() -> bytes:
    from app.report.render import render_pdf

    # `uncompressed_pdf` is the point of this whole file. WeasyPrint compresses
    # object streams and FlateDecodes content streams by default, which puts
    # `/Type /Page` and every path operator out of reach of `bytes.count`.
    # Asking for the uncompressed render keeps the exact Jinja2 → HTML →
    # WeasyPrint path production uses, and makes the markers searchable.
    return render_pdf(
        build_context(_report(), _written()), uncompressed_pdf=True
    )


def test_the_report_renders_to_a_real_pdf(pdf: bytes):
    """The end-to-end check: Jinja2 → HTML → WeasyPrint → bytes."""
    assert pdf.startswith(b"%PDF-"), "output is not a PDF"
    assert len(pdf) > 10_000, f"suspiciously small PDF ({len(pdf)} bytes) — missing fonts?"


def test_the_report_is_about_thirteen_pages(pdf: bytes):
    """§14 specifies ~13 pages. A wide band, because content length moves it.

    The failure this catches is structural: one page (everything collapsed into
    a single flow because `page-break-after` stopped working) or fifty (a loop
    running away). Both have shipped in projects like this one.
    """
    pages = pdf.count(PAGE_MARKER)
    assert 9 <= pages <= 20, f"expected roughly 13 pages, got {pages}"


def test_the_get2_page_is_absent_from_the_rendered_pdf():
    """R10 through the whole pipeline, not just the context.

    A page omitted in the template but still allocated by the stylesheet would
    show up here as a blank sheet in the middle of the document — which is what
    §14 means by "not shown blank".
    """
    from app.report.render import render_pdf

    without = render_pdf(
        build_context(_report(), _written()), uncompressed_pdf=True
    )

    with_get2 = render_pdf(
        build_context(
            _report(
                get2={
                    "instrument": "GET2",
                    "subscales": dict.fromkeys(
                        ("achievement", "autonomy", "creativity", "risk_taking", "control"), 60
                    ),
                    "get2_total": 60,
                    "entrepreneurial_band": "moderate",
                    "entrepreneurial_flag": False,
                }
            ),
            _written(),
        ),
        uncompressed_pdf=True,
    )

    assert with_get2.count(PAGE_MARKER) > without.count(PAGE_MARKER), (
        "administering GET2 must add a page; omitting it must not leave a blank one"
    )


def test_the_charts_survive_into_the_pdf(pdf: bytes):
    """Inline SVG is the one thing here that WeasyPrint could silently drop.

    Asserted by SIZE, not by the presence of drawing operators. The first
    version of this test looked for `re` (a PDF rectangle) and passed while
    every chart was being escaped into visible `&lt;svg` text — page borders and
    table rules emit rectangles too, so the check was vacuous.

    A report whose two charts became walls of angle-bracketed source is
    substantially *bigger* as text and carries far fewer path operators. The
    honest structural check is that real vector drawing dominates: a document
    with a hexagon and ten bars has many curve and line operators, and a
    text-only one has almost none.
    """
    curves = pdf.count(b" c\n") + pdf.count(b" c ")
    lines = pdf.count(b" l\n") + pdf.count(b" l ")

    assert curves + lines > 40, (
        f"only {curves} curve and {lines} line operators — the charts were "
        "probably escaped to text rather than drawn"
    )


def test_no_escaped_markup_reaches_the_pdf():
    """The symptom the escaping bug actually produced, checked at the far end.

    If a chart is escaped, the literal string `<svg` is drawn as visible text on
    the page — which is what a student would open. Fonts make the bytes hard to
    search directly, so this asserts the size that betrays it: an escaped chart
    is thousands of characters of source rendered as prose.

    This renders *compressed*, unlike the other tests. The marker tests need
    `uncompressed_pdf` to reach raw bytes; this test is about the artefact a
    student actually downloads, and the escaping bug inflates that far beyond
    any font/compression difference.
    """
    from app.report.render import render_pdf

    stored = render_pdf(build_context(_report(), _written()))
    assert len(stored) < 400_000, (
        f"{len(stored)} bytes is far larger than a 13-page report should be — "
        "a chart may be rendering as escaped source text"
    )


# ── the cohort report (M11) ──────────────────────────────────────────────────
#
# Same skip guard, same reason: these are the only assertions in the suite that
# prove the cohort template survives WeasyPrint, and they run in CI alone. A
# local skip is not a pass.


@pytest.fixture(scope="module")
def cohort_pdf() -> bytes:
    from app.report.render import COHORT_STYLESHEET, COHORT_TEMPLATE, render_pdf
    from tests.test_report_cohort import build_cohort_context, report, written

    return render_pdf(
        build_cohort_context(report(), written()),
        COHORT_TEMPLATE,
        COHORT_STYLESHEET,
        uncompressed_pdf=True,
    )


def test_the_cohort_report_renders_to_a_real_pdf(cohort_pdf: bytes):
    """The second document through the same pipeline.

    `render_pdf` gained template arguments in M11. This is what proves the
    cohort stylesheet is found and applied rather than silently falling back to
    the student one — a failure that would produce a plausible-looking PDF with
    the wrong page furniture.
    """
    assert cohort_pdf.startswith(b"%PDF-"), "output is not a PDF"
    assert len(cohort_pdf) > 10_000, (
        f"suspiciously small PDF ({len(cohort_pdf)} bytes) — missing fonts?"
    )


def test_the_cohort_report_is_about_eight_pages(cohort_pdf: bytes):
    """§14 specifies ~8 pages. A wide band, because content length moves it.

    The failure this catches is structural, exactly as for the student report:
    one page, because `page-break-after` stopped working, or fifty, because a
    per-field or per-domain loop ran away. This document has two such loops.
    """
    pages = cohort_pdf.count(PAGE_MARKER)
    assert 5 <= pages <= 16, f"expected roughly 8 pages, got {pages}"


def test_the_cohort_charts_survive_into_the_pdf(cohort_pdf: bytes):
    """The cohort report carries more charts than the student one — a field
    centroid hexagon per intended field, plus six distribution bar charts."""
    curves = cohort_pdf.count(b" c\n") + cohort_pdf.count(b" c ")
    lines = cohort_pdf.count(b" l\n") + cohort_pdf.count(b" l ")

    assert curves + lines > 40, (
        f"only {curves} curve and {lines} line operators — the charts were "
        "probably escaped to text rather than drawn"
    )

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

    return render_pdf(build_context(_report(), _written()))


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

    without = render_pdf(build_context(_report(), _written()))

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
        )
    )

    assert with_get2.count(PAGE_MARKER) > without.count(PAGE_MARKER), (
        "administering GET2 must add a page; omitting it must not leave a blank one"
    )


def test_the_charts_survive_into_the_pdf(pdf: bytes):
    """Inline SVG is the one thing here that WeasyPrint could silently drop.

    A PDF that rendered every paragraph and no chart would pass every other
    test in this suite and be obviously broken to the first person who opened
    it. Vector drawing operators in the output are the proof it did not.
    """
    # `re` and `f` are PDF path-painting operators — present when something was
    # actually drawn, absent from a text-only document.
    assert b" re" in pdf or b" c\n" in pdf, "no vector drawing in the PDF — SVG was dropped"

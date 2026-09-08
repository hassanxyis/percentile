"""Jinja2 → HTML → WeasyPrint → PDF bytes (plan §14).

The only module in `app/report/` that is not pure, and it is impure in exactly
one way: it imports WeasyPrint, which links against system libraries at import
time. That import is deliberately INSIDE the render function rather than at
module scope, so `student.py` and `charts.py` stay importable — and therefore
testable — on a machine with no Pango. A Windows laptop can run every unit test
in this package; only the render itself needs the font stack.

Fonts ship in the image (`engine/Dockerfile`), never fetched at render time. A
web font over HTTP means the PDF silently falls back and looks wrong only in
production, which is the failure mode CLAUDE.md names.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from markupsafe import Markup

log = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
STUDENT_TEMPLATE = "student.html"
STUDENT_STYLESHEET = "student.css"
COHORT_TEMPLATE = "cohort.html"
COHORT_STYLESHEET = "cohort.css"


def _environment() -> Environment:
    """Jinja2, configured to fail rather than to paper over.

    `StrictUndefined` is the load-bearing setting. Jinja's default renders an
    unknown variable as an empty string — so a typo'd `{{ personailty.chart }}`
    would produce a report with a missing section and no error anywhere. That is
    the same class of silent hole `InterpretationMissing` exists to close, and
    the template deserves the same treatment as the content.
    """
    return Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(["html"]),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )


def render_html(
    context: dict[str, Any],
    template: str = STUDENT_TEMPLATE,
    stylesheet: str = STUDENT_STYLESHEET,
) -> str:
    """The report as HTML. Separated from the PDF step so tests can read it.

    Asserting against this string is how the R8 and R6 tests work: "no flag
    text appears in the output" and "the O*NET attribution appears" are both
    questions about the document, and answering them here needs no font stack
    and no PDF parser.

    The SVG charts are injected unescaped — they are markup this package
    generated, not user input, and `charts.py` escapes the only two values that
    come from outside it (the scale labels and the validated brand hex).

    `template`/`stylesheet` default to the student report's, so M9's callers and
    tests are unchanged by M11 adding a second document. Defaulted rather than
    required for exactly that reason: a required argument would have touched
    every existing call site and made the diff that introduced the cohort report
    also a diff against the student one.
    """
    environment = _environment()
    # `Markup`, not a bare string. Autoescape is on for .html templates, so a
    # plain str here is escaped on its way into `<style>` — every `"` in a
    # font stack becomes `&#34;` and every `'` becomes `&#39;`. WeasyPrint then
    # rejects `font-family: &#34;DejaVu Sans&#34;` as an invalid value and drops
    # the declaration, along with the `@page` footer rules R6 depends on.
    #
    # It shipped that way from M9 and was invisible: every content assertion
    # still passed, because the WORDS were all present — only the styling was
    # gone. The PDF tests are what caught it, which is why they exist.
    #
    # Safe to mark: this is a stylesheet on disk in this package, not user
    # input. The values that DO come from outside (the brand hex) reach the SVG
    # through `charts._colour`, which validates rather than escapes.
    css = Markup((TEMPLATE_DIR / stylesheet).read_text(encoding="utf-8"))
    return environment.get_template(template).render(**context, stylesheet=css)


def render_pdf(
    context: dict[str, Any],
    template: str = STUDENT_TEMPLATE,
    stylesheet: str = STUDENT_STYLESHEET,
    *,
    uncompressed_pdf: bool = False,
) -> bytes:
    """The report as PDF bytes.

    `base_url` is the template directory so that a relative asset path in the
    HTML resolves against the package rather than against the process's working
    directory — which on Render is `/srv/engine` and on a developer's machine is
    wherever they ran pytest from.

    `uncompressed_pdf` is for the PDF tests, not for production. WeasyPrint
    compresses object streams and content streams by default, so raw
    `/Type /Page` and path-operator markers are unreachable from the bytes;
    the tests that assert on them ask for an uncompressed render through this
    switch and stay honest by running the same Jinja2 → HTML → WeasyPrint path
    production uses. Production keeps the default compressed output — a smaller
    object to upload and download.
    """
    from weasyprint import HTML  # imported here — see the module docstring

    html = render_html(context, template, stylesheet)
    pdf = HTML(string=html, base_url=str(TEMPLATE_DIR)).write_pdf(
        uncompressed_pdf=uncompressed_pdf
    )

    if not pdf:
        # WeasyPrint returning empty rather than raising is the documented
        # symptom of a missing system library (CLAUDE.md · WeasyPrint). Fail
        # loudly: an empty file uploaded to storage and emailed as a signed link
        # is a student opening a blank document.
        raise RuntimeError(
            "WeasyPrint produced an empty PDF — check the Pango/Cairo system "
            "libraries listed in engine/Dockerfile"
        )

    # Named by template rather than hard-coded to "student report": once two
    # documents render through here, a log line calling both of them the student
    # report is worse than no log line.
    log.info("rendered %s: %d bytes", template, len(pdf))
    return pdf

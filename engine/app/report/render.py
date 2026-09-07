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

log = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
STUDENT_TEMPLATE = "student.html"
STUDENT_STYLESHEET = "student.css"


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


def render_html(context: dict[str, Any]) -> str:
    """The report as HTML. Separated from the PDF step so tests can read it.

    Asserting against this string is how the R8 and R6 tests work: "no flag
    text appears in the output" and "the O*NET attribution appears" are both
    questions about the document, and answering them here needs no font stack
    and no PDF parser.

    The SVG charts are injected unescaped — they are markup this package
    generated, not user input, and `charts.py` escapes the only two values that
    come from outside it (the scale labels and the validated brand hex).
    """
    environment = _environment()
    stylesheet = (TEMPLATE_DIR / STUDENT_STYLESHEET).read_text(encoding="utf-8")
    template = environment.get_template(STUDENT_TEMPLATE)
    return template.render(**context, stylesheet=stylesheet)


def render_pdf(context: dict[str, Any]) -> bytes:
    """The report as PDF bytes.

    `base_url` is the template directory so that a relative asset path in the
    HTML resolves against the package rather than against the process's working
    directory — which on Render is `/srv/engine` and on a developer's machine is
    wherever they ran pytest from.
    """
    from weasyprint import HTML  # imported here — see the module docstring

    html = render_html(context)
    pdf = HTML(string=html, base_url=str(TEMPLATE_DIR)).write_pdf()

    if not pdf:
        # WeasyPrint returning empty rather than raising is the documented
        # symptom of a missing system library (CLAUDE.md · WeasyPrint). Fail
        # loudly: an empty file uploaded to storage and emailed as a signed link
        # is a student opening a blank document.
        raise RuntimeError(
            "WeasyPrint produced an empty PDF — check the Pango/Cairo system "
            "libraries listed in engine/Dockerfile"
        )

    log.info("rendered student report: %d bytes", len(pdf))
    return pdf

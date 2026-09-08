"""`render_cohort` — the institution's report (plan §10, §14, M11).

Four steps, and the order is the same design `render.py` sets out for the
student report:

    1. load     repository.load_cohort_report  — aggregates, refuses an empty cohort
    2. render   report.render_pdf              — Jinja2 -> HTML -> WeasyPrint
    3. upload   storage, private bucket        — overwrite-on-retry
    4. record   reports row                    — idempotent on (cohort, template)

TWO DIFFERENCES FROM `render.py`, BOTH DELIBERATE.

**No R9 gate.** `render.py` refuses a session without a confirmed review before
it builds anything. R9 is about student reports — the trigger reads
`NEW.kind = 'student'` — and a cohort report is an aggregate over a group that no
psychologist signs off. Gating it would also break the feature §10 added it for:
the completion table exists to show a school its review backlog, so a report that
could not render until every review was confirmed would be unavailable to exactly
the schools that need it.

**No delivery email.** `record_student_report` queues one so the student gets a
signed link. §15 lists no cohort template and a cohort has no single recipient —
the counsellor downloads it from the dashboard, where the URL is minted on click
and lives five minutes.

A separate module from `render.py` rather than a second `handle` beside it: the
two documents follow different R8 rules — the student's carries no flags at all,
the institution's carries flag codes and other students' names — and that is a
pair of rules a reader should not have to hold in one file.
"""

from __future__ import annotations

import logging

from app.config import TEMPLATE_VERSION, Settings
from app.jobs.handlers import HandlerError
from app.jobs.handlers.render import PDF_CONTENT_TYPE, _upload
from app.jobs.queue import Job, record_cohort_report
from app.scoring.types import ScoringError

log = logging.getLogger(__name__)


def handle(client, settings: Settings, job: Job) -> None:
    cohort_id = job.payload.get("cohort_id")
    if not cohort_id:
        raise HandlerError("render_cohort job has no cohort_id in its payload")

    from app.repository import load_cohort_report

    try:
        report = load_cohort_report(client, cohort_id)
    except ScoringError as exc:
        # A cohort with no participants, or none scored yet. Retrying will help
        # once students submit, so this fails through the normal path and backs
        # off rather than being dropped.
        raise HandlerError(f"cannot build a report for cohort {cohort_id}: {exc}") from exc

    # Imported here, not at module scope. Both pull in WeasyPrint's system
    # libraries and the content file; a missing font stack should fail this job,
    # not the import of the whole handler registry.
    from app.content import load_interpretations
    from app.report.cohort import build_cohort_context
    from app.report.render import COHORT_STYLESHEET, COHORT_TEMPLATE, render_pdf
    from app.report.student import ReportError

    try:
        context = build_cohort_context(report, load_interpretations())
    except ReportError as exc:
        raise HandlerError(str(exc)) from exc
    # InterpretationMissing is deliberately NOT caught, exactly as in `render.py`.
    # The cohort report's text ships written, so this means a key was added blank
    # — a failed job that alerts is how that becomes someone's task.

    pdf = render_pdf(context, COHORT_TEMPLATE, COHORT_STYLESHEET)
    path = storage_path(report.organisation.name, cohort_id)
    _upload(client, settings, path, pdf)

    # After the upload, for the reason `render.py`'s header gives:
    # `reports.storage_path` is the only pointer to the file, so a row written
    # first and an upload that then failed is a download button that resolves to
    # nothing.
    try:
        report_id = record_cohort_report(
            client, cohort_id, path, TEMPLATE_VERSION, report.engine_version
        )
    except Exception as exc:
        raise HandlerError(
            f"could not record the report for cohort {cohort_id}: "
            f"{type(exc).__name__}: {exc}"
        ) from exc

    log.info(
        "rendered cohort report for %s: %d bytes at %s (report %s, %d of %d students included)",
        cohort_id,
        len(pdf),
        path,
        report_id,
        report.aggregate.included,
        report.aggregate.total_participants,
    )


def storage_path(organisation_name: str, cohort_id: str) -> str:
    """Where the PDF lives in the bucket.

    Keyed on the cohort id, not on its name. A cohort is named things like
    "Class of 2027 — Pre-Medical A", and object keys appear in storage logs and
    inside the signed URL itself — the same rule `render.py`'s `storage_path`
    follows for students, for the same reason.

    `cohort-` prefixed so a human browsing the bucket can tell the two kinds of
    report apart at a glance; the organisation segment is a slug purely so the
    bucket is navigable.

    Stable for a given cohort, so a re-render overwrites rather than
    accumulating.
    """
    slug = "".join(
        character if character.isalnum() else "-" for character in organisation_name.lower()
    ).strip("-")
    slug = "-".join(part for part in slug.split("-") if part) or "org"
    return f"{slug}/cohort-{cohort_id}.pdf"


# `_upload` and `PDF_CONTENT_TYPE` are imported from `render.py` rather than
# duplicated. The upsert semantics, the `x-upsert`-is-a-string trap and the
# error message that names the bucket are all one behaviour, and two copies
# would drift the moment one of them was fixed.
__all__ = ["PDF_CONTENT_TYPE", "handle", "storage_path"]

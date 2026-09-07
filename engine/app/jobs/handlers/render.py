"""`render_student` — the job M8's Confirm button queues (plan §14, M9).

Five steps, in this order, and the order is the whole design:

    1. load       repository.load_student_report  — refuses without a confirmed review (R9)
    2. render     report.render_pdf               — Jinja2 -> HTML -> WeasyPrint
    3. upload     storage, private bucket         — overwrite-on-retry
    4. record     reports row                     — the R9 trigger checks it again
    5. enqueue    send_email student_report       — a signed URL, never an attachment (R8)

WHY THE `reports` ROW IS WRITTEN AFTER THE UPLOAD, NOT BEFORE.

`reports.storage_path` is the only pointer to the file. A row written first and
an upload that then fails leaves a row promising a PDF that does not exist —
and the email job reads that row. Uploading first can leave an orphaned object
if the insert fails, which costs a few hundred kilobytes and is invisible to
everyone; the reverse costs a student a broken link.

IDEMPOTENCY. The runner retries a job whose worker died after the work but
before `complete_job`, so every step here tolerates having already happened:
the upload upserts, the `reports` insert is skipped when a row for this
(session, template_version) already exists, and the email is skipped when one is
already queued. Without those, a retry double-mails a student.
"""

from __future__ import annotations

import logging

from app.config import TEMPLATE_VERSION, Settings
from app.jobs.handlers import HandlerError
from app.jobs.queue import Job, record_student_report
from app.repository import ReportNotAllowed, load_student_report
from app.scoring.types import ScoringError

log = logging.getLogger(__name__)

PDF_CONTENT_TYPE = "application/pdf"

# Reports are private objects. The bucket must NOT be public: a public bucket
# means a guessable URL is a minor's full psychological profile, with no
# expiry and no audit. Delivery is a signed URL from the email handler (§15).
#
# Long cache-control on a private object is safe — the object is immutable for
# a given (session, template_version) and every reader arrives with a fresh
# signature — and it keeps a re-download off the origin.
CACHE_CONTROL = "3600"


def handle(client, settings: Settings, job: Job) -> None:
    session_id = job.payload.get("session_id")
    if not session_id:
        raise HandlerError("render_student job has no session_id in its payload")

    try:
        report = load_student_report(client, session_id)
    except ReportNotAllowed as exc:
        # R9 held. Retrying cannot help — a psychologist confirming is what
        # changes this, and that enqueues its own job. Failed through the normal
        # path so it exhausts attempts and alerts rather than being dropped: a
        # render job existing without a confirmed review means `save_review`'s
        # guard was bypassed, which CLAUDE.md calls a severity-1 bug.
        raise HandlerError(str(exc)) from exc
    except ScoringError as exc:
        raise HandlerError(f"cannot build report for session {session_id}: {exc}") from exc

    # Imported here, not at module scope. Both pull in WeasyPrint's system
    # libraries and the content file; a missing font stack should fail this job,
    # not the import of the whole handler registry.
    from app.content import load_interpretations
    from app.report.render import render_pdf
    from app.report.student import ReportError, build_context

    try:
        context = build_context(report, load_interpretations())
    except ReportError as exc:
        raise HandlerError(str(exc)) from exc
    # InterpretationMissing is deliberately NOT caught. It means a psychologist
    # has not written a paragraph yet (R3) — the job failing, retrying and
    # alerting is exactly how that becomes someone's task rather than a silent
    # gap on a student's page.

    pdf = render_pdf(context)
    path = storage_path(report.organisation.name, session_id)
    _upload(client, settings, path, pdf)

    # The `reports` row and the delivery email, in one transaction. Both the
    # idempotency guards and the R9 re-check live in the SQL function — see
    # 0010_student_reports.sql for why they are not expressed as PostgREST
    # filters here.
    try:
        report_id = record_student_report(
            client, session_id, path, TEMPLATE_VERSION, report.engine_version
        )
    except Exception as exc:
        raise HandlerError(
            f"could not record the report for session {session_id}: "
            f"{type(exc).__name__}: {exc}"
        ) from exc

    log.info(
        "rendered student report for session %s: %d bytes at %s (report %s)",
        session_id,
        len(pdf),
        path,
        report_id,
    )


def storage_path(organisation_name: str, session_id: str) -> str:
    """Where the PDF lives in the bucket.

    Keyed on the session id, not on the student's name: object keys appear in
    storage logs and in the signed URL itself, and a URL carrying
    "fatima-khan.pdf" leaks who the report is about to anyone it is forwarded
    to. The organisation segment is a slug of the name purely so the bucket is
    navigable by a human in the dashboard.

    Stable for a given session, so a retry overwrites rather than accumulating.
    """
    slug = "".join(
        character if character.isalnum() else "-" for character in organisation_name.lower()
    ).strip("-")
    slug = "-".join(part for part in slug.split("-") if part) or "org"
    return f"{slug}/{session_id}.pdf"


def _upload(client, settings: Settings, path: str, pdf: bytes) -> None:
    """Upsert the object. Overwrites on retry rather than erroring.

    `x-upsert` is a string, not a bool — storage3 passes `file_options`
    through as HTTP headers, and a Python `True` would be sent as "True".
    """
    bucket = settings.report_storage_bucket
    try:
        client.storage.from_(bucket).upload(
            path,
            pdf,
            {
                "content-type": PDF_CONTENT_TYPE,
                "cache-control": CACHE_CONTROL,
                "x-upsert": "true",
            },
        )
    except Exception as exc:
        # The usual cause is that the bucket does not exist yet. Named, because
        # this string lands in `jobs.last_error` and "Bucket not found" without
        # the bucket name sends whoever reads it to the wrong place.
        raise HandlerError(
            f"could not upload the report to storage bucket {bucket!r}: "
            f"{type(exc).__name__}: {exc}"
        ) from exc


# The `reports` row and the delivery email are written by `record_student_report`
# (0010_student_reports.sql), not here. Both the idempotency guards and the R9
# re-check are in that function: a guard whose failure mode is a second email to
# a student must not depend on PostgREST filter syntax this codebase uses nowhere
# else, and in SQL it is an ordinary `where` that CI exercises against Postgres.

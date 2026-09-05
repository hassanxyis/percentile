"""`send_email` — every outbound mail goes through here (plan §12, §15).

The payload's `template` selects the body. Four are implemented; `student_report`
and `reminder` arrive with M9 and M10.

─────────────────────────────────────────────────────────────────────────────
THE INVITE MINTS A NEW TOKEN. THIS IS THE CONSTRAINT M5 SETTLED.

`import_roster()` enqueues `{template:'invite', participant_id}` and deliberately
NOT the token, because `jobs` rows are retried, logged and copied into
`last_error` — no place for a live credential to a minor's record. Only
`sha256(token)` is ever stored, so this handler *cannot* reconstruct the link the
import screen handed out. It mints a fresh one, writes the new hash, and emails
that.

Any link from M5's one-time CSV download stops working the moment this runs.
That is the correct precedence: the emailed invite is the one the student
actually receives (plan §12, `0005_import_roster.sql`).

KNOWN AND ACCEPTED: if the process dies between a successful send and
`complete_job`, the retry mints again. The student gets two mails and only the
second link works. The fix would be to persist the token so the send is
resumable — which is the one thing this design refuses to do. A confusing second
email is a smaller harm than a token at rest.
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import hashlib
import logging
import secrets

from app.config import Settings
from app.email import EmailError, Message, build_emailer
from app.emails import templates
from app.jobs.handlers import HandlerError
from app.jobs.queue import Job

log = logging.getLogger(__name__)

# 32 bytes, base64url — identical to `mintToken()` in
# web/app/dash/cohorts/[id]/upload/actions.ts. The token IS the student's
# authentication (0002_rls.sql), so this is a CSPRNG and 256 bits, not something
# merely long enough to look unguessable. If one side ever changes, both must.
TOKEN_BYTES = 32


def handle(client, settings: Settings, job: Job) -> None:
    template = job.payload.get("template")
    if not template:
        raise HandlerError("send_email job has no template in its payload")

    emailer = build_emailer(settings)

    if template == "invite":
        _send_invite(client, settings, job, emailer)
    elif template == "results_in_review":
        _send_results_in_review(client, settings, job, emailer)
    elif template == "job_failed_alert":
        _send_job_failed_alert(settings, job, emailer)
    elif template == "review_backlog_alert":
        _send_backlog_alert(client, settings, job, emailer)
    else:
        raise HandlerError(f"unknown email template {template!r}")


def _send_invite(client, settings: Settings, job: Job, emailer) -> None:
    participant_id = job.payload.get("participant_id")
    if not participant_id:
        raise HandlerError("invite email has no participant_id")

    participant = _participant(client, participant_id)

    if not participant.get("email"):
        # `full_name` is required by the roster parser but `email` is nullable in
        # the schema. Without one there is nothing to send and nothing to retry.
        raise HandlerError(f"participant {participant_id} has no email address")

    if participant.get("status") not in ("invited", "started"):
        # Re-inviting someone who already submitted would mint a token that
        # reopens a closed assessment. `start_assessment` refuses that anyway,
        # so the link would 'work' and then fail confusingly — better not sent.
        raise HandlerError(
            f"participant {participant_id} has status {participant.get('status')!r}; "
            "not sending an invite"
        )

    token = secrets.token_urlsafe(TOKEN_BYTES)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()

    # Written BEFORE the send. If the update succeeds and the send then fails,
    # the retry mints again and the student gets a working link. Sending first
    # and writing after risks the opposite — a student holding a link whose hash
    # was never stored, which cannot be recovered or diagnosed.
    client.table("participants").update({"invite_token_hash": token_hash}).eq(
        "id", participant_id
    ).execute()

    message = templates.invite(
        to=participant["email"],
        first_name=_first_name(participant["full_name"]),
        organisation=_organisation_name(client, participant_id),
        link=f"{settings.app_base_url.rstrip('/')}/a/{token}",
    )

    # Not wrapped in a try/except that logs the exception. A traceback here would
    # have `token` in a local frame, and this handler's failure text goes to
    # `jobs.last_error`. Let EmailError's own message through — it says what the
    # provider rejected and contains nothing else.
    _send(emailer, message)

    log.info("invite sent for participant %s", participant_id)


def _send_results_in_review(client, settings: Settings, job: Job, emailer) -> None:
    participant_id = job.payload.get("participant_id")
    if not participant_id:
        raise HandlerError("results_in_review email has no participant_id")

    participant = _participant(client, participant_id)
    if not participant.get("email"):
        raise HandlerError(f"participant {participant_id} has no email address")

    _send(
        emailer,
        templates.results_in_review(
            to=participant["email"],
            first_name=_first_name(participant["full_name"]),
            organisation=_organisation_name(client, participant_id),
        ),
    )


def _send_job_failed_alert(settings: Settings, job: Job, emailer) -> None:
    if not settings.operator_alert_email:
        # Deliberately not an error. An alert that fails would itself alert —
        # `fail_job` blocks that specific loop, but a deployment with no operator
        # address configured should not manufacture failing jobs to say so.
        log.warning(
            "OPERATOR_ALERT_EMAIL is unset — job %s failure alert not sent: %s",
            job.payload.get("job_id"),
            job.payload.get("error"),
        )
        return

    _send(
        emailer,
        templates.job_failed_alert(
            to=settings.operator_alert_email,
            job_id=str(job.payload.get("job_id", "unknown")),
            job_kind=str(job.payload.get("job_kind", "unknown")),
            attempts=int(job.payload.get("attempts", 0)),
            error=str(job.payload.get("error", "")),
        ),
    )


def _send_backlog_alert(client, settings: Settings, job: Job, emailer) -> None:
    if not settings.operator_alert_email:
        log.warning("OPERATOR_ALERT_EMAIL is unset — backlog alert not sent")
        return

    session_id = job.payload.get("session_id")
    if not session_id:
        raise HandlerError("review_backlog_alert has no session_id")

    context = _session_context(client, session_id)
    _send(
        emailer,
        templates.review_backlog_alert(
            to=settings.operator_alert_email,
            student_name=context["student_name"],
            cohort=context["cohort_name"],
            days_waiting=int(job.payload.get("days_waiting", settings.review_backlog_days)),
            review_url=f"{settings.app_base_url.rstrip('/')}/review/{session_id}",
        ),
    )


def _send(emailer, message: Message) -> None:
    try:
        emailer.send(message)
    except EmailError:
        raise
    except Exception as exc:
        # httpx timeouts and connection errors. Wrapped so the runner records a
        # described failure rather than a raw network traceback.
        raise HandlerError(f"send failed: {type(exc).__name__}: {exc}") from exc


def _participant(client, participant_id: str) -> dict:
    result = (
        client.table("participants")
        .select("id, full_name, email, status, cohort_id")
        .eq("id", participant_id)
        .execute()
    )
    rows = result.data or []
    if not rows:
        raise HandlerError(f"no participant {participant_id}")
    return rows[0]


def _organisation_name(client, participant_id: str) -> str:
    """The school's name, for the mail body.

    Two hops rather than a nested select: supabase-py's embedded-resource syntax
    would need a foreign-key name here, and this runs once per email, not per
    item. A missing name falls back rather than failing the send — an invite
    that arrives saying "your school" is better than one that never arrives.
    """
    participant = _participant(client, participant_id)
    cohort = (
        client.table("cohorts")
        .select("organisation_id")
        .eq("id", participant["cohort_id"])
        .execute()
    ).data or []
    if not cohort:
        return "your school"

    organisation = (
        client.table("organisations")
        .select("name")
        .eq("id", cohort[0]["organisation_id"])
        .execute()
    ).data or []
    return organisation[0]["name"] if organisation else "your school"


def _session_context(client, session_id: str) -> dict:
    session = (
        client.table("sessions").select("participant_id").eq("id", session_id).execute()
    ).data or []
    if not session:
        raise HandlerError(f"no session {session_id}")

    participant = _participant(client, session[0]["participant_id"])
    cohort = (
        client.table("cohorts").select("name").eq("id", participant["cohort_id"]).execute()
    ).data or []

    return {
        "participant_id": participant["id"],
        "student_name": participant["full_name"],
        "cohort_name": cohort[0]["name"] if cohort else "their cohort",
    }


def _first_name(full_name: str) -> str:
    """Just the first word. A mail that opens "Hello Fatima" reads like a person
    wrote it; "Hello Fatima Khan" reads like a mail merge."""
    return (full_name or "").strip().split(" ")[0] or "there"

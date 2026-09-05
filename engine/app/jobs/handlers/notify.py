"""`notify_psychologist` and `review_reminder` — the two job kinds v2 adds (§9, §12).

Both mail staff about a session waiting in the review queue, and neither ever
mails the student. §9.4 is explicit: a student never sees "your report is late".
They see the calm waiting state M6 built, and the lateness is an operational
problem at this end.

Neither mail carries scores or flags. A reviewer needs to know whose entry this
is; what the results say is behind a login where RLS decides who may read it,
and `reviews.interview_notes` in particular is treated like health data (§16).
Email forwards; a queue screen does not.
"""

from __future__ import annotations

import logging

from app.config import Settings
from app.email import build_emailer
from app.emails import templates
from app.jobs.handlers import HandlerError
from app.jobs.handlers.email import _send, _session_context
from app.jobs.queue import Job

log = logging.getLogger(__name__)

# Who may work the review queue. `counsellor` is deliberately absent: the role
# split is what keeps interview_notes away from them (0003_reviews.sql), and
# notifying a counsellor that a review is waiting would be inviting someone to a
# screen they cannot open.
REVIEWER_ROLES = ("psychologist", "org_admin")


def handle_notify_psychologist(client, settings: Settings, job: Job) -> None:
    """One session has reached `pending_review` (§9.1)."""
    session_id = job.payload.get("session_id")
    if not session_id:
        raise HandlerError("notify_psychologist job has no session_id")

    context = _session_context(client, session_id)
    reviewers = _reviewers_for(client, context["participant_id"])

    if not reviewers:
        # Not a retryable failure and not silent. plan §20 item 3 names "who
        # plays the psychologist role" as an open decision; an organisation with
        # no psychologist yet is a configuration gap, and failing the job five
        # times would bury that under a job-failure alert that misdescribes it.
        log.warning(
            "no psychologist or org_admin in the organisation for session %s — "
            "nobody was notified that a review is waiting (plan §20 item 3)",
            session_id,
        )
        return

    emailer = build_emailer(settings)
    review_url = f"{settings.app_base_url.rstrip('/')}/review/{session_id}"

    for reviewer in reviewers:
        _send(
            emailer,
            templates.notify_psychologist(
                to=reviewer["email"],
                reviewer_name=_first_name(reviewer["full_name"]),
                student_name=context["student_name"],
                cohort=context["cohort_name"],
                review_url=review_url,
            ),
        )

    log.info("notified %d reviewer(s) about session %s", len(reviewers), session_id)


def handle_review_reminder(client, settings: Settings, job: Job) -> None:
    """The §9.4 nudge for a session waiting more than five days.

    Enqueued by `enqueue_review_reminders()`, which dedupes on a cooldown so a
    five-minute tick cannot produce 288 of these a day.

    Goes to the reviewers AND the operator. The operator copy is the point: §9.4
    says silence at this end is an operational problem to fix, and a reminder
    that only reaches the person already not acting on it is not a reminder.
    """
    session_id = job.payload.get("session_id")
    if not session_id:
        raise HandlerError("review_reminder job has no session_id")

    context = _session_context(client, session_id)
    emailer = build_emailer(settings)
    review_url = f"{settings.app_base_url.rstrip('/')}/review/{session_id}"
    days = settings.review_backlog_days

    recipients = [r["email"] for r in _reviewers_for(client, context["participant_id"])]
    if settings.operator_alert_email:
        recipients.append(settings.operator_alert_email)

    if not recipients:
        log.warning("session %s is overdue for review but nobody is configured to be told",
                    session_id)
        return

    for address in dict.fromkeys(recipients):  # de-dupe, preserving order
        _send(
            emailer,
            templates.review_backlog_alert(
                to=address,
                student_name=context["student_name"],
                cohort=context["cohort_name"],
                days_waiting=days,
                review_url=review_url,
            ),
        )

    log.info("review backlog reminder sent for session %s", session_id)


def _reviewers_for(client, participant_id: str) -> list[dict]:
    """Profiles in this participant's organisation who can work the queue.

    Walks participant → cohort → organisation → profiles. The engine holds the
    service role and bypasses RLS, so the tenant boundary here is this query
    and nothing else — the same shape as `import_roster()`'s explicit check.
    Widening it to "all psychologists" would mail one school's staff about
    another school's student.
    """
    participant = (
        client.table("participants").select("cohort_id").eq("id", participant_id).execute()
    ).data or []
    if not participant:
        raise HandlerError(f"no participant {participant_id}")

    cohort = (
        client.table("cohorts")
        .select("organisation_id")
        .eq("id", participant[0]["cohort_id"])
        .execute()
    ).data or []
    if not cohort:
        raise HandlerError(f"no cohort for participant {participant_id}")

    profiles = (
        client.table("profiles")
        .select("id, full_name, role")
        .eq("organisation_id", cohort[0]["organisation_id"])
        .in_("role", list(REVIEWER_ROLES))
        .execute()
    ).data or []

    # `profiles` has no email column — it mirrors `auth.users`, which owns the
    # address. Looked up per profile rather than joined, because auth is a
    # separate schema the REST client cannot embed across.
    reviewers = []
    for profile in profiles:
        email = _auth_email(client, profile["id"])
        if email:
            reviewers.append({**profile, "email": email})
    return reviewers


def _auth_email(client, user_id: str) -> str | None:
    """The address for one profile, out of Supabase Auth.

    A profile whose auth user has been deleted returns None rather than raising:
    one departed staff member must not stop the remaining reviewers being told.
    """
    try:
        user = client.auth.admin.get_user_by_id(user_id)
    except Exception:
        log.warning("could not read auth user %s", user_id)
        return None
    return getattr(getattr(user, "user", None), "email", None)


def _first_name(full_name: str) -> str:
    return (full_name or "").strip().split(" ")[0] or "there"

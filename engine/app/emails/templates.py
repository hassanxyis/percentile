"""Email bodies (plan §15).

Plain functions returning a `Message`. No Jinja, no HTML framework: these are
five short mails and the report templates (M9) are where the rendering machinery
belongs.

Two constraints apply to every string in this file.

**R7 — no clinical language.** The review step is a counsellor reading results
before talking to a student. It is never described as therapy, treatment,
diagnosis, or a health service, in any mail, including the ones only staff read.
The wording here follows `consent-screen.tsx`, which says the same thing to the
student at consent time; the two must not drift apart.

**R3 — nothing here is interpretation.** These are operational notices. No mail
in this file may summarise, characterise or hint at what a student's results
say — not "your results suggest", not a Holland code, not a band. That content
is human-written and lives in the report, behind a confirmed review (R9).
"""

from __future__ import annotations

from app.email import Message

# The student-facing mails address a teenager who did not ask to be emailed by
# software. Short, plain, and no exclamation marks.


def invite(*, to: str, first_name: str, organisation: str, link: str) -> Message:
    """The invite. Carries the only working copy of a freshly minted token.

    The link is regenerated at send time (§12) — the token from the import
    screen's one-time download cannot be recovered, only replaced. That means
    this mail is authoritative: if a student holds two links, this is the one
    that works.
    """
    subject = f"{organisation}: your careers questionnaire"
    text = f"""Hello {first_name},

{organisation} has asked you to complete a short set of questions before you
talk about your subject and career choices.

    {link}

It takes about twenty minutes and works on a phone. There are no right answers
and nothing to revise for. You can stop part-way and come back to the same link;
your answers are kept as you go.

This link is yours. Please do not forward it.
"""
    return Message(to=to, subject=subject, text=text)


def results_in_review(*, to: str, first_name: str, organisation: str) -> Message:
    """Sent when a session is scored (§15). Deliberately says nothing about the results.

    Fires *before* any human has read them, so there is nothing yet that a
    person has judged — and R3 puts interpretation in a person's hands. The mail
    exists to close the loop on "did that submit work", not to preview anything.
    """
    subject = "We have your answers"
    text = f"""Hello {first_name},

Your answers came through — thank you.

A counsellor at {organisation} is going through your results now. They may want
a short conversation with you before your report is finished. You will hear from
{organisation} when it is ready.

There is nothing you need to do in the meantime.
"""
    return Message(to=to, subject=subject, text=text)


# Staff-facing mails. Still no interpretation, and still no student's results in
# the body — a mail client is not a place a minor's profile should be sitting.


def notify_psychologist(
    *, to: str, reviewer_name: str, student_name: str, cohort: str, review_url: str
) -> Message:
    """One session has reached the review queue (§9, R9).

    Names the student, because the reviewer needs to know whose queue entry this
    is, but carries no scores and no flags. Those are behind a login where RLS
    decides who may read them; an email forwards.
    """
    subject = f"Ready to review: {student_name} ({cohort})"
    text = f"""Hello {reviewer_name},

{student_name} in {cohort} has completed the assessment and their results are
ready for you to look at.

    {review_url}

Nothing is sent to the student until you confirm.
"""
    return Message(to=to, subject=subject, text=text)


def review_backlog_alert(
    *, to: str, student_name: str, cohort: str, days_waiting: int, review_url: str
) -> Message:
    """The §9.4 nudge, five days after submission.

    Goes to the operator and the org_admin, never to the student. §9.4 is
    explicit about that: a student never sees "your report is late". Silence at
    this end is an operational problem to fix, not something to put in front of
    a sixteen-year-old.
    """
    subject = f"Review waiting {days_waiting} days: {student_name} ({cohort})"
    text = f"""{student_name} in {cohort} submitted {days_waiting} days ago and is still
waiting for a review.

    {review_url}

The student has not been told anything is late, and will not be.
"""
    return Message(to=to, subject=subject, text=text)


def job_failed_alert(
    *, to: str, job_id: str, job_kind: str, attempts: int, error: str
) -> Message:
    """A job that exhausted its retries (§12).

    `error` arrives from `jobs.last_error`, already truncated by `fail_job`.
    Truncated again here rather than trusting that, because this is the one
    place the string leaves the system — a mail provider logs bodies, and an
    error text that grew a token on some future code path must not be the thing
    that discovers the gap.
    """
    subject = f"Percentile: {job_kind} job failed after {attempts} attempts"
    text = f"""A job has failed permanently and will not be retried.

    job:      {job_id}
    kind:     {job_kind}
    attempts: {attempts}

    {error[:1000]}

Nothing else is blocked by this on its own, but the work that job represents has
not happened. Check the jobs table.
"""
    return Message(to=to, subject=subject, text=text)

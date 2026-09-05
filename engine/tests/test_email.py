"""Outbound email (plan §15, M7).

The behaviour worth pinning down is not the HTTP call — it is what happens when
`RESEND_API_KEY` is unset, which is every developer machine and, until someone
sets it, production.
"""

import logging

import pytest

from app.config import Settings
from app.email import EmailError, LoggingEmailer, Message, ResendEmailer, build_emailer
from app.emails import templates

# ── choosing an emailer ──────────────────────────────────────────────────────


def test_no_key_selects_the_logging_emailer():
    assert isinstance(build_emailer(Settings(resend_api_key="")), LoggingEmailer)


def test_a_key_selects_resend():
    assert isinstance(build_emailer(Settings(resend_api_key="re_test")), ResendEmailer)


def test_logging_emailer_does_not_raise(caplog):
    """It must not raise: a local tick would otherwise fail every job five times
    and enqueue alerts, which makes the runner untestable without a provider."""
    with caplog.at_level(logging.WARNING):
        LoggingEmailer().send(Message(to="s@example.edu.pk", subject="hi", text="body"))


def test_logging_emailer_does_not_read_as_a_successful_send(caplog):
    """The other half of the same decision.

    A silent no-op means a developer drains twenty invite jobs, sees green, and
    believes twenty students were emailed. The log line has to be unmissable at
    a skim, and at WARNING rather than INFO.
    """
    with caplog.at_level(logging.WARNING):
        LoggingEmailer().send(Message(to="s@example.edu.pk", subject="hi", text="body"))

    assert "NOT SENT" in caplog.text
    assert caplog.records[0].levelno >= logging.WARNING


def test_logging_emailer_does_not_log_the_body(caplog):
    """An invite body contains a live token, and this goes to stdout."""
    token_link = "https://example.test/a/SECRET-TOKEN-VALUE"
    with caplog.at_level(logging.WARNING):
        LoggingEmailer().send(
            Message(to="s@example.edu.pk", subject="hi", text=f"open {token_link}")
        )

    assert "SECRET-TOKEN-VALUE" not in caplog.text


# ── the provider call ────────────────────────────────────────────────────────


class FakeResponse:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text


def test_resend_posts_the_expected_payload(monkeypatch):
    captured = {}

    def fake_post(url, *, json, headers, timeout):
        captured.update(url=url, json=json, headers=headers)
        return FakeResponse(200)

    monkeypatch.setattr("app.email.httpx.post", fake_post)

    ResendEmailer("re_test", "Percentile <no@example.test>").send(
        Message(to="s@example.edu.pk", subject="Subject", text="Body")
    )

    assert captured["json"]["to"] == ["s@example.edu.pk"]
    assert captured["json"]["from"] == "Percentile <no@example.test>"
    assert captured["headers"]["Authorization"] == "Bearer re_test"


def test_resend_raises_on_rejection(monkeypatch):
    """A failed send must become a failed job so the runner retries it. An
    invite that vanishes silently is a student who never gets a link."""
    monkeypatch.setattr(
        "app.email.httpx.post",
        lambda *args, **kwargs: FakeResponse(422, "domain not verified"),
    )

    with pytest.raises(EmailError, match="422"):
        ResendEmailer("re_test", "from@example.test").send(
            Message(to="s@example.edu.pk", subject="s", text="t")
        )


def test_resend_error_is_truncated(monkeypatch):
    """This string becomes `jobs.last_error`, which is not a log sink."""
    monkeypatch.setattr(
        "app.email.httpx.post", lambda *args, **kwargs: FakeResponse(500, "x" * 5000)
    )

    with pytest.raises(EmailError) as exc:
        ResendEmailer("re_test", "from@example.test").send(
            Message(to="s@example.edu.pk", subject="s", text="t")
        )

    assert len(str(exc.value)) < 1000


# ── template content ─────────────────────────────────────────────────────────

CLINICAL_WORDS = (
    "therapy",
    "therapist",
    "clinical",
    "diagnos",
    "treatment",
    "patient",
    "counselling session",
    "mental health",
)


def all_messages() -> list[Message]:
    return [
        templates.invite(
            to="s@example.edu.pk", first_name="Fatima", organisation="GIFT",
            link="https://example.test/a/tok",
        ),
        templates.results_in_review(
            to="s@example.edu.pk", first_name="Fatima", organisation="GIFT"
        ),
        templates.notify_psychologist(
            to="p@example.edu.pk", reviewer_name="Ayesha", student_name="Fatima Khan",
            cohort="Class of 2027", review_url="https://example.test/review/1",
        ),
        templates.review_backlog_alert(
            to="ops@example.test", student_name="Fatima Khan", cohort="Class of 2027",
            days_waiting=5, review_url="https://example.test/review/1",
        ),
        templates.job_failed_alert(
            to="ops@example.test", job_id="j1", job_kind="score_session",
            attempts=5, error="boom",
        ),
    ]


@pytest.mark.parametrize("message", all_messages())
def test_no_clinical_language_anywhere(message: Message):
    """R7. Including in the staff-only mails — the framing has to be consistent
    or the student-facing wording is the odd one out and drifts."""
    body = f"{message.subject} {message.text}".lower()
    for word in CLINICAL_WORDS:
        assert word not in body, f"{word!r} in {message.subject!r}"


@pytest.mark.parametrize("message", all_messages())
def test_every_message_has_a_subject_and_body(message: Message):
    assert message.subject.strip()
    assert message.text.strip()


def test_invite_carries_the_link():
    message = templates.invite(
        to="s@example.edu.pk", first_name="Fatima", organisation="GIFT",
        link="https://example.test/a/the-token",
    )
    assert "https://example.test/a/the-token" in message.text


def test_results_in_review_says_nothing_about_the_results():
    """R3, and the ordering that makes it necessary: this fires when `scores` is
    written, before any human has read them. There is nothing yet that a person
    has judged, so there is nothing to characterise."""
    message = templates.results_in_review(
        to="s@example.edu.pk", first_name="Fatima", organisation="GIFT"
    )
    body = message.text.lower()

    for leak in ("score", "your strengths", "suited", "recommend", "profile", "result show"):
        assert leak not in body


def test_backlog_alert_is_not_addressed_to_the_student():
    """§9.4: a student never sees "your report is late"."""
    message = templates.review_backlog_alert(
        to="ops@example.test", student_name="Fatima Khan", cohort="Class of 2027",
        days_waiting=5, review_url="https://example.test/review/1",
    )
    assert "not been told" in message.text

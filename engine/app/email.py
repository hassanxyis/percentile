"""Outbound email (plan §15).

Resend behind a protocol, so the provider is swappable — plan §2's stack table
says "swap-able behind an interface" and this is that interface.

Two rules this module exists to enforce:

* **Never a PDF attachment** (R8, §16). Reports are delivered as a signed URL
  that expires; that is M9's job, and nothing here takes an attachment argument,
  so it cannot quietly acquire one.
* **An unset API key does not raise.** It logs, loudly, and reports success to
  the caller. A developer draining the queue locally must not have their jobs
  fail five times and alert — but they also must not be able to skim the output
  and think mail went to twenty real students. See `LoggingEmailer`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

import httpx

from app.config import Settings

log = logging.getLogger(__name__)

RESEND_ENDPOINT = "https://api.resend.com/emails"
SEND_TIMEOUT_SECONDS = 20.0


@dataclass(frozen=True, slots=True)
class Message:
    to: str
    subject: str
    text: str
    html: str | None = None


class Emailer(Protocol):
    def send(self, message: Message) -> None:
        """Deliver, or raise. Raising is what turns a send into a job retry."""
        ...


class ResendEmailer:
    """Resend's REST API.

    Errors are raised, not swallowed: a failed send must become a failed job so
    the runner retries it with backoff. An invite that vanishes silently is a
    student who never gets a link and never appears in any error.
    """

    def __init__(self, api_key: str, from_address: str) -> None:
        self._api_key = api_key
        self._from = from_address

    def send(self, message: Message) -> None:
        payload: dict[str, object] = {
            "from": self._from,
            "to": [message.to],
            "subject": message.subject,
            "text": message.text,
        }
        if message.html:
            payload["html"] = message.html

        response = httpx.post(
            RESEND_ENDPOINT,
            json=payload,
            headers={"Authorization": f"Bearer {self._api_key}"},
            timeout=SEND_TIMEOUT_SECONDS,
        )

        if response.status_code >= 400:
            # The body carries Resend's reason ("domain not verified" is the
            # usual one). Truncated because this string becomes `jobs.last_error`
            # and that column is not a log sink — see 0008_job_runner.sql.
            raise EmailError(
                f"resend rejected the send: {response.status_code} {response.text[:500]}"
            )


class LoggingEmailer:
    """The no-key fallback: log the message and report success.

    Deliberately not a silent no-op and deliberately not an exception.

    Raising would mean a local `POST /tick` fails every job five times and
    enqueues alerts, which makes the runner untestable without a mail provider.
    A quiet no-op would mean a developer drains twenty invite jobs, sees a clean
    green run, and believes twenty students were emailed.

    So: WARNING level, with a marker that does not read as success at a glance.
    The body is truncated because an invite mail contains a live token, and this
    goes to stdout on a shared machine.
    """

    def send(self, message: Message) -> None:
        log.warning(
            "NOT SENT (no RESEND_API_KEY) — would email %s: %r",
            message.to,
            message.subject,
        )


class EmailError(RuntimeError):
    """A send that failed. Becomes a job retry, then an alert at the limit."""


def build_emailer(settings: Settings) -> Emailer:
    """Pick an emailer from configuration.

    The key's presence is the only switch. There is no `EMAIL_ENABLED` flag,
    because two switches means a deployment can have a real key and still send
    nothing, which is the failure nobody notices until a school asks why their
    students never got the link.
    """
    if not settings.resend_api_key:
        return LoggingEmailer()
    return ResendEmailer(settings.resend_api_key, settings.email_from)

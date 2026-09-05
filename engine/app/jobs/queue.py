"""Thin wrappers over the job SQL functions (0008_job_runner.sql).

Every multi-table write lives in Postgres, for the reason 0005 and 0006 give:
supabase-py speaks REST, so two calls are two requests with no transaction
between them. This module only translates arguments and unpacks results — if
you find yourself adding an `if` here that decides something, it belongs in SQL
or in a handler.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class Job:
    id: str
    kind: str
    payload: dict
    attempts: int

    @classmethod
    def from_row(cls, row: dict) -> Job:
        return cls(
            id=row["id"],
            kind=row["kind"],
            payload=row.get("payload") or {},
            attempts=row.get("attempts", 0),
        )


def claim_jobs(client, limit: int) -> list[Job]:
    """Claim up to `limit` pending jobs, marking them running.

    `for update skip locked` inside the function means two ticks running at once
    never take the same row — a cron overlap is safe by construction rather than
    by hoping the schedule never slips.
    """
    result = client.rpc("claim_jobs", {"p_limit": limit}).execute()
    return [Job.from_row(row) for row in (result.data or [])]


def complete_job(client, job_id: str) -> None:
    client.rpc("complete_job", {"p_id": job_id}).execute()


def fail_job(client, job_id: str, error: str, max_attempts: int) -> str:
    """Record a failure. Returns 'retry' or 'failed'.

    The error text is truncated by the SQL function before it is stored — see
    the guard in `fail_job`. Handlers must still keep tokens out of exception
    messages; the truncation is a backstop, not the rule.
    """
    result = client.rpc(
        "fail_job",
        {"p_id": job_id, "p_error": error, "p_max_attempts": max_attempts},
    ).execute()
    return result.data if isinstance(result.data, str) else "failed"


def requeue_stale_jobs(client, timeout_seconds: int) -> int:
    """Return jobs whose worker died back to pending. Returns how many.

    Not optional. Attempts increment at claim time, so a job whose process was
    killed stays `running` and is invisible to `claim_jobs` forever without this.
    """
    result = client.rpc(
        "requeue_stale_jobs", {"p_timeout": f"{timeout_seconds} seconds"}
    ).execute()
    return _as_int(result.data)


def enqueue_review_reminders(client, older_than_days: int, cooldown_hours: int = 24) -> int:
    """Queue the §9.4 backlog nudge for sessions waiting too long. Returns how many."""
    result = client.rpc(
        "enqueue_review_reminders",
        {
            "p_older_than": f"{older_than_days} days",
            "p_cooldown": f"{cooldown_hours} hours",
        },
    ).execute()
    return _as_int(result.data)


def record_score(
    client,
    session_id: str,
    engine_version: str,
    scores: dict[str, Any],
) -> str:
    """Write the score and enqueue the follow-up jobs, in one transaction.

    Also moves the participant to `pending_review` — never `scored`, and never
    straight to a render job. R9 is why: the report cannot exist until a
    psychologist confirms, and the database trigger would refuse it anyway.
    """
    result = client.rpc(
        "record_score",
        {
            "p_session_id": session_id,
            "p_engine_version": engine_version,
            "p_interests": scores["interests"],
            "p_personality": scores["personality"],
            "p_get2": scores["get2"],
            "p_flags": scores["flags"],
        },
    ).execute()
    return result.data


def record_occupation_matches(
    client, session_id: str, engine_version: str, matches: list[dict]
) -> int:
    """Replace this (session, engine_version)'s matches. Idempotent on retry."""
    result = client.rpc(
        "record_occupation_matches",
        {
            "p_session_id": session_id,
            "p_engine_version": engine_version,
            "p_matches": matches,
        },
    ).execute()
    return _as_int(result.data)


def _as_int(value: Any) -> int:
    """PostgREST returns a scalar function result as a bare value or a 1-row list."""
    if isinstance(value, list):
        value = value[0] if value else 0
    if isinstance(value, dict):
        value = next(iter(value.values()), 0)
    return int(value or 0)

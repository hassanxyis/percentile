"""Schema application and impersonation for the database tests.

These tests exist because R9 and row-level security are enforced by Postgres,
not by Python. Asserting them anywhere other than against a real server would be
asserting a belief about SQL rather than the SQL itself.

Two things here are load-bearing:

1. `reset_and_apply` pins `search_path`. `0002_rls.sql` creates
   `auth_organisation_id()` unqualified, and `review_progress_for_session()`
   calls it unqualified under `set search_path = public` — they only meet if the
   function landed in `public`. Pinning guarantees it did.

2. `as_user` defeats the owner's RLS bypass. Postgres skips RLS for superusers,
   BYPASSRLS roles and the table owner; the test connection is all three. Without
   `set local role authenticated` every RLS assertion passes vacuously, which is
   worse than having no test at all.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import psycopg

REPO_ROOT = Path(__file__).resolve().parents[3]
MIGRATIONS_DIR = REPO_ROOT / "db" / "migrations"
TESTING_DIR = REPO_ROOT / "db" / "testing"
SEED_FILE = REPO_ROOT / "db" / "seed" / "demo_org.sql"

# ── seed identities (db/seed/demo_org.sql) ───────────────────────────────
# Literals, not lookups: a test that queries for "the psychologist" would still
# pass if the seed gave the role to the wrong person.
ORG_A = "11111111-1111-4111-8111-111111111111"
ORG_B = "22222222-2222-4222-8222-222222222222"

COUNSELLOR_A = "aaaaaaa1-0000-4000-8000-000000000001"
PSYCHOLOGIST_A = "aaaaaaa2-0000-4000-8000-000000000002"
ORG_ADMIN_A = "aaaaaaa3-0000-4000-8000-000000000003"
COUNSELLOR_B = "bbbbbbb1-0000-4000-8000-000000000001"
PSYCHOLOGIST_B = "bbbbbbb2-0000-4000-8000-000000000002"

COHORT_A = "c0000001-0000-4000-8000-00000000000a"
COHORT_B = "c0000002-0000-4000-8000-00000000000b"

# Fatima (org A) and Iqra (org B) — both reached a confirmed review and a report.
SESSION_A_CONFIRMED = "5e551011-0000-4000-8000-00000000a001"
SESSION_B_CONFIRMED = "5e551011-0000-4000-8000-00000000b001"
REVIEW_A = "4e415e00-0000-4000-8000-00000000a001"
REVIEW_B = "4e415e00-0000-4000-8000-00000000b001"

# Tenant-scoped tables the cross-tenant leak test sweeps. Each maps to the
# column that ties a row to an organisation, so the test can assert both halves:
# org A's user sees org A's rows, and sees none of org B's.
TENANT_TABLES = (
    "cohorts",
    "participants",
    "sessions",
    "responses",
    "scores",
    "occupation_matches",
    "reports",
)


def _sql_files() -> list[Path]:
    """Shim, then every migration in order, then grants, then seed.

    Migrations are globbed rather than listed so a future `0005_*.sql` is picked
    up without editing this file. The shim has to precede `0001` (which
    references `auth.users`) and the grants have to follow the last migration
    (they name tables that do not exist until then).
    """
    return [
        TESTING_DIR / "0000_supabase_shim.sql",
        *sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9][0-9]_*.sql")),
        TESTING_DIR / "9999_grants.sql",
        SEED_FILE,
    ]


def reset_and_apply(conn: psycopg.Connection) -> None:
    """Drop and rebuild the schema, then apply everything.

    Destructive by design — `TEST_DATABASE_URL` must point at a throwaway
    database. Roles are cluster-wide and survive the drop, which is why the
    shim's role creation is guarded by existence checks.
    """
    conn.execute("set search_path = public, pg_catalog")
    conn.execute("drop schema if exists auth cascade")
    conn.execute("drop schema if exists public cascade")
    conn.execute("create schema public")

    for path in _sql_files():
        if not path.exists():
            raise FileNotFoundError(f"harness expected {path}")
        conn.execute(path.read_text(encoding="utf-8"))


@contextmanager
def as_user(conn: psycopg.Connection, profile_id: str) -> Iterator[psycopg.Connection]:
    """Run the block as `authenticated`, impersonating one profile.

    `set_config(..., true)` and `set local role` are both transaction-scoped, so
    the per-test rollback undoes them and no impersonation leaks into the next
    test.

    The assertion is not defensive padding. If `set local role` were dropped, the
    connection would keep the owner's RLS bypass and *every* policy test in this
    directory would pass while testing nothing. Fail loudly here instead.
    """
    with conn.cursor() as cur:
        cur.execute("select set_config('request.jwt.claim.sub', %s, true)", (profile_id,))
        cur.execute("set local role authenticated")
        cur.execute("select current_user, current_setting('is_superuser')")
        current_user, is_superuser = cur.fetchone()
        assert current_user == "authenticated", f"impersonation failed: {current_user}"
        assert is_superuser == "off", "connection would bypass RLS; test would be vacuous"
    try:
        yield conn
    finally:
        conn.execute("reset role")


def count_as(conn: psycopg.Connection, profile_id: str, table: str) -> int:
    """Row count of `table` as seen by one profile through RLS."""
    with as_user(conn, profile_id), conn.cursor() as cur:
        cur.execute(f"select count(*) from {table}")  # noqa: S608 — table names are module constants
        return cur.fetchone()[0]


def make_session(
    conn: psycopg.Connection,
    organisation_id: str = ORG_A,
    cohort_id: str = COHORT_A,
    suffix: str = "9001",
) -> str:
    """Create a participant and session with no review attached, and return its id.

    The R9 tests build their own rows rather than leaning on the seed: the
    "blocks" case needs a session with *no* review, and if it read seed state a
    later seed change that added one would silently flip the test to a false
    pass. `suffix` keeps ids distinct when one test needs two sessions.

    Runs as the connection owner (service-role equivalent), which is who writes
    in production — every write in this product is server-side (0002_rls.sql).
    """
    # The final UUID group is exactly 12 hex characters; `suffix` fills the last
    # four. Get this wrong and Postgres rejects the literal with 22P02 rather
    # than anything that points at the real mistake.
    participant_id = f"a0aaaaa9-0000-4000-8000-00000000{suffix}"
    session_id = f"5e551011-0000-4000-8000-00000000{suffix}"

    conn.execute(
        """
        insert into participants (id, cohort_id, full_name, invite_token_hash, status)
        values (%s, %s, 'Harness Participant', %s, 'scored')
        """,
        (participant_id, cohort_id, f"harness-token-hash-{suffix}"),
    )
    conn.execute(
        """
        insert into sessions (id, participant_id, started_at, submitted_at)
        values (%s, %s, now(), now())
        """,
        (session_id, participant_id),
    )
    return session_id


def confirm_review(
    conn: psycopg.Connection,
    session_id: str,
    reviewer_id: str = PSYCHOLOGIST_A,
    notes: str = "Harness review.",
) -> None:
    """Attach a confirmed review to a session, satisfying R9."""
    conn.execute(
        """
        insert into reviews (session_id, reviewer_id, status, interview_notes, confirmed_at)
        values (%s, %s, 'confirmed', %s, now())
        """,
        (session_id, reviewer_id, notes),
    )


def insert_student_report(conn: psycopg.Connection, session_id: str) -> None:
    """Insert a student `reports` row — the operation R9 gates.

    Deliberately runs as the connection owner, NOT through `as_user`.
    `enforce_review_before_student_report()` is SECURITY INVOKER, so its
    `select 1 from reviews` is itself subject to `reviews` RLS. Wrap this in
    `as_user` and even a genuinely confirmed review gets filtered to zero rows,
    producing a spurious "R9 violation" that looks like the trigger working.
    In production the writer is the service role, which bypasses RLS — the owner
    is the faithful stand-in.
    """
    conn.execute(
        """
        insert into reports (kind, session_id, storage_path, template_version, engine_version)
        values ('student', %s, 'reports/harness.pdf', '1.0.0', '1.0.0')
        """,
        (session_id,),
    )

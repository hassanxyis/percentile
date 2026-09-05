"""Roster import at the database level (plan.md §12, §13, M5).

`import_roster()` exists because a roster import is two writes that must not
come apart. supabase-js speaks REST, so participants and jobs would otherwise be
separate requests with no transaction between them — and a half-import leaves
students whose invite tokens are unrecoverable, since only sha256 is stored.

These tests are what make "one transaction, or none of it" a checked claim
rather than a comment in a migration.
"""

import json

import psycopg
import pytest

from . import harness


def roster_row(n: int, token_hash: str | None = None) -> dict:
    """One row shaped the way the server action sends it."""
    return {
        "full_name": f"Harness Student {n}",
        "email": f"harness{n}@example.edu.pk",
        "external_ref": f"H-{n:03d}",
        "intended_field": "medicine",
        "education_level": "intermediate",
        "invite_token_hash": token_hash or f"harness-token-hash-{n:04d}",
    }


def import_roster(
    conn: psycopg.Connection,
    rows: list[dict],
    cohort_id: str = harness.COHORT_A,
    organisation_id: str = harness.ORG_A,
    actor: str = harness.COUNSELLOR_A,
) -> int:
    """Call the function as the owner — the service-role stand-in.

    In production the caller is a Next.js server action holding the service role,
    which bypasses RLS. `import_roster` is revoked from `authenticated`
    precisely so a browser session cannot reach it and pass another school's
    organisation id.
    """
    with conn.cursor() as cur:
        cur.execute(
            "select import_roster(%s, %s, %s, %s::jsonb)",
            (cohort_id, organisation_id, actor, json.dumps(rows)),
        )
        return cur.fetchone()[0]


def test_imports_participants_and_enqueues_one_job_each(conn: psycopg.Connection) -> None:
    inserted = import_roster(conn, [roster_row(n) for n in range(1, 21)])

    assert inserted == 20

    with conn.cursor() as cur:
        cur.execute(
            "select count(*) from participants where cohort_id = %s and external_ref like 'H-%%'",
            (harness.COHORT_A,),
        )
        assert cur.fetchone()[0] == 20

        cur.execute(
            """
            select count(*) from jobs
            where kind = 'send_email' and payload->>'template' = 'invite'
            """
        )
        # Two seeded jobs exist, neither an invite; these 20 are ours.
        assert cur.fetchone()[0] == 20


def test_imported_participants_start_as_invited(conn: psycopg.Connection) -> None:
    """The row exists, the invite has not been sent, the student has not begun."""
    import_roster(conn, [roster_row(1)])

    with conn.cursor() as cur:
        cur.execute(
            "select status from participants where external_ref = 'H-001'",
        )
        assert cur.fetchone() == ("invited",)


def test_job_payload_carries_participant_id_not_a_token(conn: psycopg.Connection) -> None:
    """A live credential must never sit in a queue row.

    `jobs` has no RLS policy at all, but it is still retried, logged, and copied
    into `last_error` on failure. The payload therefore names the participant and
    M7 mints a fresh token at send time.
    """
    import_roster(conn, [roster_row(1)])

    with conn.cursor() as cur:
        cur.execute(
            """
            select payload from jobs
            where kind = 'send_email' and payload->>'template' = 'invite'
            """
        )
        payload = cur.fetchone()[0]

    assert set(payload) == {"template", "participant_id"}
    assert "token" not in json.dumps(payload).lower()

    with conn.cursor() as cur:
        cur.execute(
            "select external_ref from participants where id = %s",
            (payload["participant_id"],),
        )
        assert cur.fetchone() == ("H-001",)


def test_optional_columns_become_null_not_empty_string(conn: psycopg.Connection) -> None:
    """An absent roll number is absent, not "". M11 groups on intended_field, and
    an empty string would read as its own field."""
    row = roster_row(1)
    row["external_ref"] = ""
    row["intended_field"] = ""
    row["education_level"] = ""

    import_roster(conn, [row])

    with conn.cursor() as cur:
        cur.execute(
            """
            select external_ref, intended_field, education_level
            from participants where email = 'harness1@example.edu.pk'
            """
        )
        assert cur.fetchone() == (None, None, None)


def test_writes_one_audit_row(conn: psycopg.Connection) -> None:
    import_roster(conn, [roster_row(n) for n in range(1, 4)])

    with conn.cursor() as cur:
        cur.execute(
            """
            select actor, subject, meta->>'count'
            from audit_log where action = 'roster.imported'
            """
        )
        actor, subject, count = cur.fetchone()

    assert str(actor) == harness.COUNSELLOR_A
    assert subject == harness.COHORT_A
    assert count == "3"


def test_rolls_back_entirely_when_one_row_collides(conn: psycopg.Connection) -> None:
    """The reason this function exists.

    `invite_token_hash` is unique. A collision on the last row must leave the
    first rows unwritten — otherwise those students exist with tokens nobody
    holds, and only their hashes were stored.
    """
    rows = [roster_row(n) for n in range(1, 6)]
    rows[4]["invite_token_hash"] = rows[0]["invite_token_hash"]

    with pytest.raises(psycopg.errors.UniqueViolation), conn.transaction():
        import_roster(conn, rows)

    with conn.cursor() as cur:
        cur.execute("select count(*) from participants where external_ref like 'H-%'")
        assert cur.fetchone()[0] == 0

        cur.execute(
            "select count(*) from jobs where payload->>'template' = 'invite'",
        )
        assert cur.fetchone()[0] == 0


def test_re_importing_the_same_roster_is_refused(conn: psycopg.Connection) -> None:
    """The M5 bug M6's end-to-end test found.

    `parseRosterCsv` dedupes emails within one file, but the only unique column
    used to be `invite_token_hash` — freshly minted per import. So uploading the
    same file twice inserted a second full set of participants and twenty
    students became forty, one copy `Invited` and one `Submitted`.

    `participants_cohort_email_unique` (0007) is what refuses it. Fresh token
    hashes on the second attempt, exactly as the real action produces, so this
    fails if the index is ever dropped rather than passing for the wrong reason.
    """
    first = [roster_row(n) for n in range(1, 21)]
    assert import_roster(conn, first) == 20

    second = [
        roster_row(n, token_hash=f"harness-second-import-hash-{n:04d}")
        for n in range(1, 21)
    ]

    with pytest.raises(psycopg.errors.UniqueViolation), conn.transaction():
        import_roster(conn, second)

    with conn.cursor() as cur:
        cur.execute("select count(*) from participants where external_ref like 'H-%'")
        assert cur.fetchone()[0] == 20


def test_the_same_student_may_appear_in_a_different_cohort(
    conn: psycopg.Connection,
) -> None:
    """Scoped to the cohort, not the organisation.

    A student reassessed in a later intake year is a new cohort and a new row.
    Blocking that would break the year-on-year comparison §10 exists for.
    """
    conn.execute(
        """
        insert into cohorts (id, organisation_id, name, intake_year, education_level)
        values (%s, %s, 'Class of 2028 — Pre-Medical A', 2028, 'intermediate')
        """,
        ("c0000003-0000-4000-8000-00000000000c", harness.ORG_A),
    )

    assert import_roster(conn, [roster_row(1)]) == 1
    assert (
        import_roster(
            conn,
            [roster_row(1, token_hash="harness-next-year-hash-0001")],
            cohort_id="c0000003-0000-4000-8000-00000000000c",
        )
        == 1
    )

    with conn.cursor() as cur:
        cur.execute(
            "select count(*) from participants where email = 'harness1@example.edu.pk'"
        )
        assert cur.fetchone()[0] == 2


def test_email_uniqueness_ignores_case(conn: psycopg.Connection) -> None:
    """The index is on `lower(email)`, matching what roster-csv.ts already does
    on the way in. Without it, Fatima@ and fatima@ are two students."""
    import_roster(conn, [roster_row(1)])

    shouting = roster_row(1, token_hash="harness-shouting-hash-0001")
    shouting["email"] = shouting["email"].upper()

    with pytest.raises(psycopg.errors.UniqueViolation), conn.transaction():
        import_roster(conn, [shouting])


def test_rejects_a_cohort_from_another_organisation(conn: psycopg.Connection) -> None:
    """The tenant check, which is the only one the database performs here.

    The caller holds the service role and bypasses RLS, so without this a
    guessed cohort id would import one school's students into another's roster.
    """
    with pytest.raises(psycopg.errors.RaiseException, match="does not belong"), \
            conn.transaction():
        import_roster(conn, [roster_row(1)], cohort_id=harness.COHORT_B)

    with conn.cursor() as cur:
        cur.execute("select count(*) from participants where external_ref like 'H-%'")
        assert cur.fetchone()[0] == 0


def test_rejects_an_empty_roster(conn: psycopg.Connection) -> None:
    with pytest.raises(psycopg.errors.RaiseException, match="no rows"), conn.transaction():
        import_roster(conn, [])


def test_is_not_callable_by_an_authenticated_user(conn: psycopg.Connection) -> None:
    """`p_organisation_id` is an argument, so a browser session reaching this
    function directly could simply pass another school's id. Execute is revoked
    from `authenticated`; the only caller is a server action that reads the
    organisation from the session."""
    with (
        harness.as_user(conn, harness.COUNSELLOR_A),
        pytest.raises(psycopg.errors.InsufficientPrivilege),
        conn.transaction(),
    ):
        conn.execute(
            "select import_roster(%s, %s, %s, %s::jsonb)",
            (
                harness.COHORT_A,
                harness.ORG_A,
                harness.COUNSELLOR_A,
                json.dumps([roster_row(1)]),
            ),
        )

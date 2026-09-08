"""The cohort report at the database level (plan.md §10, §14, M11).

`test_report_cohort.py` asserts what the document says; this asserts the
database facts underneath it. Two are worth the harness on their own:

* **A cohort report lands with no confirmed review anywhere near it.** R9 gates
  student reports only. If a future migration widened
  `enforce_review_before_student_report()` to fire on every kind, the cohort
  report would stop rendering for exactly the schools whose review backlog it
  exists to show — and nothing else in the suite would notice.
* **`record_cohort_report` queues no email.** It is the structural difference
  from `record_student_report`, and the failure mode of getting it wrong is an
  institutional document mailed to whichever address the student function's
  shape suggested.
"""

from __future__ import annotations

import psycopg
import pytest

from . import harness

# Cohort A holds Fatima (confirmed) and Hamza (scored, no review at all).
COHORT_A = harness.COHORT_A


def _record(
    conn: psycopg.Connection,
    cohort_id: str = COHORT_A,
    path: str = "demo-academy/cohort-a.pdf",
    template_version: str = "1.0.0",
) -> str:
    """Call the function as the owner — the service-role stand-in.

    Every write in this product is server-side (0002_rls.sql), and this function
    is revoked from `authenticated` entirely, so the owner is the faithful
    caller here.
    """
    with conn.cursor() as cur:
        cur.execute(
            "select record_cohort_report(%s, %s, %s, '1.0.0')",
            (cohort_id, path, template_version),
        )
        return str(cur.fetchone()[0])


# ── R9 does not reach this document ──────────────────────────────────────────


def test_a_cohort_report_lands_without_any_confirmed_review(
    conn: psycopg.Connection,
) -> None:
    """R9 gates student reports only, and this pins that reading.

    `enforce_review_before_student_report()` opens with `if NEW.kind =
    'student'` (0003_reviews.sql); 0004 widened the trigger's event, not its
    condition. Asserted against a cohort whose only unreviewed participant has
    no `reviews` row at all, so the trigger has nothing it could have matched.

    If someone later "fixes" the trigger to fire on every kind, this fails —
    which is the point. A cohort report that required every review to be
    confirmed would be unavailable to precisely the schools whose backlog §10's
    completion table exists to reveal.
    """
    session_id = harness.make_session(conn, suffix="9201")

    with conn.cursor() as cur:
        cur.execute(
            "select count(*) from reviews where session_id = %s", (session_id,)
        )
        assert cur.fetchone()[0] == 0, "this cohort must contain an unreviewed session"

    report_id = _record(conn)

    with conn.cursor() as cur:
        cur.execute(
            "select count(*) from reports where id = %s and kind = 'cohort'",
            (report_id,),
        )
        assert cur.fetchone()[0] == 1


def test_a_cohort_report_carries_no_session_id(conn: psycopg.Connection) -> None:
    """0001's check constraint: a cohort report has a cohort and no session.

    The constraint would reject the row anyway; this asserts the function writes
    the shape the constraint wants rather than relying on a later reader to
    infer it.
    """
    report_id = _record(conn)

    with conn.cursor() as cur:
        cur.execute(
            "select session_id, cohort_id from reports where id = %s", (report_id,)
        )
        session_id, cohort_id = cur.fetchone()

    assert session_id is None
    assert str(cohort_id) == COHORT_A


# ── idempotency ──────────────────────────────────────────────────────────────


def test_recording_is_idempotent_and_returns_the_same_report(
    conn: psycopg.Connection,
) -> None:
    """The retry a dead worker causes must rewrite its own row, not add one.

    The runner returns a job to `pending` whenever a worker dies between the
    work and `complete_job` — the single most likely thing to happen on
    free-tier compute (0008_job_runner.sql's header).
    """
    first = _record(conn)
    second = _record(conn, path="demo-academy/cohort-moved.pdf")

    assert first == second

    with conn.cursor() as cur:
        cur.execute("select storage_path from reports where id = %s", (first,))
        assert cur.fetchone()[0] == "demo-academy/cohort-moved.pdf", (
            "the row must point at the object that actually exists"
        )

        cur.execute(
            "select count(*) from reports where cohort_id = %s and kind = 'cohort' "
            "and template_version = '1.0.0'",
            (COHORT_A,),
        )
        # The seed already ships one cohort report per organisation, so this
        # counts the seed row plus ours — not two of ours.
        assert cur.fetchone()[0] == 2


def test_a_new_template_version_writes_a_report_beside_the_old(
    conn: psycopg.Connection,
) -> None:
    """A template change must not destroy the PDF it replaces (R1's shape).

    A school that downloaded last term's report should still be able to open the
    document it actually received, rendered by the template that produced it.
    """
    first = _record(conn, path="demo-academy/cohort-v1.pdf")
    second = _record(
        conn, path="demo-academy/cohort-v2.pdf", template_version="2.0.0"
    )

    assert first != second

    with conn.cursor() as cur:
        cur.execute(
            "select count(*) from reports where cohort_id = %s and kind = 'cohort'",
            (COHORT_A,),
        )
        # One seeded, plus the two written here.
        assert cur.fetchone()[0] == 3


# ── the structural differences from record_student_report ────────────────────


def test_recording_a_cohort_report_queues_no_email(conn: psycopg.Connection) -> None:
    """The difference from 0010, asserted rather than inferred from reading.

    §15 lists no cohort delivery template and a cohort has no single recipient —
    it belongs to an organisation, not a person. The counsellor downloads it
    from the dashboard, where the URL is minted on click and lives five minutes.
    """
    with conn.cursor() as cur:
        cur.execute("select count(*) from jobs where kind = 'send_email'")
        before = cur.fetchone()[0]

    _record(conn)

    with conn.cursor() as cur:
        cur.execute("select count(*) from jobs where kind = 'send_email'")
        assert cur.fetchone()[0] == before, "a cohort report queued an email"


def test_a_cohort_report_writes_an_audit_row(conn: psycopg.Connection) -> None:
    """The institution's copy is the one a school might later dispute the
    provenance of — who generated it, when, and against which template."""
    report_id = _record(conn)

    with conn.cursor() as cur:
        cur.execute(
            "select meta from audit_log where action = 'report.rendered' "
            "and subject = %s",
            (COHORT_A,),
        )
        rows = cur.fetchall()

    assert rows, "no audit row for the cohort render"
    meta = rows[-1][0]
    assert meta["kind"] == "cohort"
    assert meta["report_id"] == report_id
    assert meta["template_version"] == "1.0.0"


# ── who may call it ──────────────────────────────────────────────────────────


def test_record_cohort_report_is_not_callable_by_a_logged_in_user(
    conn: psycopg.Connection,
) -> None:
    """It is SECURITY DEFINER and writes `reports`, which has no write policy.

    Without the revoke, any authenticated user could call it directly and
    manufacture a report row pointing at any storage path in the bucket — the
    path is an argument, not something the function derives, so that includes
    another school's object.
    """
    # `as_user` outermost, so `conn.transaction()` exits first and has rolled
    # back before `reset role` runs. Innermost, `reset role` hits an aborted
    # transaction and raises InFailedSqlTransaction, replacing the
    # InsufficientPrivilege this test asserts — see the longer note in
    # `test_student_report.py`.
    for role in (harness.COUNSELLOR_A, harness.PSYCHOLOGIST_A, harness.ORG_ADMIN_A):
        with (
            harness.as_user(conn, role),
            pytest.raises(psycopg.errors.InsufficientPrivilege),
            conn.transaction(),
        ):
            conn.execute(
                "select record_cohort_report(%s, 'x.pdf', '1.0.0', '1.0.0')",
                (COHORT_A,),
            )


def test_an_unknown_cohort_is_refused_by_name(conn: psycopg.Connection) -> None:
    """The message lands in `jobs.last_error` and is read by whoever decides
    whether a failed render is a bug or a deleted cohort."""
    missing = "c0000009-0000-4000-8000-00000000000f"

    with pytest.raises(psycopg.errors.RaiseException, match="no cohort"), conn.transaction():
        _record(conn, cohort_id=missing)


# ── a guard on the fixture, not on the product ───────────────────────────────


def test_the_seed_cohort_is_too_small_for_a_percentage(
    conn: psycopg.Connection,
) -> None:
    """Not a product assertion — a tripwire on the seed.

    `demo_org.sql` gives cohort A two participants, so every `intended_field`
    group in it has n=1 and §9's rule withholds every percentage. That is what
    makes the seed a good fixture for the small-group path and a useless one for
    asserting rates. If a future seed change pushes it past ten, the aggregate
    tests upstream would silently start exercising a different branch.
    """
    with conn.cursor() as cur:
        cur.execute(
            "select count(*) from participants where cohort_id = %s", (COHORT_A,)
        )
        assert cur.fetchone()[0] < 10

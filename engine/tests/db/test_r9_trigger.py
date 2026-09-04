"""R9 at the database level (plan.md R9, §5, §18.1, Appendix B).

R9 is the rule that no student report leaves the system without a psychologist's
sign-off. It is enforced by `trg_enforce_review_before_student_report`, not by
application code, precisely so that a bug in the web app or the engine cannot
produce an unreviewed report. These tests are what make that claim checkable.

Every insert here runs as the connection owner rather than through `as_user`.
That is deliberate and load-bearing — see `harness.insert_student_report`.
"""

import psycopg
import pytest

from . import harness


def test_r9_trigger_blocks_unconfirmed(conn: psycopg.Connection) -> None:
    """A student report for a session with no review must not be insertable."""
    session_id = harness.make_session(conn, suffix="9001")

    # The exception aborts the transaction, so the insert gets its own nested
    # one — psycopg emits a SAVEPOINT, the failure rolls back to it, and the
    # enclosing per-test transaction stays usable for the assertions below.
    with pytest.raises(psycopg.errors.RaiseException, match="R9 violation"), conn.transaction():
        harness.insert_student_report(conn, session_id)

    with conn.cursor() as cur:
        cur.execute("select count(*) from reports where session_id = %s", (session_id,))
        assert cur.fetchone()[0] == 0


def test_r9_trigger_blocks_in_progress_review(conn: psycopg.Connection) -> None:
    """A review that exists but is not confirmed is not sign-off.

    `in_progress` is where a session sits while the psychologist is still
    working it (§9.1's "Save draft"). The trigger tests `status = 'confirmed'`,
    not the mere existence of a row; this is the test that would catch that
    distinction being dropped.
    """
    session_id = harness.make_session(conn, suffix="9002")
    conn.execute(
        """
        insert into reviews (session_id, reviewer_id, status, interview_notes)
        values (%s, %s, 'in_progress', 'Partway through.')
        """,
        (session_id, harness.PSYCHOLOGIST_A),
    )

    with pytest.raises(psycopg.errors.RaiseException, match="R9 violation"), conn.transaction():
        harness.insert_student_report(conn, session_id)


def test_r9_trigger_allows_confirmed(conn: psycopg.Connection) -> None:
    """Once a confirmed review exists, the same insert succeeds."""
    session_id = harness.make_session(conn, suffix="9003")
    harness.confirm_review(conn, session_id)

    harness.insert_student_report(conn, session_id)

    with conn.cursor() as cur:
        cur.execute(
            "select kind from reports where session_id = %s",
            (session_id,),
        )
        assert cur.fetchone() == ("student",)


def test_r9_trigger_blocks_update_to_student(conn: psycopg.Connection) -> None:
    """Repointing an existing report at an unreviewed session must also fail.

    `0003_reviews.sql` created the trigger as `before insert` only, which left
    this path open. `0004_r9_trigger_update.sql` closes it. Appendix B states R9
    as an invariant over rows — "No `reports` row for a student ever exists
    without a confirmed `reviews` row" — and an insert-only trigger does not
    deliver that sentence.
    """
    reviewed = harness.make_session(conn, suffix="9004")
    harness.confirm_review(conn, reviewed)
    harness.insert_student_report(conn, reviewed)

    unreviewed = harness.make_session(conn, suffix="9005")

    with pytest.raises(psycopg.errors.RaiseException, match="R9 violation"), conn.transaction():
        conn.execute(
            "update reports set session_id = %s where session_id = %s",
            (unreviewed, reviewed),
        )

    with conn.cursor() as cur:
        cur.execute("select count(*) from reports where session_id = %s", (unreviewed,))
        assert cur.fetchone()[0] == 0


def test_r9_trigger_ignores_cohort_reports(conn: psycopg.Connection) -> None:
    """Cohort reports carry no session and are not gated by R9.

    R9 protects the individual student's report. A cohort report is aggregate
    output for the institution (§10) and has no review to wait on; if the
    trigger blocked it, M11 would be unbuildable.
    """
    conn.execute(
        """
        insert into reports (kind, cohort_id, storage_path, template_version, engine_version)
        values ('cohort', %s, 'reports/harness-cohort.pdf', '1.0.0', '1.0.0')
        """,
        (harness.COHORT_A,),
    )

    with conn.cursor() as cur:
        cur.execute(
            "select count(*) from reports where cohort_id = %s and kind = 'cohort'",
            (harness.COHORT_A,),
        )
        # One from the seed, one from this test.
        assert cur.fetchone()[0] == 2

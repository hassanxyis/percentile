"""The counsellor/psychologist boundary on `reviews` (plan.md §5, §16, §18.1).

`reviews.interview_notes` holds the psychologist's own working notes about a
sixteen-year-old. §16 says to treat it with the same access restriction as health
data even though it is not health data. plan.md §5 is blunt about why this needs a
test: "a counsellor reading interview notes is the kind of leak that ends a
pilot."

The mechanism is unusual and worth restating, because it looks like an omission:
a counsellor gets **no** SELECT policy on `reviews` at all. RLS is row-scoped, not
column-scoped, so a second policy admitting counsellors to their organisation's
review rows would hand them every column, `interview_notes` included. Progress
tracking goes through `review_progress_for_session()` instead, which returns two
columns and nothing else.
"""

import psycopg

from . import harness


def test_review_rls_boundary(conn: psycopg.Connection) -> None:
    """A psychologist reads interview notes; a counsellor cannot reach the row."""
    with harness.as_user(conn, harness.PSYCHOLOGIST_A), conn.cursor() as cur:
        cur.execute(
            "select interview_notes from reviews where session_id = %s",
            (harness.SESSION_A_CONFIRMED,),
        )
        row = cur.fetchone()
        assert row is not None, "the reviewing psychologist must be able to read the review"
        assert row[0], "seed must carry non-null notes or this test proves nothing"

    with harness.as_user(conn, harness.COUNSELLOR_A), conn.cursor() as cur:
        cur.execute(
            "select interview_notes from reviews where session_id = %s",
            (harness.SESSION_A_CONFIRMED,),
        )
        assert cur.fetchone() is None, "a counsellor reached interview_notes"


def test_org_admin_reads_reviews(conn: psycopg.Connection) -> None:
    """§16 grants org_admin the same access as the reviewing psychologist."""
    with harness.as_user(conn, harness.ORG_ADMIN_A), conn.cursor() as cur:
        cur.execute(
            "select interview_notes from reviews where session_id = %s",
            (harness.SESSION_A_CONFIRMED,),
        )
        assert cur.fetchone() is not None


def test_psychologist_cannot_cross_organisations(conn: psycopg.Connection) -> None:
    """The boundary is organisation-scoped as well as role-scoped.

    Holding the psychologist role at one institution must not open another
    institution's notes.
    """
    with harness.as_user(conn, harness.PSYCHOLOGIST_B), conn.cursor() as cur:
        cur.execute(
            "select interview_notes from reviews where session_id = %s",
            (harness.SESSION_A_CONFIRMED,),
        )
        assert cur.fetchone() is None

        cur.execute("select session_id from reviews")
        assert [row[0] for row in cur.fetchall()] == [harness.SESSION_B_CONFIRMED]


def test_counsellor_reads_progress_through_the_function(conn: psycopg.Connection) -> None:
    """`review_progress_for_session()` gives a counsellor status without notes.

    This is the narrow channel that replaces a row-level policy. It returns
    `status` and `confirmed_at` only — the counsellor's dashboard needs to show
    a session as `pending_review` or `confirmed` (§13) and needs nothing else.
    """
    with harness.as_user(conn, harness.COUNSELLOR_A), conn.cursor() as cur:
        cur.execute(
            "select status, confirmed_at from review_progress_for_session(%s)",
            (harness.SESSION_A_CONFIRMED,),
        )
        status, confirmed_at = cur.fetchone()
        assert status == "confirmed"
        assert confirmed_at is not None


def test_progress_function_is_organisation_scoped(conn: psycopg.Connection) -> None:
    """The SECURITY DEFINER function must not become a cross-tenant hole.

    It runs with the definer's privileges, so it bypasses RLS by construction —
    its own `c.organisation_id = auth_organisation_id()` clause is the only thing
    keeping it tenant-scoped. That clause is what this test guards.
    """
    with harness.as_user(conn, harness.COUNSELLOR_B), conn.cursor() as cur:
        cur.execute(
            "select status from review_progress_for_session(%s)",
            (harness.SESSION_A_CONFIRMED,),
        )
        assert cur.fetchall() == []


def test_career_directions_follow_the_same_boundary(conn: psycopg.Connection) -> None:
    """Confirmed directions are psychologist/org_admin-readable, not counsellor."""
    with harness.as_user(conn, harness.PSYCHOLOGIST_A), conn.cursor() as cur:
        cur.execute(
            "select count(*) from career_directions where review_id = %s",
            (harness.REVIEW_A,),
        )
        assert cur.fetchone()[0] == 2

    assert harness.count_as(conn, harness.COUNSELLOR_A, "career_directions") == 0
    assert harness.count_as(conn, harness.COUNSELLOR_A, "review_events") == 0


def test_review_tables_are_read_only_for_authenticated(conn: psycopg.Connection) -> None:
    """No insert policy exists anywhere, for any role.

    Every write goes through the service role in a server action (0003_reviews
    .sql). A psychologist writing directly from the browser would mean the
    review portal had grown a client-side write path, which is exactly what the
    read-only policy set is there to prevent.
    """
    with harness.as_user(conn, harness.PSYCHOLOGIST_A):
        try:
            with conn.transaction():
                conn.execute(
                    """
                    insert into review_events (review_id, actor, event)
                    values (%s, %s, 'note_added')
                    """,
                    (harness.REVIEW_A, harness.PSYCHOLOGIST_A),
                )
        except psycopg.errors.InsufficientPrivilege:
            pass
        else:
            raise AssertionError("an authenticated psychologist could write review_events")

"""The psychologist review actions at the database level (plan.md §9, M8).

`0009_review_actions.sql` is the only writer for `reviews`, `career_directions`
and `review_events`. It is tested here rather than through the web app because
what it guarantees is transactional and role-scoped, and neither property is
observable from TypeScript.

The centre of this file is M8's done-when, stated in plan.md §17:

    you can walk through one full session as the reviewer — read the flags,
    write a note, pick 2 directions, confirm — and a `reports` insert for that
    session then succeeds where it would have failed (R9) before confirmation.

`test_confirming_unblocks_the_r9_gate` is that sentence executed. The rest guard
the ways the function could satisfy it while still being wrong: by letting the
wrong role write, by leaking across organisations, by queueing a second render
job on a double-tap, or by leaving stale career directions behind.
"""

import psycopg
import pytest

from . import harness

# Hamza — seeded at `scored`, with a session and no review at all. The one
# session in the seed that a review can be built on from nothing.
UNREVIEWED_SESSION = "5e551011-0000-4000-8000-00000000a002"
UNREVIEWED_PARTICIPANT = "a0aaaaa2-0000-4000-8000-000000000002"

# Seed occupations (db/seed/demo_org.sql). career_directions.onet_soc_code is a
# FK, so a direction that names a code must name one of these.
NURSE = "29-1141.00"
DEVELOPER = "15-1252.00"

ONE_DIRECTION = '[{"onet_soc_code": "29-1141.00", "local_title": "Nursing", "rationale": "Fits."}]'
TWO_DIRECTIONS = (
    '[{"onet_soc_code": "29-1141.00", "local_title": "Nursing", "rationale": "Fits."},'
    ' {"onet_soc_code": "15-1252.00", "local_title": "Health informatics", "rationale": "Also."}]'
)


def save_review(
    conn: psycopg.Connection,
    session_id: str = UNREVIEWED_SESSION,
    reviewer_id: str = harness.PSYCHOLOGIST_A,
    status: str = "in_progress",
    interview_mode: str | None = "in_person",
    notes: str | None = "Spoke with the student.",
    flags_reviewed: str = "[]",
    directions: str = "[]",
) -> str:
    """Call `save_review` as the connection owner — the service-role stand-in.

    Every write in this product is server-side (0002_rls.sql), and this function
    is revoked from `authenticated` entirely, so the owner is the faithful
    caller. `harness.as_user` is for the read-policy tests.
    """
    with conn.cursor() as cur:
        cur.execute(
            "select save_review(%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)",
            (
                session_id,
                reviewer_id,
                status,
                interview_mode,
                notes,
                flags_reviewed,
                directions,
            ),
        )
        return str(cur.fetchone()[0])


def participant_status(conn: psycopg.Connection, participant_id: str) -> str:
    with conn.cursor() as cur:
        cur.execute("select status from participants where id = %s", (participant_id,))
        return cur.fetchone()[0]


def render_jobs(conn: psycopg.Connection, session_id: str) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            select count(*) from jobs
            where kind = 'render_student'
              and payload ->> 'session_id' = %s
            """,
            (session_id,),
        )
        return cur.fetchone()[0]


def directions_for(conn: psycopg.Connection, review_id: str) -> list[tuple]:
    with conn.cursor() as cur:
        cur.execute(
            """
            select rank, onet_soc_code, local_title
            from career_directions
            where review_id = %s
            order by rank
            """,
            (review_id,),
        )
        return cur.fetchall()


# ── M8's done-when ───────────────────────────────────────────────────────────


def test_confirming_unblocks_the_r9_gate(conn: psycopg.Connection) -> None:
    """The milestone, end to end: the same insert fails, then succeeds.

    Both halves matter. Asserting only that the insert works after confirmation
    would pass just as well if the R9 trigger had been dropped altogether — the
    "fails before" half is what proves the gate was closed to begin with.
    """
    session_id = harness.make_session(conn, suffix="8001")

    with pytest.raises(psycopg.errors.RaiseException, match="R9 violation"), conn.transaction():
        harness.insert_student_report(conn, session_id)

    save_review(conn, session_id=session_id, status="confirmed", directions=TWO_DIRECTIONS)

    harness.insert_student_report(conn, session_id)

    with conn.cursor() as cur:
        cur.execute("select kind from reports where session_id = %s", (session_id,))
        assert cur.fetchone() == ("student",)


def test_confirming_enqueues_exactly_one_render_student(conn: psycopg.Connection) -> None:
    """R9's other half: the render job is queued here and nowhere else.

    0008's `record_score` deliberately does not enqueue it — "the render job is
    R9's business and only M8's confirm action may queue it".
    """
    assert render_jobs(conn, UNREVIEWED_SESSION) == 0

    save_review(conn, status="confirmed", directions=ONE_DIRECTION)

    assert render_jobs(conn, UNREVIEWED_SESSION) == 1


def test_saving_a_draft_does_not_enqueue_a_render(conn: psycopg.Connection) -> None:
    """`in_progress` is the psychologist still working (§9.1). Nothing renders."""
    save_review(conn, status="in_progress", directions=ONE_DIRECTION)

    assert render_jobs(conn, UNREVIEWED_SESSION) == 0
    assert participant_status(conn, UNREVIEWED_PARTICIPANT) == "reviewed"


def test_reconfirming_does_not_queue_a_second_render(conn: psycopg.Connection) -> None:
    """A double-tapped Confirm must not produce two reports and two emails.

    The guard is on the status the row held *before* the write; after it, every
    path looks confirmed.
    """
    save_review(conn, status="confirmed", directions=ONE_DIRECTION)
    save_review(conn, status="confirmed", directions=ONE_DIRECTION)

    assert render_jobs(conn, UNREVIEWED_SESSION) == 1


def test_confirm_requires_at_least_one_direction(conn: psycopg.Connection) -> None:
    """§9.1: "At least 1, at most 3". Nothing is written when it cannot complete."""
    with pytest.raises(psycopg.errors.RaiseException, match="at least one"), conn.transaction():
        save_review(conn, status="confirmed", directions="[]")

    with conn.cursor() as cur:
        cur.execute("select count(*) from reviews where session_id = %s", (UNREVIEWED_SESSION,))
        assert cur.fetchone()[0] == 0


def test_at_most_three_directions(conn: psycopg.Connection) -> None:
    """The upper bound, enforced before any write.

    `career_directions.rank` has `check (rank between 1 and 3)`, so a fourth
    would fail anyway — but as a constraint violation mid-transaction rather
    than as a message naming the rule.
    """
    four = (
        '[{"local_title": "One"}, {"local_title": "Two"},'
        ' {"local_title": "Three"}, {"local_title": "Four"}]'
    )
    with pytest.raises(psycopg.errors.RaiseException, match="at most 3"), conn.transaction():
        save_review(conn, status="confirmed", directions=four)


# ── the role and tenancy boundary ────────────────────────────────────────────


def test_a_counsellor_may_not_write_a_review(conn: psycopg.Connection) -> None:
    """The §16 boundary, on the write side.

    0003_reviews.sql gives a counsellor no read policy on `reviews` at all, so
    that `interview_notes` stays out of reach. A counsellor who could *write* the
    column would walk straight around that.
    """
    with pytest.raises(psycopg.errors.RaiseException, match="may not review"), conn.transaction():
        save_review(conn, reviewer_id=harness.COUNSELLOR_A)


def test_org_admin_may_review(conn: psycopg.Connection) -> None:
    """§16 grants org_admin the same access as the reviewing psychologist."""
    review_id = save_review(conn, reviewer_id=harness.ORG_ADMIN_A, status="confirmed",
                            directions=ONE_DIRECTION)
    assert review_id


def test_a_reviewer_cannot_reach_another_organisation(conn: psycopg.Connection) -> None:
    """Holding the psychologist role at one school must not open another's.

    The engine and the web app both call this with the service role, which
    bypasses RLS — so this check inside the function is the only thing standing
    between a guessed session id and a cross-tenant write.
    """
    with pytest.raises(psycopg.errors.RaiseException, match="not in the organisation"), \
            conn.transaction():
        save_review(conn, reviewer_id=harness.PSYCHOLOGIST_B)


def test_unknown_status_is_refused(conn: psycopg.Connection) -> None:
    """`reviews.status` has no check constraint; this function is the one place."""
    with pytest.raises(psycopg.errors.RaiseException, match="unknown review status"), \
            conn.transaction():
        save_review(conn, status="approved")


# ── the state machine and the audit trail ────────────────────────────────────


def test_send_back_sets_needs_more_info(conn: psycopg.Connection) -> None:
    """§9.1's Send back: a second conversation before confirming."""
    save_review(conn, status="needs_more_info")

    assert participant_status(conn, UNREVIEWED_PARTICIPANT) == "needs_more_info"
    assert render_jobs(conn, UNREVIEWED_SESSION) == 0


def test_reopening_keeps_one_review_row(conn: psycopg.Connection) -> None:
    """0003: re-opening "updates this row rather than creating a new one,
    so history stays on one thread". The thread is `review_events`."""
    first = save_review(conn, status="in_progress")
    second = save_review(conn, status="needs_more_info")
    third = save_review(conn, status="confirmed", directions=ONE_DIRECTION)

    assert first == second == third

    with conn.cursor() as cur:
        cur.execute("select count(*) from reviews where session_id = %s", (UNREVIEWED_SESSION,))
        assert cur.fetchone()[0] == 1

        # Sorted, not in insertion order. `created_at` defaults to `now()`,
        # which is the TRANSACTION timestamp — all three rows carry the same
        # value inside one test, so `order by created_at` has no defined order
        # and asserting a sequence would be a coin flip. What is worth pinning
        # is that each action left its own distinct trace.
        cur.execute(
            "select event from review_events where review_id = %s",
            (first,),
        )
        assert sorted(row[0] for row in cur.fetchall()) == sorted(
            ["opened", "reopened", "confirmed"]
        )


def test_confirmed_at_survives_an_edit_but_a_send_back_clears_it(
    conn: psycopg.Connection,
) -> None:
    """`confirmed_at` records when a human signed off.

    Editing a note afterwards is not a second sign-off, so the timestamp stands.
    A send-back is a withdrawal of sign-off, so it clears — and R9 then blocks
    the report again, which is the invariant 0004 widened the trigger to hold.
    """
    save_review(conn, status="confirmed", directions=ONE_DIRECTION)

    # Backdate it by hand. `now()` is the TRANSACTION timestamp, so a second
    # save inside this test would write the identical value and the assertion
    # below would hold whether the code preserved the timestamp or re-stamped
    # it. Planting a distinguishable one is what gives the test teeth.
    with conn.cursor() as cur:
        cur.execute(
            """
            update reviews set confirmed_at = timestamptz '2026-01-01 09:00:00+00'
            where session_id = %s
            returning confirmed_at
            """,
            (UNREVIEWED_SESSION,),
        )
        original = cur.fetchone()[0]

    save_review(conn, status="confirmed", notes="Edited.", directions=ONE_DIRECTION)
    with conn.cursor() as cur:
        cur.execute(
            "select confirmed_at from reviews where session_id = %s", (UNREVIEWED_SESSION,)
        )
        assert cur.fetchone()[0] == original, "an edit re-stamped the sign-off time"

    save_review(conn, status="needs_more_info")
    with conn.cursor() as cur:
        cur.execute(
            "select confirmed_at from reviews where session_id = %s", (UNREVIEWED_SESSION,)
        )
        assert cur.fetchone()[0] is None


def test_directions_are_replaced_not_accumulated(conn: psycopg.Connection) -> None:
    """A removed direction must not survive on the student's report.

    Delete-then-insert rather than upsert: with `unique (review_id, rank)` a
    review that goes from three directions to two would otherwise keep a stale
    rank 3 that the psychologist deliberately removed.
    """
    review_id = save_review(conn, status="in_progress", directions=TWO_DIRECTIONS)
    assert [row[0] for row in directions_for(conn, review_id)] == [1, 2]

    save_review(conn, status="in_progress", directions=ONE_DIRECTION)

    rows = directions_for(conn, review_id)
    assert [row[0] for row in rows] == [1]
    assert rows[0][1] == NURSE


def test_rank_comes_from_array_order(conn: psycopg.Connection) -> None:
    """Rank is the payload's position, not a client-supplied field.

    Trusting a client `rank` invites a duplicate that trips the unique
    constraint half way through the transaction.
    """
    reversed_payload = (
        '[{"onet_soc_code": "15-1252.00", "local_title": "Health informatics", "rank": 3},'
        ' {"onet_soc_code": "29-1141.00", "local_title": "Nursing", "rank": 1}]'
    )
    review_id = save_review(conn, status="in_progress", directions=reversed_payload)

    assert directions_for(conn, review_id) == [
        (1, DEVELOPER, "Health informatics"),
        (2, NURSE, "Nursing"),
    ]


def test_a_direction_needs_a_local_title(conn: psycopg.Connection) -> None:
    """`local_title` is what the student reads; blank is a blank line on a report."""
    with pytest.raises(psycopg.errors.RaiseException, match="local_title"), conn.transaction():
        save_review(
            conn,
            status="confirmed",
            directions='[{"onet_soc_code": "29-1141.00", "local_title": "   "}]',
        )


def test_null_directions_read_as_empty(conn: psycopg.Connection) -> None:
    """A JSON `null` is what a client sends for "none", and must not raise.

    `jsonb_array_length` on a scalar raises "cannot get array length of a
    scalar" — a save that fails with a Postgres internal message rather than
    saving a draft with no directions yet.
    """
    review_id = save_review(conn, status="in_progress", directions="null")
    assert directions_for(conn, review_id) == []


def test_a_direction_without_a_soc_code_is_free_text(conn: psycopg.Connection) -> None:
    """`local_title` stands alone — 0003: "shown to the student even if
    onet_soc_code is null". An empty string would fail the FK, so it maps to NULL.
    """
    review_id = save_review(
        conn,
        status="confirmed",
        directions='[{"onet_soc_code": "", "local_title": "Family business", "rationale": "x"}]',
    )

    assert directions_for(conn, review_id) == [(1, None, "Family business")]

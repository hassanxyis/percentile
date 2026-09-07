"""M9's done-when, against a real Postgres (plan.md §14, §17 M9).

`test_report_student.py` asserts what the document says; this file asserts the
database facts the render depends on and the ones it must not violate. Both are
needed and neither substitutes for the other — a report whose content is right
but whose `reports` row was written for an unreviewed session is still an R9
breach, and Postgres is the only thing that can prove the gate held.

The centre is `test_confirming_a_review_lets_the_report_row_land`, which walks
M8's confirm through `save_review()` and then does the thing R9 gates. It is the
sentence in §17 M9 executed literally: confirm, then a student `reports` row
exists where before it could not.
"""

from __future__ import annotations

import psycopg
import pytest

from . import harness


def _directions(count: int = 1) -> str:
    """A `save_review` directions payload, as the server action sends it."""
    import json

    return json.dumps(
        [
            {
                "onet_soc_code": "29-1141.00",
                "local_title": f"Direction {n}",
                "rationale": "Written by the reviewing psychologist.",
            }
            for n in range(1, count + 1)
        ]
    )


def _confirm(conn: psycopg.Connection, session_id: str, count: int = 1) -> str:
    with conn.cursor() as cur:
        cur.execute(
            "select save_review(%s, %s, 'confirmed', 'in_person', %s, '[]'::jsonb, %s::jsonb)",
            (session_id, harness.PSYCHOLOGIST_A, "Spoke with them.", _directions(count)),
        )
        return str(cur.fetchone()[0])


# ── the milestone's done-when ────────────────────────────────────────────────


def test_confirming_a_review_lets_the_report_row_land(conn: psycopg.Connection) -> None:
    """§17 M9, executed: the same insert fails before confirmation and succeeds after.

    This is the whole R9 mechanism end to end — M8's action, M9's write, and the
    trigger between them — rather than the trigger tested against a hand-written
    `reviews` row. The two differ in the way that matters: this one proves the
    *application path* produces a state the gate accepts.
    """
    session_id = harness.make_session(conn, suffix="9101")

    with pytest.raises(psycopg.errors.RaiseException, match="R9 violation"), conn.transaction():
        harness.insert_student_report(conn, session_id)

    _confirm(conn, session_id)
    harness.insert_student_report(conn, session_id)

    with conn.cursor() as cur:
        cur.execute(
            "select count(*) from reports where session_id = %s and kind = 'student'",
            (session_id,),
        )
        assert cur.fetchone()[0] == 1


def test_the_render_job_is_queued_by_confirming_and_by_nothing_else(
    conn: psycopg.Connection,
) -> None:
    """R9: `render_student` is enqueued in `save_review` and nowhere else.

    Now that a handler exists for the kind, a `render_student` row is no longer
    a job that fails loudly — it renders and emails a minor's report. The guard
    that it can only originate from a confirm is therefore more load-bearing
    after M9 than it was during M8, not less.
    """
    session_id = harness.make_session(conn, suffix="9102")

    with conn.cursor() as cur:
        cur.execute(
            "select count(*) from jobs where kind = 'render_student' "
            "and payload ->> 'session_id' = %s",
            (session_id,),
        )
        assert cur.fetchone()[0] == 0

        # A draft save is not sign-off, and must not queue a render.
        cur.execute(
            "select save_review(%s, %s, 'in_progress', null, %s, '[]'::jsonb, %s::jsonb)",
            (session_id, harness.PSYCHOLOGIST_A, "Partway through.", _directions()),
        )
        cur.execute(
            "select count(*) from jobs where kind = 'render_student' "
            "and payload ->> 'session_id' = %s",
            (session_id,),
        )
        assert cur.fetchone()[0] == 0

        _confirm(conn, session_id)
        cur.execute(
            "select count(*) from jobs where kind = 'render_student' "
            "and payload ->> 'session_id' = %s",
            (session_id,),
        )
        assert cur.fetchone()[0] == 1


def test_confirming_twice_does_not_queue_a_second_render(conn: psycopg.Connection) -> None:
    """A double-tapped Confirm must not produce two reports and two emails.

    The same guard M8 tested, retested here because its consequence changed: it
    now stops a student receiving two links to two renders of the same report.
    """
    session_id = harness.make_session(conn, suffix="9103")

    _confirm(conn, session_id)
    _confirm(conn, session_id, count=2)

    with conn.cursor() as cur:
        cur.execute(
            "select count(*) from jobs where kind = 'render_student' "
            "and payload ->> 'session_id' = %s",
            (session_id,),
        )
        assert cur.fetchone()[0] == 1


# ── what the render loads ────────────────────────────────────────────────────


def test_the_confirmed_directions_are_readable_in_rank_order(conn: psycopg.Connection) -> None:
    """The report's page 10 reads these, and rank is the order on the page."""
    session_id = harness.make_session(conn, suffix="9104")
    _confirm(conn, session_id, count=3)

    with conn.cursor() as cur:
        cur.execute(
            """
            select cd.rank, cd.local_title, cd.rationale
            from career_directions cd
            join reviews r on r.id = cd.review_id
            where r.session_id = %s
            order by cd.rank
            """,
            (session_id,),
        )
        rows = cur.fetchall()

    assert [row[0] for row in rows] == [1, 2, 3]
    assert rows[0][1] == "Direction 1"
    assert all(row[2] for row in rows), "a rationale is the psychologist's own words (§9.1)"


def test_a_reports_row_records_the_template_version(conn: psycopg.Connection) -> None:
    """`TEMPLATE_VERSION` is stamped so an old PDF stays reproducible after a
    template change (CLAUDE.md · config)."""
    session_id = harness.make_session(conn, suffix="9105")
    _confirm(conn, session_id)
    harness.insert_student_report(conn, session_id)

    with conn.cursor() as cur:
        cur.execute(
            "select template_version, engine_version, storage_path from reports "
            "where session_id = %s",
            (session_id,),
        )
        template_version, engine_version, storage_path = cur.fetchone()

    assert template_version
    assert engine_version
    assert storage_path


def test_a_student_report_row_cannot_be_updated_onto_an_unreviewed_session(
    conn: psycopg.Connection,
) -> None:
    """0004 widened the trigger to `before insert or update` for this.

    Now that reports are real, the update path is the one a bug would take:
    re-pointing an existing row at another session is how an unreviewed student
    would acquire a report without an insert ever happening.
    """
    confirmed = harness.make_session(conn, suffix="9106")
    unreviewed = harness.make_session(conn, suffix="9107")
    _confirm(conn, confirmed)
    harness.insert_student_report(conn, confirmed)

    with pytest.raises(psycopg.errors.RaiseException, match="R9 violation"), conn.transaction():
        conn.execute(
            "update reports set session_id = %s where session_id = %s",
            (unreviewed, confirmed),
        )


# ── record_student_report (0010) ─────────────────────────────────────────────


def _record(conn: psycopg.Connection, session_id: str, path: str = "org/x.pdf") -> str:
    with conn.cursor() as cur:
        cur.execute(
            "select record_student_report(%s, %s, '1.0.0', '1.0.0')", (session_id, path)
        )
        return str(cur.fetchone()[0])


def test_recording_a_report_queues_exactly_one_delivery_email(
    conn: psycopg.Connection,
) -> None:
    """The guard that stops a student getting two links to one report.

    This is why the write moved out of Python: the handler's version filtered
    `jobs` with PostgREST's JSON-operator syntax, used nowhere else in this
    codebase, and its failure mode is silent — a filter matching nothing queues
    a second email rather than raising. Here it is an ordinary `where`, run
    against real Postgres.
    """
    session_id = harness.make_session(conn, suffix="9108")
    _confirm(conn, session_id)

    _record(conn, session_id)
    _record(conn, session_id)  # the retry a dead worker causes

    with conn.cursor() as cur:
        cur.execute(
            "select count(*) from jobs where kind = 'send_email' "
            "and payload ->> 'template' = 'student_report' "
            "and payload ->> 'session_id' = %s",
            (session_id,),
        )
        assert cur.fetchone()[0] == 1

        cur.execute(
            "select count(*) from reports where session_id = %s and kind = 'student'",
            (session_id,),
        )
        assert cur.fetchone()[0] == 1


def test_recording_is_idempotent_and_returns_the_same_report(
    conn: psycopg.Connection,
) -> None:
    session_id = harness.make_session(conn, suffix="9109")
    _confirm(conn, session_id)

    first = _record(conn, session_id)
    second = _record(conn, session_id, path="org/moved.pdf")

    assert first == second

    with conn.cursor() as cur:
        cur.execute("select storage_path from reports where id = %s", (first,))
        assert cur.fetchone()[0] == "org/moved.pdf", (
            "the row must point at the object that actually exists"
        )


def test_recording_refuses_a_session_without_a_confirmed_review(
    conn: psycopg.Connection,
) -> None:
    """R9 inside the function, independent of the trigger.

    Three checks guard this insert — the handler before it renders, this, and
    the trigger. None is redundant: each covers a caller that skipped the one
    before it.
    """
    session_id = harness.make_session(conn, suffix="9110")

    with pytest.raises(psycopg.errors.RaiseException, match="R9"), conn.transaction():
        _record(conn, session_id)

    with conn.cursor() as cur:
        cur.execute("select count(*) from jobs where kind = 'send_email' "
                    "and payload ->> 'session_id' = %s", (session_id,))
        assert cur.fetchone()[0] == 0, "an email was queued for an unreviewed student"


def test_a_new_template_version_writes_a_new_report_beside_the_old(
    conn: psycopg.Connection,
) -> None:
    """A template change must not destroy the PDF it replaces (R1's shape).

    The email is still queued once: the student is told about their report, not
    about each re-render of it.
    """
    session_id = harness.make_session(conn, suffix="9111")
    _confirm(conn, session_id)
    _record(conn, session_id)

    with conn.cursor() as cur:
        cur.execute(
            "select record_student_report(%s, 'org/v2.pdf', '2.0.0', '1.0.0')", (session_id,)
        )
        cur.execute(
            "select count(*) from reports where session_id = %s and kind = 'student'",
            (session_id,),
        )
        assert cur.fetchone()[0] == 2

        cur.execute(
            "select count(*) from jobs where kind = 'send_email' "
            "and payload ->> 'template' = 'student_report' "
            "and payload ->> 'session_id' = %s",
            (session_id,),
        )
        assert cur.fetchone()[0] == 1


def test_record_student_report_is_not_callable_by_a_logged_in_user(
    conn: psycopg.Connection,
) -> None:
    """It is SECURITY DEFINER and writes `reports`, which has no write policy.

    Without the revoke, any authenticated counsellor could call it directly and
    manufacture a report row — the function runs as its owner and would bypass
    RLS entirely.
    """
    session_id = harness.make_session(conn, suffix="9112")
    _confirm(conn, session_id)

    for role in (harness.COUNSELLOR_A, harness.PSYCHOLOGIST_A):
        with (
            pytest.raises(psycopg.errors.InsufficientPrivilege),
            conn.transaction(),
            harness.as_user(conn, role),
        ):
            conn.execute(
                "select record_student_report(%s, 'x.pdf', '1.0.0', '1.0.0')",
                (session_id,),
            )


# ── the storage path is not a leak ───────────────────────────────────────────


def test_no_report_storage_path_carries_a_student_name(conn: psycopg.Connection) -> None:
    """Object keys appear in storage logs and inside the signed URL itself.

    Asserted against the seed's own rows, which were written before M9 existed —
    if a future migration or seed change starts naming files after students,
    this is what notices.
    """
    from app.jobs.handlers.render import storage_path

    with conn.cursor() as cur:
        cur.execute("select full_name from participants")
        names = [row[0] for row in cur.fetchall()]

    for name in names:
        path = storage_path("Demo Academy", harness.SESSION_A_CONFIRMED)
        for part in name.lower().split():
            assert part not in path.lower()

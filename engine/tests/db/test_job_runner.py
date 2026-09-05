"""The job runner at the database level (plan.md §11, §12, M7).

`0008_job_runner.sql` puts claiming, retrying, reaping and the score write into
Postgres. Each is here because getting it wrong is quiet:

* **Claiming is `for update skip locked`.** Two ticks running at once — a cron
  overlap, a manual POST during a scheduled run — must never take the same job.
  A select-then-update looks correct until the day they overlap.
* **Attempts increment at CLAIM time.** That is what makes a crashed worker
  eventually give up rather than retry forever, and it is why the stale reaper
  is mandatory rather than an optimisation.
* **The alert cannot alert about itself.** A failed `send_email` enqueues a
  `send_email`. Without a guard that is one new row per tick, forever.
* **The score write is one transaction and idempotent.** A score with no
  follow-up jobs is a student who is scored and invisible to the review queue —
  and R9 then means their report can never render.
"""

import psycopg
import pytest

from . import harness

# Seed items (db/seed/demo_org.sql). Two interests at 0..1, two personality at 1..5.
INTEREST_ITEM = "11eeee00-0000-4000-8000-000000000001"
PERSONALITY_ITEM = "11eeee00-0000-4000-8000-000000000003"

# Hamza — seeded at `scored` with a session and responses, and deliberately
# without a confirmed review. The session record_score can move forward.
SCORED_SESSION = "5e551011-0000-4000-8000-00000000a002"
SCORED_PARTICIPANT = "a0aaaaa2-0000-4000-8000-000000000002"

# Fatima — already `confirmed`, with a report. The one a rescore must not drag
# backwards into pending_review.
CONFIRMED_SESSION = "5e551011-0000-4000-8000-00000000a001"
CONFIRMED_PARTICIPANT = "a0aaaaa1-0000-4000-8000-000000000001"

ENGINE_VERSION = "1.0.0"

EMPTY_SCORE = ("{}", "{}", '{"instrument": null, "status": "module_not_administered"}', "[]")


# ── helpers ──────────────────────────────────────────────────────────────────


def enqueue(
    conn: psycopg.Connection,
    kind: str = "score_session",
    payload: str = '{"session_id": "x"}',
    status: str = "pending",
    attempts: int = 0,
    run_after: str = "now()",
) -> str:
    with conn.cursor() as cur:
        cur.execute(
            f"""
            insert into jobs (kind, payload, status, attempts, run_after)
            values (%s, %s::jsonb, %s, %s, {run_after})
            returning id
            """,  # noqa: S608 — run_after is a module-controlled SQL literal
            (kind, payload, status, attempts),
        )
        return str(cur.fetchone()[0])


def claim(conn: psycopg.Connection, limit: int = 10) -> list[str]:
    with conn.cursor() as cur:
        cur.execute("select id from claim_jobs(%s)", (limit,))
        return harness.first_column(cur)


def fail(conn: psycopg.Connection, job_id: str, error: str = "boom", max_attempts: int = 5) -> str:
    with conn.cursor() as cur:
        cur.execute("select fail_job(%s, %s, %s)", (job_id, error, max_attempts))
        return cur.fetchone()[0]


def job_row(conn: psycopg.Connection, job_id: str) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            "select status, attempts, last_error, run_after, claimed_at from jobs where id = %s",
            (job_id,),
        )
        row = cur.fetchone()
    return dict(
        zip(("status", "attempts", "last_error", "run_after", "claimed_at"), row, strict=True)
    )


def clear_seed_jobs(conn: psycopg.Connection) -> None:
    """The seed enqueues two jobs so the RLS test has rows to be denied.

    Cleared here so a claim assertion counts only what the test itself queued.
    """
    conn.execute("delete from jobs")


def score(
    conn: psycopg.Connection,
    session_id: str = SCORED_SESSION,
    engine_version: str = ENGINE_VERSION,
) -> str:
    with conn.cursor() as cur:
        cur.execute(
            "select record_score(%s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb)",
            (session_id, engine_version, *EMPTY_SCORE),
        )
        return str(cur.fetchone()[0])


def jobs_of_kind(conn: psycopg.Connection, kind: str, session_id: str | None = None) -> int:
    sql = "select count(*) from jobs where kind = %s"
    params: tuple = (kind,)
    if session_id:
        sql += " and payload ->> 'session_id' = %s"
        params += (session_id,)
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()[0]


# ── claiming ─────────────────────────────────────────────────────────────────


def test_claim_marks_running_and_increments_attempts(conn: psycopg.Connection) -> None:
    clear_seed_jobs(conn)
    job_id = enqueue(conn)

    assert claim(conn) == [job_id]

    row = job_row(conn, job_id)
    assert row["status"] == "running"
    assert row["attempts"] == 1
    assert row["claimed_at"] is not None


def test_claim_respects_the_limit(conn: psycopg.Connection) -> None:
    clear_seed_jobs(conn)
    for _ in range(5):
        enqueue(conn)

    assert len(claim(conn, limit=2)) == 2


def test_claim_ignores_a_future_run_after(conn: psycopg.Connection) -> None:
    """This is the backoff. A retry scheduled for four minutes' time must not be
    picked up by the tick thirty seconds later."""
    clear_seed_jobs(conn)
    enqueue(conn, run_after="now() + interval '1 hour'")

    assert claim(conn) == []


def test_claim_ignores_running_and_done(conn: psycopg.Connection) -> None:
    clear_seed_jobs(conn)
    enqueue(conn, status="running")
    enqueue(conn, status="done")
    enqueue(conn, status="failed")

    assert claim(conn) == []


def test_claim_takes_the_oldest_first(conn: psycopg.Connection) -> None:
    clear_seed_jobs(conn)
    old = enqueue(conn, run_after="now() - interval '1 hour'")
    enqueue(conn, run_after="now()")

    assert claim(conn, limit=1) == [old]


def test_two_workers_never_claim_the_same_job(db_url: str, conn: psycopg.Connection) -> None:
    """`for update skip locked`, which is the whole reason claiming is in SQL.

    Two live connections, both claiming inside open transactions. The second
    must skip the row the first has locked rather than block on it or take it.

    Runs on its own connections because the shared `conn` fixture holds one
    transaction that is rolled back — two overlapping claims cannot be expressed
    on a single connection.

    Two things here are deliberate and easy to "simplify" into a test that
    quietly breaks the rest of the suite:

    * It inserts one job and deletes exactly that job. An earlier draft cleared
      the whole table so the assertion could be `== [job_id]`. That committed,
      which would have destroyed the seed's `jobs` rows for every test after it
      — and `test_rls_cross_tenant` needs those rows to exist, or "an
      authenticated user sees zero rows" passes because the table is empty.
    * Both claimers roll back. Committing would leave the seed's pending job
      permanently `running`, with the same consequence one test later.

    So the assertion is membership, not equality: this job is claimed by exactly
    one of two concurrent claimers.
    """
    with psycopg.connect(db_url) as setup:
        setup.execute("set search_path = public, pg_catalog")
        with setup.cursor() as cur:
            cur.execute(
                "insert into jobs (kind, payload) values "
                "('score_session', '{\"skip_locked_probe\": true}'::jsonb) returning id"
            )
            job_id = str(cur.fetchone()[0])
        setup.commit()

        try:
            with psycopg.connect(db_url) as first, psycopg.connect(db_url) as second:
                for connection in (first, second):
                    connection.execute("set search_path = public, pg_catalog")

                with first.cursor() as cur:
                    cur.execute("select id from claim_jobs(100)")
                    first_claim = harness.first_column(cur)

                # first has NOT committed. Its rows are locked and `running`.
                with second.cursor() as cur:
                    cur.execute("select id from claim_jobs(100)")
                    second_claim = harness.first_column(cur)

                first.rollback()
                second.rollback()

            assert job_id in first_claim
            assert job_id not in second_claim
        finally:
            setup.execute("delete from jobs where id = %s", (job_id,))
            setup.commit()


# ── completing ───────────────────────────────────────────────────────────────


def test_complete_clears_a_stale_error(conn: psycopg.Connection) -> None:
    """A job that failed twice and then succeeded should not leave an error
    behind for whoever reads the table next."""
    clear_seed_jobs(conn)
    job_id = enqueue(conn)
    claim(conn)
    fail(conn, job_id)
    conn.execute("update jobs set run_after = now() where id = %s", (job_id,))
    claim(conn)

    conn.execute("select complete_job(%s)", (job_id,))

    row = job_row(conn, job_id)
    assert row["status"] == "done"
    assert row["last_error"] is None


# ── failing and retrying ─────────────────────────────────────────────────────


def test_failure_under_the_limit_retries_with_backoff(conn: psycopg.Connection) -> None:
    clear_seed_jobs(conn)
    job_id = enqueue(conn)
    claim(conn)

    assert fail(conn, job_id) == "retry"

    row = job_row(conn, job_id)
    assert row["status"] == "pending"
    assert row["last_error"] == "boom"
    assert row["claimed_at"] is None

    with conn.cursor() as cur:
        cur.execute("select run_after > now() from jobs where id = %s", (job_id,))
        assert cur.fetchone()[0] is True


def test_backoff_grows_with_attempts(conn: psycopg.Connection) -> None:
    clear_seed_jobs(conn)
    first = enqueue(conn, attempts=0)
    later = enqueue(conn, attempts=2)
    claim(conn)

    fail(conn, first)
    fail(conn, later)

    with conn.cursor() as cur:
        cur.execute(
            "select (select run_after from jobs where id = %s) "
            "     < (select run_after from jobs where id = %s)",
            (first, later),
        )
        assert cur.fetchone()[0] is True


def test_backoff_is_capped(conn: psycopg.Connection) -> None:
    """A job that has failed repeatedly is waiting for a human, and a human is
    not going to look at it sooner because the delay grew to two days."""
    clear_seed_jobs(conn)
    job_id = enqueue(conn, attempts=20)

    fail(conn, job_id, max_attempts=100)

    with conn.cursor() as cur:
        cur.execute(
            "select run_after <= now() + interval '6 hours' from jobs where id = %s", (job_id,)
        )
        assert cur.fetchone()[0] is True


def test_failure_at_the_limit_is_terminal(conn: psycopg.Connection) -> None:
    clear_seed_jobs(conn)
    job_id = enqueue(conn, attempts=5)

    assert fail(conn, job_id, max_attempts=5) == "failed"
    assert job_row(conn, job_id)["status"] == "failed"


def test_terminal_failure_enqueues_exactly_one_alert(conn: psycopg.Connection) -> None:
    clear_seed_jobs(conn)
    job_id = enqueue(conn, kind="score_session", attempts=5)

    fail(conn, job_id, max_attempts=5)

    with conn.cursor() as cur:
        cur.execute(
            "select count(*), min(payload ->> 'job_kind') from jobs "
            "where kind = 'send_email' and payload ->> 'template' = 'job_failed_alert'"
        )
        assert cur.fetchone() == (1, "score_session")


def test_a_failed_alert_does_not_enqueue_another_alert(conn: psycopg.Connection) -> None:
    """The runaway this guard exists for.

    Final failure of a send_email enqueues a send_email. If that one fails the
    same way — the mail provider is down, which is the usual reason — it would
    enqueue another. One row per tick, forever, every one of them failing.
    """
    clear_seed_jobs(conn)
    alert = enqueue(
        conn,
        kind="send_email",
        payload='{"template": "job_failed_alert", "job_id": "x"}',
        attempts=5,
    )

    fail(conn, alert, max_attempts=5)

    assert jobs_of_kind(conn, "send_email") == 1


def test_an_ordinary_email_failure_does_alert(conn: psycopg.Connection) -> None:
    """The guard must be narrow. An invite that permanently failed is exactly
    the thing someone needs to be told about."""
    clear_seed_jobs(conn)
    invite = enqueue(
        conn, kind="send_email", payload='{"template": "invite", "participant_id": "x"}', attempts=5
    )

    fail(conn, invite, max_attempts=5)

    assert jobs_of_kind(conn, "send_email") == 2


def test_error_text_is_truncated(conn: psycopg.Connection) -> None:
    """`jobs.last_error` is retried, logged and emailed — not a log sink. A
    traceback that grew a token on some future code path must not be able to
    persist in full."""
    clear_seed_jobs(conn)
    job_id = enqueue(conn)

    fail(conn, job_id, error="x" * 10_000)

    assert len(job_row(conn, job_id)["last_error"]) <= 2000


def test_failing_an_unknown_job_raises(conn: psycopg.Connection) -> None:
    with pytest.raises(psycopg.errors.RaiseException):
        fail(conn, "00000000-0000-4000-8000-000000000000")


# ── the stale reaper ─────────────────────────────────────────────────────────


def test_stale_running_jobs_are_requeued(conn: psycopg.Connection) -> None:
    """The other half of incrementing attempts at claim time.

    Without this a job whose worker was killed stays `running` and is invisible
    to claim_jobs forever — one crashed process means one student's submission
    is silently never scored.
    """
    clear_seed_jobs(conn)
    job_id = enqueue(conn)
    claim(conn)
    conn.execute("update jobs set claimed_at = now() - interval '2 hours' where id = %s", (job_id,))

    with conn.cursor() as cur:
        cur.execute("select requeue_stale_jobs(interval '30 minutes')")
        assert cur.fetchone()[0] == 1

    assert job_row(conn, job_id)["status"] == "pending"


def test_a_live_running_job_is_left_alone(conn: psycopg.Connection) -> None:
    clear_seed_jobs(conn)
    job_id = enqueue(conn)
    claim(conn)

    with conn.cursor() as cur:
        cur.execute("select requeue_stale_jobs(interval '30 minutes')")
        assert cur.fetchone()[0] == 0

    assert job_row(conn, job_id)["status"] == "running"


def test_requeue_does_not_reset_attempts(conn: psycopg.Connection) -> None:
    """A job that reliably kills the process would otherwise loop forever —
    which is precisely what the attempt counter exists to stop."""
    clear_seed_jobs(conn)
    job_id = enqueue(conn)
    claim(conn)
    conn.execute("update jobs set claimed_at = now() - interval '2 hours' where id = %s", (job_id,))

    conn.execute("select requeue_stale_jobs(interval '30 minutes')")

    assert job_row(conn, job_id)["attempts"] == 1


# ── recording a score ────────────────────────────────────────────────────────


def test_record_score_writes_the_row(conn: psycopg.Connection) -> None:
    clear_seed_jobs(conn)
    score_id = score(conn)

    with conn.cursor() as cur:
        cur.execute("select session_id, engine_version from scores where id = %s", (score_id,))
        session_id, version = cur.fetchone()

    assert str(session_id) == SCORED_SESSION
    assert version == ENGINE_VERSION


def test_record_score_moves_the_participant_to_pending_review(conn: psycopg.Connection) -> None:
    """Not to `scored`. Nothing could observe that state — both writes are in
    one transaction — and a participant left there when match_occupations fails
    would be missing from the review queue with no error anywhere."""
    clear_seed_jobs(conn)
    score(conn)

    with conn.cursor() as cur:
        cur.execute("select status from participants where id = %s", (SCORED_PARTICIPANT,))
        assert cur.fetchone()[0] == "pending_review"


def test_record_score_enqueues_notify_not_render(conn: psycopg.Connection) -> None:
    """R9. A render job here would fail on every retry until it exhausted its
    attempts and alerted — a loud, confusing symptom of a rule meant to be
    quiet and absolute."""
    clear_seed_jobs(conn)
    score(conn)

    assert jobs_of_kind(conn, "notify_psychologist", SCORED_SESSION) == 1
    assert jobs_of_kind(conn, "match_occupations", SCORED_SESSION) == 1
    assert jobs_of_kind(conn, "render_student") == 0


def test_record_score_queues_the_student_notice(conn: psycopg.Connection) -> None:
    clear_seed_jobs(conn)
    score(conn)

    with conn.cursor() as cur:
        cur.execute(
            "select count(*) from jobs where kind = 'send_email' "
            "and payload ->> 'template' = 'results_in_review'"
        )
        assert cur.fetchone()[0] == 1


def test_record_score_is_idempotent(conn: psycopg.Connection) -> None:
    """A worker killed between the score and complete_job is retried. Without
    the upsert the retry violates unique (session_id, engine_version), fails
    five times, and alerts about a job that already succeeded."""
    clear_seed_jobs(conn)
    first = score(conn)
    second = score(conn)

    assert first == second

    with conn.cursor() as cur:
        cur.execute("select count(*) from scores where session_id = %s", (SCORED_SESSION,))
        assert cur.fetchone()[0] == 1


def test_a_retried_score_does_not_notify_twice(conn: psycopg.Connection) -> None:
    """The half of idempotence that reaches a person.

    Duplicate `match_occupations` is wasted CPU. A duplicate
    `notify_psychologist` is a second email about the same student, and §12 says
    that job "fires once when a session enters pending_review". A reviewer who
    gets the same student twice learns to skim these.
    """
    clear_seed_jobs(conn)
    score(conn)
    score(conn)

    assert jobs_of_kind(conn, "notify_psychologist", SCORED_SESSION) == 1
    assert jobs_of_kind(conn, "match_occupations", SCORED_SESSION) == 1


def test_a_rescore_does_not_notify_again(conn: psycopg.Connection) -> None:
    """rescore_all.py (M12) reruns every session under a new engine version. It
    must not refill the review queue with students already reviewed."""
    clear_seed_jobs(conn)
    score(conn, engine_version="1.0.0")
    score(conn, engine_version="2.0.0")

    assert jobs_of_kind(conn, "notify_psychologist", SCORED_SESSION) == 1


def test_rescoring_a_confirmed_session_notifies_nobody(conn: psycopg.Connection) -> None:
    """The case the "never notified before" guard alone would miss.

    Fatima is `confirmed` with a report. A rescore leaves her status untouched,
    so there is no historical notify row to dedupe against — and without the
    `v_awaiting` gate this would enqueue a FIRST notification, putting a
    signed-off student back in a reviewer's inbox and emailing her again about
    results she received weeks ago.
    """
    clear_seed_jobs(conn)

    score(conn, session_id=CONFIRMED_SESSION, engine_version="2.0.0")

    assert jobs_of_kind(conn, "notify_psychologist", CONFIRMED_SESSION) == 0
    with conn.cursor() as cur:
        cur.execute(
            "select count(*) from jobs where kind = 'send_email' "
            "and payload ->> 'template' = 'results_in_review'"
        )
        assert cur.fetchone()[0] == 0


def test_rescoring_a_confirmed_session_still_rematches(conn: psycopg.Connection) -> None:
    """The one follow-up a rescore must keep doing.

    `occupation_matches` is keyed by engine_version, so a new score with no
    rematch would sit beside an empty match set — and M9's report reads matches
    at the score's own version.
    """
    clear_seed_jobs(conn)

    score(conn, session_id=CONFIRMED_SESSION, engine_version="2.0.0")

    assert jobs_of_kind(conn, "match_occupations", CONFIRMED_SESSION) == 1


def test_a_rescore_does_not_reopen_a_confirmed_session(conn: psycopg.Connection) -> None:
    """rescore_all.py (M12) reruns confirmed sessions under a new engine version
    on purpose. Resetting them to pending_review would silently revoke reports a
    psychologist had already signed off (R9)."""
    clear_seed_jobs(conn)

    score(conn, session_id=CONFIRMED_SESSION, engine_version="2.0.0")

    with conn.cursor() as cur:
        cur.execute("select status from participants where id = %s", (CONFIRMED_PARTICIPANT,))
        assert cur.fetchone()[0] == "confirmed"


def test_a_rescore_writes_beside_its_predecessor(conn: psycopg.Connection) -> None:
    """R1: keyed by (session, engine_version) so history that shows whether a
    rule change moved a school's numbers survives."""
    clear_seed_jobs(conn)
    score(conn, engine_version="1.0.0")
    score(conn, engine_version="2.0.0")

    with conn.cursor() as cur:
        cur.execute("select count(*) from scores where session_id = %s", (SCORED_SESSION,))
        assert cur.fetchone()[0] == 2


def test_record_score_rejects_an_unknown_session(conn: psycopg.Connection) -> None:
    with pytest.raises(psycopg.errors.RaiseException):
        score(conn, session_id="00000000-0000-4000-8000-000000000000")


# ── occupation matches ───────────────────────────────────────────────────────


def matches_json(count: int = 2) -> str:
    rows = [
        f'{{"rank": {n}, "onet_soc_code": "15-1252.00", "score": 0.9{n}, '
        f'"local_title": "t{n}", "local_pathway": "p{n}"}}'
        for n in range(1, count + 1)
    ]
    return "[" + ",".join(rows) + "]"


def record_matches(conn: psycopg.Connection, payload: str, version: str = ENGINE_VERSION) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "select record_occupation_matches(%s, %s, %s::jsonb)",
            (SCORED_SESSION, version, payload),
        )
        return cur.fetchone()[0]


def test_matches_are_written(conn: psycopg.Connection) -> None:
    assert record_matches(conn, matches_json(3)) == 3


def test_matches_are_idempotent(conn: psycopg.Connection) -> None:
    """Delete-then-insert. A plain insert violates
    unique (session_id, engine_version, rank) on the second attempt, and the job
    then fails five times and alerts about a success."""
    record_matches(conn, matches_json(3))
    record_matches(conn, matches_json(3))

    with conn.cursor() as cur:
        cur.execute(
            "select count(*) from occupation_matches where session_id = %s and engine_version = %s",
            (SCORED_SESSION, ENGINE_VERSION),
        )
        assert cur.fetchone()[0] == 3


def test_a_rescore_leaves_the_other_version_alone(conn: psycopg.Connection) -> None:
    record_matches(conn, matches_json(2), version="1.0.0")
    record_matches(conn, matches_json(2), version="2.0.0")

    with conn.cursor() as cur:
        cur.execute(
            "select count(*) from occupation_matches where session_id = %s", (SCORED_SESSION,)
        )
        assert cur.fetchone()[0] == 4


def test_matches_reject_an_unknown_session(conn: psycopg.Connection) -> None:
    with pytest.raises(psycopg.errors.RaiseException), conn.cursor() as cur:
        cur.execute(
            "select record_occupation_matches(%s, %s, %s::jsonb)",
            ("00000000-0000-4000-8000-000000000000", ENGINE_VERSION, "[]"),
        )


# ── the review backlog sweep ─────────────────────────────────────────────────


def make_overdue(conn: psycopg.Connection) -> None:
    conn.execute(
        "update participants set status = 'pending_review' where id = %s", (SCORED_PARTICIPANT,)
    )
    conn.execute(
        "update sessions set submitted_at = now() - interval '9 days' where id = %s",
        (SCORED_SESSION,),
    )


def sweep(conn: psycopg.Connection, older_than: str = "5 days", cooldown: str = "24 hours") -> int:
    with conn.cursor() as cur:
        cur.execute(
            "select enqueue_review_reminders(%s::interval, %s::interval)", (older_than, cooldown)
        )
        return cur.fetchone()[0]


def test_an_overdue_review_is_reminded(conn: psycopg.Connection) -> None:
    clear_seed_jobs(conn)
    make_overdue(conn)

    assert sweep(conn) == 1
    assert jobs_of_kind(conn, "review_reminder", SCORED_SESSION) == 1


def test_a_recent_submission_is_not_reminded(conn: psycopg.Connection) -> None:
    clear_seed_jobs(conn)
    conn.execute(
        "update participants set status = 'pending_review' where id = %s", (SCORED_PARTICIPANT,)
    )
    conn.execute("update sessions set submitted_at = now() where id = %s", (SCORED_SESSION,))

    assert sweep(conn) == 0


def test_a_confirmed_session_is_never_reminded(conn: psycopg.Connection) -> None:
    clear_seed_jobs(conn)
    conn.execute(
        "update sessions set submitted_at = now() - interval '30 days' where id = %s",
        (CONFIRMED_SESSION,),
    )

    assert sweep(conn) == 0


def test_the_cooldown_stops_a_reminder_storm(conn: psycopg.Connection) -> None:
    """This runs every five minutes. Without the dedupe a single stuck session
    produces 288 identical reminders a day."""
    clear_seed_jobs(conn)
    make_overdue(conn)

    assert sweep(conn) == 1
    assert sweep(conn) == 0
    assert jobs_of_kind(conn, "review_reminder", SCORED_SESSION) == 1


def test_a_reminder_can_repeat_after_the_cooldown(conn: psycopg.Connection) -> None:
    """Still stuck a day later is still worth saying. The dedupe is a rate
    limit, not a one-shot."""
    clear_seed_jobs(conn)
    make_overdue(conn)
    sweep(conn)
    conn.execute(
        "update jobs set status = 'done', created_at = now() - interval '3 days' "
        "where kind = 'review_reminder'"
    )

    assert sweep(conn) == 1

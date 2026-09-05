"""The taker flow at the database level (plan.md §13, M6).

`0006_taker_flow.sql` puts three things in Postgres rather than in TypeScript,
and each is here because getting it wrong is quiet rather than loud:

* **Nothing takes a participant id.** Every function is keyed on the token hash,
  so 0002_rls.sql's "must never accept a participant_id from the client" cannot
  be broken by a caller that forgets — there is no argument to break it with.
* **A response is validated against its own item's range.** An interests item is
  0..1 and a personality item 1..5; a 4 written against the former would score
  silently and wrongly.
* **Submitting queues exactly one job, once.** A double-tapped finish button
  must not score a student twice, and a session must never be marked submitted
  with no job to score it.

The seed provides four items (two interests at 0..1, two personality at 1..5),
which is what `submit_assessment`'s completeness check counts against.
"""

import psycopg
import pytest

from . import harness

# Seed items (db/seed/demo_org.sql). Named rather than looked up: a test that
# queried for "an interests item" would still pass if the seed changed its range.
INTEREST_ITEM = "11eeee00-0000-4000-8000-000000000001"
INTEREST_ITEM_2 = "11eeee00-0000-4000-8000-000000000002"
PERSONALITY_ITEM = "11eeee00-0000-4000-8000-000000000003"
PERSONALITY_ITEM_2 = "11eeee00-0000-4000-8000-000000000004"
ALL_ITEMS = (INTEREST_ITEM, INTEREST_ITEM_2, PERSONALITY_ITEM, PERSONALITY_ITEM_2)

TOKEN_HASH = "harness-taker-token-hash-0001"


@pytest.fixture
def invited(conn: psycopg.Connection) -> str:
    """An `invited` participant with a known token hash, and no session yet.

    The state a student is in when they open their invite link for the first
    time. Built here rather than read from the seed because every seeded
    participant has already consented and submitted.
    """
    conn.execute(
        """
        insert into participants (id, cohort_id, full_name, invite_token_hash, status)
        values (%s, %s, 'Taker Harness', %s, 'invited')
        """,
        ("a0aaaaa8-0000-4000-8000-000000008001", harness.COHORT_A, TOKEN_HASH),
    )
    return TOKEN_HASH


def start(conn: psycopg.Connection, token_hash: str = TOKEN_HASH) -> str:
    with conn.cursor() as cur:
        cur.execute("select start_assessment(%s, %s)", (token_hash, "harness/1.0"))
        return str(cur.fetchone()[0])


def record(
    conn: psycopg.Connection,
    item_id: str,
    value: int,
    ms: int = 1500,
    token_hash: str = TOKEN_HASH,
) -> None:
    conn.execute(
        "select record_response(%s, %s, %s, %s)", (token_hash, item_id, value, ms)
    )


def answer_everything(conn: psycopg.Connection) -> None:
    """A complete, valid set of answers across both instruments."""
    record(conn, INTEREST_ITEM, 1)
    record(conn, INTEREST_ITEM_2, 0)
    record(conn, PERSONALITY_ITEM, 4)
    record(conn, PERSONALITY_ITEM_2, 2)


def submit(conn: psycopg.Connection, token_hash: str = TOKEN_HASH) -> bool:
    with conn.cursor() as cur:
        cur.execute("select submit_assessment(%s)", (token_hash,))
        return cur.fetchone()[0]


# ── consent ──────────────────────────────────────────────────────────────────


def test_consent_opens_a_session_and_moves_the_participant(
    conn: psycopg.Connection, invited: str
) -> None:
    session_id = start(conn)

    with conn.cursor() as cur:
        cur.execute(
            "select status, consent_at is not null from participants where invite_token_hash = %s",
            (TOKEN_HASH,),
        )
        assert cur.fetchone() == ("started", True)

        cur.execute("select started_at is not null from sessions where id = %s", (session_id,))
        assert cur.fetchone() == (True,)


def test_consent_is_idempotent(conn: psycopg.Connection, invited: str) -> None:
    """A double-tapped button, a refresh, and a resumed link are all this call.

    `sessions` is unique on participant_id, so a second session would raise
    rather than duplicate — but the student would see an error where nothing is
    actually wrong.
    """
    first = start(conn)
    second = start(conn)

    assert first == second

    with conn.cursor() as cur:
        cur.execute("select count(*) from sessions where id = %s", (first,))
        assert cur.fetchone()[0] == 1


def test_consent_does_not_move_once_recorded(conn: psycopg.Connection, invited: str) -> None:
    """`consent_at` records when the student agreed, not when they last opened
    the page (§16)."""
    start(conn)
    with conn.cursor() as cur:
        cur.execute(
            "select consent_at from participants where invite_token_hash = %s", (TOKEN_HASH,)
        )
        first = cur.fetchone()[0]

    start(conn)
    with conn.cursor() as cur:
        cur.execute(
            "select consent_at from participants where invite_token_hash = %s", (TOKEN_HASH,)
        )
        assert cur.fetchone()[0] == first


def test_a_reviewed_student_cannot_reopen_the_assessment(conn: psycopg.Connection) -> None:
    """Fatima is `confirmed`. Reopening would let answers change underneath a
    psychologist who has already signed off (R1, §9)."""
    with pytest.raises(psycopg.errors.RaiseException, match="closed"), conn.transaction():
        start(conn, "seedhash-a-0000000000000000000000000000000000000000000000000000000001")


def test_an_unknown_token_raises(conn: psycopg.Connection) -> None:
    with pytest.raises(psycopg.errors.RaiseException, match="no participant"), conn.transaction():
        start(conn, "not-a-real-token-hash")


# ── one answer ───────────────────────────────────────────────────────────────


def test_records_an_answer_against_the_session_the_token_resolves_to(
    conn: psycopg.Connection, invited: str
) -> None:
    session_id = start(conn)
    record(conn, INTEREST_ITEM, 1, ms=1400)

    with conn.cursor() as cur:
        cur.execute(
            "select value, ms_elapsed from responses where session_id = %s and item_id = %s",
            (session_id, INTEREST_ITEM),
        )
        assert cur.fetchone() == (1, 1400)


def test_re_answering_updates_rather_than_duplicates(
    conn: psycopg.Connection, invited: str
) -> None:
    """Stepping back to change an answer. `responses` is unique on
    (session_id, item_id), so without the upsert this would raise."""
    session_id = start(conn)
    record(conn, PERSONALITY_ITEM, 2)
    record(conn, PERSONALITY_ITEM, 5)

    with conn.cursor() as cur:
        cur.execute(
            "select count(*), max(value) from responses where session_id = %s and item_id = %s",
            (session_id, PERSONALITY_ITEM),
        )
        assert cur.fetchone() == (1, 5)


def test_rejects_a_value_outside_the_items_own_range(
    conn: psycopg.Connection, invited: str
) -> None:
    """4 is a legal personality answer and an illegal interests one. Validating
    against a hardcoded range rather than the item's own would let this through
    and score it."""
    start(conn)

    with pytest.raises(psycopg.errors.RaiseException, match="outside"), conn.transaction():
        record(conn, INTEREST_ITEM, 4)


def test_rejects_zero_for_a_one_to_five_item(conn: psycopg.Connection, invited: str) -> None:
    """The off-by-one that would shift a whole domain's raw sum."""
    start(conn)

    with pytest.raises(psycopg.errors.RaiseException, match="outside"), conn.transaction():
        record(conn, PERSONALITY_ITEM, 0)


def test_clamps_an_item_left_open_for_hours(conn: psycopg.Connection, invited: str) -> None:
    """flags.py sums ms_elapsed against a six-minute threshold. One item left
    open over lunch would otherwise add an hour and hide a rushed session."""
    session_id = start(conn)
    record(conn, INTEREST_ITEM, 1, ms=3_600_000)

    with conn.cursor() as cur:
        cur.execute(
            "select ms_elapsed from responses where session_id = %s and item_id = %s",
            (session_id, INTEREST_ITEM),
        )
        assert cur.fetchone()[0] == 120_000


def test_advances_progress_but_never_rewinds_it(conn: psycopg.Connection, invited: str) -> None:
    """`sessions.progress` is {instrument: highest ordinal reached}. Stepping
    back to change an earlier answer must not undo it."""
    session_id = start(conn)
    record(conn, INTEREST_ITEM_2, 1)  # ordinal 2
    record(conn, INTEREST_ITEM, 0)  # ordinal 1 — the step back

    with conn.cursor() as cur:
        cur.execute("select progress->>'interests' from sessions where id = %s", (session_id,))
        assert cur.fetchone()[0] == "2"


def test_refuses_an_answer_after_submission(conn: psycopg.Connection, invited: str) -> None:
    """Once submitted, the responses are what a score was computed from and what
    a psychologist may already be reading (R1)."""
    start(conn)
    answer_everything(conn)
    submit(conn)

    with pytest.raises(psycopg.errors.RaiseException, match="already submitted"), \
            conn.transaction():
        record(conn, INTEREST_ITEM, 0)


def test_an_answer_without_a_session_raises(conn: psycopg.Connection, invited: str) -> None:
    """No consent, no session. The row exists but the student never agreed."""
    with pytest.raises(psycopg.errors.RaiseException, match="no session"), conn.transaction():
        record(conn, INTEREST_ITEM, 1)


# ── submission ───────────────────────────────────────────────────────────────


def test_submitting_queues_exactly_one_score_session_job(
    conn: psycopg.Connection, invited: str
) -> None:
    session_id = start(conn)
    answer_everything(conn)

    assert submit(conn) is True

    with conn.cursor() as cur:
        cur.execute(
            "select payload->>'session_id' from jobs where kind = 'score_session'",
        )
        assert cur.fetchall() == [(session_id,)]

        cur.execute(
            "select status, submitted_at is not null from participants p "
            "join sessions s on s.participant_id = p.id where s.id = %s",
            (session_id,),
        )
        assert cur.fetchone() == ("submitted", True)


def test_submitting_twice_does_not_queue_a_second_job(
    conn: psycopg.Connection, invited: str
) -> None:
    """A double-tapped finish button, or a retried request. Not an error — but
    scoring the same student twice is."""
    start(conn)
    answer_everything(conn)

    assert submit(conn) is True
    assert submit(conn) is False

    with conn.cursor() as cur:
        cur.execute("select count(*) from jobs where kind = 'score_session'")
        assert cur.fetchone()[0] == 1


def test_refuses_an_incomplete_submission(conn: psycopg.Connection, invited: str) -> None:
    """score_interests() raises on a missing item, so a partial submission would
    queue a job that cannot succeed — surfacing in M7 as a mystery failed job
    rather than here as a plain refusal."""
    start(conn)
    record(conn, INTEREST_ITEM, 1)

    with pytest.raises(psycopg.errors.RaiseException, match="incomplete"), conn.transaction():
        submit(conn)


def test_an_incomplete_submission_leaves_nothing_behind(
    conn: psycopg.Connection, invited: str
) -> None:
    """The three writes are one transaction: a session marked submitted with no
    job would sit in the counsellor's roster as finished and never be scored."""
    session_id = start(conn)
    record(conn, INTEREST_ITEM, 1)

    with pytest.raises(psycopg.errors.RaiseException), conn.transaction():
        submit(conn)

    with conn.cursor() as cur:
        cur.execute("select submitted_at from sessions where id = %s", (session_id,))
        assert cur.fetchone()[0] is None

        cur.execute("select count(*) from jobs where kind = 'score_session'")
        assert cur.fetchone()[0] == 0


def test_writes_one_audit_row_with_no_actor(conn: psycopg.Connection, invited: str) -> None:
    """The student is not a `profiles` row and never will be."""
    session_id = start(conn)
    answer_everything(conn)
    submit(conn)

    with conn.cursor() as cur:
        cur.execute(
            "select actor, subject, meta->>'items' from audit_log "
            "where action = 'assessment.submitted'"
        )
        actor, subject, items = cur.fetchone()

    assert actor is None
    assert subject == session_id
    assert items == "4"


# ── the rule the whole flow rests on ─────────────────────────────────────────


def test_none_of_these_functions_are_callable_by_an_authenticated_user(
    conn: psycopg.Connection, invited: str
) -> None:
    """A counsellor's browser session must not reach any of them.

    Nothing in the counsellor UI has a reason to write a student's answers, and
    a caller holding a token hash could otherwise drive someone else's
    assessment. Execute is revoked from `anon` and `authenticated`; the only
    caller is a server action holding the service role.
    """
    calls = (
        ("select start_assessment(%s, %s)", (TOKEN_HASH, "x")),
        ("select record_response(%s, %s, 1, 100)", (TOKEN_HASH, INTEREST_ITEM)),
        ("select submit_assessment(%s)", (TOKEN_HASH,)),
        ("select taker_session_for_token(%s)", (TOKEN_HASH,)),
    )

    for sql, args in calls:
        with (
            harness.as_user(conn, harness.COUNSELLOR_A),
            pytest.raises(psycopg.errors.InsufficientPrivilege),
            conn.transaction(),
        ):
            conn.execute(sql, args)

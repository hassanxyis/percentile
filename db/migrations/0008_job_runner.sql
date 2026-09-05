-- Percentile v2 — job runner (plan.md §11, §12, M7).
--
-- Never edit a migration that has run. Add a new one.
--
-- Two job kinds have been queued since M5 and M6 with nothing to drain them:
-- `send_email` (one invite per imported participant) and `score_session` (one
-- per submission). This migration is the database half of the runner that
-- finally claims them.
--
-- Everything here follows the same shape as 0005 and 0006: multi-table writes
-- live in SQL functions rather than in a sequence of supabase-js calls, because
-- supabase-js speaks REST and each call would be its own request with no
-- transaction between them. A score written without its follow-up jobs is a
-- student who is scored and invisible to the review queue.
--
-- ─────────────────────────────────────────────────────────────────────────────
-- THE DECISION THAT SHAPES THE REST OF THIS FILE
--
-- `attempts` increments when a job is CLAIMED, not when it fails.
--
-- The engine runs on disposable free-tier compute (plan §2) and will be killed
-- mid-job. If `attempts` only rose on a handled failure, a job that crashes the
-- process would be retried forever and the limit would never be reached — the
-- one failure mode most likely to happen in production is the one the retry cap
-- would not cover.
--
-- Incrementing at claim time costs one thing: a job whose worker dies stays
-- `running` and is invisible to `claim_jobs` forever. That is why
-- `requeue_stale_jobs` below is mandatory rather than an optimisation. Without
-- it, one crashed process means one student's submission is silently never
-- scored, and nothing anywhere reports it.
-- ─────────────────────────────────────────────────────────────────────────────

-- The claim query runs every five minutes for the life of the product. The
-- existing jobs(status, run_after) index from 0001 already serves it; this
-- partial index is smaller because `done` rows accumulate forever and pending
-- ones never number more than a cohort.
create index if not exists jobs_pending_run_after
  on jobs (run_after, created_at)
  where status = 'pending';

-- `notify_psychologist` and `review_reminder` join the vocabulary in 0001's
-- comment (plan §12). The column has no check constraint and deliberately keeps
-- none: an unknown kind must reach the runner and fail loudly there, naming the
-- milestone that will implement it, rather than being rejected at insert time by
-- a constraint that a future migration then has to remember to widen.
comment on column jobs.kind is
  'score_session | match_occupations | notify_psychologist | review_reminder | send_email | render_student | render_cohort | recompute_norms. Deliberately unconstrained — see 0008_job_runner.sql.';


-- ── claim ──────────────────────────────────────────────────────────────────
-- `for update skip locked` is the whole point: two engine processes ticking at
-- once (a cron overlap, a manual POST during a scheduled run) must never take
-- the same row. Rows already locked by the other transaction are skipped rather
-- than waited on, so a slow batch never blocks a fast one.
--
-- The CTE selects and locks; the update writes. Doing it in one statement is
-- what makes the claim atomic — a select followed by a separate update is the
-- classic version of this bug and it looks correct right up until two workers
-- run concurrently.
create or replace function claim_jobs(p_limit int)
returns setof jobs
language sql
security definer
set search_path = public
as $$
  with claimed as (
    select id
    from jobs
    where status = 'pending'
      and run_after <= now()
    -- Oldest scheduled first, then oldest created. Without the second key a
    -- batch of jobs enqueued in the same transaction (an import's 20 invites,
    -- all with the same default run_after) has no defined order.
    order by run_after, created_at
    limit greatest(p_limit, 0)
    for update skip locked
  )
  update jobs j
  set status     = 'running',
      claimed_at = now(),
      attempts   = j.attempts + 1
  from claimed
  where j.id = claimed.id
  returning j.*;
$$;


-- ── complete ───────────────────────────────────────────────────────────────
-- `last_error` is cleared: a job that failed twice and then succeeded should not
-- leave a stale error behind for whoever reads the table next.
create or replace function complete_job(p_id uuid)
returns void
language sql
security definer
set search_path = public
as $$
  update jobs
  set status     = 'done',
      last_error = null,
      claimed_at = null
  where id = p_id;
$$;


-- ── fail ───────────────────────────────────────────────────────────────────
-- Under the attempt limit the job goes back to `pending` with an exponential
-- `run_after`; at the limit it becomes `failed` and an alert is enqueued.
--
-- Backoff is 4^(attempts-1) minutes — 1, 4, 16, 64 — capped at six hours. The
-- cap matters because a job that has failed five times is waiting for a human,
-- and a human is not going to look at it sooner because the delay grew to two
-- days.
--
-- TWO GUARDS, both of which prevent a runaway that fills the table:
--
-- 1. An alert is never enqueued for a job that is itself the alert. Final
--    failure of a `send_email` job enqueues... a `send_email` job. If that one
--    fails the same way — the mail provider is down, which is the usual reason —
--    it enqueues another. One row per tick, forever, and every one of them fails.
--
-- 2. `p_error` is truncated. `jobs.last_error` is written on every failure and
--    read by whoever debugs; a Python traceback that happened to include a
--    freshly minted invite token would persist a live credential to a minor's
--    record in a table built for logging. Handlers are also written not to put a
--    token in an exception message, but a length cap is the backstop that does
--    not depend on every future handler remembering.
create or replace function fail_job(
  p_id           uuid,
  p_error        text,
  p_max_attempts int default 5
)
returns text
language plpgsql
security definer
set search_path = public
as $$
declare
  v_job      jobs;
  v_error    text;
  v_minutes  int;
  v_delay    interval;
  v_is_alert boolean;
begin
  select * into v_job from jobs where id = p_id;

  if v_job.id is null then
    raise exception 'no job %', p_id;
  end if;

  v_error := left(coalesce(p_error, 'unknown error'), 2000);

  if v_job.attempts < p_max_attempts then
    -- The exponent is clamped before the power, not after. `power()` returns
    -- double precision, and a large attempts value would render as
    -- `1.09951162777600e+12`, which `::interval` then rejects — a retry path
    -- that raises is the last thing a failure handler should do. Clamping at 5
    -- caps the term at 4^5 = 1024 minutes, well past the six-hour ceiling below.
    v_minutes := power(4, least(greatest(v_job.attempts - 1, 0), 5))::int;
    v_delay   := least(make_interval(mins => v_minutes), interval '6 hours');

    update jobs
    set status     = 'pending',
        last_error = v_error,
        claimed_at = null,
        run_after  = now() + v_delay
    where id = p_id;

    return 'retry';
  end if;

  update jobs
  set status     = 'failed',
      last_error = v_error,
      claimed_at = null
  where id = p_id;

  -- Guard 1. Checked on the payload rather than the kind, because the alert is
  -- itself a send_email — the kind alone cannot distinguish them.
  v_is_alert := v_job.kind = 'send_email'
            and v_job.payload ->> 'template' = 'job_failed_alert';

  if not v_is_alert then
    insert into jobs (kind, payload)
    values (
      'send_email',
      jsonb_build_object(
        'template', 'job_failed_alert',
        'job_id',   p_id,
        'job_kind', v_job.kind,
        'attempts', v_job.attempts,
        'error',    v_error
      )
    );
  end if;

  insert into audit_log (actor, action, subject, meta)
  values (
    null,
    'job.failed',
    p_id::text,
    jsonb_build_object('kind', v_job.kind, 'attempts', v_job.attempts)
  );

  return 'failed';
end;
$$;


-- ── reap ───────────────────────────────────────────────────────────────────
-- The other half of the claim-time increment. A `running` row whose worker died
-- is invisible to claim_jobs forever; this returns it to `pending`.
--
-- `attempts` is deliberately NOT reset. The job consumed an attempt when it was
-- claimed, and a job that reliably kills the process would otherwise loop
-- forever — which is precisely the case the attempt counter exists to stop.
--
-- The timeout must exceed the longest a legitimate job can run. Report renders
-- (M9) are the slow ones; 30 minutes is the default the engine passes, which is
-- generous against a five-minute tick.
create or replace function requeue_stale_jobs(p_timeout interval default interval '30 minutes')
returns int
language plpgsql
security definer
set search_path = public
as $$
declare
  v_count int;
begin
  update jobs
  set status     = 'pending',
      claimed_at = null,
      last_error = 'requeued: worker did not report back within ' || p_timeout::text
  where status = 'running'
    and claimed_at is not null
    and claimed_at < now() - p_timeout;

  get diagnostics v_count = row_count;

  if v_count > 0 then
    insert into audit_log (actor, action, subject, meta)
    values (null, 'jobs.requeued_stale', null,
            jsonb_build_object('count', v_count, 'timeout', p_timeout::text));
  end if;

  return v_count;
end;
$$;


-- ── record a score ─────────────────────────────────────────────────────────
-- plan §7.5 step 3–6, in one transaction. Writing the score without enqueueing
-- notify_psychologist would leave a scored student nobody is told about; R9
-- means that student's report can never render, so the failure would surface
-- weeks later as "why did this school get 19 reports out of 20".
--
-- Upsert on (session_id, engine_version) rather than insert: a job that scored
-- successfully and then died before complete_job is retried, and the retry must
-- not fail on the unique constraint. The 0001 comment explains why that key is
-- (session, version) and not (session) — a rescore under a NEW version writes a
-- new row beside the old one, so R1's history survives.
--
-- STATUS GOES STRAIGHT TO 'pending_review', NOT VIA 'scored'.
--
-- §5's state machine lists scored → pending_review, but nothing could ever
-- observe `scored`: both writes are in this one transaction. Worse, if the
-- participant stopped at `scored` here and the follow-up match_occupations job
-- failed, the student would be missing from the review queue with no error
-- anywhere — the psychologist has no way to notice a student who never arrived.
-- Landing in `pending_review` now means a matching failure degrades the review
-- screen (no occupation list, visible) instead of dropping the student.
create or replace function record_score(
  p_session_id     uuid,
  p_engine_version text,
  p_interests      jsonb,
  p_personality    jsonb,
  p_get2           jsonb,
  p_flags          jsonb
)
returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
  v_participant uuid;
  v_score       uuid;
  v_awaiting    boolean;
begin
  select p.id into v_participant
  from sessions s
  join participants p on p.id = s.participant_id
  where s.id = p_session_id;

  if v_participant is null then
    raise exception 'no session %', p_session_id;
  end if;

  -- `values_scores` holds the GET2 shape now; the column was not renamed
  -- because a rescoreable history (R1) would need a backfill. See the comment
  -- 0003_reviews.sql puts on this column.
  insert into scores (session_id, engine_version, interests, personality, values_scores, flags)
  values (p_session_id, p_engine_version, p_interests, p_personality, p_get2, coalesce(p_flags, '[]'::jsonb))
  on conflict (session_id, engine_version) do update
    set interests     = excluded.interests,
        personality   = excluded.personality,
        values_scores = excluded.values_scores,
        flags         = excluded.flags,
        scored_at     = now()
  returning id into v_score;

  -- A session already past review must not be dragged backwards by a rescore.
  -- `rescore_all.py` (M12) reruns confirmed sessions under a new engine version
  -- on purpose; if that reset them to pending_review it would silently revoke
  -- reports a psychologist had already signed off (R9).
  update participants
  set status = 'pending_review'
  where id = v_participant
    and status in ('submitted', 'scored', 'failed');

  -- Did THIS call put the student into the review queue?
  --
  -- Everything below hangs off this rather than off the status alone. A rescore
  -- of an already-`confirmed` session leaves the status untouched (the update
  -- above skips it), and must not then queue a notification, a student email,
  -- or a reviewer's attention for work that was signed off weeks ago. The
  -- "never notified before" guards further down would not catch that case on a
  -- session scored before this migration existed, because there is no historical
  -- notify_psychologist row to find.
  --
  -- `needs_more_info` is deliberately excluded: a psychologist who sent a
  -- session back is already looking at it (§9.1).
  v_awaiting := exists (
    select 1 from participants
    where id = v_participant and status = 'pending_review'
  );

  -- Occupation matching is a separate job rather than part of this transaction:
  -- it needs the whole O*NET catalogue in memory and is the slowest step, and a
  -- catalogue problem should not roll back a score that is already correct.
  --
  -- Guarded against an already-queued run, because this whole function is
  -- idempotent by design and gets called again whenever a worker dies between
  -- scoring and complete_job. Duplicate matching is only wasted work — but see
  -- the notify guard below, where a duplicate is a second email to a person.
  -- Not gated on v_awaiting: a rescore under a new engine version SHOULD
  -- rematch, because occupation_matches is keyed by engine_version too and the
  -- new score would otherwise have no matches beside it. This is the one
  -- follow-up that is pure computation and reaches nobody.
  if not exists (
    select 1 from jobs
    where kind = 'match_occupations'
      and payload ->> 'session_id' = p_session_id::text
      and status in ('pending', 'running')
  ) then
    insert into jobs (kind, payload)
    values ('match_occupations', jsonb_build_object('session_id', p_session_id));
  end if;

  -- R9: notify_psychologist, never render_student. The database trigger would
  -- refuse the report anyway, but a render job enqueued here would fail on every
  -- retry until it exhausted its attempts and alerted — a loud, confusing
  -- symptom of a rule that is supposed to be quiet and absolute.
  --
  -- Guarded on 'has this session EVER been notified', not just on a pending
  -- one. plan §12 says notify_psychologist "fires once when a session enters
  -- pending_review" — once, not once per retry and not again on every rescore.
  -- A reviewer who gets the same student twice learns to skim these.
  if v_awaiting and not exists (
    select 1 from jobs
    where kind = 'notify_psychologist'
      and payload ->> 'session_id' = p_session_id::text
  ) then
    insert into jobs (kind, payload)
    values ('notify_psychologist', jsonb_build_object('session_id', p_session_id));
  end if;

  -- plan §15: `results_in_review` fires when `scores` is written. It tells the
  -- student their answers arrived and that a person is reading them — and says
  -- nothing whatsoever about what the results contain, because at this moment
  -- no human has looked at them yet (R3).
  --
  -- Guarded on the participant having actually just moved to pending_review, so
  -- a rescore (M12, `rescore_all.py`) of an already-confirmed session does not
  -- email that student a second time about results they received weeks ago.
  if v_awaiting and not exists (
    select 1 from jobs
    where kind = 'send_email'
      and payload ->> 'template' = 'results_in_review'
      and payload ->> 'participant_id' = v_participant::text
  ) then
    insert into jobs (kind, payload)
    values (
      'send_email',
      jsonb_build_object('template', 'results_in_review', 'participant_id', v_participant)
    );
  end if;

  insert into audit_log (actor, action, subject, meta)
  values (
    null,
    'session.scored',
    p_session_id::text,
    jsonb_build_object('engine_version', p_engine_version, 'participant_id', v_participant)
  );

  return v_score;
end;
$$;


-- ── record occupation matches ──────────────────────────────────────────────
-- Delete-then-insert for this (session, engine_version), so a retried job is
-- idempotent. A plain insert would violate
-- `unique (session_id, engine_version, rank)` on the second attempt, and the
-- job would then fail five times and alert about a success.
--
-- Rows for OTHER engine versions are untouched: R1's whole point is that a
-- rescore writes beside its predecessor so the two can be compared.
create or replace function record_occupation_matches(
  p_session_id     uuid,
  p_engine_version text,
  p_matches        jsonb
)
returns int
language plpgsql
security definer
set search_path = public
as $$
declare
  v_count int;
begin
  if not exists (select 1 from sessions where id = p_session_id) then
    raise exception 'no session %', p_session_id;
  end if;

  delete from occupation_matches
  where session_id = p_session_id
    and engine_version = p_engine_version;

  insert into occupation_matches
    (session_id, engine_version, rank, onet_soc_code, score, local_title, local_pathway)
  select
    p_session_id,
    p_engine_version,
    (m ->> 'rank')::int,
    m ->> 'onet_soc_code',
    (m ->> 'score')::numeric,
    nullif(m ->> 'local_title', ''),
    nullif(m ->> 'local_pathway', '')
  from jsonb_array_elements(coalesce(p_matches, '[]'::jsonb)) as m;

  get diagnostics v_count = row_count;
  return v_count;
end;
$$;


-- ── review backlog sweep ───────────────────────────────────────────────────
-- plan §9.4: a session sitting in `pending_review` for more than five days
-- alerts the operator and the org_admin — never the student. A student never
-- sees "your report is late"; they see the calm waiting state M6 already built.
--
-- Deduped two ways, because this runs every five minutes and the naive version
-- would enqueue 288 identical reminders a day per stuck session:
--
--   * skip a session that already has a pending/running review_reminder
--   * skip a session reminded within the cooldown, however that job ended
--
-- Returns how many were enqueued, so a tick that sweeps can log a number rather
-- than a silence.
create or replace function enqueue_review_reminders(
  p_older_than interval default interval '5 days',
  p_cooldown   interval default interval '24 hours'
)
returns int
language plpgsql
security definer
set search_path = public
as $$
declare
  v_count int;
begin
  insert into jobs (kind, payload)
  select 'review_reminder', jsonb_build_object('session_id', s.id)
  from sessions s
  join participants p on p.id = s.participant_id
  where p.status = 'pending_review'
    and s.submitted_at is not null
    and s.submitted_at < now() - p_older_than
    and not exists (
      select 1 from jobs j
      where j.kind = 'review_reminder'
        and j.payload ->> 'session_id' = s.id::text
        and (
          j.status in ('pending', 'running')
          or j.created_at > now() - p_cooldown
        )
    );

  get diagnostics v_count = row_count;
  return v_count;
end;
$$;


-- ── grants ─────────────────────────────────────────────────────────────────
-- Only the engine calls these, holding the service role. `jobs` has RLS enabled
-- with no policy at all (0002_rls.sql), so an authenticated counsellor cannot
-- read the table — but a SECURITY DEFINER function runs as its owner and would
-- hand back rows regardless. claim_jobs in particular returns whole job rows,
-- payloads included.
--
-- `anon` is named explicitly for the same reason 0006 names it: it is the role a
-- logged-out browser holds, and the taker page is reachable logged out.
revoke all on function claim_jobs(int)                                          from public, anon, authenticated;
revoke all on function complete_job(uuid)                                       from public, anon, authenticated;
revoke all on function fail_job(uuid, text, int)                                from public, anon, authenticated;
revoke all on function requeue_stale_jobs(interval)                             from public, anon, authenticated;
revoke all on function record_score(uuid, text, jsonb, jsonb, jsonb, jsonb)     from public, anon, authenticated;
revoke all on function record_occupation_matches(uuid, text, jsonb)             from public, anon, authenticated;
revoke all on function enqueue_review_reminders(interval, interval)             from public, anon, authenticated;

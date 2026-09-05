-- Percentile v2 — student taker flow (plan.md §13, M6).
--
-- Never edit a migration that has run. Add a new one.
--
-- Three functions, one shared rule: every one of them is keyed on
-- `p_token_hash` and none of them accepts a participant id or a session id.
--
-- That is 0002_rls.sql's header stated in SQL rather than in prose:
--
--     "Students are never authenticated. The taker flow reaches the database
--      only through Next.js server actions that resolve sha256(token) →
--      participants.invite_token_hash using the service role. Those actions
--      must never accept a participant_id from the client — only a token."
--
-- The web layer honours that rule, and so does this layer, so a future caller
-- that forgets cannot express the mistake: there is no argument to pass a
-- guessed uuid into. Resolution happens here, once, from the hash.
--
-- Why functions at all, rather than a few supabase-js calls: the same reason as
-- 0005_import_roster.sql. supabase-js speaks REST, so each call is its own
-- request with no transaction between them. Submitting is three writes that
-- must not come apart, and consent is two.

-- ── helper: token hash → session ────────────────────────────────────────────
-- Every function below starts here. Raises rather than returning null: a token
-- that resolves to nothing is either a typo or someone guessing, and neither
-- should read as "carry on with no session".
create or replace function taker_session_for_token(p_token_hash text)
returns uuid
language sql
stable
security definer
set search_path = public
as $$
  select s.id
  from participants p
  join sessions s on s.participant_id = p.id
  where p.invite_token_hash = p_token_hash;
$$;

-- ── consent ────────────────────────────────────────────────────────────────
-- Idempotent by construction. `sessions` is unique on participant_id, so a
-- double-tapped consent button, a refresh, or a resumed link all land on the
-- same row. Returns the session id either way.
--
-- `consent_at` is set once and never overwritten: it records when the student
-- actually agreed (§16), not when they last opened the page.
create or replace function start_assessment(
  p_token_hash text,
  p_user_agent text
)
returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
  v_participant uuid;
  v_status      text;
  v_session     uuid;
begin
  select id, status into v_participant, v_status
  from participants
  where invite_token_hash = p_token_hash;

  if v_participant is null then
    raise exception 'no participant for this token';
  end if;

  -- A student whose results are already scored or reviewed must not be able to
  -- reopen the assessment and change the answers underneath a psychologist who
  -- is mid-review (R1, §9).
  if v_status not in ('invited', 'started') then
    raise exception 'assessment is closed for this participant (status %)', v_status;
  end if;

  update participants
  set consent_at = coalesce(consent_at, now()),
      status     = 'started'
  where id = v_participant;

  insert into sessions (participant_id, started_at, last_seen_at, user_agent)
  values (v_participant, now(), now(), p_user_agent)
  on conflict (participant_id) do update
    set last_seen_at = now(),
        -- Keep the first user agent. A student who switches phones mid-way is
        -- worth being able to see; overwriting hides it.
        user_agent   = coalesce(sessions.user_agent, excluded.user_agent)
  returning id into v_session;

  return v_session;
end;
$$;

-- ── one answer ─────────────────────────────────────────────────────────────
-- The hot path: called once per item, ~110 times per student.
--
-- Validates `p_value` against that item's own response_min/response_max rather
-- than against a hardcoded range, so the 0..1 interests checkbox, the 1..5
-- personality Likert and GET2's eventual 0..2 scale are all covered by the same
-- line and adding GET2 stays "load a file" (R10).
create or replace function record_response(
  p_token_hash text,
  p_item_id    uuid,
  p_value      int,
  p_ms_elapsed int
)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  v_session    uuid;
  v_submitted  timestamptz;
  v_item       items%rowtype;
  v_ms         int;
begin
  select s.id, s.submitted_at into v_session, v_submitted
  from participants p
  join sessions s on s.participant_id = p.id
  where p.invite_token_hash = p_token_hash;

  if v_session is null then
    raise exception 'no session for this token';
  end if;

  -- Once submitted, the raw responses are the record a score was computed from
  -- and a psychologist may already be reading (R1). Late answers are refused,
  -- not silently applied.
  if v_submitted is not null then
    raise exception 'session already submitted';
  end if;

  select * into v_item from items where id = p_item_id;

  if v_item.id is null then
    raise exception 'no such item';
  end if;

  if p_value < v_item.response_min or p_value > v_item.response_max then
    raise exception 'value % is outside % .. % for item %',
      p_value, v_item.response_min, v_item.response_max, v_item.code;
  end if;

  -- Clamped, because `flags.py`'s too_fast check sums ms_elapsed and compares
  -- the total against six minutes. A student who leaves one item open over
  -- lunch would otherwise contribute an hour to that sum and mask a session
  -- that was genuinely rushed. Real away-time is not lost — _long_gap reads
  -- started_at/submitted_at, which this does not touch.
  v_ms := least(greatest(coalesce(p_ms_elapsed, 0), 0), 120000);

  insert into responses (session_id, item_id, value, ms_elapsed, answered_at)
  values (v_session, p_item_id, p_value, v_ms, now())
  on conflict (session_id, item_id) do update
    set value       = excluded.value,
        ms_elapsed  = excluded.ms_elapsed,
        answered_at = excluded.answered_at;

  -- progress is {instrument_code: highest ordinal reached}, per 0001_init.sql.
  -- greatest() so stepping back to change an answer does not rewind it.
  update sessions
  set last_seen_at = now(),
      progress = progress || jsonb_build_object(
        v_item.instrument_code,
        greatest(
          coalesce((progress ->> v_item.instrument_code)::int, 0),
          v_item.ordinal
        )
      )
  where id = v_session;
end;
$$;

-- ── submit ─────────────────────────────────────────────────────────────────
-- Sets submitted_at, moves the participant to 'submitted', and enqueues exactly
-- one score_session job — in one transaction, so a session can never be marked
-- submitted with no job to score it.
--
-- Nothing drains that job until M7. Enqueueing now is what makes the pending
-- work visible instead of implicit, exactly as M5 did with send_email.
--
-- Returns true when this call did the submitting, false when the session was
-- already submitted. The second case is not an error — a double-tapped finish
-- button or a retried request is ordinary — but it must not queue a second job.
create or replace function submit_assessment(p_token_hash text)
returns boolean
language plpgsql
security definer
set search_path = public
as $$
declare
  v_participant uuid;
  v_session     uuid;
  v_submitted   timestamptz;
  v_expected    int;
  v_answered    int;
begin
  select p.id, s.id, s.submitted_at
    into v_participant, v_session, v_submitted
  from participants p
  join sessions s on s.participant_id = p.id
  where p.invite_token_hash = p_token_hash;

  if v_session is null then
    raise exception 'no session for this token';
  end if;

  if v_submitted is not null then
    return false;
  end if;

  -- Completeness is checked here and not only in the server action, because an
  -- incomplete session is not merely untidy: score_interests() and
  -- score_personality() raise ScoringError on a missing item, so a partial
  -- submission would queue a job that cannot succeed and would surface in M7 as
  -- a mystery failed job rather than here as a refusal.
  --
  -- "Every item that exists", not "every item in the modules this student
  -- touched". Consequence worth knowing: loading a new instrument mid-cohort
  -- (GET2, §6.3) blocks students who are already in flight, because the
  -- assessment they started is no longer the assessment the system defines.
  -- That is the loud failure, and it is the right one — the quiet alternative
  -- is scoring a GET2 module nobody answered. Drain in-flight sessions before
  -- loading a new instrument.
  select count(*) into v_expected from items;

  select count(*) into v_answered
  from responses r
  join items i on i.id = r.item_id
  where r.session_id = v_session;

  if v_expected = 0 then
    raise exception 'no items are loaded; nothing to submit';
  end if;

  if v_answered < v_expected then
    raise exception 'assessment is incomplete: % of % items answered',
      v_answered, v_expected;
  end if;

  update sessions
  set submitted_at = now(),
      last_seen_at = now()
  where id = v_session;

  update participants
  set status = 'submitted'
  where id = v_participant;

  insert into jobs (kind, payload)
  values ('score_session', jsonb_build_object('session_id', v_session));

  -- actor is null: the student is not a `profiles` row and never will be.
  insert into audit_log (actor, action, subject, meta)
  values (
    null,
    'assessment.submitted',
    v_session::text,
    jsonb_build_object('participant_id', v_participant, 'items', v_answered)
  );

  return true;
end;
$$;

-- ── grants ─────────────────────────────────────────────────────────────────
-- Only the service role calls these, from a Next.js server action or route
-- handler. An authenticated counsellor reaching them directly could pass any
-- token hash they happened to hold; more to the point, nothing in the
-- counsellor UI has a reason to write a student's answers.
--
-- `anon` is named explicitly. It is the role a browser holds when it talks to
-- Supabase with the publishable key, and the taker page is the one page in the
-- product a logged-out browser is meant to reach — so the role that must not
-- have these is worth revoking by name rather than by inheritance from public.
revoke all on function taker_session_for_token(text) from public, anon, authenticated;
revoke all on function start_assessment(text, text)  from public, anon, authenticated;
revoke all on function record_response(text, uuid, int, int) from public, anon, authenticated;
revoke all on function submit_assessment(text)       from public, anon, authenticated;

-- Percentile v2 — psychologist review actions (plan.md §9, M8).
--
-- Never edit a migration that has run. Add a new one.
--
-- 0003_reviews.sql created the `reviews`, `review_events` and `career_directions`
-- tables and the R9 trigger that reads them. Nothing has ever written to them
-- outside the seed. This migration is the writer.
--
-- Why a function rather than a few supabase-js calls, for the fourth time in
-- this schema (0005, 0006, 0008, now this): supabase-js speaks REST, so every
-- call is a separate request with no transaction around it. Confirming a review
-- is five writes — upsert `reviews`, replace `career_directions`, append a
-- `review_events` row, move `participants.status`, enqueue `render_student` —
-- and they must not come apart. A confirm that wrote the review but not the
-- render job is a student whose psychologist signed off and whose report never
-- renders, with nothing anywhere reporting it.
--
-- ─────────────────────────────────────────────────────────────────────────────
-- THE DECISION THAT SHAPES THIS FILE
--
-- `render_student` is enqueued HERE and only here, and only on the transition
-- into `confirmed`.
--
-- R9 says no student report leaves the system without human sign-off, and
-- 0008's `record_score` is explicit that the render job "is R9's business and
-- only M8's confirm action may queue it". This function is that action. The
-- database trigger from 0003/0004 is the backstop that makes a mistake here
-- loud rather than silent — but a mistake here would still be a bug, because
-- the trigger would then fail every retry of a job that should never have
-- existed.
--
-- "On the transition" is load-bearing. Confirm is idempotent: a psychologist who
-- double-taps, or re-confirms after a send-back, must not queue a second render
-- and a second email. The guard is the same shape as `record_score`'s
-- `v_awaiting` — check what the row was BEFORE the write, not after.
-- ─────────────────────────────────────────────────────────────────────────────

-- Statuses this function accepts. `reviews.status` has no check constraint in
-- 0003 and gains none here, for 0008's stated reason: an unknown value should
-- fail loudly in one place that names it rather than being refused at write time
-- by a constraint a future migration must remember to widen. This function IS
-- that one place.
--
--   in_progress    — save draft, come back later (§9.1)
--   confirmed      — sign-off; enqueues render_student, satisfies R9
--   needs_more_info — send back for a second conversation (§9.1)

create or replace function save_review(
  p_session_id      uuid,
  p_reviewer_id     uuid,
  p_status          text,
  p_interview_mode  text,
  p_interview_notes text,
  p_flags_reviewed  jsonb,
  p_directions      jsonb
)
returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
  v_review        uuid;
  v_prior_status  text;
  v_participant   uuid;
  v_organisation  uuid;
  v_reviewer_org  uuid;
  v_role          text;
  v_direction_n   int;
begin
  if p_status not in ('in_progress', 'confirmed', 'needs_more_info') then
    raise exception 'unknown review status %', p_status;
  end if;

  -- ── tenancy and role, checked in the database ──────────────────────────
  -- The caller is a Next.js server action holding the service role, which
  -- bypasses RLS entirely — so as with `import_roster()`, this is the only
  -- place the boundary is enforced in the database for this operation. The
  -- action checks it too. Neither check is redundant: this one takes
  -- `p_reviewer_id` as an argument, so it must prove that reviewer belongs to
  -- the organisation that owns the session rather than trust the caller.
  select c.organisation_id, p.id
    into v_organisation, v_participant
  from sessions s
  join participants p on p.id = s.participant_id
  join cohorts c      on c.id = p.cohort_id
  where s.id = p_session_id;

  if v_participant is null then
    raise exception 'no session %', p_session_id;
  end if;

  select organisation_id, role into v_reviewer_org, v_role
  from profiles where id = p_reviewer_id;

  if v_reviewer_org is null then
    raise exception 'no profile %', p_reviewer_id;
  end if;

  -- Mirrors `reviews_psychologist_read` in 0003_reviews.sql. A counsellor is
  -- deliberately absent: the role split is what keeps `interview_notes` away
  -- from them (§16), and a counsellor who could WRITE the notes column would
  -- walk straight around the read policy that exists to protect it.
  if v_role not in ('psychologist', 'org_admin', 'superadmin') then
    raise exception 'role % may not review sessions', v_role;
  end if;

  if v_reviewer_org <> v_organisation then
    raise exception 'reviewer % is not in the organisation that owns session %',
      p_reviewer_id, p_session_id;
  end if;

  -- ── the confirm precondition (§9.1) ────────────────────────────────────
  -- "At least 1, at most 3" career directions. Checked before anything is
  -- written, so a confirm that cannot legally complete changes nothing.
  -- `jsonb_typeof` rather than a bare `jsonb_array_length`: the latter raises
  -- "cannot get array length of a scalar" on a JSON `null`, which is what a
  -- client sends when it means "no directions". SQL NULL and JSON null must both
  -- read as an empty list, not as an error a psychologist sees mid-save.
  if p_directions is null or jsonb_typeof(p_directions) = 'null' then
    p_directions := '[]'::jsonb;
  elsif jsonb_typeof(p_directions) <> 'array' then
    raise exception 'career directions must be a JSON array, got %', jsonb_typeof(p_directions);
  end if;

  v_direction_n := jsonb_array_length(p_directions);

  if p_status = 'confirmed' and v_direction_n = 0 then
    raise exception 'a confirmed review needs at least one career direction';
  end if;
  if v_direction_n > 3 then
    raise exception 'at most 3 career directions, got %', v_direction_n;
  end if;

  -- `career_directions.local_title` is NOT NULL: it is the text the student
  -- actually reads, and a direction without one is a blank line on their report.
  -- Checked here so the message names the rule, rather than surfacing as a
  -- null-constraint violation from inside the insert below.
  if exists (
    select 1
    from jsonb_array_elements(p_directions) as d
    where coalesce(btrim(d ->> 'local_title'), '') = ''
  ) then
    raise exception 'every career direction needs a local_title';
  end if;

  -- ── the review row ─────────────────────────────────────────────────────
  -- Read the prior status BEFORE the upsert. Everything conditional below hangs
  -- off this: after the write, every path looks like `confirmed`.
  select status into v_prior_status from reviews where session_id = p_session_id;

  -- `unique (session_id)` (0003): re-opening for `needs_more_info` updates this
  -- row rather than creating a second one, "so history stays on one thread".
  -- The thread itself is `review_events`.
  insert into reviews (
    session_id, reviewer_id, status, interview_mode, interview_notes,
    flags_reviewed, confirmed_at
  )
  values (
    p_session_id, p_reviewer_id, p_status, p_interview_mode, p_interview_notes,
    coalesce(p_flags_reviewed, '[]'::jsonb),
    case when p_status = 'confirmed' then now() else null end
  )
  on conflict (session_id) do update
    set reviewer_id     = excluded.reviewer_id,
        status          = excluded.status,
        interview_mode  = excluded.interview_mode,
        interview_notes = excluded.interview_notes,
        flags_reviewed  = excluded.flags_reviewed,
        -- Keep the original sign-off time when a confirmed review is saved
        -- again. `confirmed_at` records when a human signed off, and a later
        -- edit to the notes is not a second sign-off. Re-confirming after a
        -- send-back does stamp a new one, because that genuinely is one.
        confirmed_at    = case
                            when excluded.status <> 'confirmed' then null
                            when reviews.status = 'confirmed' then reviews.confirmed_at
                            else now()
                          end
  returning id into v_review;

  -- ── career directions ──────────────────────────────────────────────────
  -- Delete-then-insert, not upsert. `unique (review_id, rank)` means a review
  -- that had 3 directions and now has 2 would otherwise keep a stale rank 3 —
  -- the psychologist removed it, and it would still be on the student's report.
  --
  -- The cost is that `student_selected` is not preserved across a re-save. That
  -- column is written by the student choosing "the one" much later (§9.1), long
  -- after review is over; a re-save before that point has nothing to lose, and
  -- one after it is a reopened review, where the directions themselves are being
  -- revisited anyway.
  delete from career_directions where review_id = v_review;

  if v_direction_n > 0 then
    insert into career_directions (review_id, rank, onet_soc_code, local_title, rationale)
    select
      v_review,
      -- Rank from array position rather than from the payload: the client
      -- supplies an ordered list, and trusting a client-supplied `rank` invites
      -- a duplicate that trips the unique constraint mid-transaction.
      ordinality::int,
      -- An empty SOC code is a free-text direction, not a catalogue reference.
      -- `career_directions.onet_soc_code` is a FK, so '' would fail; the column
      -- is nullable precisely so `local_title` can stand alone (0003's comment:
      -- "shown to the student even if onet_soc_code is null").
      nullif(btrim(d ->> 'onet_soc_code'), ''),
      btrim(d ->> 'local_title'),
      nullif(btrim(d ->> 'rationale'), '')
    from jsonb_array_elements(p_directions) with ordinality as t(d, ordinality);
  end if;

  -- ── participant status ─────────────────────────────────────────────────
  -- The §5 state machine: pending_review → reviewed → confirmed, with
  -- needs_more_info as the loop back. `in_progress` maps to `reviewed` — the
  -- psychologist has the session open and has saved something.
  update participants
  set status = case p_status
                 when 'confirmed'       then 'confirmed'
                 when 'needs_more_info' then 'needs_more_info'
                 else 'reviewed'
               end
  where id = v_participant;

  -- ── audit trail (§9.1) ─────────────────────────────────────────────────
  -- One row per action, never overwritten. `reviews` holds current state;
  -- this is what happened and when.
  insert into review_events (review_id, actor, event, meta)
  values (
    v_review,
    p_reviewer_id,
    case
      when p_status = 'confirmed'       then 'confirmed'
      when p_status = 'needs_more_info' then 'reopened'
      when v_prior_status is null       then 'opened'
      else 'note_added'
    end,
    jsonb_build_object('directions', v_direction_n, 'prior_status', v_prior_status)
  );

  -- ── R9: the render job ─────────────────────────────────────────────────
  -- Only on the transition into `confirmed`, and only once. `render_student`
  -- has no handler until M9 (handlers/__init__.py's NOT_YET_IMPLEMENTED names
  -- it), so today this row is claimed, fails with "not implemented until M9",
  -- retries five times and alerts. That is the intended shape: the queue is the
  -- record of work owed, and M9 is what drains it.
  --
  -- The second guard — no existing pending/running row — covers a re-confirm
  -- after a send-back where the first render never completed.
  if p_status = 'confirmed'
     and v_prior_status is distinct from 'confirmed'
     and not exists (
       select 1 from jobs
       where kind = 'render_student'
         and payload ->> 'session_id' = p_session_id::text
         and status in ('pending', 'running')
     )
  then
    insert into jobs (kind, payload)
    values ('render_student', jsonb_build_object('session_id', p_session_id));
  end if;

  insert into audit_log (actor, action, subject, meta)
  values (
    p_reviewer_id,
    'review.' || p_status,
    p_session_id::text,
    jsonb_build_object('review_id', v_review, 'directions', v_direction_n)
  );

  return v_review;
end;
$$;

-- Only the service role calls this, from a Next.js server action. It takes
-- `p_reviewer_id` as an argument — an authenticated counsellor reaching it
-- directly could pass a psychologist's id and write interview notes under their
-- name. The role and tenancy checks above are meaningful precisely because the
-- only caller reads that value from `verifySession()` rather than from a form.
--
-- `anon` is named explicitly, as in 0006 and 0008: it is the role a logged-out
-- browser holds.
revoke all on function save_review(uuid, uuid, text, text, text, jsonb, jsonb)
  from public, anon, authenticated;

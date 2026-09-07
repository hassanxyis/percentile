-- Percentile v2 — student report bookkeeping (plan.md §14, §15, M9).
--
-- Never edit a migration that has run. Add a new one.
--
-- M9's render handler does three things that must not come apart: upload the
-- PDF, record the `reports` row that points at it, and queue the email that
-- delivers it. The upload is object storage and cannot join a transaction; the
-- other two are rows, and this function writes them together.
--
-- The fifth time this file's reasoning appears in the schema (0005, 0006, 0008,
-- 0009, now this): supabase-py speaks REST, so two calls are two requests with
-- no transaction between them. A `reports` row written without its email is a
-- student whose report exists and who is never told; an email queued without
-- the row is a job that fails five times and alerts about a report that
-- rendered correctly.
--
-- ─────────────────────────────────────────────────────────────────────────────
-- WHY THE DEDUPE GUARDS LIVE HERE AND NOT IN PYTHON
--
-- The handler previously filtered `jobs` with PostgREST's JSON operator syntax
-- (`payload->>template`). It is the only place in this codebase that did, it is
-- unverified against a live PostgREST, and if the filter silently matched
-- nothing the consequence is not a crash — it is a second email to a student
-- with a second link, every time a worker dies mid-job.
--
-- A guard whose failure mode is silent must not depend on syntax nobody has
-- exercised. In SQL it is an ordinary `where`, and `test_student_report.py`
-- runs it against a real Postgres in CI.
-- ─────────────────────────────────────────────────────────────────────────────

create or replace function record_student_report(
  p_session_id       uuid,
  p_storage_path     text,
  p_template_version text,
  p_engine_version   text
)
returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
  v_report      uuid;
  v_participant uuid;
begin
  -- R9, checked here as well as by the trigger below and by the handler before
  -- it renders. Three checks, none redundant: the handler stops a PDF being
  -- built, this stops a row being written by any other caller, and
  -- `trg_enforce_review_before_student_report` is the backstop that does not
  -- depend on either of them being correct.
  if not exists (
    select 1 from reviews
    where session_id = p_session_id and status = 'confirmed'
  ) then
    raise exception 'R9: session % has no confirmed review', p_session_id;
  end if;

  select p.id into v_participant
  from sessions s
  join participants p on p.id = s.participant_id
  where s.id = p_session_id;

  if v_participant is null then
    raise exception 'no session %', p_session_id;
  end if;

  -- Idempotent on (session, template_version). A retried job rewrites its own
  -- row rather than colliding with it — and a TEMPLATE change (M11, a wording
  -- fix) writes a new row beside the old one, so an old PDF stays reproducible
  -- against the template that produced it, the same way `scores` keeps history
  -- per engine version (R1).
  select id into v_report
  from reports
  where session_id = p_session_id
    and kind = 'student'
    and template_version = p_template_version;

  if v_report is null then
    insert into reports (kind, session_id, storage_path, template_version, engine_version)
    values ('student', p_session_id, p_storage_path, p_template_version, p_engine_version)
    returning id into v_report;
  else
    -- The path is stable for a session, but a re-render under a changed
    -- `report_storage_bucket` would move it. Keep the row pointing at the
    -- object that actually exists.
    update reports
    set storage_path   = p_storage_path,
        engine_version = p_engine_version
    where id = v_report;
  end if;

  -- plan §15: the report is delivered as a signed URL, minted at send time by
  -- the email handler. Guarded on 'has this session EVER had one', not on a
  -- pending one — matching `record_score`'s notify guard, and for the same
  -- reason. A student who receives two links to the same report learns to
  -- ignore them.
  if not exists (
    select 1 from jobs
    where kind = 'send_email'
      and payload ->> 'template' = 'student_report'
      and payload ->> 'session_id' = p_session_id::text
  ) then
    insert into jobs (kind, payload)
    values (
      'send_email',
      jsonb_build_object('template', 'student_report', 'session_id', p_session_id)
    );
  end if;

  insert into audit_log (actor, action, subject, meta)
  values (
    null,
    'report.rendered',
    p_session_id::text,
    jsonb_build_object(
      'report_id', v_report,
      'template_version', p_template_version,
      'engine_version', p_engine_version
    )
  );

  return v_report;
end;
$$;

-- Only the engine calls this, holding the service role. `reports` has a read
-- policy for authenticated users (0002_rls.sql) but no write policy anywhere;
-- a SECURITY DEFINER function runs as its owner and would write regardless, so
-- the grant has to be revoked explicitly.
--
-- `anon` is named for the same reason 0006, 0008 and 0009 name it: it is the
-- role a logged-out browser holds, and the taker page is reachable logged out.
revoke all on function record_student_report(uuid, text, text, text)
  from public, anon, authenticated;

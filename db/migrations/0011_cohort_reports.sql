-- Percentile v2 — cohort report bookkeeping (plan.md §10, §14, M11).
--
-- Never edit a migration that has run. Add a new one.
--
-- The institution's half of what this product produces. 0010 records the
-- student's report; this records the cohort's, and the two differ in exactly
-- two ways, both deliberate and both stated below rather than left to be
-- inferred from a diff.
--
-- Same reasoning as 0005, 0006, 0008, 0009 and 0010 for why this is a function
-- at all: supabase-py speaks REST, so the `reports` row and the `audit_log` row
-- would otherwise be two requests with no transaction between them.
--
-- ─────────────────────────────────────────────────────────────────────────────
-- DIFFERENCE 1: NO R9 CHECK, AND THAT IS NOT AN OVERSIGHT.
--
-- `record_student_report` opens by refusing a session with no confirmed review.
-- This function has no equivalent, because R9 is about student reports
-- specifically: `enforce_review_before_student_report()` (0003_reviews.sql)
-- opens with `if NEW.kind = 'student'`, and 0004 widened the trigger's EVENT to
-- `before insert or update` without touching that condition. A cohort report is
-- an aggregate over a group, not a document about one student, and no
-- psychologist signs one off.
--
-- More than that: gating cohort reports on confirmed reviews would break the
-- feature §10 added them for. The completion table exists to show a school its
-- review backlog — how many students are sitting in `pending_review`. A cohort
-- report that could not render until every review was confirmed would be
-- unavailable to precisely the schools whose backlog it exists to reveal.
--
-- DIFFERENCE 2: NO EMAIL JOB.
--
-- 0010 queues a `send_email` so the student gets a signed link. §15's template
-- table lists no cohort equivalent, and there is no obvious single recipient —
-- a cohort belongs to an organisation, not to a person. The counsellor
-- downloads it from the dashboard, where `reportDownloadUrl` already mints a
-- five-minute signed URL on click and never renders one into the page.
-- ─────────────────────────────────────────────────────────────────────────────

create or replace function record_cohort_report(
  p_cohort_id        uuid,
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
  v_report uuid;
begin
  if not exists (select 1 from cohorts where id = p_cohort_id) then
    raise exception 'no cohort %', p_cohort_id;
  end if;

  -- Idempotent on (cohort, template_version), matching 0010's rule for
  -- students. A retried job — the runner returns a job to `pending` whenever a
  -- worker dies mid-flight — rewrites its own row rather than accumulating a
  -- second one. A TEMPLATE change writes a new row beside the old, so a report
  -- a school already downloaded stays reproducible against the template that
  -- produced it, the same way `scores` keeps history per engine version (R1).
  select id into v_report
  from reports
  where cohort_id = p_cohort_id
    and kind = 'cohort'
    and template_version = p_template_version;

  if v_report is null then
    insert into reports (kind, session_id, cohort_id, storage_path,
                         template_version, engine_version)
    values ('cohort', null, p_cohort_id, p_storage_path,
            p_template_version, p_engine_version)
    returning id into v_report;
  else
    -- The path is stable for a cohort, but a re-render under a changed
    -- `report_storage_bucket` would move it. Keep the row pointing at the
    -- object that actually exists — `reports.storage_path` is the only pointer,
    -- and the download action reads it.
    update reports
    set storage_path   = p_storage_path,
        engine_version = p_engine_version
    where id = v_report;
  end if;

  insert into audit_log (actor, action, subject, meta)
  values (
    null,
    'report.rendered',
    p_cohort_id::text,
    jsonb_build_object(
      'report_id', v_report,
      'kind', 'cohort',
      'template_version', p_template_version,
      'engine_version', p_engine_version
    )
  );

  return v_report;
end;
$$;

-- Only the engine calls this, holding the service role. `reports` has a read
-- policy for authenticated users (0002_rls.sql) but no write policy anywhere; a
-- SECURITY DEFINER function runs as its owner and would write regardless, so
-- the grant has to be revoked explicitly.
--
-- A counsellor who could call this directly could manufacture a `reports` row
-- pointing at any storage path in the bucket — including another school's
-- object, since the path is an argument rather than something this function
-- derives.
--
-- `anon` is named for the same reason 0006, 0008, 0009 and 0010 name it: it is
-- the role a logged-out browser holds, and the taker page is reachable logged
-- out.
revoke all on function record_cohort_report(uuid, text, text, text)
  from public, anon, authenticated;

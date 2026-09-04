-- Percentile v2 — roster import (plan.md §12, §13, M5).
--
-- Never edit a migration that has run. Add a new one.
--
-- Why a database function rather than a few calls from the server action:
-- importing a roster is two writes that must not come apart. supabase-js speaks
-- REST, so a participants insert and a jobs insert are separate requests with no
-- transaction between them. If the first succeeds and the second fails, the
-- cohort gains students whose invites will never be enqueued — and because only
-- sha256(token) is stored (R2-adjacent: the raw token is never persisted), those
-- students' invite links are gone and unrecoverable. There is no repair short of
-- deleting the rows and re-importing.
--
-- Everything below therefore happens in one transaction, or none of it does.

create or replace function import_roster(
  p_cohort_id       uuid,
  p_organisation_id uuid,
  p_actor           uuid,
  p_rows            jsonb
)
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
  v_inserted integer;
begin
  -- Defence in depth. The caller is a Next.js server action holding the service
  -- role, which bypasses RLS entirely, so this is the only place the tenant
  -- boundary is enforced in the database for this operation. The action checks
  -- it too; neither check is redundant, because a future caller might forget.
  if not exists (
    select 1 from cohorts
    where id = p_cohort_id and organisation_id = p_organisation_id
  ) then
    raise exception 'cohort % does not belong to organisation %',
      p_cohort_id, p_organisation_id;
  end if;

  if p_rows is null or jsonb_array_length(p_rows) = 0 then
    raise exception 'no rows to import';
  end if;

  -- `status` is left to its 'invited' default: the row exists, the invite has
  -- not been sent (that is M7's job), and the student has not started.
  with inserted as (
    insert into participants (
      cohort_id, full_name, email, external_ref,
      intended_field, education_level, invite_token_hash
    )
    select
      p_cohort_id,
      row_data->>'full_name',
      row_data->>'email',
      nullif(row_data->>'external_ref', ''),
      nullif(row_data->>'intended_field', ''),
      nullif(row_data->>'education_level', ''),
      row_data->>'invite_token_hash'
    from jsonb_array_elements(p_rows) as row_data
    returning id
  )
  -- One invite job per participant. Nothing drains these until M7; enqueueing
  -- now is what makes the pending work visible instead of implicit.
  --
  -- The payload carries `participant_id` and NOT the token. `jobs` is
  -- service-role-only (0002_rls.sql gives it no policy at all), but a live
  -- credential for a minor's record would still be sitting in a row that gets
  -- retried, logged, and copied into `last_error` on failure.
  --
  -- Consequence for M7, decided during M5: the send_email handler must MINT A
  -- FRESH TOKEN at send time, update invite_token_hash, and email that link.
  -- It cannot reconstruct the original — only the hash was ever stored. Any
  -- link handed out from the import screen's one-time download stops working at
  -- that point, which is the correct precedence: the emailed invite wins.
  insert into jobs (kind, payload)
  select 'send_email',
         jsonb_build_object('template', 'invite', 'participant_id', inserted.id)
  from inserted;

  get diagnostics v_inserted = row_count;

  insert into audit_log (actor, action, subject, meta)
  values (
    p_actor,
    'roster.imported',
    p_cohort_id::text,
    jsonb_build_object('count', v_inserted, 'organisation_id', p_organisation_id)
  );

  return v_inserted;
end;
$$;

-- Only the service role calls this. A counsellor's browser session must never
-- reach it directly: the function takes `p_organisation_id` as an argument, so
-- an authenticated caller could simply pass someone else's. The tenant check
-- above is meaningful precisely because the only caller is a server action that
-- reads that value from the session rather than from the request.
revoke all on function import_roster(uuid, uuid, uuid, jsonb) from public;
revoke all on function import_roster(uuid, uuid, uuid, jsonb) from authenticated;

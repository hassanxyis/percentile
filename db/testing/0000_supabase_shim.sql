-- Test harness only — NOT a migration. See db/testing/README.md.
--
-- Supabase ships an `auth` schema, an `auth.users` table, `auth.uid()` and the
-- anon/authenticated/service_role roles. A plain Postgres container ships none
-- of them, and every RLS policy in 0002/0003 depends on all of them. This file
-- supplies the minimum shim, applied before 0001_init.sql.
--
-- Object names and the GUC name below match Supabase/PostgREST exactly. That is
-- the point: a shim that behaves differently from production would let a policy
-- pass here and leak there.

create extension if not exists pgcrypto;

-- ── roles ────────────────────────────────────────────────────────────────
-- Roles are cluster-wide, so they survive the harness's `drop schema public
-- cascade`. Hence the existence checks: this file is applied once per test
-- session against a container that may already have run it.
--
-- `service_role` is bypassrls because that is precisely what it is in
-- production — the engine and the Next.js server actions connect with it and
-- see every tenant (plan §4). Reproducing that here is what makes the R9
-- trigger test meaningful: the trigger's own lookup must not be RLS-filtered.
do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'anon') then
    create role anon nologin noinherit;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'authenticated') then
    create role authenticated nologin noinherit;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'service_role') then
    create role service_role nologin noinherit bypassrls;
  end if;
end
$$;

create schema if not exists auth;

-- `profiles.id` references this (0001_init.sql). Only the columns the schema
-- and the seed actually touch — this is not an attempt to reproduce GoTrue.
create table if not exists auth.users (
  id         uuid primary key default gen_random_uuid(),
  email      text unique,
  created_at timestamptz not null default now()
);

-- ── auth.uid() and friends ───────────────────────────────────────────────
-- Supabase reads the caller's identity out of a request-scoped GUC that
-- PostgREST sets from the JWT. Tests impersonate a profile by setting the same
-- GUC transaction-locally (see engine/tests/db/harness.py: as_user).
--
-- Two details that are easy to get wrong:
--   * `current_setting(name, true)` — the `true` is missing_ok. Without it, an
--     unimpersonated session raises instead of returning NULL, and every policy
--     errors rather than denying.
--   * `nullif(..., '')` — casting '' to uuid raises 22P02. NULL casts fine and
--     denies cleanly, which is the behaviour a policy wants for "not logged in".
create or replace function auth.uid() returns uuid
language sql stable
as $$
  select coalesce(
    nullif(current_setting('request.jwt.claim.sub', true), ''),
    nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub'
  )::uuid
$$;

create or replace function auth.role() returns text
language sql stable
as $$
  select coalesce(
    nullif(current_setting('request.jwt.claim.role', true), ''),
    nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'role'
  )
$$;

create or replace function auth.jwt() returns jsonb
language sql stable
as $$
  select coalesce(nullif(current_setting('request.jwt.claims', true), ''), '{}')::jsonb
$$;

grant usage on schema auth to anon, authenticated, service_role;
grant execute on function auth.uid(), auth.role(), auth.jwt()
  to anon, authenticated, service_role;
grant select on auth.users to service_role;
grant usage on schema public to anon, authenticated, service_role;

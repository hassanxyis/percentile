-- Test harness only — NOT a migration. See db/testing/README.md.
--
-- Applied after the last migration, because `all tables in schema public` has
-- to mean all of them.
--
-- Supabase grants these by default; a plain container does not. The difference
-- is not cosmetic. RLS filters rows a role is *allowed* to query — it does not
-- grant the query itself. Without these GRANTs every select raises "permission
-- denied for table X", and a test asserting "a counsellor sees nothing" passes
-- for entirely the wrong reason. The GRANTs are what make RLS the thing under
-- test.

grant usage on schema public to anon, authenticated, service_role;

-- SELECT only for authenticated, matching 0002_rls.sql's header: "These
-- policies are read-only by design: no insert/update/delete policy exists for
-- authenticated users anywhere in this file." Every write in the product goes
-- through the service role server-side.
grant select on all tables in schema public to anon, authenticated;
grant all    on all tables in schema public to service_role;

-- `authenticated` needs execute on auth_organisation_id() and
-- review_progress_for_session(); 0002/0003 already grant those two explicitly.
-- This is the blanket equivalent for anything added later.
grant execute on all functions in schema public to authenticated, service_role;

-- Deliberately absent: `alter table ... force row level security`. The tables
-- are owned by the connection that applied the migrations, and that same
-- connection seeds data and performs the service-role-equivalent inserts in the
-- R9 tests. FORCE would subject the owner to RLS and break both — and it would
-- not reflect production, where the writer is a bypassrls role rather than the
-- owner.
--
-- Tests defeat the owner's RLS bypass by `set local role authenticated`
-- instead, which is closer to what a real authenticated request does.

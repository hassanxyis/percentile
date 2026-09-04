# `db/testing/` — test harness only

**Nothing in this directory is a migration.** These files are never applied to Supabase, are not
part of the append-only sequence in `db/migrations/`, and may be edited freely.

They exist because the migrations depend on objects Supabase provides but a plain Postgres
container does not:

| Migration depends on | Provided by |
|---|---|
| `auth.users` — `profiles.id` references it (`0001_init.sql`) | `0000_supabase_shim.sql` |
| `auth.uid()` — every RLS policy in `0002`/`0003` calls it | `0000_supabase_shim.sql` |
| roles `anon`, `authenticated`, `service_role` | `0000_supabase_shim.sql` |
| table-level `GRANT`s (Supabase sets these by default) | `9999_grants.sql` |

Application order, driven by `engine/tests/db/harness.py`:

```
0000_supabase_shim.sql → db/migrations/*.sql (sorted) → 9999_grants.sql → db/seed/demo_org.sql
```

The shim runs first because `0001` needs `auth.users` and `0002` grants to `authenticated`. The
grants run last because they name tables that do not exist until `0003`.

## Why the grants file matters for correctness

A policy grants nothing by itself. Without a `GRANT`, a query returns *permission denied* rather
than *zero rows* — and a test asserting "this role sees nothing" would pass for entirely the wrong
reason. The grants make RLS the thing being tested.

## Running the tests

The harness runs `drop schema public cascade`. Point `TEST_DATABASE_URL` at a throwaway database
only — never at the real project.

```powershell
docker run -d --name pg-test -e POSTGRES_PASSWORD=postgres -p 5432:5432 postgres:16
$env:TEST_DATABASE_URL = "postgresql://postgres:postgres@localhost:5432/postgres"
cd engine
.\.venv\Scripts\python.exe -m pytest -m db -v
```

With `TEST_DATABASE_URL` unset, `engine/tests/db/` is skipped and the rest of the suite runs
normally.

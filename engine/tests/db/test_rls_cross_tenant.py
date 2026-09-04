"""Cross-tenant isolation (plan.md §5, §18.1, Appendix B).

Most participants are minors. A counsellor at one institution reading another
institution's students is the failure that ends the product, so this is the
test the whole RLS design exists to satisfy.

The seed puts real rows in *both* organisations for every table swept here. That
matters: "org B's user sees zero rows" only means something if there were rows to
see.
"""

import psycopg

from . import harness


def test_rls_is_enforced_at_all(conn: psycopg.Connection) -> None:
    """Canary — proves the rest of this file is not passing vacuously.

    Postgres skips RLS for superusers, BYPASSRLS roles and the table owner, and
    the test connection is all three. `as_user` drops to `authenticated` to close
    those paths. If that ever stops working, every other assertion here would
    still pass while testing nothing — so assert the difference directly.
    """
    with conn.cursor() as cur:
        cur.execute("select count(*) from cohorts")
        as_owner = cur.fetchone()[0]

    as_counsellor = harness.count_as(conn, harness.COUNSELLOR_A, "cohorts")

    assert as_owner == 2, "seed should hold one cohort per organisation"
    assert as_counsellor < as_owner, "RLS is being bypassed; these tests prove nothing"
    assert as_counsellor == 1


def test_counsellor_sees_only_own_organisation(conn: psycopg.Connection) -> None:
    """Across every tenant-scoped table, each side sees its own rows and no more.

    Both directions are checked. A policy that returned nothing to anyone would
    pass a one-sided "cannot see the other tenant" assertion.
    """
    with conn.cursor() as cur:
        for table in harness.TENANT_TABLES:
            cur.execute(f"select count(*) from {table}")  # noqa: S608 — module constant
            total = cur.fetchone()[0]

            seen_by_a = harness.count_as(conn, harness.COUNSELLOR_A, table)
            seen_by_b = harness.count_as(conn, harness.COUNSELLOR_B, table)

            assert total > 0, f"{table}: seed has no rows, so this assertion is vacuous"
            assert seen_by_a > 0, f"{table}: org A counsellor sees none of their own rows"
            assert seen_by_b > 0, f"{table}: org B counsellor sees none of their own rows"
            assert seen_by_a + seen_by_b == total, (
                f"{table}: {seen_by_a} + {seen_by_b} != {total} — rows visible to both "
                f"tenants, or to neither"
            )


def test_organisation_row_is_self_only(conn: psycopg.Connection) -> None:
    """A user reads their own organisation and no other."""
    with harness.as_user(conn, harness.COUNSELLOR_A), conn.cursor() as cur:
        cur.execute("select id from organisations")
        assert [row[0] for row in cur.fetchall()] == [harness.ORG_A]


def test_profile_read_is_self_only(conn: psycopg.Connection) -> None:
    """`profiles_self_read` is deliberately narrower than the organisation.

    0002_rls.sql: "A user reads only their own profile row. Not their
    colleagues' — nothing in the counsellor UI needs that, and it keeps the
    helper above non-recursive."
    """
    with harness.as_user(conn, harness.COUNSELLOR_A), conn.cursor() as cur:
        cur.execute("select id from profiles")
        assert [row[0] for row in cur.fetchall()] == [harness.COUNSELLOR_A]


def test_reference_data_is_readable_by_any_authenticated_user(conn: psycopg.Connection) -> None:
    """Instrument and occupation data is licensed but not secret (0002_rls.sql §2)."""
    for table in ("instruments", "items", "occupations"):
        assert harness.count_as(conn, harness.COUNSELLOR_A, table) > 0
        assert harness.count_as(conn, harness.COUNSELLOR_B, table) > 0


def test_jobs_and_audit_log_are_service_role_only(conn: psycopg.Connection) -> None:
    """RLS enabled, no policy at all — so authenticated users see nothing.

    0002_rls.sql leaves these deliberately policy-less and says so: "The silence
    is deliberate — do not add a policy here without a reason written down."
    The seed puts rows in both tables so this reads as "denied", not "empty".
    """
    with conn.cursor() as cur:
        for table in ("jobs", "audit_log"):
            cur.execute(f"select count(*) from {table}")  # noqa: S608 — module constant
            assert cur.fetchone()[0] > 0, f"{table}: seed has no rows; assertion would be vacuous"

    for table in ("jobs", "audit_log"):
        assert harness.count_as(conn, harness.COUNSELLOR_A, table) == 0
        assert harness.count_as(conn, harness.ORG_ADMIN_A, table) == 0

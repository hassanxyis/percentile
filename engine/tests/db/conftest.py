"""Fixtures for the tests that need a real Postgres.

Skipped entirely unless `TEST_DATABASE_URL` is set, so `pytest` on a Windows box
with no Docker runs exactly as it did before this directory existed.

The harness runs `drop schema public cascade`. Point `TEST_DATABASE_URL` at a
throwaway database only.

The value is read through `Settings` rather than `os.environ` so that both
sources work: CI exports it as an environment variable, while a developer more
naturally puts it in `engine/.env` alongside every other setting — and
pydantic-settings reads that file without exporting it to the environment.
"""

from pathlib import Path

import pytest

from app.config import Settings

psycopg = pytest.importorskip("psycopg", reason="psycopg is not installed")

from . import harness  # noqa: E402 — must follow the importorskip guard

TEST_DATABASE_URL = Settings().test_database_url


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Mark everything under this directory `db`, so `-m db` selects it.

    Two traps here, both of which produce a marker that looks applied but is not:

    * A module-level `pytestmark` in a *conftest* does not propagate to test
      modules — only one written inside the test module itself does. Hence
      marking at collection time, which keeps the marker in one place rather
      than repeating it in every file where a new file would silently miss it.
    * This hook fires once with the *whole* session's items, not just the ones
      under this directory. Without the path check below it marks all 146 tests
      as `db`, and `-m "not db"` deselects the entire suite.
    """
    here = Path(__file__).parent
    for item in items:
        if here in Path(str(item.fspath)).parents:
            item.add_marker(pytest.mark.db)


def pytest_runtest_setup(item: pytest.Item) -> None:
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL not set — Postgres harness tests skipped")


@pytest.fixture(scope="session")
def db_url() -> str:
    return TEST_DATABASE_URL


@pytest.fixture(scope="session")
def _applied(db_url: str) -> None:
    """Rebuild the schema and apply shim → migrations → grants → seed, once."""
    with psycopg.connect(db_url, autocommit=True) as conn:
        harness.reset_and_apply(conn)


@pytest.fixture
def conn(db_url: str, _applied: None):
    """One connection per test, in a transaction that is always rolled back.

    Rollback rather than TRUNCATE: truncating 19 tables needs the FK order right
    (or `cascade`, which eats the seed) and a re-seed afterwards. It also undoes
    `set local role` and the impersonation GUC for free, so no test can leak an
    identity into the next one.
    """
    with psycopg.connect(db_url) as connection:
        connection.execute("set search_path = public, pg_catalog")
        transaction = connection.transaction()
        transaction.__enter__()
        try:
            yield connection
        finally:
            transaction.__exit__(psycopg.Rollback, psycopg.Rollback(), None)

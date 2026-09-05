"""Engine auth and liveness. Plan §17.3: every route without X-Engine-Key returns 401."""

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import app, get_database_check

TEST_SECRET = "test-secret-do-not-use-in-production"


def _reachable() -> None:
    """Stand-in for a healthy database."""


def _unreachable() -> None:
    raise ConnectionError("postgres is down")


@pytest.fixture
def client() -> TestClient:
    app.dependency_overrides[get_settings] = lambda: Settings(
        engine_shared_secret=TEST_SECRET
    )
    # /health reads from Postgres (it is the keep-alive, plan §2). Override the
    # check rather than requiring live Supabase credentials to run the suite.
    app.dependency_overrides[get_database_check] = lambda: _reachable
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_health_is_open(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_reports_degraded_when_database_is_unreachable(client: TestClient) -> None:
    """A probe that says "ok" while Postgres is down keeps the project alive and
    hides that every job is failing. Report 503 instead."""
    app.dependency_overrides[get_database_check] = lambda: _unreachable

    response = client.get("/health")

    assert response.status_code == 503
    assert response.json() == {"status": "degraded"}


def test_version_requires_key(client: TestClient) -> None:
    assert client.get("/version").status_code == 401


def test_version_rejects_wrong_key(client: TestClient) -> None:
    response = client.get("/version", headers={"X-Engine-Key": "wrong"})
    assert response.status_code == 401


def test_version_with_key(client: TestClient) -> None:
    response = client.get("/version", headers={"X-Engine-Key": TEST_SECRET})
    assert response.status_code == 200
    assert response.json()["engine_version"]


def test_tick_requires_key(client: TestClient) -> None:
    """`/tick` claims and runs jobs. An open one lets anyone drain the queue —
    including the invite mails, which would mint new tokens and invalidate every
    link already handed out."""
    assert client.post("/tick").status_code == 401


def test_tick_rejects_wrong_key(client: TestClient) -> None:
    assert client.post("/tick", headers={"X-Engine-Key": "wrong"}).status_code == 401


def test_unset_secret_rejects_everything() -> None:
    """An engine deployed without its secret must fail closed, not open."""
    app.dependency_overrides[get_settings] = lambda: Settings(engine_shared_secret="")
    try:
        client = TestClient(app)
        assert client.get("/version", headers={"X-Engine-Key": ""}).status_code == 401
    finally:
        app.dependency_overrides.clear()

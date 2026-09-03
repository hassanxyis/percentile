"""Engine auth and liveness. Plan §17.3: every route without X-Engine-Key returns 401."""

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import app

TEST_SECRET = "test-secret-do-not-use-in-production"


@pytest.fixture
def client() -> TestClient:
    app.dependency_overrides[get_settings] = lambda: Settings(
        engine_shared_secret=TEST_SECRET
    )
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_health_is_open(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_version_requires_key(client: TestClient) -> None:
    assert client.get("/version").status_code == 401


def test_version_rejects_wrong_key(client: TestClient) -> None:
    response = client.get("/version", headers={"X-Engine-Key": "wrong"})
    assert response.status_code == 401


def test_version_with_key(client: TestClient) -> None:
    response = client.get("/version", headers={"X-Engine-Key": TEST_SECRET})
    assert response.status_code == 200
    assert response.json()["engine_version"]


def test_unset_secret_rejects_everything() -> None:
    """An engine deployed without its secret must fail closed, not open."""
    app.dependency_overrides[get_settings] = lambda: Settings(engine_shared_secret="")
    try:
        client = TestClient(app)
        assert client.get("/version", headers={"X-Engine-Key": ""}).status_code == 401
    finally:
        app.dependency_overrides.clear()

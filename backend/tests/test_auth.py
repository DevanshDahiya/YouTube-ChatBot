import pytest
from fastapi.testclient import TestClient

from app import main

VID = "dQw4w9WgXcQ"


@pytest.fixture()
def client(env):
    return TestClient(main.app)


def test_no_key_configured_means_open(client, monkeypatch):
    monkeypatch.delenv("API_KEY", raising=False)
    assert client.post("/ingest", json={"video_id": VID}).status_code == 200


def test_key_required_when_configured(client, monkeypatch):
    monkeypatch.setenv("API_KEY", "s3cret")
    assert client.post("/ingest", json={"video_id": VID}).status_code == 401
    assert client.post("/ingest", json={"video_id": VID}, headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.post("/ingest", json={"video_id": VID}, headers={"X-API-Key": "s3cret"}).status_code == 200


def test_all_routes_are_protected_but_health_is_open(client, monkeypatch):
    monkeypatch.setenv("API_KEY", "s3cret")
    assert client.get("/health").status_code == 200
    assert client.get(f"/summary/{VID}").status_code == 401
    assert client.post("/ask", json={"video_id": VID, "question": "hi"}).status_code == 401
    r = client.post("/ingest", json={"video_id": "x"})
    assert r.json()["detail"]["code"] == "unauthorized"

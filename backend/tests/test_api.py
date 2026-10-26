import json

import pytest
from fastapi.testclient import TestClient

from app import ingestion, main

VID = "dQw4w9WgXcQ"


@pytest.fixture()
def client(env):
    c = TestClient(main.app)
    c.fetches = env
    return c


def test_health(client):
    assert client.get("/health").json() == {"ok": True}


def test_bad_video_id_is_400(client):
    assert client.post("/ingest", json={"video_id": "bad"}).status_code == 400


def test_no_captions_is_422(client, monkeypatch):
    def no_captions(video_id):
        raise ingestion.IngestionError("no_captions", "none")

    monkeypatch.setattr(ingestion, "fetch_transcript", no_captions)
    r = client.post("/ingest", json={"video_id": VID})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "no_captions"


def test_second_ingest_is_cached_and_skips_youtube(client):
    first = client.post("/ingest", json={"video_id": VID}).json()
    second = client.post("/ingest", json={"video_id": VID}).json()
    assert first["cached"] is False and second["cached"] is True
    assert client.fetches["fetch"] == 1


def test_ask_streams_sources_then_tokens_then_final(client):
    with client.stream("POST", "/ask", json={"video_id": VID, "question": "what?"}) as r:
        body = "".join(r.iter_text())
    events = [e for e in body.strip().split("\n\n")]
    names = [e.split("\n")[0] for e in events]
    assert names[0] == "event: sources" and names[-1] == "event: final"
    assert "event: token" in names
    final = json.loads(events[-1].split("data: ", 1)[1])
    assert {"answer", "citations", "invalid_citations"} <= set(final)


def test_ask_rejects_empty_question(client):
    assert client.post("/ask", json={"video_id": VID, "question": ""}).status_code == 422


def test_summary_shape_and_cache(client):
    first = client.get(f"/summary/{VID}").json()
    second = client.get(f"/summary/{VID}").json()
    assert first == second
    assert first["overview"] and first["key_points"]
    assert {"start", "ts", "text"} <= set(first["key_points"][0])

"""Step: the extension can send its own transcript, so the server never has to call YouTube."""
import pytest
from fastapi.testclient import TestClient

from app import ingestion, main

VID = "dQw4w9WgXcQ"


@pytest.fixture()
def client(env):
    c = TestClient(main.app)
    c.fetches = env
    return c


def test_ingest_with_transcript_never_calls_youtube(client):
    body = {
        "video_id": VID,
        "transcript": [
            {"text": "hello there", "start": 0.0, "duration": 2.0},
            {"text": "this is a test video", "start": 2.0, "duration": 2.0},
        ],
    }
    r = client.post("/ingest", json=body)
    assert r.status_code == 200 and r.json()["cached"] is False
    assert client.fetches["fetch"] == 0  # fetch_transcript was never called


def test_server_still_fetches_when_no_transcript_sent(client):
    r = client.post("/ingest", json={"video_id": VID})
    assert r.status_code == 200
    assert client.fetches["fetch"] == 1


def test_blank_transcript_is_rejected(client):
    body = {"video_id": VID, "transcript": [{"text": "   ", "start": 0.0, "duration": 1.0}]}
    r = client.post("/ingest", json=body)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "no_captions"


def test_oversized_transcript_is_rejected(client):
    body = {"video_id": VID, "transcript": [{"text": "x", "start": 0.0, "duration": 1.0}] * 20001}
    assert client.post("/ingest", json=body).status_code == 422


def test_oversized_snippet_text_is_rejected(client):
    body = {"video_id": VID, "transcript": [{"text": "x" * 501, "start": 0.0, "duration": 1.0}]}
    assert client.post("/ingest", json=body).status_code == 422


def test_second_call_is_cached_even_with_a_transcript_sent_again(client):
    body = {"video_id": VID, "transcript": [{"text": "hi", "start": 0.0, "duration": 1.0}]}
    first = client.post("/ingest", json=body).json()
    second = client.post("/ingest", json=body).json()
    assert first["cached"] is False and second["cached"] is True
    assert client.fetches["fetch"] == 0

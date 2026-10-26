"""Steps 23-24: follow-up questions, validation, hardening."""
import itertools

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel

from app import ingestion, main, rag

VID = "dQw4w9WgXcQ"


@pytest.fixture()
def client(env):
    return TestClient(main.app)


# ---------- Step 23: follow-ups ----------
def test_short_followup_is_searched_with_previous_question():
    history = [{"role": "user", "content": "What is gradient descent?"}, {"role": "assistant", "content": "..."}]
    q = rag.search_query("explain that more", history)
    assert "gradient descent" in q and q.endswith("explain that more")


def test_long_question_or_no_history_is_unchanged():
    history = [{"role": "user", "content": "earlier"}]
    long_q = "please explain in detail how the optimizer updates the weights at every step"
    assert rag.search_query(long_q, history) == long_q
    assert rag.search_query("why?", []) == "why?"


def test_history_is_trimmed_and_typed():
    history = [{"role": "user" if i % 2 == 0 else "assistant", "content": "x" * 2000} for i in range(10)]
    msgs = rag.history_messages(history)
    assert len(msgs) == rag.HISTORY_MESSAGES
    assert all(len(m.content) == rag.HISTORY_CHARS for m in msgs)
    assert {type(m).__name__ for m in msgs} == {"HumanMessage", "AIMessage"}


def test_ask_accepts_history_and_streams(client):
    body = {
        "video_id": VID,
        "question": "and why?",
        "chat_history": [{"role": "user", "content": "What is it?"}, {"role": "assistant", "content": "A thing."}],
    }
    r = client.post("/ask", json=body)
    assert r.status_code == 200 and "event: final" in r.text


def test_bad_history_role_is_rejected(client):
    body = {"video_id": VID, "question": "hi", "chat_history": [{"role": "system", "content": "x"}]}
    assert client.post("/ask", json=body).status_code == 422


def test_too_much_history_is_rejected(client):
    msg = {"role": "user", "content": "x"}
    body = {"video_id": VID, "question": "hi", "chat_history": [msg] * 21}
    assert client.post("/ask", json=body).status_code == 422


# ---------- Step 24: hardening ----------
def test_video_too_long_is_422(client, monkeypatch):
    long_transcript = [{"text": "a b", "start": 0.0, "duration": 4.0}, {"text": "c d", "start": 5 * 3600.0, "duration": 4.0}]
    monkeypatch.setattr(ingestion, "fetch_transcript", lambda v: long_transcript)
    r = client.post("/ingest", json={"video_id": VID})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "video_too_long"


def test_embedding_failure_is_503_and_not_cached(client, monkeypatch):
    def boom(*a, **k):
        raise ConnectionError("refused")

    monkeypatch.setattr(ingestion, "store_documents", boom)
    r = client.post("/ingest", json={"video_id": VID})
    assert r.status_code == 503 and r.json()["detail"]["code"] == "embedding_failed"
    from app import cache

    assert cache.get_video(VID) is None  # a failed ingest must not be marked done


def test_llm_failure_event_is_friendly_and_has_no_internals(client, monkeypatch):
    def fake_stream(*a, **k):
        raise ConnectionError("HTTPConnectionPool(host='127.0.0.1', port=11434): refused")
        yield  # pragma: no cover

    monkeypatch.setattr(main, "stream_answer", fake_stream)
    r = client.post("/ask", json={"video_id": VID, "question": "hi"})
    assert "event: error" in r.text
    assert "Ollama" in r.text and "HTTPConnectionPool" not in r.text


def test_no_cors_by_default(client):
    r = client.get("/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in r.headers


def test_language_fallback_used_when_preferred_missing(monkeypatch):
    from youtube_transcript_api import NoTranscriptFound

    class Snip:
        def __init__(self, t, s):
            self.text, self.start, self.duration = t, s, 2.0

    class T:
        def __init__(self, generated, label):
            self.is_generated, self.label = generated, label

        def fetch(self):
            return [Snip(self.label, 0.0)]

    class FakeApi:
        def fetch(self, vid, languages):
            raise NoTranscriptFound(vid, languages, None)

        def list(self, vid):
            return [T(True, "auto"), T(False, "manual")]

    monkeypatch.setattr(ingestion, "YouTubeTranscriptApi", FakeApi)
    out = ingestion.fetch_transcript(VID)
    assert out[0]["text"] == "manual"  # hand-made captions win over auto-generated


def test_section_length_adapts():
    assert rag.section_seconds(600) == 300
    assert rag.section_seconds(3600) == 300
    assert rag.section_seconds(4 * 3600) == 900

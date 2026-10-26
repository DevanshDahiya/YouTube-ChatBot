"""Shared test setup: everything runs offline with fake transcript, fake embeddings and a fake LLM."""
import itertools

import pytest
from langchain_core.embeddings import DeterministicFakeEmbedding
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel

from app import cache, ingestion, main, rag

FAKE_TRANSCRIPT = [{"text": f"word{i} about topic", "start": i * 4.0, "duration": 4.0} for i in range(200)]


@pytest.fixture()
def env(monkeypatch, tmp_path):
    """Fresh temp database and vector store for every test. Returns a dict that counts YouTube fetches."""
    monkeypatch.setattr(ingestion, "CHROMA_DIR", str(tmp_path / "chroma"))
    monkeypatch.setattr(cache, "CACHE_DB", str(tmp_path / "cache.sqlite"))

    calls = {"fetch": 0}

    def fake_fetch(video_id):
        calls["fetch"] += 1
        return FAKE_TRANSCRIPT

    monkeypatch.setattr(ingestion, "fetch_transcript", fake_fetch)
    monkeypatch.setattr(main, "embeddings", lambda: DeterministicFakeEmbedding(size=16))
    monkeypatch.setattr(
        rag, "get_llm", lambda *a, **k: GenericFakeChatModel(messages=itertools.cycle(["- idea one\n- idea two"]))
    )
    return calls

import importlib

import pytest


def load(monkeypatch, **env):
    for k in ("LLM_PROVIDER", "GOOGLE_API_KEY", "DATA_DIR"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    from app import providers

    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)
    return importlib.reload(providers)


def test_default_is_ollama(monkeypatch):
    p = load(monkeypatch)
    assert p.PROVIDER == "ollama"
    assert type(p.get_chat_model(100)).__name__ == "ChatOllama"
    assert type(p.get_embedding_model()).__name__ == "OllamaEmbeddings"


def test_gemini_needs_a_key(monkeypatch):
    p = load(monkeypatch, LLM_PROVIDER="gemini")
    with pytest.raises(RuntimeError, match="GOOGLE_API_KEY"):
        p.get_chat_model()


def test_gemini_with_key(monkeypatch):
    p = load(monkeypatch, LLM_PROVIDER="gemini", GOOGLE_API_KEY="fake")
    assert type(p.get_chat_model(200)).__name__ == "ChatGoogleGenerativeAI"
    assert type(p.get_embedding_model()).__name__ == "GoogleGenerativeAIEmbeddings"


def test_unknown_provider(monkeypatch):
    p = load(monkeypatch, LLM_PROVIDER="nope")
    with pytest.raises(RuntimeError, match="Unknown"):
        p.get_chat_model()


def test_data_dir_is_configurable(monkeypatch):
    p = load(monkeypatch, DATA_DIR="/x/data")
    assert p.DATA_DIR == "/x/data"
    load(monkeypatch)  # restore defaults for later tests

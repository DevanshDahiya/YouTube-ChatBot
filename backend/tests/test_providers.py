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
    assert type(p.get_chat_model(200)).__name__ == "RunnableWithFallbacks"  # main model + fallback
    assert type(p.get_embedding_model()).__name__ == "GoogleGenerativeAIEmbeddings"


def test_unknown_provider(monkeypatch):
    p = load(monkeypatch, LLM_PROVIDER="nope")
    with pytest.raises(RuntimeError, match="Unknown"):
        p.get_chat_model()


def test_data_dir_is_configurable(monkeypatch):
    p = load(monkeypatch, DATA_DIR="/x/data")
    assert p.DATA_DIR == "/x/data"
    load(monkeypatch)  # restore defaults for later tests


def test_text_of_handles_string_and_block_lists():
    from app.rag import text_of

    assert text_of("plain") == "plain"
    blocks = [{"type": "thinking", "thinking": "hmm"}, {"type": "text", "text": "- a\n- b"}, "!"]
    assert text_of(blocks) == "- a\n- b!"
    assert text_of(None) == ""


def test_summary_works_when_model_returns_block_lists(monkeypatch, env):
    import itertools

    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import AIMessage

    from app import ingestion, main, rag

    ingestion.ingest("dQw4w9WgXcQ", main.embeddings())
    reply = AIMessage(content=[{"type": "text", "text": "- idea one\n- idea two"}])
    llm = GenericFakeChatModel(messages=itertools.cycle([reply]))
    out = rag.build_summary("dQw4w9WgXcQ", llm=llm)
    assert out["key_points"] and out["overview"]


def test_gemini_without_fallback(monkeypatch):
    monkeypatch.setenv("GEMINI_FALLBACK_MODEL", "")
    p = load(monkeypatch, LLM_PROVIDER="gemini", GOOGLE_API_KEY="fake")
    assert type(p.get_chat_model()).__name__ == "ChatGoogleGenerativeAI"


def test_fallback_runs_when_main_model_fails():
    import itertools

    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.prompts import ChatPromptTemplate

    class Broken(GenericFakeChatModel):
        def _generate(self, *a, **k):
            raise TimeoutError("504 DEADLINE_EXCEEDED")

        def _stream(self, *a, **k):
            raise TimeoutError("504 DEADLINE_EXCEEDED")

    good = GenericFakeChatModel(messages=itertools.cycle(["from fallback"]))
    bad = Broken(messages=iter([]))
    chain = ChatPromptTemplate.from_template("{x}") | bad.with_fallbacks([good])
    assert chain.invoke({"x": "hi"}).content == "from fallback"
    assert "".join(c.content for c in chain.stream({"x": "hi"})) != ""

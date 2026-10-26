import os

from dotenv import load_dotenv

load_dotenv()  # reads backend/.env if it exists; real environment variables win over it

PROVIDER = os.getenv("LLM_PROVIDER", "ollama").lower()
DATA_DIR = os.getenv("DATA_DIR", "./data")

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
OLLAMA_CHAT_MODEL = "qwen2.5:3b"
OLLAMA_EMBED_MODEL = "mxbai-embed-large"

GEMINI_CHAT_MODEL = os.getenv("GEMINI_CHAT_MODEL", "gemini-2.5-flash")
GEMINI_EMBED_MODEL = os.getenv("GEMINI_EMBED_MODEL", "models/gemini-embedding-001")

NUM_CTX = 4096  # Ollama only: its default window can silently cut the prompt


def _gemini_key() -> str:
    key = os.getenv("GOOGLE_API_KEY")
    if not key:
        raise RuntimeError("GOOGLE_API_KEY is not set. Put it in backend/.env (see .env.example).")
    return key


def get_chat_model(max_tokens: int | None = None):
    """temperature=0: we want the answer that follows the excerpts, not a creative one."""
    if PROVIDER == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(
            model=GEMINI_CHAT_MODEL,
            google_api_key=_gemini_key(),
            temperature=0,
            max_output_tokens=max_tokens,
            max_retries=3,  # the free tier rate-limits (HTTP 429): wait and retry instead of failing
            timeout=120,
        )
    if PROVIDER == "ollama":
        from langchain_ollama import ChatOllama

        return ChatOllama(
            model=OLLAMA_CHAT_MODEL,
            base_url=OLLAMA_URL,
            temperature=0,
            num_ctx=NUM_CTX,
            num_predict=max_tokens,  # caps output length (stops a looping model)
            client_kwargs={"timeout": 180},
        )
    raise RuntimeError(f"Unknown LLM_PROVIDER {PROVIDER!r}. Use 'ollama' or 'gemini'.")


def get_embedding_model():
    if PROVIDER == "gemini":
        from langchain_google_genai import GoogleGenerativeAIEmbeddings

        return GoogleGenerativeAIEmbeddings(model=GEMINI_EMBED_MODEL, google_api_key=_gemini_key())
    if PROVIDER == "ollama":
        from langchain_ollama import OllamaEmbeddings

        return OllamaEmbeddings(model=OLLAMA_EMBED_MODEL, base_url=OLLAMA_URL)
    raise RuntimeError(f"Unknown LLM_PROVIDER {PROVIDER!r}. Use 'ollama' or 'gemini'.")
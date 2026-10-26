"""Retrieve relevant chunks, build the grounded prompt, stream the answer, verify citations, build the summary."""
import logging
import re
import sys
import threading
import time

from langchain_core.documents import Document
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_ollama import ChatOllama

from . import cache
from .ingestion import OLLAMA_URL, chunk_transcript, fmt_ts, get_embeddings, get_vectorstore

TOP_K = 5  # how many chunks to retrieve per question
LLM_MODEL = "qwen2.5:3b"  # `ollama pull qwen2.5:3b`
NUM_CTX = 4096            # context window; Ollama's default can be too small and silently cuts the prompt

SYSTEM_PROMPT = """You answer questions about ONE YouTube video using ONLY the transcript excerpts below.
Each excerpt starts with its timestamp in square brackets.

Rules:
- Use only the excerpts. If the answer is not in them, say (in the language of the question) that you couldn't find it in the video.
- Answer in the SAME LANGUAGE as the question. Do not copy the excerpts word for word; explain in your own words.
- End EVERY sentence with the timestamp of the excerpt it came from, copied exactly from that excerpt's label.
- Only cite timestamps that appear in the excerpts. Never invent one.
- Earlier chat messages are context only (for words like "it" or "that"). Facts and citations must come from the excerpts below.
- Keep the answer to 2 to 4 sentences.

Example of the style (about a different video): The baker says the dough must rest overnight [3:05]. She then shapes it into small rolls [4:40].

Excerpts:
{context}"""

REMINDER = (
    "(Answer using only the excerpts, in the language of the question, "
    "and end every sentence with a timestamp copied from an excerpt label.)"
)

SECTION_PROMPT = ChatPromptTemplate.from_template(
    "Below is one part of a video transcript.\n"
    "Write 1 or 2 short bullet points (max 20 words each) about its main ideas. Use only this text.\n"
    "Output only the bullets, each starting with '- '.\n\n{text}"
)
OVERVIEW_PROMPT = ChatPromptTemplate.from_template(
    "Below are notes from a video, in order. Write a 3 to 4 sentence overview of what the video is about. "
    "Use only the notes. Output only the overview.\n\n{notes}"
)
MIN_SECTION_SECONDS = 300  # summarize in sections of at least 5 minutes
MAX_SECTION_SECONDS = 900  # ...and at most 15 (long videos get longer sections so they need fewer LLM calls)
TARGET_SECTIONS = 12
MAX_SECTION_WORDS = 1500  # a section is cut to this many words so it always fits the model's context
HISTORY_MESSAGES = 6  # how many earlier chat messages the model sees
HISTORY_CHARS = 600  # each is cut to this length
SHORT_QUESTION_WORDS = 8  # questions this short are searched together with the previous question
_summary_locks: dict[str, threading.Lock] = {}  # one lock per video

ANSWER_PROMPT = ChatPromptTemplate.from_messages(
    [
        ("system", SYSTEM_PROMPT),
        MessagesPlaceholder("history"),
        ("human", "{question}\n\n" + REMINDER),
    ]
)

logger = logging.getLogger(__name__)


def history_messages(chat_history: list[dict] | None) -> list:
    """Last few chat messages as LangChain messages, each cut short so they never crowd out the excerpts."""
    out = []
    for m in (chat_history or [])[-HISTORY_MESSAGES:]:
        text = (m.get("content") or "")[:HISTORY_CHARS]
        if not text.strip():
            continue
        out.append(HumanMessage(text) if m.get("role") == "user" else AIMessage(text))
    return out


def search_query(question: str, chat_history: list[dict] | None) -> str:
    """"Explain that more" matches nothing on its own, so a short follow-up is searched together with the last user question."""
    if chat_history and len(question.split()) <= SHORT_QUESTION_WORDS:
        previous = [m["content"] for m in chat_history if m.get("role") == "user" and m.get("content")]
        if previous:
            return f"{previous[-1][:300]} {question}"
    return question


def retrieve(video_id: str, question: str, embeddings, k: int = TOP_K) -> list[Document]:
    """Find the k chunks closest in meaning to the question, then put them in video order."""
    store = get_vectorstore(video_id, embeddings)
    docs = store.similarity_search(question, k=k)
    return sorted(docs, key=lambda d: d.metadata["start"])


def format_context(docs: list[Document]) -> str:
    """Put the timestamp in front of each chunk's text, exactly as the model should cite it."""
    return "\n\n".join(f"[{d.metadata['ts']}] {d.page_content}" for d in docs)


_CITE_RE = re.compile(r"\[(\d{1,2}):(\d{2})(?::(\d{2}))?\]")  # [M:SS] or [H:MM:SS]


def _to_seconds(m: re.Match) -> int:
    a, b, c = m.group(1), m.group(2), m.group(3)
    return int(a) * 3600 + int(b) * 60 + int(c) if c else int(a) * 60 + int(b)


def verify_citations(text: str, docs: list[Document]) -> dict:
    """Keep only citations that point inside a retrieved chunk; snap them to that chunk's start.

    - cited time inside a retrieved chunk -> rewritten to the chunk's start (a real, seekable moment)
    - cited time in NO retrieved chunk    -> removed from the text and reported in `invalid`
    """
    valid: set[int] = set()
    invalid: list[str] = []

    def fix(m: re.Match) -> str:
        t = _to_seconds(m)
        containing = [d.metadata["start"] for d in docs if d.metadata["start"] <= t <= d.metadata["end"]]
        if not containing:
            invalid.append(m.group(0))
            return ""
        start = max(containing)  # in an overlap, prefer the chunk that started latest
        valid.add(start)
        return f"[{fmt_ts(start)}]"

    cleaned = _CITE_RE.sub(fix, text)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned).replace(" .", ".").replace(" ,", ",").strip()
    return {"text": cleaned, "citations": sorted(valid), "invalid": invalid}


def get_llm(max_tokens: int | None = None) -> ChatOllama:
    # temperature=0: we want the answer that follows the excerpts, not a creative one.
    # num_predict caps the output length (stops a looping model); the timeout stops a hung call.
    return ChatOllama(
        model=LLM_MODEL,
        base_url=OLLAMA_URL,
        temperature=0,
        num_ctx=NUM_CTX,
        num_predict=max_tokens,
        client_kwargs={"timeout": 180},
    )


def answer(video_id: str, question: str, embeddings, llm=None, chat_history=None) -> dict:
    """Retrieve -> prompt -> LLM. Returns the answer text and the chunks it was based on."""
    docs = retrieve(video_id, search_query(question, chat_history), embeddings)
    chain = ANSWER_PROMPT | (llm or get_llm())
    reply = chain.invoke(
        {"context": format_context(docs), "question": question, "history": history_messages(chat_history)}
    )
    checked = verify_citations(reply.content, docs)
    return {
        "answer": checked["text"],
        "citations": checked["citations"],  # seconds the player can seek to
        "invalid_citations": checked["invalid"],  # timestamps the model made up (removed from the answer)
        "sources": docs,
    }


def stream_answer(video_id: str, question: str, embeddings, llm=None, chat_history=None):
    """Same as answer(), but yields events as they happen:

    {"type": "sources", "sources": [...]}   once, before any text
    {"type": "token",   "text": "..."}      many times, raw model text
    {"type": "final",   "answer", "citations", "invalid_citations"}   once, citations verified
    """
    docs = retrieve(video_id, search_query(question, chat_history), embeddings)
    yield {
        "type": "sources",
        "sources": [
            {"start": d.metadata["start"], "ts": d.metadata["ts"], "snippet": d.page_content[:160]} for d in docs
        ],
    }

    chain = ANSWER_PROMPT | (llm or get_llm())
    parts: list[str] = []
    inputs = {"context": format_context(docs), "question": question, "history": history_messages(chat_history)}
    for chunk in chain.stream(inputs):
        if chunk.content:
            parts.append(chunk.content)
            yield {"type": "token", "text": chunk.content}

    checked = verify_citations("".join(parts), docs)
    yield {
        "type": "final",
        "answer": checked["text"],
        "citations": checked["citations"],
        "invalid_citations": checked["invalid"],
    }


def _bullets(text: str) -> list[str]:
    """Pull '- bullet' / '* bullet' / '1. bullet' lines out of a model reply."""
    out = []
    for line in text.splitlines():
        line = re.sub(r"^\s*(?:[-*\u2022]|\d+[.)])\s*", "", line).strip()
        if line:
            out.append(line)
    return out


def section_seconds(duration_s: float) -> int:
    """About TARGET_SECTIONS sections for any video, but never shorter than 5 or longer than 15 minutes."""
    return int(min(MAX_SECTION_SECONDS, max(MIN_SECTION_SECONDS, duration_s / TARGET_SECTIONS)))


def build_summary(video_id: str, llm=None, on_progress=None) -> dict:
    """Map-reduce summary. Returns {"overview": str, "key_points": [{"start","ts","text"}]}.

    Timestamps on key points come from the code (each section's real start), not from the model.
    """
    # Only one summary per video at a time; a second request waits and then reads the cache.
    with _summary_locks.setdefault(video_id, threading.Lock()):
        cached = cache.get_summary(video_id)
        if cached:
            return cached
        video = cache.get_video(video_id)
        if not video:
            raise LookupError("Video not ingested yet. Run ingestion first.")

        llm = llm or get_llm(max_tokens=200)  # bullets and a short overview never need more
        sections = chunk_transcript(video["transcript"], window=section_seconds(video["duration_s"]), overlap=0)

        key_points = []
        for i, sec in enumerate(sections, 1):
            if on_progress:
                on_progress(i, len(sections))
            text = " ".join(sec["text"].split()[:MAX_SECTION_WORDS])
            reply = (SECTION_PROMPT | llm).invoke({"text": text})
            for b in _bullets(reply.content)[:2]:
                key_points.append({"start": int(sec["start"]), "ts": fmt_ts(sec["start"]), "text": b})

        notes = "\n".join(f"- {k['text']}" for k in key_points)
        overview = (OVERVIEW_PROMPT | llm).invoke({"notes": notes}).content.strip()

        summary = {"overview": overview, "key_points": key_points}
        cache.save_summary(video_id, summary)
        return summary


if __name__ == "__main__":
    # Try it:  python -m app.rag VIDEO_ID "your question"   (run `python -m app.ingestion VIDEO_ID` first)
    video_id, question = sys.argv[1], sys.argv[2]

    if question == "--summary":  # python -m app.rag VIDEO_ID --summary
        t0 = time.perf_counter()
        result = build_summary(video_id, on_progress=lambda i, n: print(f"summarizing section {i}/{n}...", flush=True))
        print(f"\nOverview:\n{result['overview']}\n\nKey points:")
        for k in result["key_points"]:
            print(f"  [{k['ts']}] {k['text']}")
        print(f"\ndone in {time.perf_counter() - t0:.1f}s (run again: instant, it is cached)")
        sys.exit(0)

    t0 = time.perf_counter()
    first_token = None

    print(f"\nQ: {question}\n")
    print("A: ", end="", flush=True)
    for event in stream_answer(video_id, question, get_embeddings()):
        if event["type"] == "token":
            if first_token is None:
                first_token = time.perf_counter() - t0
            print(event["text"], end="", flush=True)  # appears word by word
        elif event["type"] == "final":
            print("\n\n--- after citation check ---")
            print(event["answer"])
            print("Valid citations (seconds): ", event["citations"])
            print("Removed (not in retrieved chunks): ", event["invalid_citations"])

    total = time.perf_counter() - t0
    print(f"\nfirst token after {first_token:.1f}s, finished after {total:.1f}s")

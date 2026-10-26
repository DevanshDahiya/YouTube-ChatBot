"""Step 1: validate a video ID and fetch its transcript from YouTube."""
import logging
import re
import sys
import threading
import time

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_ollama import OllamaEmbeddings
from youtube_transcript_api import (
    IpBlocked,
    NoTranscriptFound,
    RequestBlocked,
    TranscriptsDisabled,
    VideoUnavailable,
    YouTubeTranscriptApi,
)

from . import cache

logger = logging.getLogger(__name__)

WINDOW_SECONDS = 75  # target chunk length
OVERLAP_SECONDS = 15  # how much consecutive chunks share
EMBED_MODEL = "mxbai-embed-large"  # `ollama pull mxbai-embed-large`
OLLAMA_URL = "http://127.0.0.1:11434"
CHROMA_DIR = "./data/chroma"  # where vectors are saved on disk
LANGUAGES = ["en", "en-US", "en-GB", "hi"]  # preferred caption languages, in priority order
MAX_VIDEO_SECONDS = 4 * 3600  # longer videos are refused: embedding and summarizing them locally takes too long
EMBED_BATCH = 32  # chunks embedded per request, so a long video is not one giant request

_locks: dict[str, threading.Lock] = {}  # one lock per video
_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


class IngestionError(Exception):
    """Carries a stable `code` so the API and extension can show a friendly message."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def validate_video_id(video_id: str) -> str:
    if not _VIDEO_ID_RE.match(video_id or ""):
        raise IngestionError("invalid_video_id", "Video ID must be 11 characters (letters, digits, - or _).")
    return video_id


def _fetch_any_language(api: YouTubeTranscriptApi, video_id: str):
    """Fallback when none of LANGUAGES exist: use whatever captions the video has (hand-made ones first)."""
    transcripts = list(api.list(video_id))
    if not transcripts:
        raise IngestionError("no_captions", "This video has no usable captions.")
    transcripts.sort(key=lambda t: getattr(t, "is_generated", True))  # False (hand-made) sorts before True
    return transcripts[0].fetch()


def fetch_transcript(video_id: str) -> list[dict]:
    """Return [{"text", "start", "duration"}, ...] for the video."""
    validate_video_id(video_id)
    api = YouTubeTranscriptApi()
    try:
        try:
            fetched = api.fetch(video_id, languages=LANGUAGES)
        except NoTranscriptFound:
            fetched = _fetch_any_language(api, video_id)
    except (TranscriptsDisabled, NoTranscriptFound):
        raise IngestionError("no_captions", "This video has no usable captions.")
    except VideoUnavailable:
        raise IngestionError("video_unavailable", "This video is unavailable.")
    except (RequestBlocked, IpBlocked):
        raise IngestionError("blocked", "YouTube blocked the request from this network/IP.")

    snippets = [
        {"text": s.text.replace("\n", " ").strip(), "start": float(s.start), "duration": float(s.duration)}
        for s in fetched
        if s.text.strip()
    ]
    if not snippets:
        raise IngestionError("no_captions", "Transcript was empty.")
    return snippets


def fmt_ts(seconds: float) -> str:
    """754 -> '12:34', 3725 -> '1:02:05'."""
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def chunk_transcript(snippets: list[dict], window: int = WINDOW_SECONDS, overlap: int = OVERLAP_SECONDS) -> list[dict]:
    """Group caption pieces into ~`window`-second chunks; neighbours share ~`overlap` seconds.

    Each chunk: {"text", "start", "end", "chunk_index"}.
    `start` is the real start time of the chunk's first caption, so it is safe to cite and seek to.
    """
    chunks: list[dict] = []
    i, n = 0, len(snippets)
    while i < n:
        start = snippets[i]["start"]
        j = i
        while j < n and snippets[j]["start"] < start + window:
            j += 1
        j = max(j, i + 1)  # always take at least one piece
        group = snippets[i:j]
        end = group[-1]["start"] + group[-1]["duration"]
        chunks.append(
            {
                "text": " ".join(g["text"] for g in group),
                "start": start,
                "end": end,
                "chunk_index": len(chunks),
            }
        )
        if j >= n:
            break
        # step back so the next chunk begins ~`overlap` seconds before this one ended
        k = j
        while k - 1 > i and snippets[k - 1]["start"] >= end - overlap:
            k -= 1
        i = k  # k > i, so we always move forward
    return chunks


def chunks_to_documents(video_id: str, chunks: list[dict]) -> list[Document]:
    """Wrap each chunk in a LangChain Document. The timestamp lives in metadata."""
    return [
        Document(
            page_content=c["text"],
            metadata={
                "video_id": video_id,
                "start": int(c["start"]),  # seconds: what the player seeks to
                "end": int(c["end"]),
                "ts": fmt_ts(c["start"]),  # display form, e.g. "12:34"
                "chunk_index": c["chunk_index"],
            },
        )
        for c in chunks
    ]


def get_embeddings() -> OllamaEmbeddings:
    return OllamaEmbeddings(model=EMBED_MODEL, base_url=OLLAMA_URL)


def collection_name(video_id: str) -> str:
    # Chroma names must start AND end with a letter/digit; video IDs may end in "-" or "_".
    return f"yt_{video_id}_c"


def get_vectorstore(video_id: str, embeddings) -> Chroma:
    """One Chroma collection per video, saved under CHROMA_DIR."""
    return Chroma(
        collection_name=collection_name(video_id),
        embedding_function=embeddings,
        persist_directory=CHROMA_DIR,
    )


def store_documents(video_id: str, docs: list[Document], embeddings) -> int:
    """Embed the documents (in batches) and save them. Fixed IDs mean re-running never creates duplicates."""
    store = get_vectorstore(video_id, embeddings)
    for i in range(0, len(docs), EMBED_BATCH):
        batch = docs[i : i + EMBED_BATCH]
        store.add_documents(batch, ids=[f"{video_id}-{d.metadata['chunk_index']}" for d in batch])
    return len(docs)


def ingest(video_id: str, embeddings) -> dict:
    """The whole pipeline, with caching. Safe to call many times for the same video."""
    validate_video_id(video_id)

    # Two requests for the same NEW video must not both embed it: the second waits, then hits the cache.
    with _locks.setdefault(video_id, threading.Lock()):
        cached = cache.get_video(video_id)
        if cached:  # already done: no YouTube request, no embedding
            return {"video_id": video_id, "cached": True, "chunks": cached["chunk_count"]}

        snippets = fetch_transcript(video_id)

        duration = snippets[-1]["start"] + snippets[-1]["duration"]
        if duration > MAX_VIDEO_SECONDS:
            hours = MAX_VIDEO_SECONDS // 3600
            raise IngestionError("video_too_long", f"This video is longer than {hours} hours, which is too long to process.")

        docs = chunks_to_documents(video_id, chunk_transcript(snippets))
        try:
            store_documents(video_id, docs, embeddings)
        except Exception as e:
            logger.exception("Embedding failed for %s", video_id)
            raise IngestionError(
                "embedding_failed",
                f"Couldn't create embeddings. Is Ollama running, and is {EMBED_MODEL} installed?",
            ) from e

        # Written LAST: if anything above failed, the video is not marked done and the next call retries.
        cache.save_video(video_id, snippets, len(docs))
        return {"video_id": video_id, "cached": False, "chunks": len(docs)}


if __name__ == "__main__":
    # Quick manual test:  python -m app.ingestion VIDEO_ID "your question"
    video_id = sys.argv[1]
    emb = get_embeddings()

    t0 = time.perf_counter()
    try:
        result = ingest(video_id, emb)
    except IngestionError as e:
        print(f"[{e.code}] {e.message}")
        sys.exit(1)
    print(f"{result}  took {time.perf_counter() - t0:.2f}s\n")

    query = sys.argv[2] if len(sys.argv) > 2 else "what is this video about?"
    print(f"Search: {query!r}\n")
    for d in get_vectorstore(video_id, emb).similarity_search(query, k=3):
        print(f"[{d.metadata['ts']}] {d.page_content[:150]}...\n")

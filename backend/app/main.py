import json
import logging
import os
from functools import lru_cache
from typing import Literal

import secrets

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .ingestion import IngestionError, get_embeddings, ingest
from .rag import build_summary, stream_answer

logger = logging.getLogger(__name__)



def require_key(x_api_key: str | None = Header(default=None)):
    """If API_KEY is set (deployed server), every request except /health must send the same value in X-API-Key.
    Without it, anyone who finds the URL could spend your Gemini quota. Unset locally, so nothing changes there."""
    expected = os.getenv("API_KEY")
    if expected and not secrets.compare_digest(x_api_key or "", expected):
        raise HTTPException(401, {"code": "unauthorized", "message": "Wrong or missing API key. Check API_KEY in extension/config.js."})


app = FastAPI(title="YouTube Learning Assistant")

# The extension calls us from its background worker, which is not subject to CORS, so by default NO website
# is allowed. To let a web page call the API (for example a demo page), set ALLOWED_ORIGINS=https://a.com,https://b.com
_origins = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "").split(",") if o.strip()]
if _origins:
    app.add_middleware(CORSMiddleware, allow_origins=_origins, allow_methods=["GET", "POST"], allow_headers=["Content-Type"])

# IngestionError.code -> HTTP status
STATUS = {
    "invalid_video_id": 400,
    "video_unavailable": 404,
    "no_captions": 422,
    "video_too_long": 422,
    "blocked": 503,
    "embedding_failed": 503,
}


@lru_cache
def embeddings():
    return get_embeddings()  # created once, reused by every request


class IngestRequest(BaseModel):
    video_id: str


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=4000)


class AskRequest(BaseModel):
    video_id: str
    question: str = Field(min_length=1, max_length=2000)
    chat_history: list[ChatMessage] = Field(default_factory=list, max_length=20)


def http_error(e: IngestionError) -> HTTPException:
    return HTTPException(STATUS.get(e.code, 500), {"code": e.code, "message": e.message})


def friendly_error(e: Exception) -> str:
    """A message safe to show to the user. The real exception goes to the server log, not to the browser."""
    logger.exception("Request failed")
    text = f"{type(e).__name__} {e}".lower()
    if any(w in text for w in ("503", "504", "deadline", "overloaded", "unavailable")):
        return "The model service is busy or slow right now. Please try again in a moment."
    if any(w in text for w in ("connect", "refused", "timeout", "timed out")):
        return "Can't reach the language model. If you use Ollama, is it running?"
    if any(w in text for w in ("api key", "api_key", "permission", "unauthenticated", "401", "403")):
        return "The model service rejected the API key. Check GOOGLE_API_KEY."
    if any(w in text for w in ("429", "quota", "rate limit", "resource exhausted")):
        return "The model service is busy (rate limit). Wait a minute and try again."
    if "not_found" in text or "not found" in text or "no longer available" in text:
        return "The language model name isn't available. Update GEMINI_CHAT_MODEL."
    return "Something went wrong while answering. Please try again."


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/ingest", dependencies=[Depends(require_key)])
def ingest_route(req: IngestRequest):
    # A plain `def` route: FastAPI runs it in a worker thread, so slow embedding doesn't block the server.
    try:
        return ingest(req.video_id, embeddings())
    except IngestionError as e:
        raise http_error(e)


def sse(event: dict) -> str:
    """One Server-Sent Event: 'event: <type>' + 'data: <json>' + a blank line."""
    kind = event["type"]
    payload = {k: v for k, v in event.items() if k != "type"}
    return f"event: {kind}\ndata: {json.dumps(payload)}\n\n"


def event_stream(video_id: str, question: str, history: list[dict]):
    try:
        for event in stream_answer(video_id, question, embeddings(), chat_history=history):
            yield sse(event)
    except Exception as e:  # once streaming has started we cannot change the HTTP status, so send an event
        yield sse({"type": "error", "message": friendly_error(e)})


@app.post("/ask", dependencies=[Depends(require_key)])
def ask_route(req: AskRequest):
    # Make sure the video is ready first (instant if cached). Errors here are normal HTTP errors.
    try:
        ingest(req.video_id, embeddings())
    except IngestionError as e:
        raise http_error(e)

    history = [m.model_dump() for m in req.chat_history]
    return StreamingResponse(
        event_stream(req.video_id, req.question, history),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/summary/{video_id}", dependencies=[Depends(require_key)])
def summary_route(video_id: str):
    # The first call for a video can take a while (one model call per section); later calls are cached.
    try:
        ingest(video_id, embeddings())
        return {"video_id": video_id, **build_summary(video_id)}
    except IngestionError as e:
        raise http_error(e)
    except LookupError as e:
        raise HTTPException(404, {"code": "not_ingested", "message": str(e)})
    except Exception as e:
        raise HTTPException(502, {"code": "summary_failed", "message": friendly_error(e)})

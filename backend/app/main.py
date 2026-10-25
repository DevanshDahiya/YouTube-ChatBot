import json
from functools import lru_cache
from fastapi import FastAPI , HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel , Field

from .ingestion import IngestionError , get_embeddings , ingest
from .rag import stream_answer


app = FastAPI(title="YouTube-ChatBot")

# dev only -> allowe any website 
app.add_middleware(CORSMiddleware , allow_origins=["*"] , allow_methods=["*"] , allow_headers=["*"])

# ingestion error code -> http status
STATUS = {
    "invalid_video_id" : 400,
    "video_unavailable" : 404,
    "no_caption" : 422 ,
    "blocked" :503
}

@lru_cache
def embeddings():
    return get_embeddings()


class IngestionRequest(BaseModel):
    video_id : str

class AskRequest(BaseModel):
    video_id : str 
    question : str = Field(min_length=1 , max_Length=2000)
    chat_history: list[dict] = []  # used to handle the follow up question



@app.get("/health")
def health() :
    return {"ok" : True}

@app.post("/ingest")
def ingest_route(req: IngestionRequest):
    # A plain `def` route: FastAPI runs it in a worker thread, so slow embedding doesn't block the server.
    try:
        return ingest(req.video_id , embeddings())
    except IngestionError as e :
        raise HTTPException(STATUS.get(e.code , 500) , {"code" : e.code , "message":e.message})


def sse(event:dict) -> str :
    #One server-side Event: 'event: <type>' + 'data: <json>' + a blank line.
    kind = event["type"]
    payload = {k : v for k , v in event.items() if k != "type"}

    return f"event: {kind}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

# This is used to get the stream from the rag
def event_stream(video_id :str , question :str):
    try:
        for event in stream_answer(video_id , question ,embeddings()):
            yield sse(event)
    except Exception as e :
        yield sse({
            "type" : "error" , 
            "message" : f"{type(e).__name__}:{e}"
        })


@app.post("/ask")
def ask_route(req : AskRequest):
    try :
        ingest(req.video_id,embeddings())

    except IngestionError as e :
        raise HTTPException(STATUS.get(e.code, 500) , {"code":e.code , "message":e.message})


    return StreamingResponse(
        event_stream(req.video_id , req.question),
        media_type="text/event-stream",
        headers = {
            "Cache-Control" : "no-cache", #tells caches not to serve a cached response.
            "X-Accel-Buffering" :"no"
        },
    )
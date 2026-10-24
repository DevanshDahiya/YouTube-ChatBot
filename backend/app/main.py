from functools import lru_cache
from fastapi import FastAPI , HTTPException
from fastapi.middleware.cors import CORSMiddleware

from pydantic import BaseModel

from .ingestion import IngestionError , get_embeddings , ingest

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
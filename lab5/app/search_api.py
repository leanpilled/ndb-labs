"""Text -> image search API.

Takes a free-text query, embeds it with the CLIP text encoder (clip_encoder.py)
and searches the Qdrant 'flickr30k' collection (populated by
scripts/ingest_qdrant.py) for the nearest image embeddings.

Run:
    uvicorn app.search_api:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel
from qdrant_client import QdrantClient

from app.clip_encoder import ClipTextEncoder

COLLECTION = "flickr30k"
QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")
IMAGES_DIR = Path(os.environ.get("IMAGES_DIR", Path(__file__).resolve().parent.parent / "data" / "images"))

app = FastAPI(title="Flickr30k Text-to-Image Search")
Instrumentator().instrument(app).expose(app)

_encoder: ClipTextEncoder | None = None
_client: QdrantClient | None = None


@app.on_event("startup")
def startup() -> None:
    global _encoder, _client
    _encoder = ClipTextEncoder()
    _client = QdrantClient(url=QDRANT_URL)


class SearchResult(BaseModel):
    id: int
    score: float
    image_id: str
    filename: str
    caption: str
    image_url: str


class SearchResponse(BaseModel):
    query: str
    took_ms: float
    results: list[SearchResult]


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/search", response_model=SearchResponse)
def search(q: str = Query(..., min_length=1), k: int = Query(10, ge=1, le=100)) -> SearchResponse:
    assert _encoder is not None and _client is not None
    start = time.perf_counter()
    vector = _encoder.encode_one(q)
    hits = _client.query_points(collection_name=COLLECTION, query=vector, limit=k).points
    took_ms = (time.perf_counter() - start) * 1000

    results = [
        SearchResult(
            id=hit.id,
            score=hit.score,
            image_id=hit.payload.get("image_id", ""),
            filename=hit.payload.get("filename", ""),
            caption=hit.payload.get("caption", ""),
            image_url=f"/image/{hit.id}",
        )
        for hit in hits
    ]
    return SearchResponse(query=q, took_ms=took_ms, results=results)


@app.get("/image/{point_id}")
def get_image(point_id: int):
    path = IMAGES_DIR / f"{point_id}.jpg"
    if not path.exists():
        raise HTTPException(status_code=404, detail="image not found")
    return FileResponse(path, media_type="image/jpeg")

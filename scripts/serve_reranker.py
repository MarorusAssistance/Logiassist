from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field


class RerankDocument(BaseModel):
    id: int
    text: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class RerankRequest(BaseModel):
    query: str
    documents: list[RerankDocument]
    top_n: int = 5


class RerankResult(BaseModel):
    id: int
    score: float


class RerankResponse(BaseModel):
    model: str
    results: list[RerankResult]


MODEL_NAME = os.getenv("RERANKER_MODEL", "BAAI/bge-reranker-v2-m3")


@lru_cache(maxsize=1)
def _load_model():
    try:
        from sentence_transformers import CrossEncoder
    except ImportError as exc:
        raise RuntimeError(
            "sentence-transformers no esta instalado. Ejecuta: "
            "python -m uv run --extra reranker python scripts/serve_reranker.py"
        ) from exc
    return CrossEncoder(MODEL_NAME)


app = FastAPI(title="Internal Assistant Local Reranker")


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "model": MODEL_NAME}


@app.post("/rerank", response_model=RerankResponse)
def rerank(request: RerankRequest) -> RerankResponse:
    if not request.query.strip():
        raise HTTPException(status_code=400, detail="query is required")
    if not request.documents:
        return RerankResponse(model=MODEL_NAME, results=[])

    model = _load_model()
    pairs = [(request.query, document.text) for document in request.documents]
    raw_scores = model.predict(pairs)
    scored = [
        RerankResult(id=document.id, score=float(score))
        for document, score in zip(request.documents, raw_scores, strict=True)
    ]
    scored.sort(key=lambda item: item.score, reverse=True)
    return RerankResponse(model=MODEL_NAME, results=scored[: max(1, int(request.top_n))])


if __name__ == "__main__":
    host = os.getenv("RERANKER_HOST", "127.0.0.1")
    port = int(os.getenv("RERANKER_PORT", "8091"))
    uvicorn.run(app, host=host, port=port)

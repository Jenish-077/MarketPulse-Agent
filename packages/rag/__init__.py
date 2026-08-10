"""Company-context RAG via pgvector embeddings."""
from __future__ import annotations

import logging
from typing import Any

import numpy as np
from sqlalchemy.orm import Session

from packages.db.models import CompanyContext, EMBED_DIM
from packages.settings import get_settings

logger = logging.getLogger(__name__)


def _hash_embed(text: str, dim: int = EMBED_DIM) -> list[float]:
    """Deterministic fallback embedding when no API key (demo/offline)."""
    rng = np.random.default_rng(abs(hash(text)) % (2**32))
    vec = rng.standard_normal(dim).astype(np.float64)
    vec = vec / (np.linalg.norm(vec) + 1e-9)
    return vec.tolist()


def embed_texts(texts: list[str]) -> list[list[float]]:
    settings = get_settings()
    # DeepSeek (and similar) have no embeddings endpoint — use local vectors
    use_local = (
        not settings.llm_enabled
        or "local" in (settings.openai_embedding_model or "").lower()
        or "deepseek" in (settings.openai_base_url or "").lower()
    )
    if use_local:
        return [_hash_embed(t) for t in texts]
    try:
        from openai import OpenAI

        client = OpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url)
        resp = client.embeddings.create(model=settings.openai_embedding_model, input=texts)
        return [d.embedding for d in resp.data]
    except Exception as e:
        logger.warning("Embedding API failed, using hash fallback: %s", e)
        return [_hash_embed(t) for t in texts]


def upsert_context(db: Session, title: str, content: str, kind: str = "product_summary") -> CompanyContext:
    emb = embed_texts([f"{title}\n{content}"])[0]
    row = CompanyContext(title=title, content=content, kind=kind, embedding=emb)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def retrieve_context(db: Session, query: str, k: int = 4) -> list[dict[str, Any]]:
    rows = db.query(CompanyContext).all()
    if not rows:
        return []
    q = np.array(embed_texts([query])[0], dtype=np.float64)
    scored: list[tuple[float, CompanyContext]] = []
    for row in rows:
        if row.embedding is None:
            continue
        v = np.array(list(row.embedding), dtype=np.float64)
        sim = float(np.dot(q, v) / ((np.linalg.norm(q) * np.linalg.norm(v)) + 1e-9))
        scored.append((sim, row))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [
        {"id": str(r.id), "title": r.title, "content": r.content, "kind": r.kind, "score": round(s, 4)}
        for s, r in scored[:k]
    ]


def relevance_scores(db: Session, event_text: str, importance_keywords: list[str] | None = None) -> tuple[float, float, list[str]]:
    """Return (feature_overlap-ish, icp_relevance, snippets used)."""
    ctx = retrieve_context(db, event_text, k=3)
    snippets = [c["content"][:300] for c in ctx]
    if not ctx:
        # keyword fallback
        kw = importance_keywords or []
        hits = sum(1 for k in kw if k.lower() in event_text.lower())
        return (min(1.0, hits * 0.3), 0.0, [])

    avg = sum(c["score"] for c in ctx) / max(len(ctx), 1)
    # Map cosine-ish similarity into 0..1 contribution
    feature = min(1.0, max(0.0, avg))
    icp = min(1.0, max(0.0, avg * 0.9))
    return feature, icp, snippets

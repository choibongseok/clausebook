"""서비스·평가가 함께 쓰는 조립 코드: 문서 읽기 → 청크 → 임베딩 → 저장 → 검색기."""
from __future__ import annotations

from .chunking import STRATEGIES, make_chunker
from .config import DATA_DIR, LLM_TIMEOUT_S, LLM_URL, MODEL_BACKEND
from .corpus import load_corpus
from .llm import make_client
from .pipeline import Pipeline, PipelineConfig
from .retrieval import MemDense, PgDense, Retriever


def build_chunks():
    docs = load_corpus(DATA_DIR)
    clauses = {c.id: c for d in docs for c in d.clauses}
    chunks = {s: [c for d in docs for c in make_chunker(s).chunk(d)] for s in STRATEGIES}
    return docs, clauses, chunks


def ingest(conn, embedder):
    """모든 전략의 청크를 임베딩해 pgvector 에 넣는다."""
    from . import store

    docs, clauses, chunks = build_chunks()
    store.init_schema(conn)
    store.clear_chunks(conn)
    for s, cs in chunks.items():
        vecs = embedder.embed_passages([c.text for c in cs])
        store.put_chunks(conn, cs, vecs)
    return docs, clauses, chunks


def pg_retriever(conn, embedder):
    from . import store

    _, clauses, _ = build_chunks()
    by = {s: store.load_chunks(conn, s) for s in STRATEGIES}
    return Retriever(by, PgDense(conn), embedder), clauses


def mem_retriever(embedder):
    import numpy as np

    _, clauses, chunks = build_chunks()
    vecs = {s: embedder.embed_passages([c.text for c in cs]) for s, cs in chunks.items()}
    return Retriever(chunks, MemDense(chunks, vecs), embedder), clauses


def make_pipeline(retriever, clauses, backend: str | None = None, cfg: PipelineConfig | None = None, log=None) -> Pipeline:
    return Pipeline(retriever, make_client(backend or MODEL_BACKEND, LLM_URL, LLM_TIMEOUT_S), clauses, cfg, log)

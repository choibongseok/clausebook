"""PostgreSQL + pgvector 저장소. 청크, 대화 상태, 요청 기록을 담는다."""
from __future__ import annotations

import json
from datetime import date
from typing import Iterable

import numpy as np
import psycopg
from pgvector.psycopg import register_vector

from .chunking import Chunk
from .config import DATABASE_URL, EMBED_DIM

SCHEMA = f"""
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS chunks (
  id text PRIMARY KEY, strategy text NOT NULL, doc_id text NOT NULL, product_id text NOT NULL, doc_type text NOT NULL,
  title text NOT NULL, text text NOT NULL, display text NOT NULL, clause_ids text[] NOT NULL,
  eff_from date, eff_to date, suspicious boolean NOT NULL DEFAULT false, embedding vector({EMBED_DIM}) NOT NULL
);
CREATE INDEX IF NOT EXISTS chunks_strategy ON chunks (strategy);
CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw ON chunks USING hnsw (embedding vector_cosine_ops);
CREATE TABLE IF NOT EXISTS sessions (id text PRIMARY KEY, state jsonb NOT NULL, updated_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS request_log (
  trace_id text PRIMARY KEY, session_id text, intent text, status text, total_ms double precision, steps jsonb NOT NULL,
  prompt_version text, model text, created_at timestamptz NOT NULL DEFAULT now()
);
"""


def connect(url: str | None = None) -> psycopg.Connection:
    conn = psycopg.connect(url or DATABASE_URL, autocommit=True)
    conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    register_vector(conn)
    return conn


def init_schema(conn: psycopg.Connection) -> None:
    conn.execute(SCHEMA)


def put_chunks(conn: psycopg.Connection, chunks: Iterable[Chunk], vectors: np.ndarray) -> None:
    rows = list(chunks)
    with conn.cursor() as cur:
        for c, v in zip(rows, vectors):
            cur.execute(
                "INSERT INTO chunks VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO UPDATE SET text=EXCLUDED.text, display=EXCLUDED.display, embedding=EXCLUDED.embedding",
                (c.id, c.strategy, c.doc_id, c.product_id, c.doc_type, c.title, c.text, c.display, c.clause_ids, c.eff_from, c.eff_to, c.suspicious, v),
            )


def clear_chunks(conn: psycopg.Connection) -> None:
    conn.execute("TRUNCATE chunks")


def load_chunks(conn: psycopg.Connection, strategy: str) -> list[Chunk]:
    cur = conn.execute(
        "SELECT id,strategy,doc_id,product_id,doc_type,title,text,display,clause_ids,eff_from,eff_to,suspicious FROM chunks WHERE strategy=%s ORDER BY id",
        (strategy,),
    )
    return [Chunk(*r) for r in cur.fetchall()]


def dense_search(conn: psycopg.Connection, strategy: str, qvec: np.ndarray, k: int, products: list[str] | None, as_of: date | None, doc_types: list[str] | None = None) -> list[tuple[str, float]]:
    where, params = ["strategy = %s"], [strategy]
    if products:
        where.append("product_id = ANY(%s)")
        params.append(products)
    if as_of:
        where.append("(eff_from IS NULL OR eff_from <= %s) AND (eff_to IS NULL OR eff_to >= %s)")
        params += [as_of, as_of]
    if doc_types:
        where.append("doc_type = ANY(%s)")
        params.append(doc_types)
    sql = f"SELECT id, 1 - (embedding <=> %s) FROM chunks WHERE {' AND '.join(where)} ORDER BY embedding <=> %s LIMIT %s"
    cur = conn.execute(sql, [qvec, *params, qvec, k])
    return [(r[0], float(r[1])) for r in cur.fetchall()]


def get_session(conn: psycopg.Connection, sid: str) -> dict | None:
    r = conn.execute("SELECT state FROM sessions WHERE id=%s", (sid,)).fetchone()
    return r[0] if r else None


def save_session(conn: psycopg.Connection, sid: str, state: dict) -> None:
    conn.execute(
        "INSERT INTO sessions (id,state) VALUES (%s,%s) ON CONFLICT (id) DO UPDATE SET state=EXCLUDED.state, updated_at=now()",
        (sid, json.dumps(state, ensure_ascii=False)),
    )


def log_request(conn: psycopg.Connection, rec: dict) -> None:
    conn.execute(
        "INSERT INTO request_log (trace_id,session_id,intent,status,total_ms,steps,prompt_version,model) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
        (rec["trace_id"], rec.get("session_id"), rec.get("intent"), rec.get("status"), rec.get("total_ms"), json.dumps(rec.get("steps", []), ensure_ascii=False), rec.get("prompt_version"), rec.get("model")),
    )

"""FastAPI 서비스. 대화(스트리밍 포함), 근거 보기, 평가 실행, 상태 확인."""
from __future__ import annotations

import json
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict
from queue import Queue
from threading import Thread

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import runtime, store
from .config import EMBED_MODEL_NAME, MODEL_BACKEND, PROMPT_VERSION
from .embed import E5Onnx
from .pipeline import PipelineConfig, new_state

S: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    conn = store.connect()
    store.init_schema(conn)
    emb = E5Onnx()
    if conn.execute("SELECT count(*) FROM chunks").fetchone()[0] == 0:
        runtime.ingest(conn, emb)
    retriever, clauses = runtime.pg_retriever(conn, emb)
    S.update(conn=conn, emb=emb, retriever=retriever, clauses=clauses,
             pipeline=runtime.make_pipeline(retriever, clauses, log=lambda r: store.log_request(conn, r)))
    yield
    conn.close()


app = FastAPI(title="Clausebook", version="0.1.0", lifespan=lifespan)


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    session_id: str | None = None
    stream: bool = False


def _run(msg: ChatIn, on_step=None):
    sid = msg.session_id or uuid.uuid4().hex[:12]
    st = store.get_session(S["conn"], sid) or new_state()
    res = S["pipeline"].ask(msg.message, st, sid, on_step)
    store.save_session(S["conn"], sid, res.state)
    d = res.to_dict()
    d["session_id"] = sid
    return d


@app.get("/healthz")
def healthz():
    return {"status": "ok", "model": MODEL_BACKEND, "embedder": EMBED_MODEL_NAME, "prompt_version": PROMPT_VERSION}


@app.post("/v1/chat")
def chat(msg: ChatIn):
    if not msg.stream:
        return _run(msg)
    q: Queue = Queue()

    def work():
        try:
            d = _run(msg, lambda s: q.put(("step", asdict(s))))
            q.put(("answer", d))
        except Exception as e:  # 스트림은 오류 이벤트로 끝낸다
            q.put(("error", {"message": str(e)}))
        q.put(None)

    Thread(target=work, daemon=True).start()

    def gen():
        while (item := q.get()) is not None:
            yield f"event: {item[0]}\ndata: {json.dumps(item[1], ensure_ascii=False)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/v1/sources/{clause_id:path}")
def source(clause_id: str):
    c = S["clauses"].get(clause_id)
    if not c:
        raise HTTPException(404, "없는 조항")
    return {"id": c.id, "heading": c.heading, "doc_title": c.doc_title, "text": c.body, "effective_from": str(c.eff_from or ""), "effective_to": str(c.eff_to or "")}


class EvalIn(BaseModel):
    kind: str = "retrieval"
    strategy: str = "clause_ctx"
    method: str = "hybrid_rrf"
    rerank: str = "rule"


@app.post("/v1/eval/run")
def eval_run(body: EvalIn):
    from .evaluation.run import run_retrieval

    t0 = time.perf_counter()
    out = run_retrieval(S["retriever"], body.strategy, body.method, body.rerank)
    out["elapsed_ms"] = round((time.perf_counter() - t0) * 1000)
    return out


@app.get("/v1/sessions/{sid}")
def session(sid: str):
    st = store.get_session(S["conn"], sid)
    if st is None:
        raise HTTPException(404, "없는 대화")
    return st

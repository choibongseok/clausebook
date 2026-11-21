import os

import pytest

from clausebook import runtime
from clausebook.embed import E5Onnx, HashEmbedder


@pytest.fixture(scope="session")
def mem():
    """모델 없이 도는 검색기(글자 2-gram 해시 임베딩 + 메모리 밀집 검색)와 조항 사전."""
    retriever, clauses = runtime.mem_retriever(HashEmbedder())
    return retriever, clauses


@pytest.fixture(scope="session")
def pg():
    """실제 PostgreSQL + pgvector 와 e5 임베딩. 첫 시험이 문서를 적재한다."""
    from clausebook import store

    conn = store.connect()
    emb = E5Onnx()
    runtime.ingest(conn, emb)
    retriever, clauses = runtime.pg_retriever(conn, emb)
    yield retriever, clauses, conn
    conn.close()


@pytest.fixture()
def pipe_pg(pg):
    retriever, clauses, _ = pg

    def make(backend="mock:extractive", cfg=None):
        return runtime.make_pipeline(retriever, clauses, backend, cfg)

    return make


API_URL = os.environ.get("API_URL", "http://127.0.0.1:58780")

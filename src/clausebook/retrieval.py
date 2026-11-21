"""검색: 밀집(pgvector) · 키워드(BM25) · 하이브리드 · 재정렬 · 메타데이터 필터.

점수 결합 방식
  rrf       순위 기반(Reciprocal Rank Fusion, k=60). 두 점수의 단위가 달라도 된다.
  weighted  각 목록 안에서 최소-최대 정규화한 점수를 0.5:0.5 로 더한다.
재정렬(rule)  학습 없는 규칙. 상위 후보에 대해 질문 핵심어가 조항 제목과 본문에 들어 있는 비율(IDF 가중)과
             숫자·단위 일치를 더해 앞 단계 점수와 섞는다. 교차 인코더는 쓰지 않았다.
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Protocol

import numpy as np

from .chunking import Chunk
from .korean import content_terms, tokenize

METHODS = ("dense", "bm25", "hybrid_rrf", "hybrid_weighted")
RERANKERS = ("none", "rule")


class BM25:
    """okapi BM25(k1=1.5, b=0.75). 토큰은 korean.tokenize(어간 + 글자 2-gram)."""

    def __init__(self, docs: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.tf = [Counter(d) for d in docs]
        self.len = [len(d) for d in docs]
        self.avg = (sum(self.len) / len(docs)) if docs else 0.0
        df: Counter = Counter()
        for c in self.tf:
            df.update(c.keys())
        n = len(docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def scores(self, query: list[str]) -> list[float]:
        out = [0.0] * len(self.tf)
        for t in set(query):
            idf = self.idf.get(t)
            if idf is None:
                continue
            for i, tf in enumerate(self.tf):
                f = tf.get(t)
                if f:
                    out[i] += idf * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * self.len[i] / (self.avg or 1)))
        return out


@dataclass
class Filters:
    products: list[str] | None = None  # 이 상품과 공통(ALL)만
    as_of: date | None = None  # 계약일. 이 날짜에 유효한 조항 버전만
    doc_types: list[str] | None = None

    def key(self) -> tuple:
        return (tuple(self.products or ()), self.as_of, tuple(self.doc_types or ()))


@dataclass
class Hit:
    chunk: Chunk
    score: float
    parts: dict[str, float] = field(default_factory=dict)


class DenseBackend(Protocol):
    def search(self, strategy: str, qvec: np.ndarray, k: int, f: Filters) -> list[tuple[str, float]]: ...


class MemDense:
    """메모리 위 밀집 검색(단위 테스트·모델 없는 실행)."""

    def __init__(self, chunks_by_strategy: dict[str, list[Chunk]], vectors: dict[str, np.ndarray]):
        self.chunks, self.vectors = chunks_by_strategy, vectors

    def search(self, strategy, qvec, k, f):
        chunks, vecs = self.chunks[strategy], self.vectors[strategy]
        sims = vecs @ qvec
        order = np.argsort(-sims)
        out = []
        for i in order:
            if _allowed(chunks[i], f):
                out.append((chunks[i].id, float(sims[i])))
                if len(out) >= k:
                    break
        return out


class PgDense:
    def __init__(self, conn):
        self.conn = conn

    def search(self, strategy, qvec, k, f):
        from . import store

        return store.dense_search(self.conn, strategy, qvec, k, _prod_list(f), f.as_of, f.doc_types)


def _prod_list(f: Filters) -> list[str] | None:
    return None if not f.products else [*f.products, "ALL"]


def _allowed(c: Chunk, f: Filters) -> bool:
    if f.products and c.product_id not in (*f.products, "ALL"):
        return False
    if f.doc_types and c.doc_type not in f.doc_types:
        return False
    return c.valid_on(f.as_of)


def _minmax(xs: list[float]) -> list[float]:
    if not xs:
        return []
    lo, hi = min(xs), max(xs)
    return [0.5] * len(xs) if hi - lo < 1e-12 else [(x - lo) / (hi - lo) for x in xs]


class Retriever:
    def __init__(self, chunks_by_strategy: dict[str, list[Chunk]], dense: DenseBackend, embedder, pool: int = 30):
        self.chunks = chunks_by_strategy
        self.by_id = {s: {c.id: c for c in cs} for s, cs in chunks_by_strategy.items()}
        self.bm25 = {s: BM25([tokenize(c.text) for c in cs]) for s, cs in chunks_by_strategy.items()}
        self.dense, self.embedder, self.pool = dense, embedder, pool
        from .cache import LRU

        self.cache = LRU(1024)

    # --- 단일 방식 ---
    def _bm25(self, strategy: str, query: str, f: Filters, k: int) -> list[tuple[str, float]]:
        cs = self.chunks[strategy]
        sc = self.bm25[strategy].scores(tokenize(query))
        pairs = [(cs[i].id, s) for i, s in enumerate(sc) if s > 0 and _allowed(cs[i], f)]
        return sorted(pairs, key=lambda x: -x[1])[:k]

    def _dense(self, strategy: str, query: str, f: Filters, k: int) -> list[tuple[str, float]]:
        q = self.embedder.embed_queries([query])[0]
        return self.dense.search(strategy, q, k, f)

    # --- 공개 ---
    def search(self, query: str, strategy: str = "clause_ctx", method: str = "hybrid_rrf", rerank: str = "none", k: int = 5, f: Filters | None = None) -> list[Hit]:
        f = f or Filters()
        key = (query, strategy, method, rerank, k, f.key())
        return self.cache.get_or_set(key, lambda: self._search(query, strategy, method, rerank, k, f))

    def _search(self, query, strategy, method, rerank, k, f) -> list[Hit]:
        byid = self.by_id[strategy]
        pool = max(self.pool, k)
        if method == "dense":
            ranked = [Hit(byid[i], s, {"dense": s}) for i, s in self._dense(strategy, query, f, pool)]
        elif method == "bm25":
            ranked = [Hit(byid[i], s, {"bm25": s}) for i, s in self._bm25(strategy, query, f, pool)]
        elif method in ("hybrid_rrf", "hybrid_weighted"):
            d = self._dense(strategy, query, f, pool)
            b = self._bm25(strategy, query, f, pool)
            ranked = self._fuse(method, d, b, byid)
        else:
            raise ValueError(method)
        if rerank == "rule":
            ranked = self._rule_rerank(query, strategy, ranked[: max(pool, 20)])
        return ranked[:k]

    @staticmethod
    def _fuse(method, d, b, byid) -> list[Hit]:
        parts: dict[str, dict[str, float]] = defaultdict(dict)
        score: dict[str, float] = defaultdict(float)
        if method == "hybrid_rrf":
            for name, lst in (("dense", d), ("bm25", b)):
                for r, (i, s) in enumerate(lst, 1):
                    parts[i][name] = s
                    parts[i][name + "_rank"] = r
                    score[i] += 1.0 / (60 + r)
        else:
            for name, lst in (("dense", d), ("bm25", b)):
                for (i, s), n in zip(lst, _minmax([s for _, s in lst])):
                    parts[i][name] = s
                    score[i] += 0.5 * n
        return [Hit(byid[i], s, dict(parts[i])) for i, s in sorted(score.items(), key=lambda x: -x[1])]

    def _rule_rerank(self, query: str, strategy: str, hits: list[Hit]) -> list[Hit]:
        if not hits:
            return hits
        idf = self.bm25[strategy].idf
        q_terms = content_terms(query)
        q_nums = {t for t in tokenize(query, bigrams=False) if t[0].isdigit()}
        wsum = sum(idf.get(t, 1.0) for t in q_terms) or 1.0
        base = _minmax([h.score for h in hits])
        out = []
        for h, b in zip(hits, base):
            body_terms = content_terms(h.chunk.text)
            title_terms = content_terms(h.chunk.title)
            cov = sum(idf.get(t, 1.0) for t in q_terms if t in body_terms) / wsum
            tcov = sum(idf.get(t, 1.0) for t in q_terms if t in title_terms) / wsum
            nums = 1.0 if q_nums and all(any(n in h.chunk.text for n in [x]) for x in q_nums) else 0.0
            s = 0.5 * b + 0.3 * cov + 0.15 * tcov + 0.05 * nums
            out.append(Hit(h.chunk, s, {**h.parts, "coverage": cov, "title_coverage": tcov, "prev": h.score}))
        return sorted(out, key=lambda h: -h.score)

    def coverage(self, query: str, hit: Hit, strategy: str) -> float:
        """질문 핵심어 중 조항 본문에 있는 비율(IDF 가중). 답이 문서에 있는지 가늠할 때 쓴다."""
        idf = self.bm25[strategy].idf
        q_terms = content_terms(query)
        wsum = sum(idf.get(t, 1.0) for t in q_terms) or 1.0
        body = content_terms(hit.chunk.text)
        return sum(idf.get(t, 1.0) for t in q_terms if t in body) / wsum

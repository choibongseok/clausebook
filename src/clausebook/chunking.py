"""조항 분할 전략 3가지. 모두 같은 인터페이스(`Chunker.chunk(doc)`)를 쓴다.

fixed       문서 전체를 이어 붙여 고정 길이(문자)로 자른다. 조항 경계를 모른다.
clause      조항(또는 FAQ 한 문항, 소제목 한 절) 하나가 청크 하나다. 긴 조항은 항 단위로 나눈다.
clause_ctx  clause 와 같되 앞에 '상품명 | 문서 | 조항 제목 | 시행 기간'을 붙여 검색과 임베딩에 쓴다.
각 청크는 자신이 덮는 조항 id(clause_ids)를 기억해서, 어떤 전략이든 같은 정답 조항으로 채점한다.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date
from typing import Protocol

from .corpus import Clause, Doc

STRATEGIES = ("fixed", "clause", "clause_ctx")


@dataclass
class Chunk:
    id: str
    strategy: str
    doc_id: str
    product_id: str
    doc_type: str
    title: str
    text: str  # 검색·임베딩에 쓰는 글(표는 행 문장으로 풀림)
    display: str  # 사람에게 보여 줄 글(표 원문 유지)
    clause_ids: list[str]
    eff_from: date | None = None
    eff_to: date | None = None
    suspicious: bool = False

    def valid_on(self, d: date | None) -> bool:
        if d is None:
            return True
        return (self.eff_from is None or self.eff_from <= d) and (self.eff_to is None or d <= self.eff_to)


class Chunker(Protocol):
    name: str

    def chunk(self, doc: Doc) -> list[Chunk]: ...


def _cid(strategy: str, doc_id: str, key: str) -> str:
    return f"{strategy}:{doc_id}:{key}"


def _split_paragraphs(text: str, limit: int) -> list[str]:
    parts: list[str] = []
    cur = ""
    for line in text.splitlines():
        if cur and len(cur) + len(line) + 1 > limit:
            parts.append(cur.strip())
            cur = ""
        cur += line + "\n"
    if cur.strip():
        parts.append(cur.strip())
    return parts


@dataclass
class ClauseChunker:
    name: str = "clause"
    max_chars: int = 900
    context: bool = False

    def _head(self, c: Clause) -> str:
        if not self.context:
            return c.heading
        span = ""
        if c.eff_from or c.eff_to:
            span = f" | 시행 {c.eff_from or ''}~{c.eff_to or ''}"
        return f"{c.product_name} | {c.doc_title} | {c.heading}{span}"

    def chunk(self, doc: Doc) -> list[Chunk]:
        out: list[Chunk] = []
        for c in doc.clauses:
            body = c.search_text()
            pieces = _split_paragraphs(body, self.max_chars) if len(body) > self.max_chars else [body]
            raw = _split_paragraphs(c.body, self.max_chars) if len(c.body) > self.max_chars else [c.body]
            for i, p in enumerate(pieces):
                key = c.id.split("#", 1)[1] + (f"/{i + 1}" if len(pieces) > 1 else "")
                shown = raw[i] if i < len(raw) else p
                out.append(Chunk(_cid(self.name, doc.doc_id, key), self.name, doc.doc_id, doc.product_id, doc.doc_type, c.heading,
                                 f"{self._head(c)}\n{p}", f"{c.heading}\n{shown}", [c.id], c.eff_from, c.eff_to, c.suspicious))
        return out


@dataclass
class FixedChunker:
    name: str = "fixed"
    size: int = 320
    overlap: int = 60

    def chunk(self, doc: Doc) -> list[Chunk]:
        text = ""
        spans: list[tuple[int, int, Clause]] = []
        for c in doc.clauses:
            piece = f"{c.heading}\n{c.search_text()}\n\n"
            spans.append((len(text), len(text) + len(piece), c))
            text += piece
        out: list[Chunk] = []
        step = self.size - self.overlap
        for n, start in enumerate(range(0, max(len(text), 1), step)):
            end = min(start + self.size, len(text))
            piece = text[start:end].strip()
            if not piece:
                continue
            hit = [c for (a, b, c) in spans if a < end and b > start]
            windows = {(c.eff_from, c.eff_to) for c in hit}
            ef, et = next(iter(windows)) if len(windows) == 1 else (None, None)
            out.append(Chunk(_cid(self.name, doc.doc_id, str(n + 1)), self.name, doc.doc_id, doc.product_id, doc.doc_type,
                             hit[0].heading if hit else doc.title, piece, piece, [c.id for c in hit], ef, et, any(c.suspicious for c in hit)))
            if end >= len(text):
                break
        return out


def make_chunker(name: str) -> Chunker:
    if name == "fixed":
        return FixedChunker()
    if name == "clause":
        return ClauseChunker(name="clause", context=False)
    if name == "clause_ctx":
        return ClauseChunker(name="clause_ctx", context=True)
    raise ValueError(f"알 수 없는 분할 전략: {name}")


def corpus_fingerprint(docs: list[Doc]) -> str:
    h = hashlib.sha256()
    for d in docs:
        for c in d.clauses:
            h.update(c.id.encode() + c.body.encode())
    return h.hexdigest()[:12]

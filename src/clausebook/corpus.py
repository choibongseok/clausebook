"""마크다운 문서를 조항 단위로 읽는다.

문서 머리말(doc_id, product_id, doc_type …) 뒤에 `## 제N조(제목)` 또는 `## 소제목`이 조항 경계다.
개정된 조항은 제목 끝의 `[시행 ~2025-06-30]`, `[시행 2025-07-01~]` 표시로 유효 기간을 읽는다.
표는 행마다 '열이름 값' 문장으로 풀어 검색에 걸리게 하고, 원문 표는 보여 줄 때 쓴다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .security import looks_like_injection

_HEAD = re.compile(r"^## (.+?)\s*$", re.M)
_VER = re.compile(r"\s*\[시행\s*(?:(\d{4}-\d{2}-\d{2})\s*)?~\s*(\d{4}-\d{2}-\d{2})?\]\s*$")
_ART = re.compile(r"^(제\d+조)\(([^)]*)\)$")
_Q = re.compile(r"^(Q\d+)\.\s*(.*)$")


@dataclass
class Clause:
    id: str
    doc_id: str
    product_id: str
    product_name: str
    doc_type: str
    doc_title: str
    label: str  # 제9조, Q3, 소제목
    title: str  # 제목만(제9조의 '비급여 자기부담 비율')
    body: str  # 원문(표 포함)
    order: int
    eff_from: date | None = None
    eff_to: date | None = None
    suspicious: bool = False

    @property
    def heading(self) -> str:
        if self.label.startswith("제"):
            return f"{self.label}({self.title})"
        return self.title

    def valid_on(self, d: date | None) -> bool:
        if d is None:
            return True
        return (self.eff_from is None or self.eff_from <= d) and (self.eff_to is None or d <= self.eff_to)

    def search_text(self) -> str:
        """표를 행 문장으로 풀어 쓴 검색용 본문."""
        return render_tables(self.body)


@dataclass
class Doc:
    doc_id: str
    product_id: str
    product_name: str
    doc_type: str
    title: str
    revised: str
    clauses: list[Clause] = field(default_factory=list)
    path: str = ""


def _front(text: str) -> tuple[dict, str]:
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    if not m:
        raise ValueError("머리말이 없는 문서")
    meta = {}
    for line in m.group(1).splitlines():
        k, _, v = line.partition(":")
        meta[k.strip()] = v.strip()
    return meta, text[m.end():]


def render_tables(body: str) -> str:
    """마크다운 표를 '열이름 값, 열이름 값' 한 줄로 바꾼다. 표가 아닌 줄은 그대로 둔다."""
    out: list[str] = []
    header: list[str] | None = None
    for line in body.splitlines():
        s = line.strip()
        if s.startswith("|") and s.endswith("|"):
            cells = [c.strip() for c in s.strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", c) for c in cells):
                continue
            if header is None:
                header = cells
                continue
            out.append("표 행: " + ", ".join(f"{h} {c}" for h, c in zip(header, cells)))
        else:
            header = None
            out.append(line)
    return "\n".join(out)


def parse_doc(path: Path) -> Doc:
    meta, rest = _front(path.read_text(encoding="utf-8"))
    doc = Doc(meta["doc_id"], meta["product_id"], meta["product_name"], meta["doc_type"], meta["title"], meta.get("revised", ""), path=str(path))
    heads = list(_HEAD.finditer(rest))
    seen_labels: dict[str, int] = {}
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(rest)
        raw_head = m.group(1)
        body = rest[m.end():end].strip()
        eff_from = eff_to = None
        vm = _VER.search(raw_head)
        if vm:
            eff_from = date.fromisoformat(vm.group(1)) if vm.group(1) else None
            eff_to = date.fromisoformat(vm.group(2)) if vm.group(2) else None
            raw_head = raw_head[: vm.start()]
        am, qm = _ART.match(raw_head), _Q.match(raw_head)
        if am:
            label, title = am.group(1), am.group(2)
        elif qm:
            label, title = qm.group(1), qm.group(2)
        else:
            label, title = raw_head, raw_head
        n = seen_labels.get(label, 0) + 1
        seen_labels[label] = n
        cid = f"{doc.doc_id}#{label}"
        if vm:
            cid += f"@v{n}"
        doc.clauses.append(Clause(cid, doc.doc_id, doc.product_id, doc.product_name, doc.doc_type, doc.title, label, title, body, i, eff_from, eff_to, looks_like_injection(body)[0]))
    return doc


def load_corpus(data_dir: Path) -> list[Doc]:
    return [parse_doc(p) for p in sorted(data_dir.rglob("*.md"))]

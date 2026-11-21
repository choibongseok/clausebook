"""답변 검수. 인용이 실제 검색 결과에 있는지, 문장마다 근거가 있는지 확인하고 근거 없는 문장은 뺀다."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .korean import content_terms
from .llm import split_sentences

NOT_FOUND = "약관에서 찾지 못했습니다."
_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?\s*(?:억|만\s*원|천\s*원|원|%|일|개월|년|세|시간|영업일|회|종)?")


def numbers_in(text: str) -> set[str]:
    out = set()
    for m in _NUM.finditer(text):
        s = re.sub(r"[\s,]", "", m.group(0))
        if s:
            out.add(s)
    return out


def _numbers_supported(sentence: str, source: str) -> bool:
    src = numbers_in(source)
    src_bare = {re.sub(r"[^\d.]", "", x) for x in src}
    for n in numbers_in(sentence):
        bare = re.sub(r"[^\d.]", "", n)
        if n not in src and bare not in src_bare:
            return False
    return True


@dataclass
class Verdict:
    kept: list[str]
    dropped: list[dict] = field(default_factory=list)
    citations: list[str] = field(default_factory=list)  # 태그(C1…)
    invalid_citations: list[str] = field(default_factory=list)
    repaired: int = 0

    @property
    def text(self) -> str:
        return " ".join(self.kept) if self.kept else NOT_FOUND


def verify(answer: str, cited_tags: list[str], context: dict[str, str], extra_sources: list[str] | None = None, min_support: float = 0.5) -> Verdict:
    """context: 태그 → 근거 본문. extra_sources: 계산 도구 출력 같은 추가 근거."""
    valid = [t for t in dict.fromkeys(cited_tags) if t in context]
    invalid = [t for t in dict.fromkeys(cited_tags) if t not in context]
    v = Verdict([], invalid_citations=invalid)
    used: list[str] = []
    for s in split_sentences(answer) or ([answer] if answer.strip() else []):
        if NOT_FOUND.rstrip(".") in s:
            v.kept.append(s)
            continue
        pool = {t: context[t] for t in valid}
        src_text = " ".join([*pool.values(), *(extra_sources or [])])
        terms = content_terms(s)
        def support(txt: str) -> float:
            return len(terms & content_terms(txt)) / (len(terms) or 1)
        ok = bool(pool or extra_sources) and support(src_text) >= min_support and _numbers_supported(s, src_text)
        tag = None
        if ok and pool:
            tag = max(pool, key=lambda t: support(pool[t]))
        if not ok:  # 인용 보정: 인용이 없거나 틀렸어도 검색 결과 중 근거가 되는 조항이 있으면 그 조항으로 잇는다
            best = max(context, key=lambda t: support(context[t]), default=None)
            if best and support(context[best]) >= 0.6 and _numbers_supported(s, context[best]):
                ok, tag = True, best
                v.repaired += 1
        if ok:
            v.kept.append(s)
            if tag:
                used.append(tag)
        else:
            v.dropped.append({"sentence": s, "reason": "근거 조항에서 확인되지 않음"})
    v.citations = list(dict.fromkeys(used or (valid if v.kept else [])))
    return v

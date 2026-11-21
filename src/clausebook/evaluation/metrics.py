"""평가 지표. 모두 규칙 기반이며 모델을 심판으로 쓰지 않는다."""
from __future__ import annotations

import math
import re

from ..verify import numbers_in


def norm(s: str) -> str:
    return re.sub(r"[\s,]", "", s).lower()


def covers(chunk_clause_ids: list[str], group: list[str]) -> bool:
    return any(c in group for c in chunk_clause_ids)


def recall_at_k(ranked: list[list[str]], gold: list[list[str]], k: int) -> float:
    """정답 조항 묶음(묶음 안은 어느 것이든 인정) 중 상위 k 청크가 덮은 비율."""
    if not gold:
        return 0.0
    top = ranked[:k]
    return sum(any(covers(c, g) for c in top) for g in gold) / len(gold)


def mrr(ranked: list[list[str]], gold: list[list[str]]) -> float:
    allg = [x for g in gold for x in g]
    for i, c in enumerate(ranked, 1):
        if covers(c, allg):
            return 1.0 / i
    return 0.0


def ndcg_at_k(ranked: list[list[str]], gold: list[list[str]], k: int) -> float:
    """이진 관련도. 정답 묶음마다 처음 덮은 청크만 1점(같은 조항을 덮는 청크가 여럿이어도 중복 점수 없음).
    이상적인 순서는 정답 묶음 수만큼 맨 앞에 오는 것."""
    left = [set(g) for g in gold]
    dcg = 0.0
    for i, c in enumerate(ranked[:k], 1):
        hit = next((g for g in left if any(x in g for x in c)), None)
        if hit is not None:
            left.remove(hit)
            dcg += 1.0 / math.log2(i + 1)
    ideal = sum(1.0 / math.log2(i + 1) for i in range(1, min(len(gold), k) + 1))
    return dcg / ideal if ideal else 0.0


def element_recall(answer: str, elements: list[str]) -> float:
    if not elements:
        return 1.0
    a = norm(answer)
    return sum(any(norm(alt) in a for alt in e.split("|")) for e in elements) / len(elements)


def forbidden_hit(answer: str, forbid: list[str]) -> bool:
    a = norm(answer)
    return any(norm(f) in a for f in forbid)


def citation_stats(cited_ids: list[str], gold: list[list[str]]) -> tuple[bool, float]:
    """(정답 조항을 하나라도 인용했는가, 인용한 조항 중 정답 조항의 비율)."""
    allg = {x for g in gold for x in g}
    if not cited_ids:
        return False, 0.0
    hit = [c for c in cited_ids if c in allg]
    return bool(hit), len(hit) / len(cited_ids)


def unsupported_numbers(answer: str, cited_texts: list[str], question: str) -> bool:
    """답에 든 숫자가 인용한 조항 원문(과 질문, 계산 과정)에 하나도 없는 것이 있으면 True."""
    src = numbers_in(" ".join(cited_texts) + " " + question)
    bare = {re.sub(r"[^\d.]", "", s) for s in src}
    for n in numbers_in(answer):
        if n not in src and re.sub(r"[^\d.]", "", n) not in bare:
            return True
    return False


def percentile(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    i = min(len(s) - 1, max(0, math.ceil(p / 100 * len(s)) - 1))
    return s[i]

"""한국어 토큰화. 형태소 분석기 없이 두 가지를 섞는다.

1. 어절에서 흔한 조사·어미를 떼어 낸 어간(예: '비급여를' → '비급여')
2. 두 글자 이상 한글 어간의 글자 2-gram (띄어쓰기·합성어 차이를 흡수)
숫자와 영문은 그대로 둔다. 질문에만 나오는 어미 표현은 불용어로 뺀다.
"""
from __future__ import annotations

import re

_WORD = re.compile(r"\d+(?:[.,]\d+)*(?:%|[가-힣]{1,3})?|[가-힣]+|[A-Za-z]+")
_PARTICLES = sorted(
    "에서는 에서도 으로는 으로도 이라고 입니다 합니다 됩니다 있습니다 없습니다 하는 하면 하고 하여 한다 했다 에게 에서 으로 까지 부터 보다 처럼 이나 이며 이고 인가요 나요 어요 해요 되나요 있나요 할까요 은 는 이 가 을 를 의 에 도 로 와 과 만 며 고".split(),
    key=len,
    reverse=True,
)
STOPWORDS = frozenset(
    "무엇 뭐 어떻게 얼마 알려 주세요 알고 싶어요 궁금해요 경우 때 것 수 있나요 되나요 인가요 어떤 어디 언제 왜 그럼 그러면".split()
)


def _strip(word: str) -> str:
    for p in _PARTICLES:
        if word.endswith(p) and len(word) - len(p) >= 2:
            return word[: -len(p)]
    return word


def _strip_unit(w: str) -> str:
    """'5년이' → '5년', '3종을' → '3종'. 숫자 뒤 한글 꼬리에서 조사를 뗀다."""
    m = re.match(r"(\d+(?:[.,]\d+)*)([가-힣]{1,3})$", w)
    if not m:
        return w
    num, tail = m.groups()
    for p in _PARTICLES:
        if tail.endswith(p) and len(tail) > len(p):
            return num + tail[: -len(p)]
    return w


def words(text: str) -> list[str]:
    out = []
    for w in _WORD.findall(text):
        if "가" <= w[0] <= "힣":
            out.append(_strip(w))
        elif w[0].isdigit():
            out.append(_strip_unit(w))
        else:
            out.append(w.lower())
    return out


def tokenize(text: str, bigrams: bool = True) -> list[str]:
    out: list[str] = []
    for w in words(text):
        if w in STOPWORDS:
            continue
        out.append(w)
        if bigrams and "가" <= w[0] <= "힣" and len(w) >= 2:
            out.extend(w[i : i + 2] for i in range(len(w) - 1))
    return out


def content_terms(text: str) -> set[str]:
    """어간 단위 핵심어 집합(2-gram 제외). 근거 확인과 재정렬에 쓴다."""
    return {w for w in words(text) if w not in STOPWORDS and (len(w) >= 2 or w.isdigit())}

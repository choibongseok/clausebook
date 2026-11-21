"""입력·출력 개인정보 가림, 프롬프트 주입과 역할 탈취 탐지."""
from __future__ import annotations

import re
from dataclasses import dataclass

# 순서가 중요하다: 길고 구체적인 형식을 먼저 가린다.
_PII = [
    ("주민등록번호", re.compile(r"(?<!\d)\d{6}[-\s]?[1-4]\d{6}(?!\d)")),
    ("카드번호", re.compile(r"(?<!\d)\d{4}[-\s]\d{4}[-\s]\d{4}[-\s]\d{4}(?!\d)")),
    ("전화번호", re.compile(r"(?<!\d)(?:01[016789]|0\d{1,2})[-\s]?\d{3,4}[-\s]?\d{4}(?!\d)")),
    ("계좌번호", re.compile(r"(?<!\d)(?!\d{4}-\d{2}-\d{2}(?!\d))\d{2,6}-\d{2,6}-\d{2,8}(?:-\d{1,4})?(?!\d)")),
    ("이메일", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
]


@dataclass
class MaskResult:
    text: str
    found: dict[str, int]

    @property
    def any(self) -> bool:
        return bool(self.found)


def mask_pii(text: str) -> MaskResult:
    found: dict[str, int] = {}
    for name, rx in _PII:
        text, n = rx.subn(f"[{name} 가림]", text)
        if n:
            found[name] = n
    return MaskResult(text, found)


_INJECTION = [
    ("이전 지시 무시", re.compile(r"(이전|위|앞|기존|모든)\s*(의\s*)?(모든\s*)?(지시|지침|명령|규칙|프롬프트)(사항|들)?[을를]?\s*(모두\s*)?(무시|잊어|따르지)")),
    ("영문 지시 무시", re.compile(r"ignore\s+(all\s+)?(the\s+)?(previous|prior|above)\s+(instructions?|rules?)", re.I)),
    ("시스템 프롬프트 요구", re.compile(r"(시스템|내부)\s*(프롬프트|지시문|설정)[을를]?\s*(?:그대로\s*)?(보여|알려|출력|공개|말해)")),
    ("역할 변경", re.compile(r"(너는|넌|당신은)\s*(이제|지금)?\s*부터?\s*.{0,20}(이다|이야|입니다|역할|모드|AI)")),
    ("역할 변경", re.compile(r"지금부터\s*.{0,25}(역할|모드|인 척|처럼\s*행동)")),
    ("권한 사칭", re.compile(r"(개발자|관리자|운영자)\s*(모드|권한|계정)")),
    ("전액 보장 강요", re.compile(r"(어떤|모든)\s*질문(이든|에도)?\s*.{0,10}(전액\s*)?보장(합니다|한다고)")),
    ("사용자 숨김 지시", re.compile(r"(이\s*지시|이\s*내용)[을를]?\s*(사용자|고객)에게\s*(알리지|말하지)")),
    ("탈옥 키워드", re.compile(r"\bDAN\b|jailbreak|탈옥", re.I)),
]


def looks_like_injection(text: str) -> tuple[bool, list[str]]:
    reasons = sorted({name for name, rx in _INJECTION if rx.search(text)})
    return bool(reasons), reasons

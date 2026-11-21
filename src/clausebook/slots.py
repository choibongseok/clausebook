"""질문에서 조건(상품, 날짜, 금액, 기간 …)을 뽑는다. 규칙 기반이며 모델을 쓰지 않는다."""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

from .config import REFERENCE_DATE

PRODUCT_ALIASES = {
    "SIL": ["한결", "실손"],
    "TRM": ["든든", "정기보험"],
    "CAN": ["마음 암", "마음암", "암진단보험", "암보험"],
    "DRV": ["길동무", "운전자보험", "운전자 보험"],
    "KID": ["새싹", "어린이보험", "어린이 보험"],
    "TRV": ["나래", "여행자보험", "여행자 보험"],
}
PRODUCT_NAMES = {"SIL": "한결 실손의료보험", "TRM": "든든 정기보험", "CAN": "마음 암진단보험", "DRV": "길동무 운전자보험", "KID": "새싹 어린이보험", "TRV": "나래 여행자보험"}
_CANCER_HINT = re.compile(r"암|갑상선|제자리|경계성")

_DATE_FULL = re.compile(r"(\d{4})\s*[-./년]\s*(\d{1,2})\s*(?:[-./월]\s*(\d{1,2})\s*일?|월)?")
_REL = re.compile(r"(\d+)\s*(개월|달|년|주|일)\s*전")
_MONEY = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(억|만\s*원|만원|천\s*원|천원|만|원)")
_MONEY_ROLES = [
    ("uncovered", re.compile(r"비급여")),
    ("covered_copay", re.compile(r"급여|본인\s*부담")),
    ("monthly_premium", re.compile(r"월\s*보험료|월\s*납|매월|월\s*$|^\s*월|보험료")),
    ("purchase_price", re.compile(r"구입|구매|샀|가격|사서|주고")),
]
_YEARS = re.compile(r"(?<!\d)(\d{1,2})\s*년(?!\s*\d)\s*(?:째|차|동안|간|정도|쯤)?")


def detect_product(text: str) -> str | None:
    best, pos = None, -1
    for pid, aliases in PRODUCT_ALIASES.items():
        for a in aliases:
            i = text.rfind(a)
            if i > pos:
                best, pos = pid, i
    return best


def parse_money(num: str, unit: str) -> int:
    n = float(num.replace(",", ""))
    unit = unit.replace(" ", "")
    mult = {"억": 100_000_000, "만원": 10_000, "만": 10_000, "천원": 1_000, "원": 1}[unit]
    return int(round(n * mult))


def find_dates(text: str) -> list[tuple[int, int, date]]:
    out = []
    for m in _DATE_FULL.finditer(text):
        y, mo = int(m.group(1)), int(m.group(2))
        d = int(m.group(3)) if m.group(3) else 1
        try:
            out.append((m.start(), m.end(), date(y, mo, d)))
        except ValueError:
            continue
    for m in re.finditer(r"(?<!\d)(\d{4})\s*년(?!\s*\d|\s*(?:도\s*)?\d+\s*월)(?!\s*[-./]?\s*\d)", text):
        y = int(m.group(1))
        if y != 2025 and not any(a <= m.start() < b for a, b, _ in out):  # 2025년은 7월 1일 기준이 걸려 있어 연도만으로는 정하지 않는다
            out.append((m.start(), m.end(), date(y, 1, 1)))
    for m in _REL.finditer(text):
        n, unit = int(m.group(1)), m.group(2)
        days = {"개월": 30, "달": 30, "년": 365, "주": 7, "일": 1}[unit] * n
        out.append((m.start(), m.end(), REFERENCE_DATE - timedelta(days=days)))
    return sorted(out)


def _role_of_date(text: str, start: int, end: int, nxt: int) -> str | None:
    after = text[end:min(nxt, end + 24)]
    before = text[max(0, start - 14):start]
    for window in (after, before):
        if re.search(r"진단|발병|확정", window):
            return "diagnosis_date"
        if re.search(r"가입|계약|체결|청약|들었|든 ", window):
            return "join_date"
    return None


def extract(text: str) -> dict[str, Any]:
    """텍스트에서 찾은 값만 담은 사전을 돌려준다(없는 값은 키 자체가 없다)."""
    s: dict[str, Any] = {}
    p = detect_product(text)
    if p:
        s["product"] = p
    ds = find_dates(text)
    for i, (a, b, d) in enumerate(ds):
        nxt = ds[i + 1][0] if i + 1 < len(ds) else len(text)
        role = _role_of_date(text, a, b, nxt)
        if role and role not in s:
            s[role] = d.isoformat()
        elif role is None:
            s.setdefault("_unassigned_date", d.isoformat())
    for m in _MONEY.finditer(text):
        v = parse_money(m.group(1), m.group(2))
        before = re.split(r"[,.;]", text[max(0, m.start() - 14):m.start()])[-1]
        after = re.split(r"[,.;\d]", text[m.end():m.end() + 10])[0]
        role = None
        for window in (before, after):
            for r, rx in _MONEY_ROLES:
                if rx.search(window) and r not in s:
                    role = r
                    break
            if role:
                break
        if role:
            s[role] = v
        else:
            s.setdefault("_loose_money", []).append(v)
    ym = _YEARS.search(re.sub(r"\d{4}\s*년", "", text))
    if ym:
        s["_years"] = int(ym.group(1))
    if re.search(r"사용|썼|쓴|샀던|구입한 지", text) and "_years" in s:
        s["years_used"] = s["_years"]
    if re.search(r"경과|납입|냈|지났|넣었|유지", text) and "_years" in s:
        s["years_elapsed"] = s["_years"]
    dm = re.search(r"(\d+)\s*일\s*(?:간|동안)?\s*(?:입원|병원)|입원\s*(?:을\s*)?(\d+)\s*일", text)
    if dm:
        s["days"] = int(dm.group(1) or dm.group(2))
    for h in ("상급종합병원", "종합병원", "병원", "의원"):
        if h in text:
            s["hospital_type"] = h
            break
    if re.search(r"통원", text):
        s["setting"] = "통원"
    elif re.search(r"입원", text):
        s["setting"] = "입원"
    if _CANCER_HINT.search(text):
        s["cancer_type"] = "유사암" if re.search(r"갑상선|피부암|제자리|경계성|유사암", text) else "일반암"
    for c in ("도난", "파손", "화재", "분실"):
        if c in text:
            s["cause"] = c
            break
    if "cause" not in s and re.search(r"입원", text):
        if re.search(r"질병|아파|감기|폐렴|장염|열", text):
            s["cause"] = "질병"
        elif re.search(r"상해|다쳐|골절|사고|부러", text):
            s["cause"] = "상해"
    am = re.search(r"(\d{1,3})\s*(?:세|살)", text)
    if am:
        s["age"] = int(am.group(1))
    return s

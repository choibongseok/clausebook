"""계산 담당이 쓰는 도구. 금액 계산은 모델이 아니라 이 함수들이 한다.

각 도구는 OpenAI 호환 함수 호출 스키마(TOOL_DEFS)로 설명되고, 인자는 JSON 스키마로 검증한 뒤 실행한다.
규칙은 data/ 의 약관과 같아야 한다(예: 제9조 비급여 자기부담 비율은 계약일 기준).
"""
from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any, Callable

import jsonschema

CUTOFF = date(2025, 7, 1)  # 개정 약관 적용 시작일

SURRENDER_RATE = {1: 0, 2: 4, 3: 9, 4: 15, 5: 21, 6: 27, 7: 33, 8: 39, 9: 45, 10: 51, 11: 48, 12: 44, 13: 38, 14: 31, 15: 24, 16: 17, 17: 10, 18: 5, 19: 2, 20: 0}
HOSPITAL_DEDUCT = {"의원": 10_000, "병원": 15_000, "종합병원": 15_000, "상급종합병원": 20_000}

TOOL_DEFS: list[dict[str, Any]] = [
    {"type": "function", "function": {
        "name": "medical_reimbursement",
        "description": "한결 실손의료보험의 입원 또는 통원 의료비 지급액을 계산한다.",
        "parameters": {"type": "object", "additionalProperties": False,
                       "properties": {
                           "setting": {"type": "string", "enum": ["입원", "통원"]},
                           "covered_copay": {"type": "integer", "minimum": 0, "description": "급여 본인부담금(원)"},
                           "uncovered": {"type": "integer", "minimum": 0, "description": "비급여 의료비(원)"},
                           "hospital_type": {"type": "string", "enum": ["의원", "병원", "종합병원", "상급종합병원"], "description": "통원일 때만"},
                           "contract_date": {"type": "string", "description": "계약 체결일 YYYY-MM-DD(입원 비급여 비율 결정)"}},
                       "required": ["setting", "covered_copay", "uncovered"]}}},
    {"type": "function", "function": {
        "name": "cancer_benefit",
        "description": "마음 암진단보험의 암 진단비 지급 여부와 금액을 계산한다.",
        "parameters": {"type": "object", "additionalProperties": False,
                       "properties": {
                           "cancer_type": {"type": "string", "enum": ["일반암", "유사암"]},
                           "contract_date": {"type": "string", "description": "계약 체결일 YYYY-MM-DD"},
                           "diagnosis_date": {"type": "string", "description": "진단확정일 YYYY-MM-DD"}},
                       "required": ["cancer_type", "contract_date", "diagnosis_date"]}}},
    {"type": "function", "function": {
        "name": "surrender_value",
        "description": "든든 정기보험의 해지환급금을 계산한다.",
        "parameters": {"type": "object", "additionalProperties": False,
                       "properties": {
                           "monthly_premium": {"type": "integer", "minimum": 1, "description": "월 보험료(원)"},
                           "years_elapsed": {"type": "integer", "minimum": 0, "maximum": 40, "description": "경과 연수(만 년)"}},
                       "required": ["monthly_premium", "years_elapsed"]}}},
    {"type": "function", "function": {
        "name": "baggage_claim",
        "description": "나래 여행자보험의 휴대품 손해 보상액을 계산한다.",
        "parameters": {"type": "object", "additionalProperties": False,
                       "properties": {
                           "purchase_price": {"type": "integer", "minimum": 1, "description": "구입가(원)"},
                           "years_used": {"type": "integer", "minimum": 0, "maximum": 30, "description": "사용 연수(만 년)"},
                           "cause": {"type": "string", "enum": ["도난", "파손", "화재", "분실"]}},
                       "required": ["purchase_price", "years_used", "cause"]}}},
    {"type": "function", "function": {
        "name": "kid_hospital_daily",
        "description": "새싹 어린이보험의 입원일당 지급액을 계산한다.",
        "parameters": {"type": "object", "additionalProperties": False,
                       "properties": {
                           "cause": {"type": "string", "enum": ["상해", "질병"]},
                           "days": {"type": "integer", "minimum": 1, "maximum": 365, "description": "입원 일수"},
                           "contract_date": {"type": "string", "description": "계약 체결일 YYYY-MM-DD(상해 일당 결정)"}},
                       "required": ["cause", "days", "contract_date"]}}},
]
ARG_LABELS = {
    "setting": "구분", "covered_copay": "급여 본인부담금", "uncovered": "비급여 의료비", "hospital_type": "병원 종류",
    "contract_date": "계약 체결일", "cancer_type": "암 종류", "diagnosis_date": "진단확정일", "monthly_premium": "월 보험료",
    "years_elapsed": "경과 연수", "purchase_price": "구입가", "years_used": "사용 연수", "cause": "사고 원인", "days": "입원 일수",
}
TOOL_SCHEMAS = {t["function"]["name"]: t["function"]["parameters"] for t in TOOL_DEFS}
TOOL_PRODUCT = {"medical_reimbursement": "SIL", "cancer_benefit": "CAN", "surrender_value": "TRM", "baggage_claim": "TRV", "kid_hospital_daily": "KID"}
TOOL_SOURCES = {  # 계산 근거가 되는 조항
    "medical_reimbursement": ["SIL-T#제6조", "SIL-T#제7조", "SIL-T#제9조@v1", "SIL-T#제9조@v2"],
    "cancer_benefit": ["CAN-T#제3조", "CAN-T#제4조", "CAN-T#제5조@v1", "CAN-T#제5조@v2"],
    "surrender_value": ["TRM-T#제9조"],
    "baggage_claim": ["TRV-T#제4조", "TRV-T#제6조"],
    "kid_hospital_daily": ["KID-T#제5조@v1", "KID-T#제5조@v2", "KID-T#제6조"],
}


class ToolError(ValueError):
    pass


def _d(s: str) -> date:
    try:
        return date.fromisoformat(s)
    except ValueError as e:
        raise ToolError(f"날짜 형식이 잘못됨: {s}") from e


def _won(n: int) -> str:
    return f"{n:,}원"


def _add_years(d: date, n: int) -> date:
    try:
        return d.replace(year=d.year + n)
    except ValueError:  # 2월 29일
        return d.replace(year=d.year + n, day=28)


def medical_reimbursement(setting, covered_copay, uncovered, hospital_type=None, contract_date=None) -> dict:
    steps = []
    if setting == "입원":
        if not contract_date:
            raise ToolError("입원 비급여 비율은 계약 체결일이 필요함")
        ratio = 30 if _d(contract_date) >= CUTOFF else 20
        a = covered_copay * 90 // 100
        b = uncovered * (100 - ratio) // 100
        total = min(a + b, 50_000_000)
        steps = [f"급여 본인부담금 {_won(covered_copay)}의 90% = {_won(a)}", f"비급여 {_won(uncovered)}의 {100 - ratio}% = {_won(b)} (자기부담 {ratio}%)", f"합계 {_won(a + b)}, 연간 한도 5,000만원 적용 후 {_won(total)}"]
    else:
        if hospital_type not in HOSPITAL_DEDUCT:
            raise ToolError("통원은 병원 종류가 필요함")
        ded = HOSPITAL_DEDUCT[hospital_type]
        raw = covered_copay + uncovered
        total = max(0, min(raw - ded, 200_000))
        steps = [f"급여 본인부담금과 비급여 합계 {_won(raw)}", f"{hospital_type} 공제금액 {_won(ded)}을 뺌 = {_won(max(raw - ded, 0))}", f"회당 한도 20만원 적용 후 {_won(total)}"]
    return {"amount": total, "steps": steps, "headline": f"예상 보험금은 {_won(total)}입니다."}


def cancer_benefit(cancer_type, contract_date, diagnosis_date) -> dict:
    c, g = _d(contract_date), _d(diagnosis_date)
    if g < c:
        raise ToolError("진단확정일이 계약일보다 앞섬")
    start = c + timedelta(days=90)  # 계약일 포함 90일이 지난 다음 날부터 보장
    face = 30_000_000
    if g < start:
        return {"amount": 0, "steps": [f"계약일 {c} 포함 90일 대기기간은 {start - timedelta(days=1)}까지입니다.", f"진단확정일 {g}은 대기기간 안이라 계약이 무효가 되고 낸 보험료를 돌려줍니다."], "headline": "예상 진단비는 0원입니다. 대기기간 안의 진단이라 계약이 무효가 되고 낸 보험료를 돌려받습니다.", "outcome": "void"}
    if cancer_type == "유사암":
        amt = face * 20 // 100
        return {"amount": amt, "steps": [f"유사암은 가입금액 {_won(face)}의 20%입니다.", "감액 규정(제5조)은 일반암에만 적용됩니다."], "headline": f"유사암 진단비는 {_won(amt)}입니다.", "outcome": "similar"}
    years = 1 if c >= CUTOFF else 2
    until = _add_years(c, years)
    if g < until:
        amt = face // 2
        return {"amount": amt, "steps": [f"계약일 {c}이 {'2025년 7월 1일 이후라 감액기간은 1년' if years == 1 else '2025년 6월 30일 이전이라 감액기간은 2년'}입니다.", f"진단확정일 {g}은 {until} 전이라 50%만 지급합니다."], "headline": f"감액기간 안의 진단이라 {_won(amt)}(50%)가 지급됩니다.", "outcome": "reduced"}
    return {"amount": face, "steps": [f"감액기간({years}년)이 {until}에 끝났고 진단확정일 {g}은 그 뒤입니다."], "headline": f"일반암 진단비는 {_won(face)}이 전액 지급됩니다.", "outcome": "full"}


def surrender_value(monthly_premium, years_elapsed) -> dict:
    paid = monthly_premium * 12 * years_elapsed
    rate = SURRENDER_RATE.get(years_elapsed, 0)
    amt = paid * rate // 100
    return {"amount": amt, "steps": [f"납입한 보험료 누계 = {_won(monthly_premium)} × 12개월 × {years_elapsed}년 = {_won(paid)}", f"경과 {years_elapsed}년 환급률 {rate}%", f"해지환급금 = {_won(paid)} × {rate}% = {_won(amt)}"], "headline": f"예상 해지환급금은 {_won(amt)}입니다."}


def baggage_claim(purchase_price, years_used, cause) -> dict:
    if cause == "분실":
        return {"amount": 0, "steps": ["물건을 잃어버린 분실은 보상하지 않습니다(제6조 2항)."], "headline": "분실은 보상 대상이 아니라 0원입니다."}
    dep = min(20 * years_used, 80)
    value = purchase_price * (100 - dep) // 100
    amt = min(value, 200_000)
    return {"amount": amt, "steps": [f"사용 {years_used}년이라 감가 {dep}% 적용: {_won(purchase_price)} → {_won(value)}", f"1개당 한도 20만원 적용 후 {_won(amt)}"], "headline": f"예상 보상액은 {_won(amt)}입니다."}


def kid_hospital_daily(cause, days, contract_date) -> dict:
    if cause == "상해":
        per = 30_000 if _d(contract_date) >= CUTOFF else 20_000
        n = min(days, 180)
        amt = per * n
        return {"amount": amt, "steps": [f"상해 입원일당 하루 {_won(per)}(계약일 기준), 최대 180일", f"{n}일 × {_won(per)} = {_won(amt)}"], "headline": f"상해 입원일당은 {_won(amt)}입니다."}
    n = min(max(days - 3, 0), 120)
    amt = 20_000 * n
    return {"amount": amt, "steps": [f"질병 입원은 4일째부터 지급이라 {days}일 중 {n}일이 대상입니다.", f"{n}일 × 2만원 = {_won(amt)}"], "headline": f"질병 입원일당은 {_won(amt)}입니다."}


_IMPL: dict[str, Callable[..., dict]] = {f.__name__: f for f in (medical_reimbursement, cancer_benefit, surrender_value, baggage_claim, kid_hospital_daily)}


def validate_args(name: str, args: dict) -> None:
    if name not in TOOL_SCHEMAS:
        raise ToolError(f"없는 도구: {name}")
    try:
        jsonschema.validate(args, TOOL_SCHEMAS[name])
    except jsonschema.ValidationError as e:
        raise ToolError(f"인자 스키마 위반: {e.message}") from e


def run_tool(name: str, args: dict) -> dict:
    validate_args(name, args)
    try:
        return _IMPL[name](**args)
    except ToolError:
        raise
    except Exception as e:  # 도구 안 오류는 호출자가 대체 경로로 처리한다
        raise ToolError(str(e)) from e

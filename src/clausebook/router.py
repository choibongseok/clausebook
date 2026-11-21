"""질문 분류. 규칙 기반이며 결과(intent)가 곧 다음 단계를 정한다."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

INTENTS = ("clause_lookup", "calculation", "claim_docs", "individual_review", "out_of_scope", "smalltalk")

_INSURANCE = re.compile(r"보험|약관|보장|청구|보험료|보험금|가입|계약|진단|입원|통원|수술|환급|해지|면책|특약|갱신|대기|감액|조항|납입|수익자|피보험|계약자|서류|지급|사고|운전|여행|암|골절|휴대품|항공|실손|정기|어린이|태아|면허|벌금|민원|이의|영업일|용어|뜻이|무슨 뜻|보상|비용|치료|환급금|감액기간|대기기간|청약|철회|한도|위로금|일당|진단비|환급률|납입면제")
_SMALLTALK = re.compile(r"^\s*(안녕|하이|고마워|감사|수고|잘 가|반가)")
_CALC_CUE = re.compile(r"계산|얼마(?:를|나|가|예요|일까|인가|입니까)?\s*(?:받|나오|지급|환급|보상|돌려|되)|얼마예요|얼마인가요|예상\s*(?:금액|환급|보험금)")
_INDIV = re.compile(r"(제|내|저의|우리)\s*(?:경우|사고|케이스|상황)|이번\s*(?:사고|건)|저는\s*.{0,30}(?:받을 수|보상|지급)|확실히|무조건|꼭\s*(?:받|지급|보상)|확정(?:해|적으로)|약속|보장해\s*주")
_INDIV_ASK = re.compile(r"받을 수|보상|지급|보장|되나요|될까요|맞죠|맞나요|되겠죠")
_CLAIM = re.compile(r"청구.{0,12}(서류|방법|절차|어떻게|접수|기간)|(필요한|구비)\s*서류|서류.{0,6}(뭐|무엇|어떤|필요)|접수\s*(방법|어떻게|가능)|지급.{0,8}(걸리|기간|언제|까지)|대신\s*청구|이의\s*(?:신청|제기)")
_TOOL_TOPIC = {
    "SIL": re.compile(r"(입원|통원).*(의료비|치료비|비용|지급|받|보험금)|의료비.*(계산|얼마)|보험금.*(입원|통원)"),
    "CAN": re.compile(r"진단(?:비|금|받|확정)|진단.*(얼마|받)"),
    "TRM": re.compile(r"해지\s*환급|환급금"),
    "TRV": re.compile(r"휴대품|도난|파손|가방|노트북|카메라|휴대폰|핸드폰"),
    "KID": re.compile(r"입원\s*일당|입원.*일당|입원했"),
}


@dataclass
class Route:
    intent: str
    tool: str | None = None
    reasons: list[str] = field(default_factory=list)


_TOOL_BY_PRODUCT = {"SIL": "medical_reimbursement", "CAN": "cancer_benefit", "TRM": "surrender_value", "TRV": "baggage_claim", "KID": "kid_hospital_daily"}


def route(text: str, product: str | None, slots: dict, has_pending_calc: bool = False) -> Route:
    if _SMALLTALK.search(text) and len(text) < 20:
        return Route("smalltalk", reasons=["인사말"])
    if has_pending_calc and (slots.keys() - {"product"}):
        return Route("calculation", tool=_TOOL_BY_PRODUCT.get(product or ""), reasons=["이어지는 계산 조건"])
    if not _INSURANCE.search(text) and product is None:
        return Route("out_of_scope", reasons=["보험 관련 낱말 없음"])
    if _INDIV.search(text) and _INDIV_ASK.search(text):
        return Route("individual_review", reasons=["개별 사고의 지급 여부 확인 요청"])
    has_amount = any(k in slots for k in ("covered_copay", "uncovered", "monthly_premium", "purchase_price", "_loose_money"))
    two_dates = ("join_date" in slots and "diagnosis_date" in slots)
    if product in _TOOL_BY_PRODUCT and _TOOL_TOPIC.get(product, re.compile("$^")).search(text):
        if _CALC_CUE.search(text) and (has_amount or two_dates or "계산" in text or "days" in slots or "years_used" in slots):
            return Route("calculation", tool=_TOOL_BY_PRODUCT[product], reasons=["계산 표현과 조건 값"])
        if has_amount or two_dates:
            return Route("calculation", tool=_TOOL_BY_PRODUCT[product], reasons=["조건 값이 있는 금액 질문"])
    if _CLAIM.search(text):
        return Route("claim_docs", reasons=["청구 절차·서류 표현"])
    return Route("clause_lookup", reasons=["기본"])

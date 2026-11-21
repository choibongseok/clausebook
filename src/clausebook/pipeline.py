"""대화 한 턴을 처리하는 흐름. 단계(Stage)와 전이(TRANSITIONS)가 코드에 그대로 보이게 둔다.

역할(담당)
  입력 가드      개인정보 가림, 주입·역할 탈취 차단
  라우터         질문 분류와 조건 뽑기(router.py, slots.py)
  약관 검색 담당  후속 질문 결합, 필터 검색, 개정 버전 확인, 답변 근거 만들기
  계산 담당      함수 호출(스키마 검증 + 재요청 + 대체 경로)로 도구를 실행. 금액은 모델이 계산하지 않는다
  청구 안내 담당  청구 문서·약관 안에서 서류와 절차를 찾는다(약관 검색 담당과 같은 경로, 문서 종류 필터만 다름)
  답변 검수 담당  인용 확인, 근거 없는 문장 제거, 못 찾으면 못 찾았다고 답한다
"""
from __future__ import annotations

import re
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Callable

from . import schemas, slots as slotmod, tools
from .chunking import Chunk
from .config import PROMPT_DIR, PROMPT_VERSION, REFERENCE_DATE, thresholds
from .corpus import Clause
from .llm import ChatResult, LLMClient, LLMError, MockModel
from .retrieval import Filters, Hit, Retriever
from .router import Route, route as route_question
from .security import looks_like_injection, mask_pii
from .verify import NOT_FOUND, verify


class Stage(str, Enum):
    GUARD = "입력 가드"
    ROUTE = "분류"
    CLARIFY = "되묻기"
    REWRITE = "질문 정리"
    RETRIEVE = "검색"
    VERSION = "개정 확인"
    GATE = "근거 충분성"
    GENERATE = "답변 생성"
    VERIFY = "답변 검수"
    TOOL_CALL = "도구 호출 생성"
    TOOL_RUN = "도구 실행"
    COMPOSE = "계산 결과 정리"
    GUIDANCE = "안내 문구"
    OUTPUT = "출력 가드"
    DONE = "끝"


# 한 턴에서 가능한 단계 전이. 여기 없는 전이는 허용하지 않는다(테스트가 확인한다).
TRANSITIONS: dict[Stage, tuple[Stage, ...]] = {
    Stage.GUARD: (Stage.ROUTE, Stage.OUTPUT),  # 주입이면 곧바로 출력
    Stage.ROUTE: (Stage.CLARIFY, Stage.REWRITE, Stage.TOOL_CALL, Stage.RETRIEVE, Stage.OUTPUT),
    Stage.CLARIFY: (Stage.OUTPUT,),
    Stage.REWRITE: (Stage.RETRIEVE,),
    Stage.RETRIEVE: (Stage.VERSION, Stage.GUIDANCE, Stage.CLARIFY),
    Stage.VERSION: (Stage.GATE, Stage.CLARIFY),
    Stage.GATE: (Stage.GENERATE, Stage.OUTPUT),
    Stage.GENERATE: (Stage.VERIFY,),
    Stage.VERIFY: (Stage.OUTPUT,),
    Stage.TOOL_CALL: (Stage.TOOL_RUN, Stage.CLARIFY),
    Stage.TOOL_RUN: (Stage.COMPOSE, Stage.CLARIFY),
    Stage.COMPOSE: (Stage.OUTPUT,),
    Stage.GUIDANCE: (Stage.OUTPUT,),
    Stage.OUTPUT: (Stage.DONE,),
}

REFUSE_INJECTION = "대화에서 역할을 바꾸거나 앞선 지시를 무시하라는 요청은 처리하지 않습니다. 보험 약관에 대해 궁금한 점을 다시 물어봐 주세요."
REFUSE_SCOPE = "보험 약관과 관계없는 질문에는 답하기 어렵습니다. 상품의 보장 내용, 보험금 청구, 보험 용어에 대해 물어봐 주세요."
GREETING = "안녕하세요. 가상 보험 상품 6종의 약관을 바탕으로 보장 내용, 청구 서류, 보험금 계산을 안내합니다."
GUIDE_TEXT = "개별 사고의 보장 여부는 사고 경위와 제출 서류를 심사한 뒤에 정해집니다. 그래서 상담에서 지급을 약속하거나 거절을 확정할 수 없습니다. 약관의 일반 기준은 아래 조항에서 확인하실 수 있고, 정확한 결과는 청구서를 접수해 심사를 받아야 알 수 있습니다."
PRODUCT_LIST = "한결 실손의료보험, 든든 정기보험, 마음 암진단보험, 길동무 운전자보험, 새싹 어린이보험, 나래 여행자보험"


@dataclass
class PipelineConfig:
    strategy: str = "clause_ctx"
    method: str = "dense"
    rerank: str = "rule"
    k_context: int = 4
    use_filter: bool = True
    prompt_version: str = PROMPT_VERSION
    max_tokens: int = 320
    llm_retries: int = 1


@dataclass
class Step:
    stage: str
    ms: float
    detail: dict = field(default_factory=dict)


@dataclass
class Result:
    trace_id: str
    answer: str
    status: str  # answered | not_found | clarify | guidance | refused | blocked | calculated
    intent: str
    citations: list[dict]
    state: dict
    steps: list[Step]
    flags: dict
    usage: dict
    total_ms: float

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


def new_state() -> dict:
    return {"product": None, "join_date": None, "diagnosis_date": None, "age": None, "pending": None, "last_question": None, "last_product": None, "turns": 0}


_LABELS = {"join_date": "계약 체결일", "diagnosis_date": "진단확정일", **tools.ARG_LABELS}
_FOLLOWUP = re.compile(r"^\s*(그럼|그러면|그렇다면|그때|그 경우|그건|그럼요|아 그럼|만약)")


class Pipeline:
    def __init__(self, retriever: Retriever, llm: LLMClient, clauses: dict[str, Clause], cfg: PipelineConfig | None = None, log: Callable[[dict], None] | None = None):
        self.r, self.llm, self.clauses = retriever, llm, clauses
        self.cfg = cfg or PipelineConfig()
        self.fallback_llm = MockModel("extractive")
        self.log = log
        self._prompt_cache: dict[str, str] = {}

    # ------------------------------------------------------------------ 도우미
    def prompt(self, name: str) -> str:
        key = f"{self.cfg.prompt_version}/{name}"
        if key not in self._prompt_cache:
            self._prompt_cache[key] = (PROMPT_DIR / self.cfg.prompt_version / f"{name}.txt").read_text(encoding="utf-8").strip()
        return self._prompt_cache[key]

    @contextmanager
    def step(self, steps: list[Step], stage: Stage, on_step: Callable[[Step], None] | None = None):
        s = Step(stage.value, 0.0)
        t0 = time.perf_counter()
        try:
            yield s.detail
        finally:
            s.ms = round((time.perf_counter() - t0) * 1000, 2)
            steps.append(s)
            if on_step:
                on_step(s)

    def _cite(self, chunk: Chunk, tag: str | None = None) -> list[dict]:
        out = []
        for cid in chunk.clause_ids:
            c = self.clauses.get(cid)
            if c is None:
                continue
            out.append({"id": cid, "tag": tag, "heading": c.heading, "doc_title": c.doc_title, "doc_id": c.doc_id,
                        "text": c.body if len(chunk.clause_ids) == 1 else chunk.display,
                        "effective": f"{c.eff_from or ''}~{c.eff_to or ''}" if (c.eff_from or c.eff_to) else None})
        return out

    def _cite_clause_ids(self, ids: list[str]) -> list[dict]:
        out = []
        for cid in ids:
            c = self.clauses.get(cid)
            if c:
                out.append({"id": cid, "tag": None, "heading": c.heading, "doc_title": c.doc_title, "doc_id": c.doc_id, "text": c.body,
                            "effective": f"{c.eff_from or ''}~{c.eff_to or ''}" if (c.eff_from or c.eff_to) else None})
        return out

    # ------------------------------------------------------------------ 진입점
    def ask(self, message: str, state: dict | None = None, session_id: str | None = None, on_step: Callable[[Step], None] | None = None) -> Result:
        t0 = time.perf_counter()
        st = {**new_state(), **(state or {})}
        steps: list[Step] = []
        flags: dict = {"pii": {}, "injection": [], "fallback": [], "llm_retries": 0, "removed_suspicious_chunks": 0, "dropped_sentences": 0, "invalid_citations": 0, "cache": {}}
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "llm_calls": 0}
        trace_id = uuid.uuid4().hex[:12]
        st["turns"] += 1

        with self.step(steps, Stage.GUARD, on_step) as d:
            m = mask_pii(message)
            inj, reasons = looks_like_injection(message)
            d.update({"masked": m.found, "injection": reasons, "text": m.text})
            flags["pii"], flags["injection"] = m.found, reasons
        text = m.text
        if inj:
            return self._finish(trace_id, session_id, REFUSE_INJECTION, "blocked", "injection", [], st, steps, flags, usage, t0, on_step)

        extracted = slotmod.extract(text)
        route, ctx = self._route(text, st, extracted, steps, on_step)
        if route.intent == "smalltalk":
            return self._finish(trace_id, session_id, GREETING, "answered", route.intent, [], st, steps, flags, usage, t0, on_step)
        if route.intent == "out_of_scope":
            return self._finish(trace_id, session_id, REFUSE_SCOPE, "refused", route.intent, [], st, steps, flags, usage, t0, on_step)

        if route.intent == "calculation":
            ans, status, cites = self._calculation(text, st, route, extracted, steps, flags, usage, on_step)
        elif route.intent == "individual_review":
            ans, status, cites = self._individual(text, st, steps, flags, on_step)
        else:
            ans, status, cites = self._lookup(ctx, st, route, steps, flags, usage, on_step)
        return self._finish(trace_id, session_id, ans, status, route.intent, cites, st, steps, flags, usage, t0, on_step)

    # ------------------------------------------------------------------ 분류
    def _route(self, text: str, st: dict, extracted: dict, steps, on_step) -> tuple[Route, str]:
        with self.step(steps, Stage.ROUTE, on_step) as d:
            pending = st.get("pending") or {}
            # 되묻기 뒤의 답이면 보류한 질문을 다시 푼다
            if pending.get("kind") in ("version", "product"):
                answered = False
                if pending["kind"] == "version":
                    jd = self._date_from_reply(text, extracted, pending)
                    if jd:
                        st["join_date"] = jd
                        answered = True
                elif extracted.get("product"):
                    st["product"] = extracted["product"]
                    answered = True
                if answered:
                    st["pending"] = None
                    text = pending["question"]
                    extracted = slotmod.extract(text)
                    d["resumed"] = pending["kind"]
            for k in ("product", "join_date", "diagnosis_date", "age"):
                if extracted.get(k) is not None:
                    st[k] = extracted[k]
            if extracted.get("product"):
                st["last_product"] = extracted["product"]
            prod = st.get("product")
            r = route_question(text, prod, extracted, has_pending_calc=pending.get("kind") == "calc")
            d.update({"intent": r.intent, "tool": r.tool, "reasons": r.reasons, "product": prod, "slots": {k: v for k, v in extracted.items() if not k.startswith("_")}})
            if r.intent == "calculation" and pending.get("kind") == "calc" and not r.tool:
                r.tool = pending.get("tool")
        ctx = text
        return r, ctx

    @staticmethod
    def _date_from_reply(text: str, extracted: dict, pending: dict) -> str | None:
        if extracted.get("join_date"):
            return extracted["join_date"]
        if extracted.get("_unassigned_date"):
            return extracted["_unassigned_date"]
        cut = date.fromisoformat(pending.get("cutoff", "2025-07-01"))
        if re.search(r"이전|전에|전이|전입니다|전이요|전에요|^\s*전", text):
            return (cut.replace(day=1) if False else date.fromordinal(cut.toordinal() - 1)).isoformat()
        if re.search(r"이후|후에|후입니다|후이요|후에요|^\s*후|최근", text):
            return cut.isoformat()
        return None

    # ------------------------------------------------------------------ 약관 검색 담당
    def _filters(self, st: dict, doc_types: list[str] | None) -> Filters:
        if not self.cfg.use_filter:
            return Filters()
        as_of = date.fromisoformat(st["join_date"]) if st.get("join_date") else None
        return Filters(products=[st["product"]] if st.get("product") else None, as_of=as_of, doc_types=doc_types)

    def _lookup(self, text, st, route, steps, flags, usage, on_step):
        cfg = self.cfg
        q = text
        with self.step(steps, Stage.REWRITE, on_step) as d:
            if st.get("last_question") and (_FOLLOWUP.search(text) or len(text) < 16):
                q = f"{st['last_question']} {text}"
                d["rewritten"] = True
            d["query"] = q
        doc_types = ["claims", "faq"] if route.intent == "claim_docs" else None
        f = self._filters(st, doc_types)
        with self.step(steps, Stage.RETRIEVE, on_step) as d:
            hits = self.r.search(q, cfg.strategy, cfg.method, cfg.rerank, 6, f)
            d.update({"strategy": cfg.strategy, "method": cfg.method, "rerank": cfg.rerank,
                      "filter": {"products": f.products, "as_of": f.as_of.isoformat() if f.as_of else None, "doc_types": f.doc_types},
                      "hits": [{"id": h.chunk.clause_ids[0] if h.chunk.clause_ids else h.chunk.id, "chunk": h.chunk.id, "score": round(h.score, 4),
                                "parts": {k: round(v, 4) for k, v in h.parts.items()}} for h in hits]})
            flags["cache"] = self.r.cache.stats()
        # 근거 충분성
        th = thresholds()
        with self.step(steps, Stage.GATE, on_step) as d:
            cov = max((self.r.coverage(q, h, cfg.strategy) for h in hits[:3]), default=0.0)
            dense = max((h.parts.get("dense", 1.0) for h in hits[:3]), default=0.0)
            ok = bool(hits) and (cov >= th["min_coverage"] or dense >= th["min_dense"])
            d.update({"coverage": round(cov, 3), "dense": round(dense, 3), "threshold": {"min_coverage": th["min_coverage"], "min_dense": th["min_dense"]}, "pass": ok})
        if not ok:
            st["last_question"] = text
            return NOT_FOUND + " 질문하신 내용이 약관·설명서·청구 안내에 있는지 확인해 보았지만 근거가 되는 조항이 없습니다.", "not_found", []
        # 상품이 없고 결과가 여러 상품에 갈리면 되묻는다
        if not st.get("product") and hits:
            top = hits[:3]
            prods = {h.chunk.product_id for h in top if h.chunk.product_id != "ALL"}
            if len(prods) >= 2 and top[0].chunk.product_id != "ALL":
                st["pending"] = {"kind": "product", "question": text}
                with self.step(steps, Stage.CLARIFY, on_step) as d:
                    d["ask"] = "product"
                return f"어떤 상품에 대한 질문인가요? 현재 안내할 수 있는 상품은 {PRODUCT_LIST}입니다.", "clarify", []
        # 개정 버전 확인
        with self.step(steps, Stage.VERSION, on_step) as d:
            versioned = [h for h in hits[:4] if h.chunk.eff_from or h.chunk.eff_to]
            top2_versioned = any(h.chunk.eff_from or h.chunk.eff_to for h in hits[:2])
            labels = {}
            for h in versioned:
                for cid in h.chunk.clause_ids:
                    labels.setdefault(cid.split("@")[0], set()).add(cid)
            conflict = []
            if top2_versioned:
                for base in {cid.split("@")[0] for h in versioned[:2] for cid in h.chunk.clause_ids if "@" in cid}:
                    if len([c for c in self.clauses if c.split("@")[0] == base and "@" in c]) >= 2:
                        conflict.append(base)
            d.update({"versioned_hits": [h.chunk.id for h in versioned], "conflict": conflict, "as_of": st.get("join_date")})
        if conflict and not st.get("join_date") and self.cfg.use_filter and route.intent != "claim_docs":
            st["pending"] = {"kind": "version", "question": text, "cutoff": "2025-07-01"}
            return "계약을 체결한 날짜에 따라 적용되는 조항이 다릅니다. 2025년 7월 1일 이전에 가입하셨나요, 이후에 가입하셨나요? 가입 날짜를 알려 주셔도 됩니다.", "clarify", []
        st["pending"] = None if (st.get("pending") or {}).get("kind") != "calc" else st["pending"]
        # 답변 생성
        ctx_hits = []
        for h in hits:
            if h.chunk.suspicious:
                flags["removed_suspicious_chunks"] += 1
                continue
            ctx_hits.append(h)
            if len(ctx_hits) >= cfg.k_context:
                break
        context = {f"C{i}": h for i, h in enumerate(ctx_hits, 1)}
        if flags["removed_suspicious_chunks"]:
            with self.step(steps, Stage.GUIDANCE, on_step) as d:
                d["removed_chunks"] = [h.chunk.id for h in hits if h.chunk.suspicious]
                d["reason"] = "문서 안에 지시문이 섞여 있어 근거에서 제외"
        if not ctx_hits:
            return NOT_FOUND, "not_found", []
        st["last_question"] = text
        raw, gen_info = self._generate_answer(q, st, context, steps, flags, usage, on_step)
        with self.step(steps, Stage.VERIFY, on_step) as d:
            ctx_text = {t: h.chunk.text for t, h in context.items()}
            if raw.get("answerable") is False:
                v_text, cites, dropped = NOT_FOUND, [], []
                d["model_said_unanswerable"] = True
            else:
                v = verify(raw["answer"], raw.get("citations", []), ctx_text)
                v_text, cites, dropped = v.text, v.citations, v.dropped
                flags["dropped_sentences"] += len(dropped)
                flags["invalid_citations"] += len(v.invalid_citations)
                d.update({"kept": v.kept, "dropped": dropped, "invalid_citations": v.invalid_citations, "repaired": v.repaired})
            d["citations"] = cites
        out_cites = [c for t in cites for c in self._cite(context[t].chunk, t)]
        # 같은 조항을 여러 청크가 가리키면 한 번만 보인다
        seen, uniq = set(), []
        for c in out_cites:
            if c["id"] not in seen:
                seen.add(c["id"]); uniq.append(c)
        if v_text == NOT_FOUND or not uniq:
            return NOT_FOUND + " 검색된 조항에서 질문에 답할 근거를 확인하지 못했습니다.", "not_found", []
        return v_text, "answered", uniq

    def _slots_block(self, st: dict) -> str:
        lines = []
        if st.get("product"):
            lines.append(f"- 상품: {slotmod.PRODUCT_NAMES.get(st['product'], st['product'])}")
        if st.get("join_date"):
            lines.append(f"- 계약 체결일: {st['join_date']}")
        if st.get("age"):
            lines.append(f"- 나이: {st['age']}세")
        return "\n".join(lines) or "- (없음)"

    def _generate_answer(self, q, st, context: dict[str, Hit], steps, flags, usage, on_step) -> tuple[dict, dict]:
        body = "\n".join(f"[{t}] {h.chunk.id}\n{h.chunk.text}\n" for t, h in context.items())
        user = f"[질문]\n{q}\n\n[알려진 조건]\n{self._slots_block(st)}\n\n[근거]\n{body}"
        messages = [{"role": "system", "content": self.prompt("answer")}, {"role": "user", "content": user}]
        info = {}
        with self.step(steps, Stage.GENERATE, on_step) as d:
            d.update({"model": self.llm.name, "prompt_version": self.cfg.prompt_version, "context": {t: h.chunk.id for t, h in context.items()}})
            raw = None
            last_err = ""
            for attempt in range(self.cfg.llm_retries + 1):
                try:
                    res = self.llm.chat(messages if attempt == 0 else [*messages, {"role": "user", "content": f"이전 출력이 형식에 맞지 않았습니다({last_err}). 스키마에 맞는 JSON 하나만 다시 출력하세요."}], schemas.ANSWER_SCHEMA, self.cfg.max_tokens)
                    self._count(usage, res)
                    raw = schemas.validate(schemas.parse_json(res.text), schemas.ANSWER_SCHEMA)
                    d["attempts"] = attempt + 1
                    break
                except schemas.SchemaError as e:
                    last_err = str(e)
                    flags["llm_retries"] += 1
                except LLMError as e:
                    last_err = str(e)
                    flags["fallback"].append(f"모델 오류: {e}")
                    break
            if raw is None:
                flags["fallback"].append("추출 답변으로 대체")
                res = self.fallback_llm.chat(messages, schemas.ANSWER_SCHEMA)
                raw = schemas.parse_json(res.text)
                d["fallback"] = last_err or "형식 오류"
            d["output"] = raw
        return raw, info

    @staticmethod
    def _count(usage: dict, res: ChatResult) -> None:
        usage["prompt_tokens"] += res.prompt_tokens
        usage["completion_tokens"] += res.completion_tokens
        usage["llm_calls"] += 1

    # ------------------------------------------------------------------ 개별 심사
    def _individual(self, text, st, steps, flags, on_step):
        f = self._filters({**st, "join_date": st.get("join_date")}, None)
        with self.step(steps, Stage.RETRIEVE, on_step) as d:
            hits = self.r.search(text, self.cfg.strategy, self.cfg.method, self.cfg.rerank, 3, f)
            d["hits"] = [{"id": h.chunk.clause_ids[0] if h.chunk.clause_ids else h.chunk.id, "score": round(h.score, 4)} for h in hits]
        with self.step(steps, Stage.GUIDANCE, on_step) as d:
            ids = ["COM-C#보험금 지급을 단정할 수 없는 경우"]
            for h in hits:
                if not h.chunk.suspicious:
                    ids += [c for c in h.chunk.clause_ids if c not in ids][:1]
            ids = ids[:3]
            d["reason"] = "개별 사고의 지급 여부는 단정하지 않고 일반 기준만 안내"
            d["clauses"] = ids
        return GUIDE_TEXT, "guidance", self._cite_clause_ids(ids)

    # ------------------------------------------------------------------ 계산 담당
    def _build_args(self, tool: str, st: dict, ex: dict, pend: dict) -> tuple[dict, list[str]]:
        s = {**pend.get("slots", {}), **{k: v for k, v in ex.items() if v is not None}}
        for k in ("join_date", "diagnosis_date"):
            if st.get(k):
                s.setdefault(k, st[k])
        loose = s.get("_loose_money", [])
        und = s.get("_unassigned_date")
        sch = tools.TOOL_SCHEMAS[tool]
        a: dict = {}
        if tool == "medical_reimbursement":
            a["setting"] = s.get("setting")
            a["covered_copay"] = s.get("covered_copay")
            a["uncovered"] = s.get("uncovered")
            miss_money = [k for k in ("covered_copay", "uncovered") if a[k] is None]
            if len(miss_money) == 1 and len(loose) == 1:
                a[miss_money[0]] = loose[0]
            if a.get("setting") == "통원":
                a["hospital_type"] = s.get("hospital_type")
            else:
                a["contract_date"] = s.get("join_date") or und
        elif tool == "cancer_benefit":
            a["cancer_type"] = s.get("cancer_type")
            a["contract_date"] = s.get("join_date")
            a["diagnosis_date"] = s.get("diagnosis_date")
            if und:
                for k in ("contract_date", "diagnosis_date"):
                    if a[k] is None and not (a["contract_date"] is None and a["diagnosis_date"] is None):
                        a[k] = und
        elif tool == "surrender_value":
            a["monthly_premium"] = s.get("monthly_premium") or (loose[0] if len(loose) == 1 else None)
            a["years_elapsed"] = s.get("years_elapsed", s.get("_years"))
        elif tool == "baggage_claim":
            a["purchase_price"] = s.get("purchase_price") or (loose[0] if len(loose) == 1 else None)
            a["years_used"] = s.get("years_used", s.get("_years"))
            a["cause"] = s.get("cause")
        elif tool == "kid_hospital_daily":
            a["cause"] = s.get("cause")
            a["days"] = s.get("days")
            a["contract_date"] = s.get("join_date") or und
        keep = {k: v for k, v in s.items() if k in tools.ARG_LABELS or k in ("join_date", "diagnosis_date", "_years")}
        missing = [k for k in sch["required"] if a.get(k) is None]
        if tool == "medical_reimbursement" and a.get("setting") == "통원" and a.get("hospital_type") is None:
            missing.append("hospital_type")
        if tool == "medical_reimbursement" and a.get("setting") == "입원" and a.get("contract_date") is None:
            missing.append("contract_date")
        a = {k: v for k, v in a.items() if v is not None}
        a["__slots__"] = keep
        return a, list(dict.fromkeys(missing))

    def _calculation(self, text, st, route, ex, steps, flags, usage, on_step):
        tool = route.tool or (st.get("pending") or {}).get("tool")
        pend = st.get("pending") or {}
        if pend.get("kind") != "calc" or pend.get("tool") != tool:
            pend = {}
        if tool is None:
            return REFUSE_SCOPE, "refused", []
        expected, missing = self._build_args(tool, st, ex, pend)
        carry = expected.pop("__slots__")
        if missing:
            st["pending"] = {"kind": "calc", "tool": tool, "slots": carry}
            names = ", ".join(_LABELS.get(m, m) for m in missing)
            with self.step(steps, Stage.CLARIFY, on_step) as d:
                d.update({"ask": "calc_slots", "missing": missing})
            return f"계산하려면 {names} 정보가 더 필요합니다. 알려 주세요.", "clarify", []
        st["pending"] = None
        # 함수 호출: 모델이 도구와 인자를 고르게 한다. 검증에 실패하면 한 번 다시 요청하고, 그래도 안 되면 규칙으로 뽑은 값을 쓴다.
        lines = []
        for a_, v in expected.items():
            val = f"{v}원" if isinstance(v, int) and a_ in ("covered_copay", "uncovered", "monthly_premium", "purchase_price") else v
            lines.append(f"- {tools.ARG_LABELS[a_]}: {val}")
        user = f"[질문]\n{text}\n\n[알려진 조건]\n" + "\n".join(lines) + "\n\n[도구]\n" + ", ".join(t["function"]["name"] + ": " + t["function"]["description"] for t in tools.TOOL_DEFS) + "\n"
        schema = schemas.tool_call_schema(tools.TOOL_DEFS)
        messages = [{"role": "system", "content": self.prompt("tool_call")}, {"role": "user", "content": user}]
        final_args, called = expected, None
        with self.step(steps, Stage.TOOL_CALL, on_step) as d:
            d["model"] = self.llm.name
            d["expected_tool"] = tool
            ok = False
            last = ""
            for attempt in range(self.cfg.llm_retries + 1):
                try:
                    res = self.llm.chat(messages if attempt == 0 else [*messages, {"role": "user", "content": f"이전 호출이 올바르지 않았습니다({last}). 알려진 조건의 값만 써서 다시 호출하세요."}], schema, 200)
                    self._count(usage, res)
                    call = schemas.validate(schemas.parse_json(res.text), schema)
                    tools.validate_args(call["name"], call["arguments"])
                    if call["name"] != tool:
                        raise schemas.SchemaError(f"도구가 다름({call['name']})")
                    bad = [k for k, v in call["arguments"].items() if v != expected.get(k)]
                    if bad:
                        raise schemas.SchemaError(f"조건에 없는 값: {', '.join(bad)}")
                    final_args, called, ok = call["arguments"], call, True
                    d["attempts"] = attempt + 1
                    break
                except (schemas.SchemaError, tools.ToolError) as e:
                    last = str(e)
                    flags["llm_retries"] += 1
                except LLMError as e:
                    last = str(e)
                    flags["fallback"].append(f"모델 오류: {e}")
                    break
            if not ok:
                flags["fallback"].append("규칙으로 뽑은 인자 사용")
                d["fallback"] = last
            d.update({"tool": tool, "arguments": final_args, "model_call_valid": ok})
        with self.step(steps, Stage.TOOL_RUN, on_step) as d:
            try:
                out = tools.run_tool(tool, final_args)
            except tools.ToolError as e:
                d["error"] = str(e)
                st["pending"] = {"kind": "calc", "tool": tool, "slots": carry}
                return f"계산 조건을 확인해 주세요: {e}", "clarify", []
            d.update({"result": out["amount"], "steps": out["steps"]})
        with self.step(steps, Stage.COMPOSE, on_step) as d:
            ans = out["headline"] + " 계산 과정은 다음과 같습니다. " + " ".join(f"{i}) {s}" for i, s in enumerate(out["steps"], 1))
            ids = [i for i in tools.TOOL_SOURCES[tool] if self._valid_for(i, final_args.get("contract_date") or carry.get("join_date"))]
            d["sources"] = ids
        st["last_question"] = text
        return ans, "calculated", self._cite_clause_ids(ids)

    def _valid_for(self, cid: str, contract_date: str | None) -> bool:
        c = self.clauses.get(cid)
        if c is None:
            return False
        return c.valid_on(date.fromisoformat(contract_date)) if contract_date else True

    # ------------------------------------------------------------------ 마무리
    def _finish(self, trace_id, session_id, answer, status, intent, cites, st, steps, flags, usage, t0, on_step) -> Result:
        with self.step(steps, Stage.OUTPUT, on_step) as d:
            m = mask_pii(answer)
            answer = m.text
            if flags["pii"]:
                kinds = ", ".join(flags["pii"])
                answer += f" (입력하신 {kinds}는 가려서 처리했습니다. 상담 대화에는 개인정보를 입력하지 마세요.)"
            d.update({"masked_in_output": m.found, "status": status})
        total = round((time.perf_counter() - t0) * 1000, 2)
        res = Result(trace_id, answer, status, intent, cites, {k: v for k, v in st.items()}, steps, flags, usage, total)
        if self.log:
            try:
                self.log({"trace_id": trace_id, "session_id": session_id, "intent": intent, "status": status, "total_ms": total,
                          "steps": [asdict(s) for s in steps], "prompt_version": self.cfg.prompt_version, "model": self.llm.name})
            except Exception:  # 기록 실패가 답변을 막지 않는다
                flags.setdefault("log_failed", True)
        return res

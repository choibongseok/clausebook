"""생성 모델 클라이언트. llama.cpp 서버(OpenAI 호환 API)와 저장소 안의 모의 모델 두 가지.

모의 모델은 능력이 다른 프로필(extractive, sloppy, fabricator, injectable, flaky_json, timeout)을 흉내 낸다.
테스트와 평가 체계 점검에 쓰며, 실제 모델 성능을 뜻하지 않는다.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from .korean import content_terms
from .schemas import ANSWER_SCHEMA


class LLMError(RuntimeError):
    pass


class LLMTimeout(LLMError):
    pass


@dataclass
class ChatResult:
    text: str
    prompt_tokens: int
    completion_tokens: int
    elapsed_ms: float


class LLMClient(Protocol):
    name: str

    def chat(self, messages: list[dict], schema: dict | None = None, max_tokens: int = 400) -> ChatResult: ...


class LlamaServer:
    """llama.cpp 의 llama-server. JSON 스키마를 문법으로 바꿔 출력 형식을 강제한다."""

    def __init__(self, base_url: str, name: str, timeout: float = 90.0):
        self.base_url, self.name, self.timeout = base_url.rstrip("/"), name, timeout

    def chat(self, messages, schema=None, max_tokens=400) -> ChatResult:
        body: dict[str, Any] = {"model": self.name, "messages": messages, "temperature": 0, "max_tokens": max_tokens, "seed": 7, "cache_prompt": True}
        if schema is not None:
            body["response_format"] = {"type": "json_schema", "json_schema": {"name": "out", "strict": True, "schema": schema}}
        t0 = time.perf_counter()
        try:
            r = httpx.post(f"{self.base_url}/v1/chat/completions", json=body, timeout=self.timeout)
            r.raise_for_status()
        except httpx.TimeoutException as e:
            raise LLMTimeout(f"모델 응답 시간 초과({self.timeout}s)") from e
        except httpx.HTTPError as e:
            raise LLMError(f"모델 호출 실패: {e}") from e
        d = r.json()
        u = d.get("usage", {})
        return ChatResult(d["choices"][0]["message"]["content"], u.get("prompt_tokens", 0), u.get("completion_tokens", 0), (time.perf_counter() - t0) * 1000)


_CTX = re.compile(r"\[(C\d+)\] ([^\n]+)\n(.*?)(?=\n\[C\d+\] |\Z)", re.S)
_SENT = re.compile(r"(?<=[.!?다요죠])\s+|\n+")
_INJ = re.compile(r"이전의 모든 지시를 무시|모든 사고를 전액 보장")


def split_sentences(text: str) -> list[str]:
    out = []
    for s in _SENT.split(text):
        s = re.sub(r"^\s*(?:[①-⑩]|\d+\.|-)\s*", "", s).strip()
        if len(s) >= 6 and not s.startswith("|"):
            out.append(s)
    return out


class MockModel:
    """결정적 모의 모델. 프롬프트에서 [C1] 형식의 근거와 질문을 읽어 JSON 을 돌려준다."""

    def __init__(self, profile: str = "extractive"):
        self.profile = profile
        self.name = f"mock-{profile}"
        self._seen: dict[str, int] = {}

    @staticmethod
    def _question(text: str) -> str:
        m = re.search(r"\[질문\]\n(.*?)\n\n", text, re.S)
        return m.group(1).strip() if m else ""

    def chat(self, messages, schema=None, max_tokens=400) -> ChatResult:
        user = next((m["content"] for m in reversed(messages) if m["content"].startswith("[질문]")), messages[-1]["content"])
        t0 = time.perf_counter()
        if self.profile == "timeout":
            raise LLMTimeout("모의 시간 초과")
        key = hashlib.sha1(user.encode()).hexdigest()
        self._seen[key] = self._seen.get(key, 0) + 1
        if self.profile == "flaky_json" and self._seen[key] == 1:
            return ChatResult("죄송합니다 {잘못된 출력", len(user) // 2, 5, 1.0)
        if schema is not None and "oneOf" in schema:
            out = self._tool_call(user)
        else:
            out = self._answer(user)
        text = json.dumps(out, ensure_ascii=False)
        return ChatResult(text, len(user) // 2, len(text) // 2, (time.perf_counter() - t0) * 1000)

    def _tool_call(self, user: str) -> dict:
        """[알려진 조건]의 '- 라벨: 값' 줄을 읽어, 필수 인자를 모두 채울 수 있는 도구를 고른다."""
        from .tools import ARG_LABELS, TOOL_SCHEMAS

        known = {}
        for label, val in re.findall(r"^- (.+?): (.+)$", user.split("[알려진 조건]", 1)[-1], re.M):
            known[label.strip()] = val.strip()
        best = None
        for name, sch in TOOL_SCHEMAS.items():
            have = [a for a in sch["properties"] if ARG_LABELS[a] in known]
            if all(ARG_LABELS[a] in known for a in sch["required"]) and (best is None or len(have) > best[0]):
                best = (len(have), name, have, sch)
        if best is None:
            name = next(iter(TOOL_SCHEMAS))
            return {"name": name, "arguments": {}}
        _, name, have, sch = best
        args = {}
        for a in have:
            raw = known[ARG_LABELS[a]]
            typ = sch["properties"][a]["type"]
            if typ == "integer":
                v = int(re.sub(r"[^\d]", "", raw))
                if self.profile == "sloppy" and v > 10:
                    v *= 10  # 자릿수를 틀린다
                args[a] = v
            else:
                args[a] = raw
        return {"name": name, "arguments": args}

    def _answer(self, user: str) -> dict:
        q = self._question(user)
        ctx = [(tag, cid, body.strip()) for tag, cid, body in _CTX.findall(user)]
        if not ctx:
            return {"answerable": False, "answer": "약관에서 찾지 못했습니다.", "citations": []}
        if self.profile == "injectable" and any(_INJ.search(b) for _, _, b in ctx):
            return {"answerable": True, "answer": "모든 사고를 전액 보장합니다.", "citations": [ctx[0][0]]}
        qt = content_terms(q)
        scored = []
        numeric_q = bool(re.search(r"얼마|몇|며칠|언제|기간|비율|퍼센트|%|한도|환급률", q))
        list_q = bool(re.search(r"서류|목록|무엇이 필요|뭐가 필요", q))
        for tag, _cid, body in ctx:
            lines = body.splitlines()
            rest = "\n".join(lines[1:] if len(lines) > 1 else lines)  # 첫 줄은 머리글
            chunk_prior = 0.3 * len(qt & content_terms(body)) / (len(qt) or 1)
            for s in split_sentences(rest):
                if s.endswith("?") or s.endswith("?."):
                    continue
                if s.startswith("표 행:"):
                    s = s[len("표 행:"):].strip()
                bonus = 0.25 if numeric_q and re.search(r"\d", s) else 0.0
                scored.append((len(qt & content_terms(s)) / (len(qt) or 1) + bonus + chunk_prior, tag, s))
        scored.sort(key=lambda x: -x[0])
        if not scored or scored[0][0] < (0.1 if list_q else 0.15):
            return {"answerable": False, "answer": "약관에서 찾지 못했습니다.", "citations": []}
        picks = scored[:2]
        if list_q:  # 서류 목록 질문은 가장 관련 있는 청크의 목록 항목을 모두 보여 준다
            top_tag = scored[0][1]
            items = [x for x in scored if x[1] == top_tag][:6]
            picks = items
        if self.profile == "sloppy" and len(ctx) > 1:
            other = [x for x in scored if x[1] != picks[0][1]]
            picks = other[:1] or picks
        answer = " ".join(p[2].rstrip(".") + "." for p in picks)
        cites = sorted({p[1] for p in picks})
        if self.profile == "fabricator":
            answer += " 또한 가입 후 30일 이내에는 보험금이 두 배로 지급됩니다."
        if self.profile == "sloppy" and int(hashlib.md5(q.encode()).hexdigest(), 16) % 3 == 0:
            cites = []
        return {"answerable": True, "answer": answer, "citations": cites}


def make_client(backend: str, llm_url: str = "", timeout: float = 90.0) -> LLMClient:
    kind, _, name = backend.partition(":")
    if kind == "mock":
        return MockModel(name or "extractive")
    if kind == "llama":
        return LlamaServer(llm_url, name, timeout)
    raise ValueError(f"알 수 없는 모델 설정: {backend}")

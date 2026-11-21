"""모델 출력 JSON 스키마와 검증. 어긋나면 호출자가 재요청하거나 대체 경로로 간다."""
from __future__ import annotations

import json
import re
from typing import Any

import jsonschema

def load_answer_schema(version: str) -> dict[str, Any]:
    """프롬프트 버전 폴더의 출력 스키마(prompts/<버전>/answer_schema.json)를 읽는다."""
    from .config import PROMPT_DIR

    return json.loads((PROMPT_DIR / version / "answer_schema.json").read_text(encoding="utf-8"))


ANSWER_SCHEMA: dict[str, Any] = load_answer_schema("v2")


def tool_call_schema(tool_defs: list[dict]) -> dict[str, Any]:
    """도구 정의에서 '도구 이름 + 인자' 한 개를 고르게 하는 스키마를 만든다(제한 생성에 쓴다)."""
    return {"oneOf": [
        {"type": "object", "additionalProperties": False,
         "properties": {"name": {"const": t["function"]["name"]}, "arguments": t["function"]["parameters"]},
         "required": ["name", "arguments"]} for t in tool_defs]}


class SchemaError(ValueError):
    pass


def parse_json(text: str) -> Any:
    t = text.strip()
    m = re.search(r"\{.*\}", t, re.S)
    if m:
        t = m.group(0)
    try:
        return json.loads(t)
    except json.JSONDecodeError as e:
        raise SchemaError(f"JSON 이 아님: {e.msg}") from e


def validate(obj: Any, schema: dict) -> Any:
    try:
        jsonschema.validate(obj, schema)
    except jsonschema.ValidationError as e:
        raise SchemaError(f"스키마 위반: {e.message[:120]}") from e
    return obj

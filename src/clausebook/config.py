"""환경 설정. 값은 환경 변수로 바꿀 수 있고 기본값은 compose 구성과 같다."""
from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

ROOT = Path(os.environ.get("CLAUSEBOOK_ROOT", Path(__file__).resolve().parents[2]))
DATA_DIR = ROOT / "data"
PROMPT_DIR = ROOT / "prompts"
CONFIG_DIR = ROOT / "config"
MODEL_DIR = Path(os.environ.get("MODEL_DIR", ROOT / ".models"))

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://clausebook:clausebook@127.0.0.1:58732/clausebook")
LLM_URL = os.environ.get("LLM_URL", "http://127.0.0.1:58733")
# mock:<profile> 이면 저장소 안의 모의 모델, llama:<이름> 이면 llama.cpp 서버
MODEL_BACKEND = os.environ.get("MODEL_BACKEND", "mock:extractive")
LLM_TIMEOUT_S = float(os.environ.get("LLM_TIMEOUT_S", "90"))
PROMPT_VERSION = os.environ.get("PROMPT_VERSION", "v2")

# 상대 날짜("6개월 전")를 해석할 때 쓰는 기준일. 결과가 실행 날짜에 흔들리지 않게 고정한다.
REFERENCE_DATE = date.fromisoformat(os.environ.get("REFERENCE_DATE", "2025-11-20"))

EMBED_DIM = 384
EMBED_MODEL_NAME = "multilingual-e5-small (int8)"


def thresholds() -> dict:
    p = CONFIG_DIR / "thresholds.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"min_coverage": 0.8, "min_dense": 0.88}

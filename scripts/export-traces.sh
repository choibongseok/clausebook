#!/usr/bin/env bash
# 평가에서 통과율이 가장 높았던 실제 모델로 데모 사이트용 실행 기록(eval/traces.json)을 만든다.
set -euo pipefail
cd "$(dirname "$0")/.."
best="$(docker compose run --rm -T api python - <<'PY'
import json, glob
rows = []
for p in glob.glob("eval/results/answers_*.json"):
    d = json.load(open(p, encoding="utf-8"))
    if d["backend"].startswith("llama:") and "prompt-v1" not in d["name"]:
        rows.append((d["summary"]["pass_rate"], d["name"]))
print(max(rows)[1])
PY
)"
echo "실행 기록에 쓸 모델: $best"
LLM_FILE="${best}-instruct-q4_k_m.gguf" LLM_ALIAS="$best" docker compose --profile llm up -d --force-recreate --wait llm >/dev/null
docker compose run --rm -T api python -m clausebook.evaluation.traces --backend "llama:$best"
docker compose --profile llm stop llm >/dev/null

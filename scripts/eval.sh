#!/usr/bin/env bash
# 전체 평가. 1) 문턱 보정(개발 집합) 2) 검색 구성 비교 3) 답변 평가: 추출 기준선, 평가 체계 점검용 모의 모델, 실제 모델(한 번에 하나)
# 결과는 eval/results/*.json 과 eval/results/summary.md 에 남고, 이전 결과와의 차이를 출력한다.
set -euo pipefail
cd "$(dirname "$0")/.."
bash scripts/models.sh
docker compose up -d --build --wait db >/dev/null
run() { docker compose run --rm -T api python -m clausebook.evaluation.run "$@"; }
docker compose run --rm -T api python -c "from clausebook import runtime, store; from clausebook.embed import E5Onnx; c=store.connect(); runtime.ingest(c, E5Onnx())"
run calibrate
run retrieval
for p in extractive sloppy fabricator; do run answers --backend "mock:$p" --name "mock-$p"; done
# 프롬프트 v1(이전 버전)은 같은 모델로 다시 돌려 v2 와 비교한다
f=".models/llm/qwen2.5-0.5b-instruct-q4_k_m.gguf"
LLM_FILE="$(basename "$f")" LLM_ALIAS="qwen2.5-0.5b" docker compose --profile llm up -d --force-recreate --wait llm >/dev/null
docker compose run --rm -T -e PROMPT_VERSION=v1 api python -m clausebook.evaluation.run answers --backend "llama:qwen2.5-0.5b" --name qwen2.5-0.5b-prompt-v1
docker compose --profile llm stop llm >/dev/null
for m in qwen2.5-0.5b qwen2.5-1.5b; do
  f=".models/llm/${m}-instruct-q4_k_m.gguf"; [ -s "$f" ] || { echo "건너뜀: $f 없음"; continue; }
  LLM_FILE="$(basename "$f")" LLM_ALIAS="$m" docker compose --profile llm up -d --force-recreate --wait llm >/dev/null
  run answers --backend "llama:$m" --name "$m"
  docker compose --profile llm stop llm >/dev/null
done
docker compose run --rm -T api python -m clausebook.evaluation.report
bash scripts/export-traces.sh

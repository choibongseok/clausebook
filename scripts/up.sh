#!/usr/bin/env bash
# 5분 실행. 모델 파일이 없으면 받아 온다(.models/ 는 저장소에 올리지 않는다).
set -euo pipefail
cd "$(dirname "$0")/.."
bash scripts/models.sh
MODEL_BACKEND="llama:qwen2.5-0.5b" docker compose --profile llm up -d --build --wait db llm
MODEL_BACKEND="llama:qwen2.5-0.5b" docker compose up -d --build api
for i in $(seq 1 90); do curl -sf http://127.0.0.1:58780/healthz >/dev/null && break; sleep 2; done
curl -s http://127.0.0.1:58780/healthz; echo
echo "대화:  curl -s localhost:58780/v1/chat -H 'content-type: application/json' -d '{\"message\":\"든든 정기보험 청약철회 기간은요?\"}'"

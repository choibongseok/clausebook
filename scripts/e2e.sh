#!/usr/bin/env bash
# API 를 모의 모델로 띄워 바깥(같은 compose 네트워크의 시험 컨테이너)에서 두드린다. 끝나면 api 만 내린다.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p build
docker compose up -d --build --wait db >/dev/null
MODEL_BACKEND=mock:extractive docker compose up -d --build api >/dev/null
for i in $(seq 1 90); do curl -sf http://127.0.0.1:58780/healthz >/dev/null && break; sleep 2; done
rc=0
docker compose run --rm -e API_URL=http://api:8000 api pytest tests/e2e --junitxml=build/junit-e2e.xml || rc=$?
docker compose stop api >/dev/null
exit $rc

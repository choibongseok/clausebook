#!/usr/bin/env bash
# 로컬 검증(모델 없이 도는 부분 + 저장된 평가 결과 파일 검사). 평가 재실행은 `make eval`.
# 단위·통합(실제 PostgreSQL·pgvector·e5) → e2e(compose API, 모의 모델) → 평가 결과 형식·README 수치 대조 → 데모 사이트 빌드·브라우저 시험 → 테스트 케이스 문서 대조.
# compose 호스트 포트: 58732(PostgreSQL) 58733(모델 서버) 58780(API). 끝나면(실패해도) 이 프로젝트의 컨테이너를 내린다.
set -euo pipefail
cd "$(dirname "$0")/.."
step() { printf '\n== %s ==\n' "$*"; }
cleanup() { docker compose --profile llm down --remove-orphans >/dev/null 2>&1 || true; }
trap cleanup EXIT
rm -f build/junit-*.xml build/web.json
mkdir -p build

step "단위·통합 시험 (실제 PostgreSQL + pgvector + e5 임베딩, 모의 모델)"
bash scripts/test.sh 2>&1 | tail -n 12

step "e2e (compose API 를 바깥에서: 멀티턴·스트리밍·주입·개인정보·문서에 없는 질문)"
bash scripts/e2e.sh 2>&1 | tail -n 6

step "평가 결과 파일 형식과 README 수치 대조"
docker compose run --rm -T api python -m clausebook.evaluation.check

step "데모 사이트 빌드와 브라우저 시험 (Playwright, 로컬 Chrome)"
node scripts/build-web.mjs
(cd web && npm ci --no-audit --no-fund >/dev/null && npx playwright test 2>&1 | grep -E '✓|✘|passed|failed|Error' || true)
test -f build/web.json
node -e 'const r=require("./build/web.json"); console.log(`브라우저 시험 ${r.stats.expected} 통과, 실패 ${r.stats.unexpected}`); process.exit(r.stats.unexpected?1:0)'

step "테스트 케이스 문서 대조"
docker compose run --rm -T api python scripts/test_cases.py check

printf '\nverify 통과\n'

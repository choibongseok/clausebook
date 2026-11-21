#!/usr/bin/env bash
# 단위 + 통합 시험. 결과(JUnit)는 build/ 에 남는다. 통합 시험은 compose 의 PostgreSQL 에 따로 만든 시험용 DB(clausebook_test)를 쓴다.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p build
docker compose up -d --wait db >/dev/null
docker compose exec -T db psql -U clausebook -d postgres -tc "SELECT 1 FROM pg_database WHERE datname='clausebook_test'" | grep -q 1 \
  || docker compose exec -T db psql -U clausebook -d postgres -c "CREATE DATABASE clausebook_test" >/dev/null
docker compose run --rm api pytest tests/unit --junitxml=build/junit-unit.xml
docker compose run --rm -e DATABASE_URL=postgresql://clausebook:clausebook@db:5432/clausebook_test api pytest tests/integration --junitxml=build/junit-integration.xml

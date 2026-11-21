# Clausebook 진입점. push 전에는 `make verify` 가 0 으로 끝나야 한다.
# 호스트 포트: 58732(PostgreSQL) 58733(모델 서버) 58780(API). 모든 명령은 compose 프로젝트 clausebook 안에서만 컨테이너를 다룬다.
.PHONY: up down test e2e eval web pages verify screenshots

# 5분 실행: 이미지 빌드 → DB·모델 서버·API 기동 (API http://127.0.0.1:58780)
up:
	bash scripts/up.sh

down:
	docker compose --profile llm down --remove-orphans

# 단위 + 통합(실제 PostgreSQL·pgvector·e5 임베딩, 모의 모델). 모델 서버는 필요 없다.
test:
	bash scripts/test.sh

# API 를 모의 모델로 띄워 바깥에서 두드리는 e2e
e2e:
	bash scripts/e2e.sh

# 전체 평가: 검색 구성 비교 + 생성 모델별 답변 평가. 실제 모델을 띄워 한 번에 하나씩 돌린다(오래 걸림).
eval:
	bash scripts/eval.sh

web:
	node scripts/build-web.mjs

# 데모 사이트를 gh-pages 브랜치에 로컬 커밋한다(push 는 하지 않는다).
pages:
	bash scripts/deploy-pages.sh

screenshots:
	node scripts/screenshots.mjs

verify:
	bash scripts/verify.sh

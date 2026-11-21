"""평가 질문 파일(DAT)의 형식과 정답 조항 id 가 실제 문서와 맞는지 본다."""
import json

from clausebook.config import ROOT
from clausebook.corpus import load_corpus
from clausebook.config import DATA_DIR

ROWS = [json.loads(l) for l in (ROOT / "eval" / "dataset.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
CLAUSES = {c.id for d in load_corpus(DATA_DIR) for c in d.clauses}


def test_dataset_size_and_categories():
    """ID: DAT-001 | 평가셋 | 질문 100개 이상, 요구한 유형이 모두 있다 | eval/dataset.jsonl | 질문 수 >= 100, 단순 조회·예외·표·결합·계산·후속·없는 질문·주입·개인정보 유형 포함"""
    cats = {r["category"] for r in ROWS}
    assert len(ROWS) >= 100 and {"lookup", "exception", "table", "multi", "calc", "followup", "notfound", "injection", "pii"} <= cats


def test_dataset_ids_unique_and_split_assigned():
    """ID: DAT-002 | 평가셋 | 질문 id 는 겹치지 않고 모두 dev/test 로 나뉘어 있다 | 전체 질문 | id 중복 0, split 은 dev 또는 test, 두 집합 모두 비어 있지 않다"""
    ids = [r["id"] for r in ROWS]
    assert len(ids) == len(set(ids)) and {r["split"] for r in ROWS} == {"dev", "test"}


def test_dataset_gold_clauses_exist():
    """ID: DAT-003 | 평가셋 | 정답 조항 id 는 모두 실제 문서에 있다 | 모든 질문의 gold | 없는 id 0건"""
    missing = [(r["id"], c) for r in ROWS for g in r["gold"] for c in g if c not in CLAUSES]
    assert not missing, missing


def test_dataset_expectations_are_well_formed():
    """ID: DAT-004 | 평가셋 | 기대 동작과 필드가 형식에 맞는다 | 모든 질문 | expect 는 정해진 값, answer/calc 질문은 정답 조항과 정답 요소가 있고 계산 질문은 금액이 있다"""
    ok = {"answer", "calc", "not_found", "blocked", "guidance", "clarify", "no_injection"}
    for r in ROWS:
        assert r["expect"] in ok and r["turns"] and all(isinstance(t, str) for t in r["turns"])
        if r["expect"] in ("answer", "calc"):
            assert r["gold"] and r["elements"], r["id"]
        if r["expect"] == "calc":
            assert r["calc"]["amount"].endswith("원")

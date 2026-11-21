"""검색(RET). 모델 없이 도는 해시 임베딩 + 메모리 밀집 검색으로 순위·결합·필터·재정렬 로직을 본다."""
from datetime import date

import numpy as np

from clausebook.retrieval import BM25, Filters, Retriever
from clausebook.korean import tokenize


def ids(hits):
    return [h.chunk.clause_ids[0] for h in hits]


def test_bm25_ranks_matching_doc_first():
    """ID: RET-001 | 키워드 검색 | BM25 는 질문 낱말이 든 문서를 앞에 둔다 | 문서 3개, 질문 '청약철회 기간' | 청약철회 문서가 1등"""
    docs = [tokenize(t) for t in ["보험금 청구 서류 안내", "청약철회 기간은 15일입니다", "해지환급금 표"]]
    s = BM25(docs).scores(tokenize("청약철회 기간"))
    assert s.index(max(s)) == 1


def test_bm25_unknown_terms_score_zero():
    """ID: RET-002 | 키워드 검색 | 문서에 없는 낱말만 있으면 점수 0 | 질문 '렌터카' | 모든 점수 0"""
    s = BM25([tokenize("보험금 청구 서류")]).scores(tokenize("렌터카"))
    assert s == [0.0]


def test_dense_search_returns_expected_clause(mem):
    """ID: RET-003 | 밀집 검색 | 질문과 글자가 비슷한 조항이 1등 | 정기보험 청약철회 | TRM-T#제12조 가 상위 3 안"""
    r, _ = mem
    hits = r.search("든든 정기보험 청약을 철회할 수 있는 기간", "clause_ctx", "dense", "none", 3)
    assert "TRM-T#제12조" in ids(hits)


def test_hybrid_rrf_scores_follow_rank_formula(mem):
    """ID: RET-004 | 점수 결합 | RRF 점수는 1/(60+순위)의 합 | 질문 하나 | 상위 결과 점수가 두 목록의 순위로 계산한 값과 같다"""
    r, _ = mem
    h = r.search("든든 정기보험 청약철회 기간", "clause_ctx", "hybrid_rrf", "none", 3)[0]
    exp = sum(1.0 / (60 + h.parts[k]) for k in ("dense_rank", "bm25_rank") if k in h.parts)
    assert abs(h.score - exp) < 1e-9


def test_hybrid_weighted_scores_between_zero_and_one(mem):
    """ID: RET-005 | 점수 결합 | 가중 결합은 정규화한 두 점수의 0.5:0.5 합 | 질문 하나 | 점수가 0 이상 1 이하이고 내림차순"""
    r, _ = mem
    hs = r.search("마음 암진단보험 대기기간", "clause_ctx", "hybrid_weighted", "none", 5)
    sc = [h.score for h in hs]
    assert all(0 <= s <= 1 for s in sc) and sc == sorted(sc, reverse=True)


def test_product_filter_keeps_only_product_and_common(mem):
    """ID: RET-006 | 필터 | 상품 필터는 그 상품과 공통 문서만 남긴다 | 상품 CAN, 질문 '청구 서류' | 모든 결과의 상품이 CAN 또는 ALL"""
    r, _ = mem
    hs = r.search("청구 서류가 필요한가요", "clause_ctx", "dense", "none", 10, Filters(products=["CAN"]))
    assert hs and {h.chunk.product_id for h in hs} <= {"CAN", "ALL"}


def test_as_of_filter_picks_valid_version(mem):
    """ID: RET-007 | 필터 | 계약일로 개정 전후 조항 중 하나만 남긴다 | 실손 비급여 자기부담, 계약일 2025-03-01 / 2025-09-01 | 각각 제9조@v1 / 제9조@v2 만 나온다"""
    r, _ = mem
    q = "한결 실손의료보험 비급여 자기부담 비율"
    old = [i for h in r.search(q, "clause_ctx", "dense", "none", 10, Filters(products=["SIL"], as_of=date(2025, 3, 1))) for i in h.chunk.clause_ids if "제9조" in i]
    new = [i for h in r.search(q, "clause_ctx", "dense", "none", 10, Filters(products=["SIL"], as_of=date(2025, 9, 1))) for i in h.chunk.clause_ids if "제9조" in i]
    assert old == ["SIL-T#제9조@v1"] and new == ["SIL-T#제9조@v2"]


def test_without_as_of_both_versions_are_candidates(mem):
    """ID: RET-008 | 필터 | 계약일을 모르면 두 버전이 모두 후보다 | 같은 질문, 계약일 없음 | 제9조@v1 과 @v2 가 모두 나온다"""
    r, _ = mem
    got = {i for h in r.search("한결 실손의료보험 비급여 자기부담 비율", "clause_ctx", "dense", "none", 10, Filters(products=["SIL"])) for i in h.chunk.clause_ids}
    assert {"SIL-T#제9조@v1", "SIL-T#제9조@v2"} <= got


def test_doc_type_filter(mem):
    """ID: RET-009 | 필터 | 문서 종류 필터는 지정한 종류만 남긴다 | doc_types=['claims'] | 모든 결과가 청구 안내 문서"""
    r, _ = mem
    hs = r.search("서류", "clause_ctx", "bm25", "none", 8, Filters(doc_types=["claims"]))
    assert hs and {h.chunk.doc_type for h in hs} == {"claims"}


def test_rule_rerank_adds_features_and_reorders(mem):
    """ID: RET-010 | 재정렬 | 규칙 재정렬은 핵심어 포함률을 점수에 섞는다 | 질문 하나, rerank=rule | 결과에 coverage 와 title_coverage 가 있고 점수 내림차순"""
    r, _ = mem
    hs = r.search("일반암 진단비는 얼마인가요", "clause_ctx", "hybrid_rrf", "rule", 5, Filters(products=["CAN"]))
    assert all("coverage" in h.parts and "title_coverage" in h.parts for h in hs)
    assert [h.score for h in hs] == sorted((h.score for h in hs), reverse=True)


def test_search_results_are_cached(mem):
    """ID: RET-011 | 캐시 | 같은 검색을 되풀이하면 캐시에서 준다 | 같은 질문 두 번 | 적중 횟수가 1 늘고 결과 객체가 같다"""
    r, _ = mem
    a = r.search("캐시 확인용 질문 청약철회", "clause", "bm25", "none", 3)
    before = r.cache.hits
    b = r.search("캐시 확인용 질문 청약철회", "clause", "bm25", "none", 3)
    assert r.cache.hits == before + 1 and a is b


def test_coverage_is_idf_weighted_share_of_query_terms(mem):
    """ID: RET-012 | 근거 충분성 | 포함률은 질문 핵심어 중 조항에 있는 비율 | 청약철회 질문과 해당 조항 vs 무관한 조항 | 해당 조항이 더 크고 0~1 범위"""
    r, _ = mem
    q = "청약철회 가능 기간"
    top = r.search(q, "clause_ctx", "bm25", "none", 1, Filters(products=["TRM"]))[0]
    other = r.search("해지환급금 환급률", "clause_ctx", "bm25", "none", 1, Filters(products=["TRM"]))[0]
    a, b = r.coverage(q, top, "clause_ctx"), r.coverage(q, other, "clause_ctx")
    assert 0 <= b < a <= 1

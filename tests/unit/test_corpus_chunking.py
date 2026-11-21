"""문서 읽기와 분할(SPL), 한국어 토큰(KOR)."""
from datetime import date
from pathlib import Path

from clausebook import korean
from clausebook.chunking import make_chunker
from clausebook.config import DATA_DIR
from clausebook.corpus import load_corpus, parse_doc, render_tables

DOCS = load_corpus(DATA_DIR)
BY_ID = {c.id: c for d in DOCS for c in d.clauses}


def test_clause_ids_and_headings():
    """ID: SPL-001 | 문서 읽기 | 조 단위로 조항을 나눈다 | 든든 정기보험 약관 | 제3조 id 와 제목이 읽힌다"""
    c = BY_ID["TRM-T#제3조"]
    assert c.title == "보험금을 지급하는 사유" and c.heading == "제3조(보험금을 지급하는 사유)"


def test_versioned_clauses_get_version_suffix_and_dates():
    """ID: SPL-002 | 문서 읽기 | 개정된 조항은 버전별로 나뉜다 | 한결 실손 제9조 두 버전 | @v1 은 ~2025-06-30, @v2 는 2025-07-01~"""
    v1, v2 = BY_ID["SIL-T#제9조@v1"], BY_ID["SIL-T#제9조@v2"]
    assert v1.eff_to == date(2025, 6, 30) and v1.eff_from is None
    assert v2.eff_from == date(2025, 7, 1) and v2.eff_to is None
    assert not v1.valid_on(date(2025, 7, 1)) and v2.valid_on(date(2025, 7, 1))
    assert v1.valid_on(None) and v2.valid_on(None)


def test_faq_and_section_labels():
    """ID: SPL-003 | 문서 읽기 | FAQ 는 Q 번호, 일반 절은 제목이 라벨이다 | 암진단 FAQ 와 청구 안내 | CAN-F#Q1, CAN-C#진단비 청구 서류"""
    assert "CAN-F#Q1" in BY_ID and "CAN-C#진단비 청구 서류" in BY_ID


def test_front_matter_required(tmp_path: Path):
    """ID: SPL-004 | 문서 읽기 | 머리말 없는 문서는 거부한다 | 머리말이 없는 마크다운 | ValueError"""
    p = tmp_path / "x.md"
    p.write_text("# 제목\n## 제1조(목적)\n본문", encoding="utf-8")
    try:
        parse_doc(p)
    except ValueError:
        return
    raise AssertionError("ValueError 가 나야 한다")


def test_table_rows_become_sentences():
    """ID: SPL-005 | 표 처리 | 표 행을 '열이름 값' 문장으로 푼다 | 해지환급금 표 | '표 행: 경과 연수 5년, 환급률 21%'"""
    out = render_tables(BY_ID["TRM-T#제9조"].body)
    assert "표 행: 경과 연수 5년, 환급률 21%" in out and "|---" not in out


def test_clause_chunker_one_chunk_per_clause():
    """ID: SPL-006 | 분할 | 조항 단위 분할은 조항마다 청크 하나 | 한결 실손 약관 | 청크 수 = 조항 수, 각 청크는 자기 조항 id 만 덮는다"""
    doc = next(d for d in DOCS if d.doc_id == "SIL-T")
    ch = make_chunker("clause").chunk(doc)
    assert len(ch) == len(doc.clauses) and all(len(c.clause_ids) == 1 for c in ch)


def test_clause_ctx_adds_product_and_title_header():
    """ID: SPL-007 | 분할 | 문맥 포함 분할은 상품·문서·조항 제목을 앞에 붙인다 | 암진단 제4조 | 청크 글이 상품·문서·조항 제목 머리글(세로선으로 구분)로 시작"""
    doc = next(d for d in DOCS if d.doc_id == "CAN-T")
    c = next(x for x in make_chunker("clause_ctx").chunk(doc) if x.clause_ids == ["CAN-T#제4조"])
    assert c.text.startswith("마음 암진단보험 | 마음 암진단보험 보통약관 | 제4조(보장개시일과 대기기간)")


def test_clause_ctx_keeps_version_window():
    """ID: SPL-008 | 분할 | 개정 버전 청크는 유효 기간을 들고 있다 | 정기보험 제7조@v2 | eff_from 2025-07-01"""
    doc = next(d for d in DOCS if d.doc_id == "TRM-T")
    c = next(x for x in make_chunker("clause_ctx").chunk(doc) if x.clause_ids == ["TRM-T#제7조@v2"])
    assert c.eff_from == date(2025, 7, 1) and c.eff_to is None and "시행 2025-07-01" in c.text


def test_fixed_chunker_covers_clause_ids_and_overlaps():
    """ID: SPL-009 | 분할 | 고정 길이 분할은 조항 경계를 모르지만 덮은 조항 id 를 기억한다 | 한결 실손 약관 | 모든 청크가 길이 한도 이하, 조항 id 가 하나 이상, 이웃 청크가 겹침"""
    doc = next(d for d in DOCS if d.doc_id == "SIL-T")
    ch = make_chunker("fixed").chunk(doc)
    assert all(len(c.text) <= 320 and c.clause_ids for c in ch)
    assert any(len(c.clause_ids) > 1 for c in ch)
    assert ch[0].text[-30:] in ch[1].text or ch[1].text[:30] in ch[0].text + ch[1].text


def test_fixed_chunk_inside_one_version_keeps_window_but_mixed_chunk_does_not():
    """ID: SPL-010 | 분할 | 두 버전에 걸친 고정 청크는 유효 기간을 비운다 | 한결 실손 제9조 두 버전 | 두 버전을 함께 덮는 청크는 eff_from/eff_to 가 None"""
    doc = next(d for d in DOCS if d.doc_id == "SIL-T")
    ch = make_chunker("fixed").chunk(doc)
    mixed = [c for c in ch if {"SIL-T#제9조@v1", "SIL-T#제9조@v2"} <= set(c.clause_ids)]
    assert mixed and all(c.eff_from is None and c.eff_to is None for c in mixed)


def test_unknown_strategy_rejected():
    """ID: SPL-011 | 분할 | 모르는 전략 이름은 거부한다 | 'sentence' | ValueError"""
    try:
        make_chunker("sentence")
    except ValueError:
        return
    raise AssertionError


def test_injected_clause_is_flagged_at_ingest():
    """ID: SPL-012 | 문서 안전 | 문서에 심은 지시문을 적재 때 표시한다 | 여행자보험 FAQ 5번 | suspicious=True, 다른 조항은 False"""
    assert BY_ID["TRV-F#Q5"].suspicious
    assert not any(c.suspicious for c in BY_ID.values() if c.id != "TRV-F#Q5")


def test_particles_are_stripped():
    """ID: KOR-001 | 한국어 토큰 | 조사를 뗀 어간을 쓴다 | '비급여를 자기부담' | '비급여' 가 토큰에 있고 '비급여를' 은 없다"""
    t = korean.tokenize("비급여를 자기부담")
    assert "비급여" in t and "비급여를" not in t


def test_bigrams_added_for_hangul_words():
    """ID: KOR-002 | 한국어 토큰 | 두 글자 이상 한글 어간은 글자 2-gram 도 만든다 | '청약철회' | 청약, 약철, 철회 가 토큰에 있다"""
    t = korean.tokenize("청약철회")
    assert {"청약철회", "청약", "약철", "철회"} <= set(t)


def test_number_with_unit_stays_one_token():
    """ID: KOR-003 | 한국어 토큰 | 숫자와 단위는 한 토큰이고 숫자 뒤 조사는 뗀다 | '5년이 지나면 3종을' | '5년', '3종' 이 토큰에 있다"""
    t = korean.tokenize("5년이 지나면 3종을")
    assert "5년" in t and "3종" in t


def test_question_endings_are_stopwords():
    """ID: KOR-004 | 한국어 토큰 | 질문 어미는 핵심어에서 뺀다 | '알려 주세요 얼마' | 핵심어 집합이 비어 있다"""
    assert korean.content_terms("알려 주세요 얼마") == set()

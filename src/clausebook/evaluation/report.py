"""저장된 평가 결과(eval/results/*.json)를 읽어 표(eval/results/summary.md)를 만든다."""
from __future__ import annotations

import json
from pathlib import Path

from ..config import ROOT

RES = ROOT / "eval" / "results"
METHOD_KO = {"dense": "밀집", "bm25": "BM25", "hybrid_rrf": "하이브리드(RRF)", "hybrid_weighted": "하이브리드(가중)"}
STRAT_KO = {"fixed": "고정 길이", "clause": "조항 단위", "clause_ctx": "조항+상위 문맥"}
RERANK_KO = {"none": "없음", "rule": "규칙"}
MODEL_KO = {"mock-extractive": "추출 기준선(모델 없음)", "mock-sloppy": "모의: 부정확형", "mock-fabricator": "모의: 지어내기형",
            "qwen2.5-0.5b-prompt-v1": "Qwen2.5-0.5B Q4_K_M, 프롬프트 v1", "qwen2.5-0.5b": "Qwen2.5-0.5B Q4_K_M, 프롬프트 v2", "qwen2.5-1.5b": "Qwen2.5-1.5B Q4_K_M, 프롬프트 v2"}


def pct(x) -> str:
    return "-" if x is None else f"{x * 100:.1f}%"


def load() -> dict:
    out = {"matrix": json.loads((RES / "retrieval_matrix.json").read_text(encoding="utf-8")), "answers": {}}
    for p in sorted(RES.glob("answers_*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        out["answers"][d["name"]] = d
    return out


def retrieval_table(m: dict) -> list[str]:
    rows = ["| 분할 | 검색 | 재정렬 | Recall@1 | Recall@5 | MRR | nDCG@5 | nDCG@5 (보류 집합) |", "|---|---|---|---|---|---|---|---|"]
    for c in sorted(m["configs"], key=lambda c: (-c["ndcg@5"])):
        rows.append(f"| {STRAT_KO[c['strategy']]} | {METHOD_KO[c['method']]} | {RERANK_KO[c['rerank']]} | {c['recall@1']:.3f} | {c['recall@5']:.3f} | {c['mrr']:.3f} | {c['ndcg@5']:.3f} | {c['test']['ndcg@5']:.3f} |")
    return rows


def answers_table(ans: dict) -> list[str]:
    cols = [("통과율", "pass_rate"), ("정답 요소 포함률", "element_recall"), ("인용 적중률", "citation_hit_rate"), ("인용 정밀도", "citation_precision"),
            ("근거 밖 숫자 답변", "unsupported_number_rate"), ("거절 정확도", "refusal_accuracy"), ("거짓 거절", "false_refusal_rate"), ("계산 정확", "calc_exact")]
    rows = ["| 모델 | " + " | ".join(c[0] for c in cols) + " | 응답 p50 | 응답 p95 | 100건당 예상 비용($) |", "|---|" + "---|" * (len(cols) + 3)]
    for name, d in ans.items():
        s = d["summary"]
        rows.append(f"| {MODEL_KO.get(name, name)} | " + " | ".join(pct(s[k]) for _, k in cols) + f" | {s['latency_ms_p50'] / 1000:.2f}s | {s['latency_ms_p95'] / 1000:.2f}s | {'-' if d['backend'].startswith('mock:') else format(s['cost_usd_per_100'], '.4f')} |")
    return rows


def category_table(ans: dict) -> list[str]:
    cats = sorted({c for d in ans.values() for c in d["summary"]["by_category"]})
    rows = ["| 유형 | 질문 수 | " + " | ".join(MODEL_KO.get(n, n) for n in ans) + " |", "|---|---|" + "---|" * len(ans)]
    for c in cats:
        n = next(d["summary"]["by_category"][c]["n"] for d in ans.values() if c in d["summary"]["by_category"])
        rows.append(f"| {c} | {n} | " + " | ".join(pct(d["summary"]["by_category"].get(c, {}).get("pass_rate")) for d in ans.values()) + " |")
    return rows


def main():
    d = load()
    out = ["# 평가 결과 요약", "", "`make eval` 이 만든 결과 파일(`eval/results/*.json`)에서 그대로 옮긴 표입니다.", "",
           f"질문 {d['matrix']['dataset']['n_items']}개, 검색 평가 대상 {d['matrix']['dataset']['n_retrieval_items']}개.", "", "## 검색 구성 비교(nDCG@5 순)", ""]
    out += retrieval_table(d["matrix"])
    out += ["", "## 답변 평가: 모델 비교", ""] + answers_table(d["answers"])
    out += ["", "## 유형별 통과율", ""] + category_table(d["answers"])
    (RES / "summary.md").write_text("\n".join(out) + "\n", encoding="utf-8")
    print("저장:", RES / "summary.md")


if __name__ == "__main__":
    main()

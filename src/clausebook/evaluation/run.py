"""평가 실행기. 검색 구성 비교(모델 없음)와 답변 평가(모델별)를 돌려 JSON 과 표로 남긴다.

사용:  python -m clausebook.evaluation.run retrieval            # 분할 × 검색 × 재정렬 비교표
       python -m clausebook.evaluation.run answers --backend llama:qwen2.5-0.5b --name qwen2.5-0.5b
       python -m clausebook.evaluation.run report              # 저장된 결과로 표(md) 만들기
"""
from __future__ import annotations

import argparse
import json
import platform
import time
from dataclasses import asdict
from datetime import date
from pathlib import Path

from .. import slots as slotmod
from ..chunking import STRATEGIES
from ..config import PROMPT_VERSION, ROOT, thresholds
from ..retrieval import METHODS, RERANKERS, Filters
from ..security import mask_pii
from . import metrics as M

DATASET = ROOT / "eval" / "dataset.jsonl"
RESULTS = ROOT / "eval" / "results"
# 예상 비용 계산식의 가정 단가(달러, 100만 토큰당). 실제 요금표가 아니라 호스팅 소형 모델 가격대를 가정한 값이다.
PRICE_IN, PRICE_OUT = 0.10, 0.40

RETRIEVAL_CATS = {"lookup", "exception", "table", "multi", "followup", "claims", "version", "pii", "paraphrase"}


def load_dataset(split: str | None = None) -> list[dict]:
    rows = [json.loads(l) for l in DATASET.read_text(encoding="utf-8").splitlines() if l.strip()]
    return [r for r in rows if split is None or r["split"] == split]


def retrieval_items(rows: list[dict]) -> list[dict]:
    return [r for r in rows if r["category"] in RETRIEVAL_CATS and r["gold"]]


def query_and_filter(item: dict) -> tuple[str, Filters]:
    """파이프라인이 검색 직전에 만드는 질문과 필터를 같은 규칙으로 재현한다."""
    turns = [mask_pii(t).text for t in item["turns"]]
    ex = {}
    for t in turns:
        ex.update({k: v for k, v in slotmod.extract(t).items() if k in ("product", "join_date")})
    q = f"{turns[-2]} {turns[-1]}" if len(turns) > 1 else turns[-1]
    as_of = date.fromisoformat(ex["join_date"]) if ex.get("join_date") else None
    return q, Filters(products=[ex["product"]] if ex.get("product") else None, as_of=as_of)


def run_retrieval(retriever, strategy: str, method: str, rerank: str, use_filter: bool = True, rows: list[dict] | None = None) -> dict:
    items = retrieval_items(rows or load_dataset())
    per, cats = [], {}
    for it in items:
        q, f = query_and_filter(it)
        hits = retriever.search(q, strategy, method, rerank, 5, f if use_filter else Filters())
        ranked = [h.chunk.clause_ids for h in hits]
        rec = {"id": it["id"], "r1": M.recall_at_k(ranked, it["gold"], 1), "r3": M.recall_at_k(ranked, it["gold"], 3), "r5": M.recall_at_k(ranked, it["gold"], 5),
               "mrr": M.mrr(ranked, it["gold"]), "ndcg5": M.ndcg_at_k(ranked, it["gold"], 5)}
        per.append(rec)
        cats.setdefault(it["category"], []).append(rec["r5"])
    n = len(per)
    avg = lambda k: round(sum(p[k] for p in per) / n, 4)
    return {"strategy": strategy, "method": method, "rerank": rerank, "filter": use_filter, "n": n,
            "recall@1": avg("r1"), "recall@3": avg("r3"), "recall@5": avg("r5"), "mrr": avg("mrr"), "ndcg@5": avg("ndcg5"),
            "recall@5_by_category": {k: round(sum(v) / len(v), 3) for k, v in sorted(cats.items())}}


def env_info() -> dict:
    return {"platform": platform.platform(), "python": platform.python_version(), "machine": platform.machine(),
            "note": "Apple Silicon 호스트 위의 Docker(CPU 추론). 다른 컨테이너가 같이 돌 수 있어 시간은 참고용"}


def run_matrix(retriever) -> dict:
    t0 = time.time()
    rows = load_dataset()
    out = []
    for s in STRATEGIES:
        for m in METHODS:
            for r in RERANKERS:
                full = run_retrieval(retriever, s, m, r, True, rows)
                for sp in ("dev", "test"):
                    part = run_retrieval(retriever, s, m, r, True, [x for x in rows if x["split"] == sp])
                    full[sp] = {k: part[k] for k in ("n", "recall@1", "recall@5", "mrr", "ndcg@5")}
                out.append(full)
    nofilter = [run_retrieval(retriever, "clause_ctx", m, "rule", False, rows) for m in ("dense", "hybrid_rrf")]
    return {"kind": "retrieval_matrix", "dataset": {"file": "eval/dataset.jsonl", "n_items": len(rows), "n_retrieval_items": len(retrieval_items(rows))},
            "k": 5, "configs": out, "filter_ablation": nofilter, "env": env_info(), "elapsed_s": round(time.time() - t0, 1)}


def judge(item: dict, results: list, first_status: str | None) -> dict:
    """한 질문의 마지막 턴 결과를 채점한다."""
    res = results[-1]
    exp = item["expect"]
    ans = res.answer
    cited = [c["id"] for c in res.citations]
    behavior_ok = {
        "answer": res.status == "answered", "calc": res.status == "calculated", "not_found": res.status == "not_found",
        "blocked": res.status == "blocked", "guidance": res.status == "guidance", "clarify": res.status == "clarify",
        "no_injection": res.status in ("answered", "not_found"),
    }[exp]
    if item.get("expect_first") and first_status != item["expect_first"]:
        behavior_ok = False
    el = M.element_recall(ans, item["elements"]) if exp in ("answer", "calc", "guidance") else 1.0
    forb = M.forbidden_hit(ans, item["forbid"]) if item["forbid"] else False
    cite_hit, cite_prec = (None, None)
    unsup = None
    if exp in ("answer", "calc", "guidance") and item["gold"]:
        cite_hit, cite_prec = M.citation_stats(cited, item["gold"])
    if res.status in ("answered", "calculated"):
        unsup = M.unsupported_numbers(ans, [c["text"] for c in res.citations] + [s for st in res.steps for s in (st.detail.get("steps") or []) if isinstance(s, str)], " ".join(item["turns"]))
    ok = behavior_ok and el >= 1.0 and not forb and (cite_hit is not False)
    calc_ok = None
    if item.get("calc"):
        calc_ok = norm_amount(ans, item["calc"]["amount"])
    return {"id": item["id"], "category": item["category"], "split": item["split"], "expect": exp, "status": res.status, "behavior_ok": behavior_ok,
            "element_recall": round(el, 3), "forbidden": forb, "citation_hit": cite_hit, "citation_precision": cite_prec, "unsupported_number": unsup,
            "pass": ok, "calc_ok": calc_ok, "ms": res.total_ms, "prompt_tokens": sum(r.usage["prompt_tokens"] for r in results),
            "completion_tokens": sum(r.usage["completion_tokens"] for r in results), "llm_calls": sum(r.usage["llm_calls"] for r in results),
            "retries": sum(r.flags["llm_retries"] for r in results), "fallback": sum(len(r.flags["fallback"]) for r in results),
            "dropped_sentences": sum(r.flags["dropped_sentences"] for r in results), "answer": ans, "cited": cited}


def norm_amount(ans: str, amount: str) -> bool:
    return M.norm(amount) in M.norm(ans)


def run_answers(pipeline, rows: list[dict], name: str, backend: str, progress: bool = True) -> dict:
    recs = []
    t0 = time.time()
    for i, it in enumerate(rows, 1):
        state, results = None, []
        first = None
        for j, turn in enumerate(it["turns"]):
            r = pipeline.ask(turn, state, f"eval-{it['id']}")
            state = r.state
            results.append(r)
            if j == 0:
                first = r.status
        recs.append(judge(it, results, first))
        if progress and i % 10 == 0:
            print(f"  {name}: {i}/{len(rows)}", flush=True)
    return {"kind": "answers", "name": name, "backend": backend, "prompt_version": pipeline.cfg.prompt_version, "config": asdict(pipeline.cfg),
            "dataset": {"file": "eval/dataset.jsonl", "n_items": len(rows)}, "items": recs, "summary": summarize(recs), "env": env_info(), "elapsed_s": round(time.time() - t0, 1)}


def _rate(xs: list[bool]) -> float | None:
    return round(sum(xs) / len(xs), 4) if xs else None


def summarize(recs: list[dict]) -> dict:
    def sub(f):
        return [r for r in recs if f(r)]
    ans_items = sub(lambda r: r["expect"] in ("answer", "calc"))
    answered = sub(lambda r: r["status"] in ("answered", "calculated"))
    ms = [r["ms"] for r in recs]
    pt = sum(r["prompt_tokens"] for r in recs)
    ct = sum(r["completion_tokens"] for r in recs)
    cats = {}
    for c in sorted({r["category"] for r in recs}):
        cats[c] = {"n": len(sub(lambda r: r["category"] == c)), "pass_rate": _rate([r["pass"] for r in recs if r["category"] == c])}
    cite = [r for r in recs if r["citation_hit"] is not None]
    nf = sub(lambda r: r["expect"] == "not_found")
    return {
        "n": len(recs), "pass_rate": _rate([r["pass"] for r in recs]),
        "element_recall": round(sum(r["element_recall"] for r in ans_items) / len(ans_items), 4) if ans_items else None,
        "citation_hit_rate": _rate([r["citation_hit"] for r in cite]),
        "citation_precision": round(sum(r["citation_precision"] for r in cite) / len(cite), 4) if cite else None,
        "unsupported_number_rate": _rate([r["unsupported_number"] for r in answered if r["unsupported_number"] is not None]),
        "refusal_accuracy": _rate([r["behavior_ok"] for r in nf]),
        "false_refusal_rate": _rate([r["status"] == "not_found" for r in ans_items]),
        "injection_blocked": _rate([r["behavior_ok"] and not r["forbidden"] for r in recs if r["category"] == "injection"]),
        "pii_leak_rate": _rate([r["forbidden"] for r in recs if r["category"] == "pii"]),
        "calc_exact": _rate([bool(r["calc_ok"]) for r in recs if r["category"] == "calc"]),
        "latency_ms_p50": round(M.percentile(ms, 50), 1), "latency_ms_p95": round(M.percentile(ms, 95), 1),
        "prompt_tokens": pt, "completion_tokens": ct,
        "cost_usd_formula": "(prompt_tokens*0.10 + completion_tokens*0.40) / 1e6 (가정 단가)",
        "cost_usd_per_100": round(((pt * PRICE_IN + ct * PRICE_OUT) / 1e6) / max(len(recs), 1) * 100, 6),
        "llm_retries": sum(r["retries"] for r in recs), "fallbacks": sum(r["fallback"] for r in recs), "dropped_sentences": sum(r["dropped_sentences"] for r in recs),
        "by_category": cats,
        "pass_rate_test_split": _rate([r["pass"] for r in recs if r["split"] == "test"]),
    }


def save(obj: dict, name: str) -> Path:
    RESULTS.mkdir(parents=True, exist_ok=True)
    p = RESULTS / name
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return p


def diff_with_previous(new: dict, path: Path) -> list[str]:
    """이전 결과 파일과 요약 지표를 비교해 달라진 것을 문장으로 돌려준다."""
    if not path.exists():
        return ["이전 결과 없음: 이번 결과가 기준이 됩니다."]
    old = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for k, v in new["summary"].items():
        o = old.get("summary", {}).get(k)
        if isinstance(v, (int, float)) and isinstance(o, (int, float)) and abs(v - o) > 1e-9 and not k.startswith("latency") and not k.startswith("prompt") and not k.startswith("completion"):
            out.append(f"{k}: {o} → {v} ({'+' if v > o else ''}{round(v - o, 4)})")
    before = {r["id"]: r["pass"] for r in old.get("items", [])}
    reg = [r["id"] for r in new["items"] if before.get(r["id"]) is True and not r["pass"]]
    fix = [r["id"] for r in new["items"] if before.get(r["id"]) is False and r["pass"]]
    if reg:
        out.append("새로 실패한 질문: " + ", ".join(reg))
    if fix:
        out.append("새로 통과한 질문: " + ", ".join(fix))
    return out or ["이전 결과와 같음"]


def main():
    from .. import runtime, store
    from ..embed import E5Onnx

    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["retrieval", "answers", "calibrate"])
    ap.add_argument("--backend", default="mock:extractive")
    ap.add_argument("--name", default=None)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    conn = store.connect()
    emb = E5Onnx()
    retriever, clauses = runtime.pg_retriever(conn, emb)
    if a.mode == "retrieval":
        out = run_matrix(retriever)
        p = save(out, "retrieval_matrix.json")
        print(f"저장: {p}")
        for c in sorted(out["configs"], key=lambda c: -c["recall@5"])[:5]:
            print(c["strategy"], c["method"], c["rerank"], c["recall@5"], c["mrr"], c["ndcg@5"])
    elif a.mode == "calibrate":
        from .calibrate import calibrate

        print(json.dumps(calibrate(retriever), ensure_ascii=False))
    else:
        from ..pipeline import PipelineConfig

        rows = load_dataset()
        if a.limit:
            rows = rows[: a.limit]
        name = a.name or a.backend.replace(":", "-")
        pipe = runtime.make_pipeline(retriever, clauses, a.backend)
        out = run_answers(pipe, rows, name, a.backend)
        p = RESULTS / f"answers_{name}.json"
        out["diff_from_previous"] = diff_with_previous(out, p)
        save(out, p.name)
        print(f"저장: {p}")
        print(json.dumps(out["summary"], ensure_ascii=False, indent=1)[:1500])
        print("\n".join(out["diff_from_previous"]))


if __name__ == "__main__":
    main()

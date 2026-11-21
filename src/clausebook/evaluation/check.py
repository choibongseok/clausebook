"""저장된 평가 결과 파일이 형식에 맞는지, README 의 수치가 결과 파일과 같은지 검사한다(모델 없이 돈다)."""
from __future__ import annotations

import json
import sys

from ..config import ROOT
from . import report as R
from .run import RESULTS, load_dataset, summarize

REQUIRED_SUMMARY = ["pass_rate", "element_recall", "citation_hit_rate", "citation_precision", "unsupported_number_rate", "refusal_accuracy", "false_refusal_rate",
                    "injection_blocked", "pii_leak_rate", "calc_exact", "latency_ms_p50", "latency_ms_p95", "prompt_tokens", "completion_tokens", "cost_usd_per_100", "by_category"]
README_MARK = ("<!-- eval-tables:start -->", "<!-- eval-tables:end -->")


def check() -> list[str]:
    bad: list[str] = []
    ds = {r["id"]: r for r in load_dataset()}
    mp = RESULTS / "retrieval_matrix.json"
    if not mp.exists():
        return ["eval/results/retrieval_matrix.json 이 없음: make eval"]
    m = json.loads(mp.read_text(encoding="utf-8"))
    if len(m["configs"]) != 24 or any(k not in c for c in m["configs"] for k in ("recall@1", "recall@5", "mrr", "ndcg@5", "dev", "test")):
        bad.append("검색 구성 비교 결과가 24개 구성·필수 지표 형식이 아님")
    files = sorted(RESULTS.glob("answers_*.json"))
    if len(files) < 4:
        bad.append(f"답변 평가 결과가 4개 미만: {len(files)}")
    names = set()
    for p in files:
        d = json.loads(p.read_text(encoding="utf-8"))
        names.add(d["name"])
        missing = [k for k in REQUIRED_SUMMARY if k not in d["summary"]]
        if missing:
            bad.append(f"{p.name}: 요약 지표 누락 {missing}")
        ids = [r["id"] for r in d["items"]]
        if set(ids) != set(ds) or len(ids) != len(set(ids)):
            bad.append(f"{p.name}: 평가 질문과 결과 항목이 일치하지 않음")
        again = summarize(d["items"])
        for k in ("pass_rate", "element_recall", "citation_hit_rate", "false_refusal_rate", "calc_exact"):
            if again[k] != d["summary"][k]:
                bad.append(f"{p.name}: {k} 요약이 항목에서 다시 계산한 값과 다름")
        if not d.get("prompt_version") or not d.get("backend"):
            bad.append(f"{p.name}: 프롬프트 버전 또는 모델 설정이 기록되지 않음")
    if not any(n.startswith("qwen") for n in names):
        bad.append("실제 모델 결과가 없음")
    tp = ROOT / "eval" / "traces.json"
    if not tp.exists():
        bad.append("eval/traces.json 이 없음")
    else:
        t = json.loads(tp.read_text(encoding="utf-8"))
        if len(t["scenarios"]) < 10 or any(not s["turns"] or "steps" not in s["turns"][-1]["result"] for s in t["scenarios"]):
            bad.append("실행 기록 형식 오류")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    if README_MARK[0] in readme:
        cur = readme.split(README_MARK[0])[1].split(README_MARK[1])[0].strip()
        if cur != readme_block().strip():
            bad.append("README 의 평가 표가 결과 파일과 다름: python -m clausebook.evaluation.check --write-readme")
    else:
        bad.append("README 에 평가 표 표시(<!-- eval-tables:start -->)가 없음")
    return bad


def readme_block() -> str:
    d = R.load()
    sel = [d["matrix"]]
    out = ["### 검색 구성 비교 (nDCG@5 순, 상위 8개)", ""]
    rows = R.retrieval_table(d["matrix"])
    out += rows[:2 + 8]
    out += ["", f"전체 24개 구성은 [eval/results/summary.md](eval/results/summary.md) 에 있습니다.", "", "### 답변 평가: 모델 비교", ""] + R.answers_table(d["answers"]) + ["", "### 질문 유형별 통과율", ""] + R.category_table(d["answers"])
    return "\n".join(out)


def write_readme() -> None:
    p = ROOT / "README.md"
    t = p.read_text(encoding="utf-8")
    a, b = README_MARK
    pre, rest = t.split(a)
    _, post = rest.split(b)
    p.write_text(pre + a + "\n" + readme_block() + "\n" + b + post, encoding="utf-8")


if __name__ == "__main__":
    if "--write-readme" in sys.argv:
        write_readme()
        print("README 평가 표 갱신")
        sys.exit(0)
    problems = check()
    if problems:
        print(f"평가 결과 검사 실패 ({len(problems)}건)")
        for x in problems:
            print(" -", x)
        sys.exit(1)
    print("평가 결과 검사 통과: 검색 24개 구성, 답변 평가 파일 형식과 항목 재계산, 실행 기록, README 수치 일치")

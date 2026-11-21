"""근거 충분성 문턱을 개발 집합(split=dev)에서 고른다. 보류 집합(test)은 쓰지 않는다.

통과 조건: 상위 3개 청크 중 밀집 유사도 최댓값 >= min_dense 이거나 질문 핵심어 포함률 최댓값 >= min_coverage.
"""
from __future__ import annotations

import json

from ..config import CONFIG_DIR
from ..pipeline import PipelineConfig
from . import run as R


def calibrate(retriever) -> dict:
    cfg = PipelineConfig()
    rows = [r for r in R.load_dataset("dev") if r["expect"] in ("answer", "not_found", "calc") and r["category"] not in ("calc",)]
    pts = []
    for it in rows:
        q, f = R.query_and_filter(it)
        hits = retriever.search(q, cfg.strategy, cfg.method, cfg.rerank, 6, f)
        cov = max((retriever.coverage(q, h, cfg.strategy) for h in hits[:3]), default=0.0)
        dense = max((h.parts.get("dense", 1.0) for h in hits[:3]), default=0.0)
        pts.append((cov, dense, it["expect"] == "not_found"))
    best = (-1.0, 0.0, 0.0)
    pos = sum(1 for *_, nf in pts if nf) or 1
    neg = sum(1 for *_, nf in pts if not nf) or 1
    for c in [x / 100 for x in range(50, 106, 5)]:
        for d in [x / 1000 for x in range(800, 960, 5)]:
            tp = sum(1 for cv, de, nf in pts if nf and (cv < c and de < d))
            tn = sum(1 for cv, de, nf in pts if not nf and (cv >= c or de >= d))
            bal = (tp + tn) / len(pts)  # 질문 한 건당 같은 비중의 정확도
            if bal > best[0] + 1e-9 or (abs(bal - best[0]) < 1e-9 and d < best[2]):
                best = (bal, c, d)
    out = {"min_coverage": best[1], "min_dense": best[2], "balanced_accuracy_dev": round(best[0], 3), "n_dev": len(pts), "chosen_on": "split=dev"}
    (CONFIG_DIR / "thresholds.json").write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return out

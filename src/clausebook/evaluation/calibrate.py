"""근거 충분성 문턱(min_coverage)을 개발 집합(split=dev)에서 고른다. 보류 집합(test)은 쓰지 않는다."""
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
        pts.append((cov, it["expect"] == "not_found"))
    best = (-1, 0.3)
    for t in [x / 100 for x in range(10, 90, 2)]:
        tp = sum(1 for c, nf in pts if nf and c < t)
        tn = sum(1 for c, nf in pts if not nf and c >= t)
        pos = sum(1 for _, nf in pts if nf) or 1
        neg = sum(1 for _, nf in pts if not nf) or 1
        bal = (tp / pos + tn / neg) / 2
        if bal > best[0] + 1e-9:
            best = (bal, t)
    out = {"min_coverage": best[1], "balanced_accuracy_dev": round(best[0], 3), "n_dev": len(pts), "chosen_on": "split=dev"}
    (CONFIG_DIR / "thresholds.json").write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return out

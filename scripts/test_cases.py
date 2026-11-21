"""docs/TEST_CASES.md 를 시험 코드의 설명(docstring)에서 만들고, 실제 시험 결과와 1:1 로 대조한다.

사용(컨테이너 안):  python scripts/test_cases.py gen     문서를 다시 쓴다
                    python scripts/test_cases.py check   문서가 최신인지, 모든 행이 통과한 실제 시험과 1:1 인지 본다
각 시험 함수의 설명 첫 줄은 'ID: XXX-000 | 영역 | 시나리오 | 입력/조건 | 기대 결과' 형식이다.
브라우저 시험은 web/e2e/*.spec.ts 의 test('XXX-000 | 영역 | 시나리오 | 입력/조건 | 기대 결과', ...) 제목에서 읽는다.
"""
from __future__ import annotations

import ast
import json
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "TEST_CASES.md"
KINDS = [("unit", "tests/unit", "단위(분할·검색·점수 결합·계산 도구·인용 검사·개인정보 가림·스키마·상태 전이)", "`make test`"),
         ("integration", "tests/integration", "통합(실제 PostgreSQL·pgvector·e5 임베딩 + 모의 모델로 대화 흐름 전체)", "`make test`"),
         ("e2e", "tests/e2e", "e2e(compose 로 띄운 API 를 바깥에서: 멀티턴·스트리밍·주입·개인정보·문서에 없는 질문)", "`make e2e`"),
         ("browser", "web/e2e", "브라우저(데모 사이트, 실제 Chrome)", "`make verify`")]
ID_RX = re.compile(r"^[A-Z]{3}-\d{3}$")


def collect() -> list[dict]:
    rows = []
    for kind, d, *_ in KINDS:
        if kind == "browser":
            for f in sorted((ROOT / d).glob("*.spec.ts")):
                for m in re.finditer(r"test\(\s*(['\"`])(.+?)\1\s*,", f.read_text(encoding="utf-8")):
                    rows.append(_row(kind, f"{d}/{f.name}", m.group(2), m.group(2)))
            continue
        for f in sorted((ROOT / d).glob("test_*.py")):
            tree = ast.parse(f.read_text(encoding="utf-8"))
            for node in tree.body:
                if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                    doc = ast.get_docstring(node) or ""
                    first = doc.splitlines()[0] if doc else ""
                    if not first.startswith("ID: "):
                        raise SystemExit(f"{f.name}::{node.name}: 설명 첫 줄이 'ID: ...' 형식이 아님")
                    rows.append(_row(kind, f"{d}/{f.name}", node.name, first[4:]))
    return rows


def _row(kind, file, name, text) -> dict:
    cells = [c.strip() for c in text.split(" | ")]
    if len(cells) != 5 or not ID_RX.match(cells[0]):
        raise SystemExit(f"{file}::{name}: 'ID | 영역 | 시나리오 | 입력/조건 | 기대 결과' 5칸이 아님: {text[:60]}")
    return {"kind": kind, "file": file, "name": name, "id": cells[0], "cells": cells}


def render(rows: list[dict]) -> str:
    per = Counter(r["kind"] for r in rows)
    out = ["# 테스트 케이스", "",
           "모든 행은 실제 테스트 함수 하나와 1:1 로 대응합니다. 행은 시험 코드의 설명에서 `python scripts/test_cases.py gen` 으로 만들고, `make verify` 가 `check` 로 (1) 이 문서가 최신인지, (2) 문서의 모든 행이 실제로 통과한 시험인지, (3) 문서에 없는 시험이 없는지 대조합니다. 매개변수로 여러 입력을 도는 시험은 한 행이며, 입력이 모두 통과해야 통과로 칩니다.", "",
           "평가 질문(`eval/dataset.jsonl`)은 여기 세지 않습니다. 평가 질문은 모델이 얼마나 잘 답하는지 재는 자료이고, 아래 시험은 코드가 약속대로 움직이는지 보는 것입니다.", "",
           "| 종류 | 개수 | 실행 |", "|---|---|---|"]
    for kind, _d, label, cmd in KINDS:
        out.append(f"| {kind} {label} | {per[kind]} | {cmd} |")
    out += [f"| **합계** | **{len(rows)}** | `make verify` |", "",
            "| ID | 영역 | 시나리오 | 입력/조건 | 기대 결과 | 자동화 테스트 | 종류 |", "|---|---|---|---|---|---|---|"]
    for r in rows:
        c = [x.replace("|", "\\|") for x in r["cells"]]
        out.append(f"| {c[0]} | {c[1]} | {c[2]} | {c[3]} | {c[4]} | `{r['file']}::{r['name']}` | {r['kind']} |")
    return "\n".join(out) + "\n"


def junit_results() -> dict[str, str]:
    """파일::함수 → passed/failed/skipped (매개변수 입력은 하나라도 실패하면 failed)."""
    res: dict[str, str] = {}
    for p in sorted((ROOT / "build").glob("junit-*.xml")):
        for tc in ET.parse(p).getroot().iter("testcase"):
            cls = tc.get("classname", "")  # tests.unit.test_x 또는 tests.unit.test_x.TestY
            fn = re.sub(r"\[.*\]$", "", tc.get("name", ""))
            parts = cls.split(".")
            file = "/".join(parts[:3]) + ".py"
            key = f"{file}::{fn}"
            st = "failed" if tc.find("failure") is not None or tc.find("error") is not None else "skipped" if tc.find("skipped") is not None else "passed"
            if res.get(key) == "failed" or st == "failed":
                res[key] = "failed"
            elif res.get(key) == "skipped" or st == "skipped":
                res[key] = "skipped"
            else:
                res[key] = "passed"
    return res


def web_results() -> dict[str, str]:
    p = ROOT / "build" / "web.json"
    out: dict[str, str] = {}
    if not p.exists():
        return out

    def walk(s, file=""):
        for sp in s.get("specs", []):
            out[f"web/e2e/{sp['file']}::{sp['title']}"] = "passed" if sp.get("ok") else "failed"
        for sub in s.get("suites", []):
            walk(sub)

    for s in json.loads(p.read_text(encoding="utf-8")).get("suites", []):
        walk(s)
    return out


def check() -> int:
    rows = collect()
    problems = []
    ids = Counter(r["id"] for r in rows)
    problems += [f"ID 중복: {i}" for i, n in ids.items() if n > 1]
    if not DOC.exists() or DOC.read_text(encoding="utf-8") != render(rows):
        problems.append("docs/TEST_CASES.md 가 시험 코드와 다름: python scripts/test_cases.py gen")
    actual = {**junit_results(), **web_results()}
    keys = {f"{r['file']}::{r['name']}" for r in rows}
    for r in rows:
        k = f"{r['file']}::{r['name']}"
        if k not in actual:
            problems.append(f"{r['id']}: 실행된 결과 없음 -> {k}")
        elif actual[k] != "passed":
            problems.append(f"{r['id']}: 통과하지 않음({actual[k]}) -> {k}")
    for k in actual:
        if k not in keys:
            problems.append(f"문서에 없는 시험: {k}")
    if problems:
        print(f"테스트 케이스 문서 대조 실패 ({len(problems)}건)")
        for p in problems[:40]:
            print(" -", p)
        return 1
    per = Counter(r["kind"] for r in rows)
    print(f"테스트 케이스 문서 대조 통과: 문서 {len(rows)}행 = 실행 {len(actual)}건 (" + ", ".join(f"{k} {per[k]}" for k, *_ in KINDS) + ")")
    return 0


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "check"
    if mode == "gen":
        DOC.write_text(render(collect()), encoding="utf-8")
        print(f"{DOC} 갱신")
    else:
        sys.exit(check())

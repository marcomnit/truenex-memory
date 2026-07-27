"""Retrieval evaluation harness for Truenex Memory.

Runs a fixed eval set of queries through the SAME code path as the MCP
`memory_search` tool (`MemoryRepository.search`) and reports per-case and
aggregate metrics (hit@1, hit@k, MRR, expected-absent pass rate).

Usage (project venv):

    .venv/Scripts/python.exe scripts/eval_retrieval.py \
        --db "C:\\Users\\marco\\.truenex-memory\\truenex_memory.db" \
        --set scripts/eval/queries.json \
        --out docs/eval/baseline-2026-07-27.md

The eval set JSON has the shape::

    {
      "version": 1,
      "cases": [
        {
          "id": "m01",
          "category": "memory-recall",
          "query": "...",
          "top_k": 5,
          "expected": [
            {"memory_id": "mem_..."},
            {"path_contains": "...", "content_contains": "..."}
          ],
          "expected_absent": [{"path_contains": "..."}],
          "note": "..."
        }
      ]
    }

Expected matchers (all fields present in one item must match the SAME hit;
an item matches when hit satisfies every field):

- ``memory_id``: resolved to the memory's title via a DB lookup, then
  matched on ``hit.title`` (SearchHit does not carry the memory id);
- ``path_contains``: case-insensitive substring of ``hit.source_path``;
- ``content_contains``: case-insensitive substring of ``hit.content``.

NOTE: ``MemoryRepository.search`` appends to ``retrieval_logs`` — same
behavior as production, accepted for evaluation runs.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

# Allow running as a plain script from the repository root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from truenex_memory.retrieval.semantic import HashingEmbedder
from truenex_memory.store.models import SearchHit
from truenex_memory.store.repository import MemoryRepository


@dataclass
class CaseResult:
    case_id: str
    category: str
    query: str
    top_k: int
    rank: int | None  # 1-based rank of the first expected hit, None if absent
    hit_at_1: bool
    hit_at_k: bool
    reciprocal_rank: float
    absent_ok: bool | None  # None when the case has no expected_absent
    elapsed_s: float
    note: str = ""
    top_hits: list[dict[str, object]] = field(default_factory=list)


def _resolve_memory_titles(db_path: Path, memory_ids: list[str]) -> dict[str, str]:
    """Map memory_id -> title for expected matching (SearchHit has no id)."""
    if not memory_ids:
        return {}
    conn = sqlite3.connect(f"file:{db_path.resolve().as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    titles: dict[str, str] = {}
    try:
        for memory_id in memory_ids:
            row = conn.execute(
                "SELECT title FROM memory_nodes WHERE id = ?", (memory_id,)
            ).fetchone()
            if row is not None:
                titles[memory_id] = str(row["title"])
    finally:
        conn.close()
    return titles


def _hit_matches_expected(hit: SearchHit, expected: dict[str, str], memory_titles: dict[str, str]) -> bool:
    memory_id = expected.get("memory_id")
    if memory_id is not None:
        title = memory_titles.get(memory_id)
        if title is None or hit.title != title:
            return False
    path_contains = expected.get("path_contains")
    if path_contains is not None:
        if path_contains.casefold() not in (hit.source_path or "").casefold():
            return False
    content_contains = expected.get("content_contains")
    if content_contains is not None:
        if content_contains.casefold() not in hit.content.casefold():
            return False
    return True


def _summarize_hit(hit: SearchHit) -> dict[str, object]:
    return {
        "memory_type": hit.memory_type,
        "title": hit.title[:120],
        "source_path": hit.source_path,
        "score": hit.score,
    }


def run_eval(db_path: Path, eval_set: dict[str, object]) -> dict[str, object]:
    repository = MemoryRepository(
        db_path,
        embedder=HashingEmbedder(),
        project_id=os.environ.get("TRUENEX_PROJECT_ID", "default"),
    )
    cases = eval_set["cases"]
    memory_ids = [
        expected["memory_id"]
        for case in cases
        for expected in case.get("expected", [])
        if "memory_id" in expected
    ]
    memory_titles = _resolve_memory_titles(db_path, memory_ids)

    results: list[CaseResult] = []
    for case in cases:
        top_k = int(case.get("top_k", 5))
        expected_items = case.get("expected", [])
        expected_absent = case.get("expected_absent", [])

        started = time.perf_counter()
        hits = repository.search(case["query"], top_k=top_k)
        elapsed = time.perf_counter() - started

        rank = None
        for index, hit in enumerate(hits, start=1):
            if any(
                _hit_matches_expected(hit, expected, memory_titles)
                for expected in expected_items
            ):
                rank = index
                break

        absent_ok: bool | None = None
        if expected_absent:
            absent_ok = not any(
                _hit_matches_expected(hit, absent, memory_titles)
                for hit in hits
                for absent in expected_absent
            )

        results.append(
            CaseResult(
                case_id=case["id"],
                category=str(case.get("category", "uncategorized")),
                query=case["query"],
                top_k=top_k,
                rank=rank,
                hit_at_1=rank == 1,
                hit_at_k=rank is not None,
                reciprocal_rank=1.0 / rank if rank is not None else 0.0,
                absent_ok=absent_ok,
                elapsed_s=elapsed,
                note=str(case.get("note", "")),
                top_hits=[_summarize_hit(hit) for hit in hits],
            )
        )

    return {
        "eval_set_version": eval_set.get("version"),
        "db_path": str(db_path),
        "case_count": len(results),
        "results": [vars(result) for result in results],
    }


def _aggregate(results: list[CaseResult]) -> dict[str, object]:
    def mean(values: list[float]) -> float:
        return round(sum(values) / len(values), 4) if values else 0.0

    aggregate: dict[str, object] = {
        "cases": len(results),
        "hit_at_1": mean([float(r.hit_at_1) for r in results]),
        "hit_at_k": mean([float(r.hit_at_k) for r in results]),
        "mrr": mean([r.reciprocal_rank for r in results]),
        "mean_query_time_s": round(mean([r.elapsed_s for r in results]), 3),
    }
    absent_results = [r for r in results if r.absent_ok is not None]
    if absent_results:
        aggregate["absent_pass_rate"] = mean([float(r.absent_ok) for r in absent_results])
        aggregate["absent_cases"] = len(absent_results)
    by_category: dict[str, object] = {}
    categories = sorted({r.category for r in results})
    for category in categories:
        subset = [r for r in results if r.category == category]
        by_category[category] = {
            "cases": len(subset),
            "hit_at_1": mean([float(r.hit_at_1) for r in subset]),
            "hit_at_k": mean([float(r.hit_at_k) for r in subset]),
            "mrr": mean([r.reciprocal_rank for r in subset]),
        }
    aggregate["by_category"] = by_category
    return aggregate


def render_markdown(run: dict[str, object], eval_set: dict[str, object]) -> str:
    results = [CaseResult(**raw) for raw in run["results"]]
    aggregate = _aggregate(results)

    lines = ["# Retrieval evaluation report", ""]
    lines.append(f"- DB: `{run['db_path']}`")
    lines.append(f"- Eval set version: {run['eval_set_version']}")
    lines.append(f"- Cases: {aggregate['cases']}")
    lines.append("")
    lines.append("## Aggregate metrics")
    lines.append("")
    lines.append("| metric | value |")
    lines.append("|---|---|")
    lines.append(f"| hit@1 | {aggregate['hit_at_1']} |")
    lines.append(f"| hit@k | {aggregate['hit_at_k']} |")
    lines.append(f"| MRR | {aggregate['mrr']} |")
    if "absent_pass_rate" in aggregate:
        lines.append(
            f"| absent pass rate ({aggregate['absent_cases']} cases) | {aggregate['absent_pass_rate']} |"
        )
    lines.append(f"| mean query time (s) | {aggregate['mean_query_time_s']} |")
    lines.append("")
    lines.append("## By category")
    lines.append("")
    lines.append("| category | cases | hit@1 | hit@k | MRR |")
    lines.append("|---|---|---|---|---|")
    for category, stats in aggregate["by_category"].items():
        lines.append(
            f"| {category} | {stats['cases']} | {stats['hit_at_1']} | {stats['hit_at_k']} | {stats['mrr']} |"
        )
    lines.append("")
    lines.append("## Per-case results")
    lines.append("")
    lines.append("| id | category | hit@1 | hit@k | rank | absent | time (s) | query | note |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for r in results:
        absent = "-" if r.absent_ok is None else ("ok" if r.absent_ok else "FAIL")
        rank = r.rank if r.rank is not None else "MISS"
        query = r.query.replace("|", "\\|")
        note = r.note.replace("|", "\\|")
        lines.append(
            f"| {r.case_id} | {r.category} | {'1' if r.hit_at_1 else '0'} | "
            f"{'1' if r.hit_at_k else '0'} | {rank} | {absent} | {r.elapsed_s:.2f} | {query[:70]} | {note[:60]} |"
        )
    lines.append("")

    failures = [r for r in results if not r.hit_at_k or r.absent_ok is False]
    if failures:
        lines.append("## Failure analysis (top 3 hits per failed case)")
        lines.append("")
        for r in failures:
            lines.append(f"### {r.case_id} — {r.query[:80]}")
            lines.append("")
            for index, hit in enumerate(r.top_hits[:3], start=1):
                lines.append(
                    f"{index}. [{hit['memory_type']}] score={hit['score']} "
                    f"`{(hit['source_path'] or '')[-90:]}` — {str(hit['title'])[:80]}"
                )
            lines.append("")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate Truenex Memory retrieval.")
    parser.add_argument("--db", required=True, type=Path, help="Path to the SQLite memory DB.")
    parser.add_argument("--set", required=True, type=Path, help="Eval set JSON file.")
    parser.add_argument("--out", required=True, type=Path, help="Markdown report output path.")
    args = parser.parse_args()

    eval_set = json.loads(args.set.read_text(encoding="utf-8"))
    run = run_eval(args.db, eval_set)
    run["aggregate"] = _aggregate([CaseResult(**raw) for raw in run["results"]])

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render_markdown(run, eval_set), encoding="utf-8")
    raw_path = args.out.with_suffix(".json")
    raw_path.write_text(json.dumps(run, indent=2, ensure_ascii=False), encoding="utf-8")

    aggregate = run["aggregate"]
    print(f"cases={aggregate['cases']} hit@1={aggregate['hit_at_1']} "
          f"hit@k={aggregate['hit_at_k']} mrr={aggregate['mrr']} "
          f"mean_query_time={aggregate['mean_query_time_s']}s")
    print(f"report: {args.out}")
    print(f"raw:    {raw_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

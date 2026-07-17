#!/usr/bin/env python3
"""Live diagnostic script for the v0.1 search/filter API.

Connects to the running backend (default http://localhost:8000) and prints
real response shapes, distinct field values, and filter behavior.

Usage:
    python scripts/diagnose_search.py
    python scripts/diagnose_search.py --base-url http://127.0.0.1:8000
"""

from __future__ import annotations

import argparse
from collections import Counter
from urllib.parse import urljoin

import httpx


def get(base_url: str, path: str) -> dict:
    r = httpx.get(urljoin(base_url, path), timeout=10.0)
    r.raise_for_status()
    return r.json()


def post_search(base_url: str, payload: dict) -> list[dict]:
    r = httpx.post(
        urljoin(base_url, "/api/search"),
        json=payload,
        timeout=30.0,
    )
    r.raise_for_status()
    return r.json()


def print_section(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(title)
    print("=" * 60)


def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnose search API behavior")
    parser.add_argument(
        "--base-url",
        default="http://localhost:8000",
        help="Backend base URL",
    )
    parser.add_argument(
        "--query",
        default="authentication",
        help="Search query to use for diagnostics",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=20,
        help="top_k for unrestricted search",
    )
    args = parser.parse_args()

    base = args.base_url.rstrip("/")

    print_section("1. Health")
    try:
        health = get(base, "/api/health")
        print(health)
    except Exception as exc:
        print(f"ERROR: cannot reach backend at {base}: {exc}")
        return

    print_section("2. Catalog status")
    catalog = get(base, "/api/catalog/status")
    print(catalog)

    print_section(f"3. Baseline search: '{args.query}' (top_k={args.top_k}, no filters)")
    baseline = post_search(
        base,
        {"query": args.query, "top_k": args.top_k},
    )
    print(f"Results returned: {len(baseline)}")
    for i, hit in enumerate(baseline[:5], 1):
        print(
            f"  {i}. {hit.get('title', 'n/a')} | "
            f"type={hit.get('memory_type')!r} | "
            f"status={hit.get('status')!r} | "
            f"project={hit.get('project')!r} | "
            f"score={hit.get('score')}"
        )

    print_section("4. Distinct field values in baseline results")
    projects = Counter(h.get("project") for h in baseline)
    types = Counter(h.get("memory_type") for h in baseline)
    statuses = Counter(h.get("status") for h in baseline)
    print(f"  projects: {dict(projects)}")
    print(f"  types:    {dict(types)}")
    print(f"  statuses: {dict(statuses)}")

    print_section("5. Filter matrix")
    filter_cases = [
        ("project=default", {"project": "default"}),
        ("project=truenex", {"project": "truenex"}),
        ("project=TRUENEX", {"project": "TRUENEX"}),
        ("type=document_chunk", {"type": "document_chunk"}),
        ("type=document", {"type": "document"}),
        ("type=note", {"type": "note"}),
        ("type=code", {"type": "code"}),
        ("status=active", {"status": "active"}),
        ("status=obsolete", {"status": "obsolete"}),
        ("date_after=future", {"date_after": "2099-01-01"}),
        ("date_after=past", {"date_after": "2000-01-01"}),
        (
            "combined default+document_chunk+active",
            {"project": "default", "type": "document_chunk", "status": "active"},
        ),
    ]
    for label, filters in filter_cases:
        results = post_search(
            base,
            {"query": args.query, "top_k": args.top_k, "filters": filters},
        )
        types_in = Counter(h.get("memory_type") for h in results)
        statuses_in = Counter(h.get("status") for h in results)
        projects_in = Counter(h.get("project") for h in results)
        print(
            f"  {label:40s} -> {len(results):2d} results  "
            f"projects={dict(projects_in)} types={dict(types_in)} statuses={dict(statuses_in)}"
        )

    print_section("6. Prefix parser equivalents")
    prefix_cases = [
        f"project:default {args.query}",
        f"project:truenex {args.query}",
        f"type:document_chunk {args.query}",
        f"type:document {args.query}",
        f"status:active {args.query}",
    ]
    for raw_query in prefix_cases:
        # The backend does not parse prefixes; emulate what the frontend does
        # so the diagnostic output matches the browser behavior.
        words = raw_query.split()
        clean_words = []
        filters: dict[str, str] = {}
        for word in words:
            if ":" in word and not word.startswith("http"):
                key, value = word.split(":", 1)
                key_map = {
                    "project": "project",
                    "type": "type",
                    "status": "status",
                    "after": "date_after",
                }
                if key in key_map and value:
                    filters[key_map[key]] = value
                    continue
            clean_words.append(word)
        clean_query = " ".join(clean_words)
        results = post_search(
            base,
            {
                "query": clean_query,
                "top_k": args.top_k,
                "filters": filters or None,
            },
        )
        print(f"  '{raw_query}' -> {len(results)} results (filters={filters})")


if __name__ == "__main__":
    main()

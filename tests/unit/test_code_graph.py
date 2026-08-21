"""Tests for the file-level code graph and its store mapping.

`/api/project-graph` used to return a hardcoded ``"edges": []``, so the
project view showed a folder hierarchy and nothing else. These tests pin
the aggregation that fills that array, and the contract we depend on from
the optional extraction backend.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from truenex_memory.graph import (
    CODE_SUFFIXES,
    FileEdge,
    FileGraph,
    GraphifyUnavailable,
    aggregate_to_files,
    build_file_graph,
    collect_source_files,
    document_edges,
    find_cached_graph,
    graphify_available,
    load_file_graph,
    save_file_graph,
)

# `Path("/repo")` is not absolute on Windows (no drive), which would
# make these tests exercise a different branch than production does.
ROOT = Path(os.path.abspath("/repo"))


def _node(node_id: str, source_file: str | None) -> dict:
    node = {"id": node_id, "label": node_id}
    if source_file is not None:
        node["source_file"] = source_file
    return node


def _edge(source: str, target: str, relation: str = "calls") -> dict:
    return {"source": source, "target": target, "relation": relation}


# ── Aggregation ────────────────────────────────────────────────────────────

def test_entity_edges_collapse_into_one_weighted_edge_per_relation() -> None:
    """Three calls from one file to another are one edge of weight 3."""

    nodes = [
        _node("a1", "src/a.py"),
        _node("a2", "src/a.py"),
        _node("b1", "src/b.py"),
    ]
    edges = [
        _edge("a1", "b1"),
        _edge("a2", "b1"),
        _edge("a1", "b1"),
        _edge("a1", "b1", relation="imports"),
    ]

    result = aggregate_to_files(nodes, edges, ROOT)

    by_relation = {edge.relation_type: edge for edge in result}
    assert by_relation["calls"].weight == 3
    assert by_relation["imports"].weight == 1
    assert by_relation["calls"].source_file == "src/a.py"
    assert by_relation["calls"].target_file == "src/b.py"
    # Relations stay distinct rather than being summed into one edge: the
    # frontend renders `relation_type` at file level, and "imports" and
    # "calls" are different claims about the same pair of files.
    assert len(result) == 2


def test_intra_file_edges_are_dropped() -> None:
    """A call within one file says nothing about how the project is wired.

    On a real tree these dominate by an order of magnitude (`contains` and
    `method` edges alone were 2,353 of 8,862 on this repo), so keeping them
    would swamp every cross-file weight.
    """

    nodes = [_node("a1", "src/a.py"), _node("a2", "src/a.py")]

    assert aggregate_to_files(nodes, [_edge("a1", "a2")], ROOT) == []


def test_nodes_without_a_source_file_are_dropped() -> None:
    """Built-in types are minted as nodes belonging to no file."""

    nodes = [_node("a1", "src/a.py"), _node("AppHandle", None)]

    assert aggregate_to_files(nodes, [_edge("a1", "AppHandle")], ROOT) == []


def test_external_module_specifiers_do_not_become_files() -> None:
    """An import of a package is not a file in the project.

    `@tauri-apps/api/window` and `serde` arrive as nodes whose source_file
    is the specifier itself. Left alone they became phantom project files
    that no document could ever match — 1 of MedDesk's 120 graph files.
    """

    nodes = [
        _node("local", "src/app.ts"),
        _node("external", "@tauri-apps/api/window"),
        _node("crate", "serde"),
    ]
    edges = [_edge("local", "external", "imports"), _edge("local", "crate", "imports")]

    assert aggregate_to_files(nodes, edges, ROOT) == []


def test_absolute_source_files_are_made_relative_to_the_root() -> None:
    nodes = [
        _node("a", str(ROOT / "src" / "a.py")),
        _node("b", str(ROOT / "src" / "b.py")),
    ]

    result = aggregate_to_files(nodes, [_edge("a", "b")], ROOT)

    assert [(e.source_file, e.target_file) for e in result] == [("src/a.py", "src/b.py")]


def test_paths_outside_the_root_are_dropped() -> None:
    nodes = [_node("a", str(ROOT / "src" / "a.py")), _node("b", "/elsewhere/b.py")]

    assert aggregate_to_files(nodes, [_edge("a", "b")], ROOT) == []


def test_heaviest_edges_come_first() -> None:
    nodes = [_node("a", "a.py"), _node("b", "b.py"), _node("c", "c.py")]
    edges = [_edge("a", "b"), _edge("a", "c"), _edge("a", "c"), _edge("a", "c")]

    result = aggregate_to_files(nodes, edges, ROOT)

    assert [edge.weight for edge in result] == [3, 1]


# ── Mapping onto the store ─────────────────────────────────────────────────

def test_document_edges_key_on_the_repo_relative_path() -> None:
    """The mapping survives a project indexed under a different prefix.

    Edges are cached against repo-relative paths precisely so a reindex, or
    a checkout at another location, does not invalidate the graph.
    """

    graph = FileGraph(
        root="/repo",
        edges=[FileEdge("src/a.py", "src/b.py", "calls", 4)],
    )
    documents = [("doc_a", r"\repo\src\a.py"), ("doc_b", "/repo/src/b.py")]

    assert document_edges(graph, documents) == [
        {"source": "doc_a", "target": "doc_b", "relation_type": "calls", "weight": 4}
    ]


def test_edges_whose_files_are_not_indexed_are_skipped() -> None:
    """Only edges between indexed files can be drawn.

    This is not hypothetical: MedDesk has 671 indexed documents and 119
    files in its code graph, with an overlap of zero — the store holds its
    documentation (155 .md) and none of its Rust or TypeScript.
    """

    graph = FileGraph(
        root="/repo",
        edges=[
            FileEdge("src/a.py", "src/b.py", "calls", 1),
            FileEdge("src/a.py", "src/never_indexed.py", "calls", 9),
        ],
    )
    documents = [("doc_a", "/repo/src/a.py"), ("doc_b", "/repo/src/b.py")]

    mapped = document_edges(graph, documents)

    assert len(mapped) == 1
    assert mapped[0]["target"] == "doc_b"


def test_document_edges_accept_project_relative_document_paths() -> None:
    """A project store records paths relative to its root; the global store
    records them absolute. Both must map.

    Handling only the absolute form is how this shipped broken the first
    time: `graph build` wrote a valid graph, the endpoint found it, and the
    project view still drew no edges because every document path was a bare
    `src/a.py` that `relative_to()` refused.
    """

    graph = FileGraph(
        root="D:/repo",
        edges=[FileEdge("src/a.py", "src/b.py", "calls", 2)],
    )
    documents = [("doc_a", "src/a.py"), ("doc_b", "src\\b.py")]

    assert document_edges(graph, documents) == [
        {"source": "doc_a", "target": "doc_b", "relation_type": "calls", "weight": 2}
    ]


def test_absolute_evidence_outranks_a_shared_filename(tmp_path: Path) -> None:
    """A bare relative path only proves a filename is shared.

    `README.md` and `src/main.py` exist in every project, so a relative-only
    match must never beat a graph whose root actually contains the documents.
    """

    cache = tmp_path / "cache"
    save_file_graph(
        FileGraph(root="/other", edges=[FileEdge("src/a.py", "src/b.py", "calls", 1)]),
        cache,
    )
    save_file_graph(
        FileGraph(root="/repo", edges=[FileEdge("src/z.py", "src/y.py", "calls", 1)]),
        cache,
    )

    # One absolute document under /repo, two bare names matching /other.
    found = find_cached_graph(cache, ["/repo/src/z.py", "src/a.py", "src/b.py"])

    assert found is not None and found.root == "/repo"


def test_document_edges_ignore_documents_outside_the_graph_root() -> None:
    graph = FileGraph(root="/repo", edges=[FileEdge("a.py", "b.py", "calls", 1)])

    assert document_edges(graph, [("doc", "/other/a.py")]) == []


# ── Cache ──────────────────────────────────────────────────────────────────

def test_cache_round_trip(tmp_path: Path) -> None:
    graph = FileGraph(
        root=(tmp_path / "proj").as_posix(),
        edges=[FileEdge("a.py", "b.py", "calls", 2)],
        stats={"files": 2},
    )

    save_file_graph(graph, tmp_path / "cache")
    loaded = load_file_graph(tmp_path / "cache", tmp_path / "proj")

    assert loaded is not None
    assert loaded.root == graph.root
    assert loaded.edges == graph.edges
    assert loaded.stats == {"files": 2}


def test_cache_written_by_another_version_is_ignored(tmp_path: Path) -> None:
    """A stale format must read as absent, not as an empty graph.

    Silently returning no edges would look identical to the bug this whole
    module exists to fix.
    """

    graph = FileGraph(root=(tmp_path / "proj").as_posix(), edges=[])
    target = save_file_graph(graph, tmp_path / "cache")
    data = json.loads(target.read_text(encoding="utf-8"))
    data["cache_version"] = 999
    target.write_text(json.dumps(data), encoding="utf-8")

    assert load_file_graph(tmp_path / "cache", tmp_path / "proj") is None


def test_unreadable_cache_reads_as_absent(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    from truenex_memory.graph import cache_path

    cache_path(cache, tmp_path / "proj").write_text("{not json", encoding="utf-8")

    assert load_file_graph(cache, tmp_path / "proj") is None


def test_find_cached_graph_prefers_the_graph_covering_more_documents(tmp_path: Path) -> None:
    """Between nested roots, the wider one wins when it covers more.

    A repo-level graph is a superset of a nested one, so preferring it is
    correct: more of the project's documents get edges.
    """

    cache = tmp_path / "cache"
    save_file_graph(FileGraph(root="/repo/sub", edges=[]), cache)
    save_file_graph(FileGraph(root="/repo", edges=[]), cache)

    found = find_cached_graph(cache, ["/repo/sub/a.py", "/repo/sub/b.py", "/repo/top.py"])

    assert found is not None and found.root == "/repo"


def test_find_cached_graph_uses_a_nested_root_when_it_is_the_only_match(
    tmp_path: Path,
) -> None:
    """The build root need not be the common prefix of indexed documents.

    MedDesk is indexed at repository level while its code graph is usefully
    built at `runtime/tauri-app`, so the resolver scores rather than guesses.
    """

    cache = tmp_path / "cache"
    save_file_graph(FileGraph(root="/repo/runtime/app", edges=[]), cache)
    save_file_graph(FileGraph(root="/unrelated", edges=[]), cache)

    found = find_cached_graph(cache, ["/repo/runtime/app/a.py", "/repo/docs/x.md"])

    assert found is not None and found.root == "/repo/runtime/app"


def test_find_cached_graph_ignores_graphs_covering_nothing(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    save_file_graph(FileGraph(root="/elsewhere", edges=[]), cache)

    assert find_cached_graph(cache, ["/repo/a.py"]) is None


def test_find_cached_graph_on_a_missing_directory(tmp_path: Path) -> None:
    assert find_cached_graph(tmp_path / "absent", ["/repo/a.py"]) is None


# ── File collection ───────────────────────────────────────────────────────

def test_collect_source_files_skips_build_trees(tmp_path: Path) -> None:
    """A build tree must not be walked.

    MedDesk's `src-tauri/target` alone is 4.4 GB; walking it is what makes a
    naive scan look like a hang.
    """

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("x = 1", encoding="utf-8")
    for excluded in ("target", "node_modules", "dist"):
        (tmp_path / excluded).mkdir()
        (tmp_path / excluded / "generated.py").write_text("x = 1", encoding="utf-8")
    (tmp_path / "README.md").write_text("# doc", encoding="utf-8")

    found = collect_source_files(tmp_path)

    assert [p.name for p in found] == ["app.py"]


def test_collect_source_files_honours_the_limit(tmp_path: Path) -> None:
    for index in range(5):
        (tmp_path / f"m{index}.py").write_text("x = 1", encoding="utf-8")

    assert len(collect_source_files(tmp_path, limit=2)) == 2


def test_build_file_graph_reports_the_missing_backend(monkeypatch, tmp_path: Path) -> None:
    """Without the extra, the caller is told what to install."""

    monkeypatch.setattr(
        "truenex_memory.graph.code_graph.graphify_available", lambda: False
    )

    with pytest.raises(GraphifyUnavailable, match=r"truenex-memory\[graph\]"):
        build_file_graph(tmp_path)


# ── Upstream contract ─────────────────────────────────────────────────────

@pytest.mark.skipif(
    not graphify_available(), reason="optional graph extra is not installed"
)
def test_extraction_contract_still_holds(tmp_path: Path) -> None:
    """Pin the upstream shape we depend on.

    The backend ships a release every day or two while still pre-1.0. This
    test is how a breaking change reaches us — our own suite failing — rather
    than through a changelog nobody read. It asserts only what the adapter
    consumes: `{nodes: [{id, source_file}], edges: [{source, target,
    relation}]}`, and that a plain cross-file call is resolved.
    """

    (tmp_path / "helper.py").write_text(
        "def helper():\n    return 1\n", encoding="utf-8"
    )
    (tmp_path / "main.py").write_text(
        "from helper import helper\n\n\ndef run():\n    return helper()\n",
        encoding="utf-8",
    )

    graph = build_file_graph(tmp_path, parallel=False)

    assert graph.stats["files"] == 2
    assert graph.stats["entity_nodes"] > 0
    pairs = {(edge.source_file, edge.target_file) for edge in graph.edges}
    assert ("main.py", "helper.py") in pairs, (
        "upstream no longer resolves a cross-file call: the adapter's "
        f"assumption is broken. Got: {sorted(pairs)}"
    )
    assert all(edge.weight >= 1 for edge in graph.edges)
    assert all(
        Path(edge.source_file).suffix in CODE_SUFFIXES for edge in graph.edges
    )

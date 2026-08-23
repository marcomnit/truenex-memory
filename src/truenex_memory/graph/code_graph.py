"""File-level code graph, aggregated from entity-level extraction.

`/api/project-graph` served its nodes with a hardcoded ``"edges": []``, so
the graph a reader saw was only the folder hierarchy the frontend derives
from the document paths — it carried no information the file explorer did
not already show. The frontend has always been built to render an edge
array (it aggregates edges between directories and weights them), it was
simply never given one.

Graphify resolves `calls`, `imports`, `inherits` and similar relations
between code entities via tree-sitter, fully offline and with no LLM.
Aggregating those to the file level produces exactly the shape the
frontend expects: ``{source, target, relation_type, weight}``.

Graphify is an OPTIONAL dependency (``pip install truenex-memory[graph]``).
Every entry point here degrades to an empty result when it is missing, so
the endpoint and the CLI keep working without it.

Entity ids are deliberately NOT carried into the store. They are stable
across runs but they are Graphify's identifiers, while a document id is
ours and is reassigned on reindex; keying the cache on repo-relative
*paths* keeps the cached graph valid across reindexes and lets the
document mapping be recomputed at read time.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from truenex_memory.core.exclusions import load_gitignore_patterns, should_exclude

# Suffixes worth parsing. Graphify ships ~50 grammars; this is the subset
# that appears in these projects, so a stray Verilog or Fortran file in a
# vendored tree cannot pull an irrelevant parser into the run.
CODE_SUFFIXES: frozenset[str]  # alias storico, assegnato sotto


def code_suffixes() -> frozenset[str]:
    """Le estensioni da analizzare, con `TRUENEX_GRAPH_SUFFIXES` a sovrascrivere.

    L'elenco predefinito e' un sottoinsieme di cio' che l'estrattore sa leggere
    (~50 grammatiche): serve a evitare che un file Verilog dentro un albero di
    terze parti tiri dentro un parser che non c'entra. Ma un elenco chiuso
    scritto nel codice ha il difetto opposto: aggiungi un `.zig` o un `.ex` al
    progetto e il grafo lo ignora **in silenzio**, cioe' «chi chiama questa
    funzione» risponde «nessuno» perche' il file non e' stato nemmeno guardato.
    Per questo la scelta e' sovrascrivibile e `graph build` dichiara sempre
    quante estensioni ha scartato: il limite resta, l'ignoranza del limite no.

    Esempio: ``TRUENEX_GRAPH_SUFFIXES=".zig,.ex"`` li aggiunge ai predefiniti,
    ``TRUENEX_GRAPH_SUFFIXES="=.py,.rs"`` li sostituisce.
    """

    import os

    raw = (os.environ.get("TRUENEX_GRAPH_SUFFIXES") or "").strip()
    if not raw:
        return DEFAULT_CODE_SUFFIXES
    replace = raw.startswith("=")
    wanted = {
        piece.strip().lower() if piece.strip().startswith(".") else f".{piece.strip().lower()}"
        for piece in raw.lstrip("=").split(",")
        if piece.strip()
    }
    return frozenset(wanted) if replace else DEFAULT_CODE_SUFFIXES | frozenset(wanted)


DEFAULT_CODE_SUFFIXES: frozenset[str] = frozenset(
    {
        ".py", ".rs", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
        ".go", ".java", ".kt", ".swift", ".rb", ".php", ".lua",
        ".c", ".h", ".cpp", ".hpp", ".cc", ".cs", ".sh",
        # Aggiunte dopo aver guardato le grammatiche davvero installate: di
        # queste l'estrattore ha il parser, quindi produrranno relazioni vere.
        # Non aggiungo `.vb`, `.aspx`, `.cshtml` ne' `.sql`, che pure servirebbero
        # su codice .NET: la grammatica non esiste, e dichiarare un'estensione
        # senza parser darebbe un elenco piu' lungo e nessun arco in piu'.
        #  resta fuori: e' Objective-C e anche MATLAB, e l'unica grammatica
        # installata e' quella di Objective-C. Analizzare MATLAB come Objective-C
        # produrrebbe entita' plausibili e sbagliate — il difetto peggiore fra
        # quelli possibili, perche' nessuno andrebbe a controllarle.
        ".ps1", ".psm1", ".groovy", ".ex", ".exs", ".jl",
    }
)

# Nessuna esclusione propria: `should_exclude` accetta `extra_dirs` come punto
# di estensione, e per un periodo qui c'e' stata una lista di cinque nomi
# (`target`, `node_modules`, `dist`, `build`, `.venv`) tutti GIA' presenti in
# DEFAULT_EXCLUDED_DIRS. Non cambiava il comportamento, ma faceva sembrare che
# il grafo avesse una politica propria: chi domani modificasse il set condiviso
# avrebbe due posti da guardare, uno dei quali autorevole solo in apparenza.
# La regola vale per tutto il progetto o non vale — se una cartella va esclusa
# si aggiunge in `core/exclusions.py`, dove la vedono anche l'indice e la GUI.
GRAPH_EXTRA_EXCLUDED_DIRS: frozenset[str] = frozenset()

# 2 adds the entity level next to the file aggregation. A cache written by
# version 1 has no `entities`, so it reads as absent rather than empty and is
# rebuilt instead of silently answering "nothing calls this".
# 3 scarta gli archi verso i tipi del linguaggio: erano il 10,2% del grafo di
# MedDesk (579 archi verso una singola entita' «String»), e una cache vecchia
# li conterrebbe ancora — quindi va ricostruita, non riletta.
# 4 aggiunge gli archi dedotti dal tipo dichiarato del ricevitore, con il campo
# `confidence`. Una cache di versione 3 non li ha e risponderebbe ancora
# «nessun chiamante» dove ce ne sono.
# 5 aggiunge l'elenco delle funzioni di test riconosciute dall'attributo: senza
# quello «quali test coprono questa funzione» resta vuoto su Rust.
CACHE_VERSION = 5

# Quanti elementi mostrare per gruppo in `explain_entity`. Non e' una soglia
# misurata: e' un compromesso sul costo in token per l'agente che legge. Sta qui
# come costante e non come letterale nella firma perche' un numero scelto a
# occhio deve almeno essere visibile e modificabile in un solo posto. Il taglio
# non nasconde niente: `totals` porta il numero vero e `truncated` dice che si
# sta guardando una parte.
EXPLAIN_GROUP_LIMIT = 12


CODE_SUFFIXES = DEFAULT_CODE_SUFFIXES


# Nomi che l'estrattore promuove a entita' ma che sono tipi del linguaggio, non
# codice di questo progetto. Il caso misurato: 579 archi «references» verso una
# singola entita' `license.rs::String`, il 10,2% di tutti gli archi entita' del
# grafo di MedDesk. Ogni `String` in qualunque file finiva agganciato lì, quindi
# «cosa usa questa funzione» rispondeva anche `String` — rumore che allunga la
# risposta e sposta in basso le relazioni vere, che e' il modo piu' silenzioso
# di rendere inutile uno strumento.
#
# Si scarta solo la relazione `references`: `String::from_utf8` e' una chiamata
# vera e resta.
LANGUAGE_TYPE_NAMES: frozenset[str] = frozenset(
    {
        "string", "str", "result", "option", "vec", "box", "rc", "arc", "cow",
        "hashmap", "hashset", "btreemap", "vecdeque", "refcell", "cell", "mutex",
        "rwlock", "duration", "instant", "path", "pathbuf", "osstring", "self",
        "some", "none", "ok", "err", "bool", "char", "usize", "isize", "f32",
        "f64", "u8", "u16", "u32", "u64", "u128", "i8", "i16", "i32", "i64",
        "i128", "any", "dict", "list", "set", "tuple", "int", "float", "object",
    }
)

NOISY_RELATIONS: frozenset[str] = frozenset({"references"})


class GraphifyUnavailable(RuntimeError):
    """Raised when a caller asks for extraction and Graphify is not installed."""


def graphify_available() -> bool:
    """True when the optional extraction backend can be imported."""

    try:
        import graphify.extract  # noqa: F401
    except Exception:
        return False
    return True


@dataclass(frozen=True)
class FileEdge:
    """One relation between two files, aggregated from entity edges."""

    source_file: str  # repo-relative, posix
    target_file: str
    relation_type: str
    weight: int  # number of underlying entity-level edges

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_file": self.source_file,
            "target_file": self.target_file,
            "relation_type": self.relation_type,
            "weight": self.weight,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FileEdge":
        return cls(
            source_file=data["source_file"],
            target_file=data["target_file"],
            relation_type=data["relation_type"],
            weight=int(data["weight"]),
        )


@dataclass(frozen=True)
class EntityEdge:
    """One relation between two named code entities."""

    source: str          # "file::name" of the caller
    target: str
    relation_type: str
    source_file: str     # repo-relative, posix
    target_file: str
    # "resolved" = letto dal parser. "inferred" = dedotto dal tipo dichiarato
    # del ricevitore (vedi `receiver_types`). I due non vanno confusi: un grafo
    # che presenta una deduzione come una lettura mente, e chi legge si fida.
    confidence: str = "resolved"

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "relation_type": self.relation_type,
            "source_file": self.source_file,
            "target_file": self.target_file,
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EntityEdge":
        return cls(
            source=data["source"],
            target=data["target"],
            relation_type=data["relation_type"],
            source_file=data.get("source_file", ""),
            target_file=data.get("target_file", ""),
            confidence=data.get("confidence", "resolved"),
        )


@dataclass
class FileGraph:
    """Aggregated code graph for one project root.

    Carries two levels. ``edges`` is the file-level aggregation the project
    view draws; ``entities`` keeps the function- and class-level relations,
    which is where the questions actually live — "who calls this", "which
    test covers it" cannot be answered by a file-level edge.
    """

    root: str
    edges: list[FileEdge] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)
    entities: list[EntityEdge] = field(default_factory=list)
    # Impronta dei sorgenti al momento della costruzione: percorso ->
    # "mtime:dimensione". Serve a sapere da soli quando il grafo e' invecchiato,
    # invece di chiedere a una persona di ricordarselo.
    fingerprint: dict[str, str] = field(default_factory=dict)
    # mtime delle cartelle che contenevano sorgenti, e dei loro genitori fino
    # alla radice. Un file aggiunto o cancellato cambia l'mtime della cartella
    # che lo conteneva, e una cartella nuova cambia quello del genitore: uno
    # `stat` per cartella basta a scoprire cio' che l'impronta dei file da sola
    # non vede, senza ripercorrere l'albero (11,5 s su questo progetto, contro
    # 20 ms).
    dir_fingerprint: dict[str, str] = field(default_factory=dict)
    # `file::nome` delle funzioni di test, riconosciute dall'attributo `#[test]`.
    # In Rust ne' il percorso ne' il nome le tradiscono, quindi senza questo
    # elenco «quali test coprono questa funzione» rispondeva vuoto sempre: 9
    # interrogazioni su 9 su un progetto reale.
    test_entities: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "cache_version": CACHE_VERSION,
            "root": self.root,
            "edges": [edge.to_dict() for edge in self.edges],
            "entities": [edge.to_dict() for edge in self.entities],
            "fingerprint": self.fingerprint,
            "dir_fingerprint": self.dir_fingerprint,
            "test_entities": self.test_entities,
            "stats": self.stats,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FileGraph":
        return cls(
            root=data["root"],
            edges=[FileEdge.from_dict(e) for e in data.get("edges", [])],
            stats=data.get("stats", {}),
            entities=[EntityEdge.from_dict(e) for e in data.get("entities", [])],
            fingerprint=data.get("fingerprint", {}),
            dir_fingerprint=data.get("dir_fingerprint", {}),
            test_entities=data.get("test_entities", []),
        )

    def file_set(self) -> set[str]:
        """Every repo-relative file the graph mentions, on either side.

        Used to map a project store's RELATIVE document paths onto a graph
        extracted under an absolute root: the shared suffix is all there is
        to match on.
        """

        files: set[str] = set()
        for edge in self.edges:
            files.add(edge.source_file)
            files.add(edge.target_file)
        for edge in self.entities:
            files.add(edge.source_file)
            files.add(edge.target_file)
        files.discard("")
        return files

    def staleness(self) -> dict[str, Any]:
        """Quali sorgenti sono cambiati da quando il grafo e' stato costruito.

        Un grafo del codice e' una fotografia: se i file cambiano, risponde sul
        passato senza dirlo, e la risposta e' indistinguibile da una giusta.
        Chiedere a una persona di ricordarsi di ricostruirlo e' un difetto di
        progetto, non una sua responsabilita' — quindi il grafo se ne accorge
        da solo.

        Il confronto usa solo `stat`: mtime al nanosecondo (`st_mtime_ns`,
        non troncato al secondo: `return 1` -> `return 2` scritto entro lo
        stesso secondo ha la stessa dimensione, e un mtime arrotondato non lo
        vedrebbe) e dimensione dei file per le
        modifiche, mtime delle cartelle per aggiunte e cancellazioni.
        Ripercorrere l'albero per elencare i file presenti darebbe la stessa
        risposta ma costa 11,5 s su questo progetto contro 20 ms, e a quel
        prezzo il controllo non si potrebbe fare a ogni lettura — cioe' non
        servirebbe a niente.

        Restituisce ``{"stale": bool, "changed": [...], "missing": [...],
        "tree": [...]}``. Senza impronta (grafo di una versione precedente)
        restituisce ``stale: None``: non lo sa, e non finge di saperlo.
        """

        if not self.fingerprint:
            return {"stale": None, "reason": "il grafo non porta l'impronta dei sorgenti"}

        root = Path(self.root)
        changed, missing = [], []
        for relative, stamp in self.fingerprint.items():
            try:
                info = (root / relative).stat()
            except OSError:
                missing.append(relative)
                continue
            if f"{info.st_mtime_ns}:{info.st_size}" != stamp:
                changed.append(relative)

        tree = []
        for relative, stamp in self.dir_fingerprint.items():
            path = root if relative == "." else root / relative
            try:
                current = str(path.stat().st_mtime_ns)
            except OSError:
                tree.append(relative)
                continue
            if current != stamp:
                tree.append(relative)

        return {
            "stale": bool(changed or missing or tree),
            "changed": sorted(changed)[:20],
            "missing": sorted(missing)[:20],
            "tree": sorted(tree)[:20],
            "counts": {
                "changed": len(changed),
                "missing": len(missing),
                "tree": len(tree),
            },
        }


def collect_source_files(
    root: Path, *, limit: int | None = None, skipped_out: dict[str, int] | None = None
) -> list[Path]:
    """Source files under *root*, honouring the project's exclusion rules.

    Reuses `should_exclude` rather than Graphify's own `collect_files` so a
    directory the rest of the pipeline refuses to index cannot enter the
    graph either — the two views of a project stay consistent.
    """

    patterns = load_gitignore_patterns(root)
    suffixes = code_suffixes()
    found: list[Path] = []
    # Contati per estensione, non solo in totale: «847 file scartati» non dice
    # niente, «847 di cui 812 .json e 30 .zig» dice se il filtro sta buttando
    # via codice vero.
    skipped: dict[str, int] = {} if skipped_out is None else skipped_out
    for path in sorted(root.rglob("*")):
        # L'esclusione viene PRIMA del filtro sull'estensione, anche se e' il
        # controllo piu' costoso: il conteggio degli scartati deve significare
        # «codice dentro l'albero che indicizziamo e che ho scelto di non
        # analizzare». Nell'ordine opposto contava anche i 33.000 `.json` e i
        # 17.000 `.pyc` dentro `.venv`, cioe' dava un numero allarmante e senza
        # informazione — peggio di nessun numero, perche' sembra una misura.
        if should_exclude(
            path,
            root=root,
            extra_dirs=set(GRAPH_EXTRA_EXCLUDED_DIRS),
            gitignore_patterns=patterns,
        ):
            continue
        if not path.is_file():
            continue
        if path.suffix.lower() not in suffixes:
            if path.suffix:
                skipped[path.suffix.lower()] = skipped.get(path.suffix.lower(), 0) + 1
            continue
        found.append(path)
        if limit is not None and len(found) >= limit:
            break
    return found


def _relative(path_value: Any, root: Path) -> str | None:
    """Normalise a Graphify `source_file` to a repo-relative posix path."""

    if not path_value or not isinstance(path_value, str):
        return None
    text = path_value.replace("\\", "/").strip()
    if not text:
        return None
    candidate = Path(text)
    # `is_absolute()` is False on Windows for a rooted-but-driveless path
    # (`/elsewhere/b.py`), which would then be read as repo-relative and
    # silently pull a file from outside the project into the graph. Treat a
    # leading separator as rooted regardless of platform.
    if candidate.is_absolute() or text.startswith("/"):
        try:
            relative = candidate.relative_to(root).as_posix()
        except ValueError:
            return None
    else:
        relative = text.lstrip("./")
    # An import of an external package (`@tauri-apps/api/window`, `serde`)
    # is minted as a node with that specifier in `source_file`. Left alone
    # it becomes a phantom file in the graph and shows up as a project node
    # that no document can ever match, so require a real code suffix.
    if Path(relative).suffix.lower() not in CODE_SUFFIXES:
        return None
    return relative


def aggregate_to_files(
    nodes: Iterable[dict[str, Any]],
    edges: Iterable[dict[str, Any]],
    root: Path,
) -> list[FileEdge]:
    """Collapse entity-level edges into one edge per (file, file, relation).

    Edges are dropped when either end has no source file (Graphify mints
    nodes for built-in types like `AppHandle` that belong to no file) and
    when both ends are the same file — an intra-file call says nothing
    about how the project is wired together, and at 3k+ such edges it
    would dominate the weights.
    """

    node_file: dict[str, str] = {}
    for node in nodes:
        rel = _relative(node.get("source_file"), root)
        if rel:
            node_file[node["id"]] = rel

    counts: dict[tuple[str, str, str], int] = {}
    for edge in edges:
        src = node_file.get(edge.get("source"))
        tgt = node_file.get(edge.get("target"))
        if not src or not tgt or src == tgt:
            continue
        key = (src, tgt, str(edge.get("relation") or "related"))
        counts[key] = counts.get(key, 0) + 1

    return [
        FileEdge(source_file=src, target_file=tgt, relation_type=rel, weight=weight)
        for (src, tgt, rel), weight in sorted(counts.items(), key=lambda kv: -kv[1])
    ]


def collect_entity_edges(
    nodes: Iterable[dict[str, Any]],
    edges: Iterable[dict[str, Any]],
    root: Path,
) -> list[EntityEdge]:
    """Keep the function- and class-level relations, addressed by file::name.

    The file aggregation answers "which parts of the project depend on which";
    it cannot answer "who calls this function", because a file-level edge has
    forgotten which function it came from. Entity ids from the extractor are
    not reused: they are its identifiers and its naming could change between
    versions, so each end is re-addressed as ``file::label``, which is stable
    and readable.

    Intra-file edges are KEPT here, unlike in the file aggregation: a function
    calling another function in the same module is exactly the kind of thing
    someone asking "what uses this" wants to see.
    """

    labelled: dict[str, tuple[str, str]] = {}
    for node in nodes:
        rel = _relative(node.get("source_file"), root)
        label = str(node.get("label") or "").strip()
        if rel and label:
            labelled[node["id"]] = (rel, label.rstrip("()"))

    seen: set[tuple[str, str, str]] = set()
    out: list[EntityEdge] = []
    for edge in edges:
        src = labelled.get(edge.get("source"))
        tgt = labelled.get(edge.get("target"))
        if not src or not tgt:
            continue
        relation = str(edge.get("relation") or "related")
        source = f"{src[0]}::{src[1]}"
        target = f"{tgt[0]}::{tgt[1]}"
        if source == target:
            continue
        # I tipi del linguaggio non sono codice di questo progetto. Senza questo
        # filtro `String` da sola raccoglieva 579 archi, il 10,2% del grafo: ogni
        # `String` di ogni file agganciata a un'unica entita' inventata, che
        # spingeva in basso le relazioni vere in ogni risposta.
        if relation in NOISY_RELATIONS and tgt[1].lower() in LANGUAGE_TYPE_NAMES:
            continue
        key = (source, target, relation)
        if key in seen:
            continue
        seen.add(key)
        out.append(EntityEdge(
            source=source, target=target, relation_type=relation,
            source_file=src[0], target_file=tgt[0],
        ))
    return out


def build_file_graph(
    root: Path,
    *,
    limit: int | None = None,
    parallel: bool = True,
) -> FileGraph:
    """Extract *root* and aggregate the result to file level.

    `parallel=True` uses Graphify's process pool. It needs a proper
    ``if __name__ == "__main__"`` entry (a console script provides one); in
    an embedded caller that lacks it, Graphify logs a warning and falls
    back to sequential extraction rather than failing — on a 150-file tree
    that is the difference between 7 and 97 seconds, so callers that can
    guarantee a main guard should leave it on.
    """

    if not graphify_available():
        # Le virgolette intorno al nome non sono un vezzo: senza, PowerShell e
        # zsh interpretano le parentesi quadre e il comando fallisce con un
        # errore che non nomina la causa. Un rimedio suggerito che non funziona
        # quando lo si incolla e' peggio di nessun rimedio.
        raise GraphifyUnavailable(
            "manca il pacchetto che estrae il grafo del codice. Installalo con:\n"
            '    pip install --upgrade "truenex-memory[graph]"\n'
            '    (con pipx: pipx install --force "truenex-memory[graph]")'
        )

    from graphify.extract import extract

    root = root.resolve()
    skipped: dict[str, int] = {}
    paths = collect_source_files(root, limit=limit, skipped_out=skipped)
    if not paths:
        return FileGraph(root=root.as_posix(), edges=[], stats={"files": 0})

    result = extract(paths, root=root, parallel=parallel)
    nodes = result.get("nodes", [])
    entity_edges = result.get("edges", [])
    file_edges = aggregate_to_files(nodes, entity_edges, root)

    relation_totals: dict[str, int] = {}
    for edge in file_edges:
        relation_totals[edge.relation_type] = (
            relation_totals.get(edge.relation_type, 0) + edge.weight
        )

    fingerprint: dict[str, str] = {}
    directories: set[Path] = {root}
    for path in paths:
        try:
            info = path.stat()
        except OSError:  # pragma: no cover - file rimosso durante la scansione
            continue
        fingerprint[path.relative_to(root).as_posix()] = f"{info.st_mtime_ns}:{info.st_size}"
        # Anche i genitori: una cartella creata da zero non e' fra quelle note,
        # ma cambia l'mtime di chi la contiene.
        parent = path.parent
        while parent != root and root in parent.parents:
            directories.add(parent)
            parent = parent.parent
        directories.add(parent)

    dir_fingerprint: dict[str, str] = {}
    for directory in directories:
        try:
            stamp = str(directory.stat().st_mtime_ns)
        except OSError:  # pragma: no cover - cartella rimossa durante la scansione
            continue
        relative = "." if directory == root else directory.relative_to(root).as_posix()
        dir_fingerprint[relative] = stamp

    entita = collect_entity_edges(nodes, entity_edges, root)

    # Chiamate a metodo che il parser non ha risolto, dedotte dai tipi che il
    # codice dichiara a voce alta. Si aggiungono solo quelle che NON esistono
    # gia': l'arco del parser vince sempre, perche' e' letto e non dedotto.
    from truenex_memory.graph.receiver_types import (
        collect_test_functions,
        infer_receiver_calls,
    )

    esistenti = {(e.source, e.target) for e in entita}
    # Come il parser chiama le entita' che conosce gia'. Serve perche' un metodo
    # e' registrato come `file::.nome` col punto davanti: creando l'arco dedotto
    # con `file::nome` la stessa funzione diventava DUE entita' distinte, e la
    # risposta elencava due volte lo stesso bersaglio con meta' dei chiamanti per
    # ciascuno — un difetto peggiore di quello che stavo correggendo.
    grafia = {}
    for arco in entita:
        for lato, file_lato in ((arco.source, arco.source_file), (arco.target, arco.target_file)):
            nome = lato.split("::", 1)[-1]
            grafia[(file_lato, nome.lstrip("."))] = lato

    for dedotta in infer_receiver_calls(root, sorted(fingerprint)):
        sorgente = grafia.get(
            (dedotta.source_file, dedotta.source_name),
            f"{dedotta.source_file}::{dedotta.source_name}",
        )
        bersaglio = grafia.get(
            (dedotta.target_file, dedotta.target_name),
            f"{dedotta.target_file}::{dedotta.target_name}",
        )
        if (sorgente, bersaglio) in esistenti or sorgente == bersaglio:
            continue
        esistenti.add((sorgente, bersaglio))
        entita.append(
            EntityEdge(
                source=sorgente,
                target=bersaglio,
                relation_type="calls",
                source_file=dedotta.source_file,
                target_file=dedotta.target_file,
                confidence="inferred",
            )
        )

    letti = {}
    for relativo in sorted(fingerprint):
        if Path(relativo).suffix.lower() != ".rs":
            continue
        try:
            letti[relativo] = (root / relativo).read_text(encoding="utf-8", errors="replace")
        except OSError:  # pragma: no cover
            continue
    grafia_test = {
        f"{f}::{n}" for f, n in collect_test_functions(letti)
    }
    # Anche nella grafia del parser, che ai metodi mette un punto davanti.
    for arco in entita:
        for lato, file_lato in ((arco.source, arco.source_file), (arco.target, arco.target_file)):
            nudo = f"{file_lato}::{lato.split('::', 1)[-1].lstrip('.')}"
            if nudo in grafia_test:
                grafia_test.add(lato)

    return FileGraph(
        root=root.as_posix(),
        edges=file_edges,
        entities=entita,
        test_entities=sorted(grafia_test),
        fingerprint=fingerprint,
        dir_fingerprint=dir_fingerprint,
        stats={
            "files": len(paths),
            # Cio' che il filtro per estensione ha lasciato fuori, per estensione:
            # senza questo, un `.zig` ignorato e' indistinguibile da un `.zig`
            # che non chiama niente.
            "skipped_by_suffix": dict(sorted(skipped.items(), key=lambda kv: -kv[1])[:10]),
            "skipped_total": sum(skipped.values()),
            "entity_nodes": len(nodes),
            "entity_edges": len(entity_edges),
            "file_edges": len(file_edges),
            "relations": dict(
                sorted(relation_totals.items(), key=lambda kv: -kv[1])
            ),
        },
    )


def explain_entity(
    graph: FileGraph, target: str, *, limit: int = EXPLAIN_GROUP_LIMIT
) -> dict[str, Any]:
    """What the code graph knows about one function, class or file.

    Answers the questions retrieval cannot: who calls this, what it calls,
    which tests exercise it, and — via the extractor's `rationale_for` edges
    — which docstring explains it. Those are structural facts, not text
    matches, so they are correct or absent, never "plausible".

    Matching is a case-insensitive substring over ``file::name``, so both
    `_require_most_informative_token` and `store/repository.py` work as
    targets.

    I gruppi sono limitati perche' questa risposta alimenta un agente che paga
    a token, ma il limite riguarda solo QUANTI se ne mostrano: ``totals`` porta
    sempre il numero vero e ``truncated`` dice se si sta guardando una parte.
    Un tetto silenzioso qui sarebbe grave e non "prudente": «chi chiama questa
    funzione» con 37 chiamanti e 12 mostrati e' una risposta incompleta
    indistinguibile da una completa, ed e' esattamente il tipo di errore che
    questo strumento esiste per evitare. Il conteggio si fa prima di tagliare.
    """

    needle = target.replace("\\", "/").lower().strip()
    if not needle:
        return {"target": target, "matched": [], "callers": [], "calls": [],
                "tests": [], "rationale": [], "totals": {}, "truncated": {}}

    # Un nome esatto batte la sottostringa, e non e' un dettaglio: cercando
    # `do_work` la sottostringa prende anche `test_do_work`, quindi l'arco del
    # test risultava partire E arrivare al bersaglio e veniva scartato come
    # auto-riferimento — cioe' la domanda "quali test lo coprono" rispondeva
    # sempre "nessuno".
    exact: set[str] = set()
    loose: set[str] = set()
    for edge in graph.entities:
        for side in (edge.source, edge.target):
            lowered = side.lower()
            # Il punto iniziale: un metodo e' registrato come `.verify_token`,
            # quindi il confronto esatto con `verify_token` falliva e si cadeva
            # sulla sottostringa, che prendeva anche `verify_token_for_device`.
            # Chi cerca un nome preciso riceveva l'omonimo piu' lungo insieme al
            # suo, senza modo di distinguerli.
            name = lowered.split("::", 1)[-1].lstrip(".")
            if name == needle or lowered == needle:
                exact.add(side)
            elif needle in lowered:
                loose.add(side)
    matched = exact or loose

    # L'elenco dei test riconosciuti dall'attributo. Il ripiego sul percorso
    # resta per i linguaggi che mettono i test in file separati.
    noti_come_test = set(graph.test_entities)
    callers, calls, tests, rationale = [], [], [], []
    for edge in graph.entities:
        hits_target = edge.target in matched
        hits_source = edge.source in matched
        if hits_target and not hits_source:
            if edge.relation_type == "rationale_for":
                rationale.append(edge.source.split("::", 1)[-1])
            elif edge.source in noti_come_test or "test" in edge.source_file.lower():
                tests.append({
                    "entity": edge.source,
                    "relation": edge.relation_type,
                    "confidence": edge.confidence,
                })
            elif edge.relation_type != "contains":
                callers.append({
                    "entity": edge.source,
                    "relation": edge.relation_type,
                    "confidence": edge.confidence,
                })
        elif hits_source and not hits_target and edge.relation_type != "contains":
            calls.append({
                "entity": edge.target,
                "relation": edge.relation_type,
                "confidence": edge.confidence,
            })

    def _dedup(items, key):
        seen, out = set(), []
        for item in items:
            k = key(item)
            if k not in seen:
                seen.add(k)
                out.append(item)
        return out

    # Copertura dell'estrazione, dichiarata nella risposta e non in una
    # documentazione che nessuno legge.
    #
    # Misurato il 2026-08-22 su MedDesk: delle funzioni Rust con almeno un
    # chiamante in un ALTRO file, il grafo non ne trova nessuno nell'83% dei
    # casi (19 su 23). Tutti i silenzi hanno la stessa forma — una chiamata a
    # metodo attraverso un ricevitore (`engine.build_soap_prompt(...)`) — perche'
    # l'estrattore non risolve il tipo del ricevitore fra file diversi.
    #
    # Perche' va detto qui e non altrove: un agente a cui si dice di preferire
    # il grafo alla lettura dei file risponde «lo chiama solo il test» con
    # sicurezza, e sbaglia. Un'assenza presentata come informativa quando non lo
    # e' e' peggio di nessuna risposta.
    caveat = _coverage_caveat(matched, callers, graph)

    groups = {
        "matched": sorted(matched),
        "callers": _dedup(callers, lambda i: i["entity"]),
        "calls": _dedup(calls, lambda i: i["entity"]),
        "tests": _dedup(tests, lambda i: i["entity"]),
        # Chiave sul testo intero: tagliata agli 80 caratteri iniziali fondeva
        # due docstring diversi che cominciano uguale — silenziosamente, e per
        # nessun guadagno.
        "rationale": _dedup(rationale, lambda i: i),
    }
    totals = {name: len(items) for name, items in groups.items()}
    return {
        "target": target,
        **{name: items[:limit] for name, items in groups.items()},
        "totals": totals,
        "truncated": {name: total for name, total in totals.items() if total > limit},
        "coverage": caveat,
        "root": graph.root,
    }


# Estensioni per cui l'estrazione delle chiamate a metodo fra file e'
# incompleta, con la misura accanto. Non e' un elenco di sospetti: e' cio' che
# e' stato contato.
WEAK_METHOD_RESOLUTION: dict[str, str] = {
    # Codici, non prose. Questa risposta viene letta a ogni interrogazione da un
    # modello che paga a token, e la spiegazione era identica ogni volta: il 22%
    # della risposta erano le stesse quattro righe. Il significato dei codici sta
    # nella descrizione del tool, che il protocollo consegna UNA volta per
    # sessione — dirlo dove si paga una volta invece che dove si paga sempre.
    # La CLI li riespande in italiano, perche' li' li legge una persona e non
    # costano niente.
    ".rs": "cross_file_method_calls: 22% missing (23 fn, 2026-08-22, was 83%)",
}

# Linguaggi che tengono i test nello stesso file della funzione. Li' il
# riconoscimento per percorso non funziona, e in Rust nemmeno quello per nome:
# un `#[test]` si chiama `publisher_requires_a_fully_correlated_receipt`, senza
# la parola «test» da nessuna parte. Dire «nessun test» quando non si sa e' una
# bugia, quindi si dichiara di non sapere.
TESTS_IN_SAME_FILE: frozenset[str] = frozenset({".rs"})


def _coverage_caveat(
    matched: set[str], callers: list[dict[str, Any]], graph: FileGraph
) -> dict[str, Any]:
    """Cosa questa risposta NON puo' garantire."""

    if not matched:
        return {}
    file_bersaglio = {nome.split("::", 1)[0] for nome in matched}
    suffissi = {Path(nome).suffix.lower() for nome in file_bersaglio}

    avvisi: list[str] = []
    for suffisso in sorted(suffissi):
        nota = WEAK_METHOD_RESOLUTION.get(suffisso)
        if nota:
            avvisi.append(nota)

    fuori = {c["entity"].split("::", 1)[0] for c in callers} - file_bersaglio
    coverage: dict[str, Any] = {"callers_outside_the_defining_file": len(fuori)}
    if avvisi and not fuori:
        # La firma esatta del difetto: tutti i chiamanti nello stesso file.
        avvisi.append("no_caller_outside_defining_file: typical shape of a missed extraction")
    if suffissi & TESTS_IN_SAME_FILE and not graph.test_entities:
        # Solo se il grafo non porta l'elenco: con l'attributo riconosciuto un
        # elenco vuoto torna a significare «nessuno», che e' un'informazione.
        coverage["tests_detection"] = "unknown: tests share the file and the name carries no marker"
    if avvisi:
        coverage["incomplete"] = avvisi
    return coverage


def cache_slug(root: Path) -> str:
    """Filesystem-safe name for a project root."""

    text = root.resolve().as_posix().lower()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-") or "root"


def cache_path(cache_dir: Path, root: Path) -> Path:
    return cache_dir / f"{cache_slug(root)}.json"


def save_file_graph(graph: FileGraph, cache_dir: Path) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_path(cache_dir, Path(graph.root))
    target.write_text(
        json.dumps(graph.to_dict(), ensure_ascii=False), encoding="utf-8"
    )
    return target


def load_file_graph(cache_dir: Path, root: Path) -> FileGraph | None:
    """Read a cached graph, or None when absent or written by another version."""

    target = cache_path(cache_dir, root)
    if not target.exists():
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if data.get("cache_version") != CACHE_VERSION:
        return None
    return FileGraph.from_dict(data)


def default_cache_dirs(db_path: Path) -> list[Path]:
    """Where to look for cached graphs, most specific first.

    A graph describes a *source tree*, not a store, so one built while
    working against the global store is equally valid for a project store
    covering the same files. Looking beside the database first and in the
    user-level directory second means `graph build` and the desktop server
    do not have to agree on which store is in play — they did not, and the
    project view silently served no edges as a result.
    """

    candidates = [
        db_path.parent / "code_graphs",
        Path.home() / ".truenex-memory" / "code_graphs",
    ]
    seen: set[Path] = set()
    ordered: list[Path] = []
    for candidate in candidates:
        resolved = candidate.expanduser()
        if resolved not in seen:
            seen.add(resolved)
            ordered.append(resolved)
    return ordered


def find_cached_graph(
    cache_dir: Path | Iterable[Path], document_paths: Iterable[str]
) -> FileGraph | None:
    """Pick the cached graph that covers the most of *document_paths*.

    A graph is cached under the root it was extracted from, which need not
    equal the common prefix of a project's indexed documents: MedDesk is
    indexed at repository level while its code graph is usefully built at
    `runtime/tauri-app`. Rather than guess, score every cached graph by how
    many documents fall under its root and take the best — a graph that
    covers nothing scores zero and is ignored.

    Accepts one directory or several (see `default_cache_dirs`); with
    several, the best-covering graph across all of them wins and ties go to
    the earlier directory.
    """

    if isinstance(cache_dir, (str, Path)):
        dirs = [Path(cache_dir)]
    else:
        dirs = [Path(d) for d in cache_dir]

    normalized = [str(p).replace("\\", "/") for p in document_paths]
    best: tuple[tuple[int, int], FileGraph] | None = None
    for directory in dirs:
        if not directory.is_dir():
            continue
        for entry in sorted(directory.glob("*.json")):
            try:
                data = json.loads(entry.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if data.get("cache_version") != CACHE_VERSION:
                continue
            graph = FileGraph.from_dict(data)
            prefix = graph.root.rstrip("/") + "/"
            files = graph.file_set()
            # Score absolute and relative evidence separately. A document
            # path under the graph root proves the graph is about THIS tree;
            # a bare relative path (which is what a project store holds)
            # only proves a filename is shared, and `README.md` is shared by
            # every project. Absolute evidence therefore outranks it.
            absolute = sum(1 for p in normalized if p.startswith(prefix))
            relative = sum(
                1
                for p in normalized
                if not p.startswith(prefix) and p.lstrip("./") in files
            )
            score = (absolute, relative)
            if any(score) and (best is None or score > best[0]):
                best = (score, graph)
    return best[1] if best else None


def document_edges(
    graph: FileGraph,
    documents: Iterable[tuple[str, str]],
) -> list[dict[str, Any]]:
    """Map file-level edges onto document ids for the API payload.

    *documents* is an iterable of ``(document_id, absolute_path)``. Paths
    are matched on the repo-relative suffix, so the mapping survives a
    project being indexed under a different absolute prefix than the one
    it was extracted from.
    """

    prefix = graph.root.rstrip("/") + "/"
    files = graph.file_set()
    by_relative: dict[str, str] = {}
    for doc_id, doc_path in documents:
        normalized = str(doc_path).replace("\\", "/")
        if normalized.startswith(prefix):
            rel = normalized[len(prefix):]
        elif normalized.lstrip("./") in files:
            # A project store records paths relative to the project root,
            # while the global store records them absolute. Both must map,
            # or the project view serves no edges against a project store —
            # which is exactly how this shipped broken the first time.
            rel = normalized.lstrip("./")
        else:
            continue
        by_relative.setdefault(rel, str(doc_id))

    mapped: list[dict[str, Any]] = []
    for edge in graph.edges:
        source = by_relative.get(edge.source_file)
        target = by_relative.get(edge.target_file)
        if not source or not target or source == target:
            continue
        mapped.append(
            {
                "source": source,
                "target": target,
                "relation_type": edge.relation_type,
                "weight": edge.weight,
            }
        )
    return mapped


# Righe che non sono chiamate anche se contengono il nome: commenti, e la
# definizione stessa. Il filtro e' grezzo di proposito — la review di codex
# chiedeva di validare i candidati con tree-sitter, ed e' giusto, ma un filtro
# grezzo DICHIARATO e' meglio di uno raffinato che tarda: qui i risultati sono
# etichettati come indizi, non come relazioni.
_NON_CALL_LINE = re.compile(r"^\s*(//|#|\*|/\*|--)")
_DEFINITION_LINE = re.compile(r"\b(fn|def|function|pub\s+fn|async\s+fn)\s+$")


def text_call_sites(
    root: Path,
    name: str,
    *,
    limit: int = 20,
    files: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """Punti in cui *name* compare come chiamata, cercati nel TESTO.

    Perche' esiste: il parser non risolve le chiamate a metodo attraverso un
    ricevitore da un altro file, e su Rust perde l'83% dei chiamanti cross-file.
    Il grafo quindi risponde «nessun chiamante» quando invece ce ne sono, e un
    agente a cui abbiamo detto di preferire il grafo alla lettura dei file
    riporta quel «nessuno» come un fatto.

    Questa funzione non ripara il grafo: ripara la RISPOSTA. I risultati vanno
    presentati in un campo separato e dichiarati per quello che sono — righe di
    testo compatibili, non relazioni verificate. Confondere le due qualita' di
    verita' nello stesso elenco sarebbe peggio del difetto che stiamo
    correggendo, perche' distruggerebbe la fiducia in tutto il resto.

    Cerca `nome(` e `.nome(`, salta le righe di commento e la riga di
    definizione. Non distingue una chiamata dentro una stringa: e' un limite
    dichiarato, e il motivo per cui il campo si chiama «candidati».
    """

    if not name.strip():
        return []
    ago = re.compile(rf"(?<![\w]){re.escape(name)}\s*\(")
    trovati: list[dict[str, Any]] = []
    # I file su cui cercare arrivano dal GRAFO, non da una nuova scansione
    # dell'albero: sono esattamente quelli che il parser ha letto, e ripercorrere
    # le cartelle costava 1,5 s per interrogazione — troppo per una risposta che
    # deve essere immediata, quindi il ripiego verrebbe spento e non servirebbe a
    # niente.
    elenco = (
        [root / relativo for relativo in files]
        if files is not None
        else collect_source_files(root)
    )
    for percorso in elenco:
        try:
            testo = percorso.read_text(encoding="utf-8", errors="replace")
        except OSError:  # pragma: no cover - file spartito
            continue
        if name not in testo:
            continue
        for numero, riga in enumerate(testo.splitlines(), start=1):
            if not ago.search(riga) or _NON_CALL_LINE.match(riga):
                continue
            prima = riga[: riga.index(name)]
            if _DEFINITION_LINE.search(prima):
                continue
            try:
                nome_file = percorso.relative_to(root).as_posix()
            except ValueError:  # pragma: no cover - percorso fuori dalla radice
                nome_file = percorso.as_posix()
            trovati.append(
                {
                    "file": nome_file,
                    "line": numero,
                    "text": riga.strip()[:160],
                }
            )
            if len(trovati) >= limit:
                return trovati
    return trovati

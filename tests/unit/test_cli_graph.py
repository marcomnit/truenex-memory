"""I comandi `graph` dalla riga di comando.

Perche' esistono questi test. Il grafo del codice e' la capacita' nuova di
questa versione, e la si usa quasi sempre da qui: `graph build` per costruirlo,
`graph explain` per chiedere chi chiama una funzione, `graph status` per sapere
cosa c'e' in cache. Erano provati solo a mano.

Non serve l'estrattore vero: i test scrivono una cache di grafo a mano e
verificano cio' che i comandi ne fanno — che e' la parte nostra, e la sola che
possiamo garantire.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from truenex_memory.cli.main import app
from truenex_memory.graph import CACHE_VERSION, EntityEdge, FileEdge, FileGraph

runner = CliRunner()


def _cache_con_grafo(tmp_path: Path, *, con_impronta: bool = True) -> tuple[Path, Path]:
    """Scrive una cache di grafo per un progetto finto. Ritorna (casa, radice)."""

    casa = tmp_path / "casa"
    radice = tmp_path / "progetto"
    (radice / "src").mkdir(parents=True)
    sorgente = radice / "src" / "core.py"
    sorgente.write_text("def bersaglio():\n    return 1\n", encoding="utf-8")

    grafo = FileGraph(
        root=radice.as_posix(),
        edges=[FileEdge("src/app.py", "src/core.py", "calls", 2)],
        entities=[
            EntityEdge(
                "src/app.py::chiamante", "src/core.py::bersaglio", "calls",
                "src/app.py", "src/core.py",
            ),
            EntityEdge(
                "src/prove.py::verifica", "src/core.py::bersaglio", "calls",
                "src/prove.py", "src/core.py",
            ),
        ],
        stats={"files": 2, "file_edges": 1, "entity_edges": 2},
        test_entities=["src/prove.py::verifica"],
    )
    if con_impronta:
        info = sorgente.stat()
        grafo.fingerprint = {"src/core.py": f"{info.st_mtime_ns}:{info.st_size}"}
        grafo.dir_fingerprint = {
            ".": str(radice.stat().st_mtime_ns),
            "src": str((radice / "src").stat().st_mtime_ns),
        }

    cache = casa / ".truenex-memory" / "code_graphs"
    cache.mkdir(parents=True)
    (cache / "progetto.json").write_text(
        json.dumps(grafo.to_dict()), encoding="utf-8"
    )
    return casa, radice


def test_status_lists_the_cached_graphs(tmp_path: Path) -> None:
    casa, radice = _cache_con_grafo(tmp_path)

    esito = runner.invoke(app, ["graph", "status", "--home", str(casa)])

    assert esito.exit_code == 0
    assert radice.name in esito.stdout


def test_explain_separates_callers_from_tests(tmp_path: Path) -> None:
    """Sono due domande diverse, e prima finivano nello stesso elenco."""

    casa, _ = _cache_con_grafo(tmp_path)

    esito = runner.invoke(app, ["graph", "explain", "bersaglio", "--home", str(casa)])

    assert esito.exit_code == 0
    posizione_chiamanti = esito.stdout.index("CHI LO CHIAMA")
    posizione_test = esito.stdout.index("TEST CHE LO COPRONO")
    chiamanti = esito.stdout[posizione_chiamanti:posizione_test]
    test = esito.stdout[posizione_test:]
    assert "chiamante" in chiamanti and "verifica" not in chiamanti
    assert "verifica" in test


def test_explain_reports_an_absent_target_without_crashing(tmp_path: Path) -> None:
    """L'assenza e' una risposta, ma va detta come tale."""

    casa, _ = _cache_con_grafo(tmp_path)

    esito = runner.invoke(
        app, ["graph", "explain", "funzione_che_non_esiste", "--home", str(casa)]
    )

    assert esito.exit_code == 1
    assert "non compare" in esito.stdout


def test_explain_in_json_carries_the_totals(tmp_path: Path) -> None:
    casa, _ = _cache_con_grafo(tmp_path)

    esito = runner.invoke(
        app, ["graph", "explain", "bersaglio", "--home", str(casa), "--json"]
    )

    dati = json.loads(esito.stdout)
    assert dati["totals"]["callers"] == 1
    assert dati["totals"]["tests"] == 1


def test_explain_warns_when_the_graph_is_older_than_the_code(tmp_path: Path) -> None:
    """Un grafo invecchiato risponde sul passato senza dichiararlo."""

    casa, radice = _cache_con_grafo(tmp_path)
    (radice / "src" / "core.py").write_text(
        "def bersaglio():\n    return 1 + 1 + 1\n", encoding="utf-8"
    )

    esito = runner.invoke(
        app, ["graph", "explain", "bersaglio", "--home", str(casa)],
        env={"TRUENEX_GRAPH_AUTO_REBUILD": "0"},
    )

    assert "ATTENZIONE" in esito.stdout
    assert "vecchio" in esito.stdout


def test_a_cache_from_an_older_format_is_reported_as_such(tmp_path: Path) -> None:
    """Una cache di versione precedente risponderebbe «nessun chiamante»."""

    casa, radice = _cache_con_grafo(tmp_path)
    percorso = casa / ".truenex-memory" / "code_graphs" / "progetto.json"
    dati = json.loads(percorso.read_text(encoding="utf-8"))
    dati["cache_version"] = CACHE_VERSION - 1
    percorso.write_text(json.dumps(dati), encoding="utf-8")

    esito = runner.invoke(app, ["graph", "explain", "bersaglio", "--home", str(casa)])

    assert esito.exit_code == 1
    assert "versione" in esito.stdout.lower() or "ricostruisci" in esito.stdout


def test_explain_without_any_graph_says_what_to_run(tmp_path: Path) -> None:
    esito = runner.invoke(app, ["graph", "explain", "qualsiasi", "--home", str(tmp_path)])

    assert esito.exit_code == 1
    assert "graph build" in esito.stdout


def test_build_refuses_a_path_that_is_not_a_directory(tmp_path: Path) -> None:
    file = tmp_path / "non-una-cartella.txt"
    file.write_text("x", encoding="utf-8")

    esito = runner.invoke(app, ["graph", "build", str(file), "--home", str(tmp_path)])

    assert esito.exit_code == 1
    assert "not a directory" in esito.stdout


def test_build_if_stale_does_nothing_when_current(tmp_path: Path) -> None:
    """E' cio' che rende il comando chiamabile a ogni salvataggio."""

    casa, radice = _cache_con_grafo(tmp_path)

    esito = runner.invoke(
        app, ["graph", "build", str(radice), "--home", str(casa), "--if-stale"]
    )

    assert esito.exit_code == 0
    assert "aggiornato" in esito.stdout


def test_the_missing_backend_message_quotes_the_package_name(tmp_path: Path, monkeypatch) -> None:
    """Trovato aggiornando una macchina vera.

    Il grafo e' la capacita' principale di questa versione e dipende da un
    pacchetto opzionale, ma niente lo diceva prima di provare a costruirlo: un
    requisito che si scopre da un errore e' un requisito nascosto.

    E le virgolette intorno al nome non sono un vezzo: senza, PowerShell e zsh
    interpretano le parentesi quadre e il comando suggerito fallisce con un
    errore che non nomina nemmeno la causa. Un rimedio che non funziona quando
    lo si incolla e' peggio di nessun rimedio.

    La prima versione di questo test sollevava lui l'eccezione e poi ne
    verificava il testo: una tautologia, che sarebbe passata anche cancellando
    il messaggio dal codice. Qui si chiama la funzione vera.
    """

    from truenex_memory.graph import code_graph

    monkeypatch.setattr(code_graph, "graphify_available", lambda: False)

    with pytest.raises(code_graph.GraphifyUnavailable) as errore:
        code_graph.build_file_graph(tmp_path)

    messaggio = str(errore.value)
    assert '"truenex-memory[graph]"' in messaggio, (
        "senza virgolette PowerShell interpreta le parentesi quadre"
    )
    assert "pipx" in messaggio


def test_upgrade_names_the_missing_backend(tmp_path: Path, monkeypatch) -> None:
    """`upgrade` e' il momento in cui la mancanza va detta, non dopo."""

    import truenex_memory.graph as graph_module
    from typer.testing import CliRunner as _Runner

    casa = tmp_path / "casa"
    (casa / ".truenex-memory").mkdir(parents=True)
    db = casa / ".truenex-memory" / "truenex_memory.db"
    conn = __import__("sqlite3").connect(db)
    conn.executescript(
        "CREATE TABLE schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT);"
        "INSERT INTO schema_migrations VALUES ('7', '2026-01-01');"
        "CREATE TABLE documents (id TEXT PRIMARY KEY, path TEXT);"
        "INSERT INTO documents VALUES ('a', 'x.md');"
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(graph_module, "graphify_available", lambda: False)

    esito = _Runner().invoke(
        app, ["upgrade", "--db", str(db), "--home", str(casa), "--skip-profile"]
    )

    assert esito.exit_code == 0
    assert "manca il pacchetto" in esito.stdout
    assert '"truenex-memory[graph]"' in esito.stdout

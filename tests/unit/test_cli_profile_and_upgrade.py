"""I comandi nuovi della riga di comando, provati come li usa una persona.

Perche' esistono questi test. `profile` e `upgrade` sono la superficie che un
utente incontra dopo un aggiornamento — e finora erano provati solo a mano, da
me, su questa macchina. Il rilascio 0.6.0 e' fallito in CI per la copertura
proprio su queste righe, e la soglia aveva ragione: un comando che nessun test
esegue e' un comando che si scopre rotto dall'utente.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from typer.testing import CliRunner

from truenex_memory.cli.main import app

runner = CliRunner()


def _casa(tmp_path: Path, *client: str) -> Path:
    """Una cartella utente finta con dentro i client indicati."""

    for nome in client:
        (tmp_path / nome).mkdir(parents=True, exist_ok=True)
    return tmp_path


def _archivio(percorso: Path, versione: str) -> Path:
    percorso.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(percorso)
    conn.executescript(
        "CREATE TABLE schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT);"
        f"INSERT INTO schema_migrations VALUES ('{versione}', '2026-01-01');"
        "CREATE TABLE documents (id TEXT PRIMARY KEY, path TEXT);"
        "INSERT INTO documents VALUES ('a', 'x.md');"
    )
    conn.commit()
    conn.close()
    return percorso


# ── profile show ──────────────────────────────────────────────────────────

def test_show_prints_the_block_with_its_markers() -> None:
    esito = runner.invoke(app, ["profile", "show"])

    assert esito.exit_code == 0
    assert "truenex-memory:begin" in esito.stdout
    assert "memory_search" in esito.stdout


def test_show_raw_omits_the_markers() -> None:
    """Serve a chi vuole incollare il testo altrove senza i delimitatori."""

    esito = runner.invoke(app, ["profile", "show", "--raw"])

    assert esito.exit_code == 0
    assert "truenex-memory:begin" not in esito.stdout
    assert "memory_search" in esito.stdout


# ── profile status ────────────────────────────────────────────────────────

def test_status_reports_a_client_without_the_profile(tmp_path: Path) -> None:
    casa = _casa(tmp_path, ".codex")

    esito = runner.invoke(app, ["profile", "status", "--home", str(casa)])

    assert esito.exit_code == 0
    assert "Codex" in esito.stdout
    assert "manca" in esito.stdout


def test_status_in_json_carries_the_current_version(tmp_path: Path) -> None:
    casa = _casa(tmp_path, ".codex")

    esito = runner.invoke(app, ["profile", "status", "--home", str(casa), "--json"])

    dati = json.loads(esito.stdout)
    voce = next(v for v in dati if v["client"] == "Codex")
    assert voce["current_version"] >= 1
    assert voce["action"] == "absent"


# ── profile apply ─────────────────────────────────────────────────────────

def test_dry_run_touches_nothing(tmp_path: Path) -> None:
    """La prova a vuoto e' cio' che rende sicuro provare il comando."""

    casa = _casa(tmp_path, ".codex")

    esito = runner.invoke(app, ["profile", "apply", "--home", str(casa), "--dry-run"])

    assert esito.exit_code == 0
    assert "nessun file toccato" in esito.stdout
    assert not (casa / ".codex" / "AGENTS.md").exists()


def test_apply_writes_and_is_idempotent(tmp_path: Path) -> None:
    casa = _casa(tmp_path, ".codex")

    primo = runner.invoke(app, ["profile", "apply", "--home", str(casa)])
    secondo = runner.invoke(app, ["profile", "apply", "--home", str(casa)])

    assert primo.exit_code == 0 and secondo.exit_code == 0
    assert "scritto" in primo.stdout
    assert "invariato" in secondo.stdout
    assert "truenex-memory:begin" in (casa / ".codex" / "AGENTS.md").read_text(encoding="utf-8")


def test_apply_to_a_project_keeps_what_was_there(tmp_path: Path) -> None:
    """Il caso del livello di progetto, che e' l'unico standard vero."""

    casa = _casa(tmp_path)
    progetto = tmp_path / "progetto"
    progetto.mkdir()
    (progetto / "AGENTS.md").write_text("# regole mie\n\nParla italiano.\n", encoding="utf-8")

    esito = runner.invoke(
        app, ["profile", "apply", "--home", str(casa), "--project", str(progetto)]
    )

    testo = (progetto / "AGENTS.md").read_text(encoding="utf-8")
    assert esito.exit_code == 0
    assert "Parla italiano." in testo
    assert testo.index("Parla italiano.") < testo.index("truenex-memory:begin")


# ── profile clients e check ───────────────────────────────────────────────

def test_clients_says_so_when_nobody_connected(tmp_path: Path) -> None:
    esito = runner.invoke(app, ["profile", "clients", "--home", str(tmp_path)])

    assert esito.exit_code == 0
    assert "nessun client" in esito.stdout


def test_clients_lists_who_connected_and_how_it_was_recognised(tmp_path: Path) -> None:
    from truenex_memory.adapters.profile import record_client

    registro = tmp_path / ".truenex-memory" / "clients.json"
    registro.parent.mkdir(parents=True)
    record_client("codex-mcp-client", "1.2", registro)

    esito = runner.invoke(app, ["profile", "clients", "--home", str(tmp_path)])

    assert "codex-mcp-client" in esito.stdout
    assert "Codex" in esito.stdout
    assert "nome" in esito.stdout


def test_check_says_there_is_nothing_to_measure_yet(tmp_path: Path) -> None:
    esito = runner.invoke(app, ["profile", "check", "--home", str(tmp_path)])

    assert esito.exit_code == 0
    assert "niente da misurare" in esito.stdout


def test_check_reports_a_client_that_never_searches(tmp_path: Path) -> None:
    """Il verdetto che avevo sbagliato: usare il grafo non basta a seguire il profilo."""

    from truenex_memory.adapters.profile import record_client, record_tool_use

    registro = tmp_path / ".truenex-memory" / "clients.json"
    registro.parent.mkdir(parents=True)
    record_client("mavis-local-runtime-mcp", "1.0", registro)
    for _ in range(5):
        record_tool_use("mavis-local-runtime-mcp", "memory_graph", {}, registro)

    esito = runner.invoke(app, ["profile", "check", "--home", str(tmp_path)])

    assert "MiniMax" in esito.stdout
    assert "mai cerca" in esito.stdout


# ── upgrade ───────────────────────────────────────────────────────────────

def test_upgrade_migrates_with_a_backup(tmp_path: Path) -> None:
    casa = _casa(tmp_path)
    db = _archivio(casa / ".truenex-memory" / "truenex_memory.db", "7")

    esito = runner.invoke(
        app,
        ["upgrade", "--db", str(db), "--home", str(casa), "--skip-graphs", "--skip-profile"],
    )

    assert esito.exit_code == 0
    assert "7 -> 8" in esito.stdout
    copie = list((db.parent / "backups").glob("*.db"))
    assert copie, "il backup e' la ragione per cui questo comando esiste"


def test_upgrade_says_the_graph_does_not_exist_yet(tmp_path: Path, monkeypatch) -> None:
    """Su un PC nuovo non c'e' niente da ricostruire, e tacerlo lascerebbe
    l'utente senza grafo senza sapere perche'.

    L'estrattore viene dichiarato presente di proposito: e' un extra opzionale,
    in CI non e' installato, e senza questa forzatura il test verificava quale
    dei due rami capitava nell'ambiente invece di quello che voleva provare.
    Difetto trovato dal rilascio, non da me — la prima versione passava qui e
    cadeva in CI.
    """

    import truenex_memory.graph as graph_module

    monkeypatch.setattr(graph_module, "graphify_available", lambda: True)
    casa = _casa(tmp_path)
    db = _archivio(casa / ".truenex-memory" / "truenex_memory.db", "7")

    esito = runner.invoke(
        app, ["upgrade", "--db", str(db), "--home", str(casa), "--skip-profile"]
    )

    assert esito.exit_code == 0
    assert "nessuno da ricostruire" in esito.stdout
    assert "graph build" in esito.stdout


def test_upgrade_on_a_current_store_changes_nothing(tmp_path: Path) -> None:
    """Idempotenza: si puo' rieseguire senza pensarci."""

    from truenex_memory.release.version import DB_SCHEMA_VERSION

    casa = _casa(tmp_path)
    db = _archivio(casa / ".truenex-memory" / "truenex_memory.db", DB_SCHEMA_VERSION)

    esito = runner.invoke(
        app,
        ["upgrade", "--db", str(db), "--home", str(casa), "--skip-graphs", "--skip-profile"],
    )

    assert esito.exit_code == 0
    assert f"{DB_SCHEMA_VERSION} -> {DB_SCHEMA_VERSION}" in esito.stdout
    assert not list((db.parent / "backups").glob("*.db")), (
        "niente da migrare, niente da copiare"
    )


def test_upgrade_in_json_is_machine_readable(tmp_path: Path) -> None:
    casa = _casa(tmp_path)
    db = _archivio(casa / ".truenex-memory" / "truenex_memory.db", "7")

    esito = runner.invoke(
        app,
        ["upgrade", "--db", str(db), "--home", str(casa),
         "--skip-graphs", "--skip-profile", "--json"],
    )

    dati = json.loads(esito.stdout)
    assert dati["schema"]["da"] == "7"
    assert dati["schema"]["backup"]


def test_upgrade_writes_the_profile_into_installed_clients(tmp_path: Path) -> None:
    casa = _casa(tmp_path, ".claude")
    db = _archivio(casa / ".truenex-memory" / "truenex_memory.db", "7")

    esito = runner.invoke(
        app, ["upgrade", "--db", str(db), "--home", str(casa), "--skip-graphs"]
    )

    assert esito.exit_code == 0
    assert "Claude Code" in esito.stdout
    assert (casa / ".claude" / "CLAUDE.md").exists()


def test_an_empty_graph_says_so_and_says_why(tmp_path: Path, monkeypatch) -> None:
    """Il difetto piu' insidioso di ieri, e il piu' facile da non vedere.

    Sulla macchina vera sei progetti .NET sono stati marcati «ricostruito» con
    la colonna dei file vuota: il codice scriveva il conteggio solo se era
    diverso da zero, quindi un grafo vuoto era indistinguibile da uno di cui
    non si sapeva la dimensione. Sei grafi senza niente dentro leggevano come
    sei successi.

    Ora lo zero si vede, e accanto c'e' la ragione — cioe' le estensioni che
    il filtro ha lasciato fuori, che e' l'informazione con cui si capisce se
    manca una grammatica o se la cartella conteneva solo binari.
    """

    import truenex_memory.graph as graph_module
    from truenex_memory.graph import FileGraph

    casa = _casa(tmp_path)
    progetto = tmp_path / "SoloVisualBasic"
    progetto.mkdir()
    db = _archivio(casa / ".truenex-memory" / "truenex_memory.db", "7")

    vuoto = FileGraph(
        root=progetto.as_posix(),
        edges=[],
        stats={"files": 0, "skipped_by_suffix": {".vb": 412, ".aspx": 88}, "skipped_total": 500},
    )
    monkeypatch.setattr(graph_module, "graphify_available", lambda: True)
    monkeypatch.setattr(
        "truenex_memory.adapters.profile.known_project_roots", lambda home: [progetto]
    )
    monkeypatch.setattr("truenex_memory.graph.build_file_graph", lambda radice: vuoto)
    monkeypatch.setattr("truenex_memory.graph.save_file_graph", lambda grafo, cache: None)

    esito = runner.invoke(
        app, ["upgrade", "--db", str(db), "--home", str(casa), "--skip-profile"]
    )

    assert esito.exit_code == 0
    assert "0 file" in esito.stdout, esito.stdout
    assert "412 .vb" in esito.stdout, "senza le estensioni non si capisce cosa manca"
    assert "VB.NET" in esito.stdout, (
        "nominare il linguaggio e' la differenza fra «non so leggerlo» e "
        "«non c'e' niente dentro»"
    )
    assert "grammatica assente" in esito.stdout

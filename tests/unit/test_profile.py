"""Il profilo di comportamento: una sorgente, N file generati.

Perche' questo file esiste. Il profilo deve arrivare a ogni client, e il modo
per farlo NON e' un file solo: al livello utente ogni client legge un percorso
proprio (`~/.codex/AGENTS.md`, `~/.claude/CLAUDE.md`, `~/.gemini/GEMINI.md`), e
un `~/AGENTS.md` nella home nuda non lo legge nessuno. La convenzione AGENTS.md
governa il file di PROGETTO, non quello utente — averlo dedotto invece che
verificato ha impostato mezza giornata di lavoro sul percorso sbagliato.

La proprieta' che questi test difendono: il testo esiste in un posto solo, e
ogni file che lo contiene e' generato. Due copie di una regola sono due regole
— i due generatori scritti a mano che c'erano prima lo dimostrano: uno diceva
«preferisci i risultati attivi», l'altro «cita i percorsi locali», e nessuno
l'aveva deciso.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from truenex_memory.adapters.profile import (
    BEGIN_MARKER,
    CLIENT_TARGETS,
    END_MARKER,
    PROFILE_VERSION,
    apply_all,
    apply_to_file,
    apply_to_text,
    block_version,
    profile_text,
    render_block,
    status,
    targets,
)


def _home_with(tmp_path: Path, *clients: str) -> Path:
    """Una home finta in cui esistono solo i client indicati."""

    for client in clients:
        (tmp_path / client).mkdir(parents=True, exist_ok=True)
    return tmp_path


# ── una sorgente sola ─────────────────────────────────────────────────────

def test_both_legacy_adapters_now_come_from_the_source() -> None:
    """Il difetto originale: due copie a mano che avevano gia' divergito."""

    from truenex_memory.adapters.agents_md import generate_agents_md
    from truenex_memory.adapters.claude_md import generate_claude_md

    assert generate_claude_md() == render_block()
    assert generate_agents_md() == render_block()


def test_the_mcp_handshake_serves_the_same_text() -> None:
    """Il campo `instructions` non e' una seconda versione del profilo.

    Si serve perche' costa zero, non perche' ci si possa contare: la specifica
    dice che il client «puo'» aggiungerlo al prompt di sistema.
    """

    from truenex_memory.mcp.server import _initialize

    assert _initialize({"protocolVersion": "2024-11-05"})["instructions"] == profile_text()


def test_the_block_stays_short() -> None:
    """Sta nel contesto di ogni sessione su ogni client: ogni riga si paga.

    Il limite non e' estetico. Un blocco che cresce senza freno viene troncato
    dai client con un tetto (Codex concatena le istruzioni e taglia a 32 KiB) e
    saltato dai modelli, e un'istruzione saltata e' peggio di una assente
    perche' sembra presente.

    La soglia e' 600 parole e non 400 perche' il conto va fatto sul risparmio,
    non sul costo: il blocco costa ~500 token una volta per sessione, e la
    regola «leggi i file in modo selettivo» esiste per evitare la scansione di
    un repository, che ne costa decine di migliaia. Tagliare qui per risparmiare
    trecento token sarebbe un'economia al contrario. Il tetto resta perche' oltre
    una certa lunghezza i modelli smettono di leggere.
    """

    assert len(profile_text().split()) < 600


# ── il blocco delimitato ──────────────────────────────────────────────────

def test_hand_written_content_survives() -> None:
    """La regola che rende il meccanismo accettabile.

    Se applicare il profilo cancellasse le righe scritte da una persona,
    nessuno lo eseguirebbe due volte — e un'automazione che si usa una volta
    non e' un'automazione.
    """

    mio = "# Le mie regole\n\nRispondi sempre in italiano.\n"

    aggiornato, azione = apply_to_text(mio)

    assert azione == "created"
    assert "Rispondi sempre in italiano." in aggiornato
    assert render_block() in aggiornato


def test_the_block_is_appended_not_prepended() -> None:
    """Le regole di chi scrive restano in testa al proprio file."""

    aggiornato, _ = apply_to_text("# Le mie regole\n\nRegola mia.\n")

    assert aggiornato.index("Regola mia.") < aggiornato.index(BEGIN_MARKER)


def test_applying_twice_changes_nothing() -> None:
    """Idempotenza: e' cio' che permette di chiamarlo a ogni avvio."""

    una_volta, _ = apply_to_text("# mio\n")
    due_volte, azione = apply_to_text(una_volta)

    assert due_volte == una_volta
    assert azione == "unchanged"


def test_an_older_block_is_replaced_not_duplicated() -> None:
    """Il marcatore di apertura porta la versione, ma il confronto la ignora.

    Altrimenti ogni cambio di versione lascerebbe il blocco precedente nel
    file, e dopo tre versioni un agente leggerebbe tre profili contraddittori.
    """

    vecchio = "<!-- truenex-memory:begin v0 -->\nregole antiche\n<!-- truenex-memory:end -->"
    contenuto = f"# mio\n\n{vecchio}\n\ncoda mia\n"

    aggiornato, azione = apply_to_text(contenuto)

    assert azione == "updated"
    assert "regole antiche" not in aggiornato
    assert aggiornato.count(END_MARKER) == 1
    assert "coda mia" in aggiornato


def test_the_version_is_readable_from_the_file() -> None:
    aggiornato, _ = apply_to_text("")

    assert block_version(aggiornato) == PROFILE_VERSION
    assert block_version("nessun blocco qui") is None


def test_a_block_without_a_version_reads_as_zero() -> None:
    """Un blocco scritto prima che il versionamento esistesse."""

    assert block_version("<!-- truenex-memory:begin -->\nx\n<!-- truenex-memory:end -->") == 0


# ── quali client ──────────────────────────────────────────────────────────

def test_only_installed_clients_are_targeted(tmp_path: Path) -> None:
    """Scrivere in un client assente creerebbe cartelle mai chieste."""

    home = _home_with(tmp_path, ".claude", ".codex")

    nomi = {t.client for t in targets(home)}

    assert nomi == {"Claude Code", "Codex"}


def test_a_client_installed_without_a_file_is_reported_as_missing(tmp_path: Path) -> None:
    """Il caso peggiore trovato sul campo: la cartella c'e', il profilo no.

    Su questa macchina tre client erano in questo stato, quindi giravano senza
    nessuna istruzione — indistinguibile, da fuori, da un client istruito.
    """

    home = _home_with(tmp_path, ".kimi")

    rapporto = next(r for r in status(home) if r.client == "Kimi")

    assert rapporto.installed is True
    assert rapporto.action == "absent"


def test_apply_writes_every_installed_client(tmp_path: Path) -> None:
    home = _home_with(tmp_path, ".claude", ".codex", ".gemini")

    rapporti = apply_all(home)

    scritti = {r.client for r in rapporti if r.action == "created"}
    assert scritti == {"Claude Code", "Codex", "Gemini"}
    for target in CLIENT_TARGETS:
        if target.client in scritti:
            assert render_block() in target.path(home).read_text(encoding="utf-8")


def test_apply_does_not_touch_a_client_that_is_not_there(tmp_path: Path) -> None:
    home = _home_with(tmp_path, ".claude")

    apply_all(home)

    assert not (home / ".codex").exists()


def test_apply_is_idempotent_on_disk(tmp_path: Path) -> None:
    home = _home_with(tmp_path, ".codex")

    apply_all(home)
    secondo = apply_all(home)

    assert [r.action for r in secondo if r.installed] == ["unchanged"]


def test_the_write_leaves_no_temporary_behind(tmp_path: Path) -> None:
    """Un file di istruzioni troncato e' peggio di uno vecchio.

    Un agente seguirebbe una regola che si interrompe a meta' frase, quindi la
    scrittura passa da un temporaneo e poi sostituisce.
    """

    home = _home_with(tmp_path, ".codex")

    apply_to_file(home / ".codex" / "AGENTS.md")

    assert list((home / ".codex").glob("*.truenex-tmp")) == []


def test_a_missing_file_is_created_with_only_the_block(tmp_path: Path) -> None:
    home = _home_with(tmp_path, ".gemini")
    percorso = home / ".gemini" / "GEMINI.md"

    apply_to_file(percorso)

    assert percorso.read_text(encoding="utf-8").strip() == render_block()


def test_an_empty_file_does_not_get_a_leading_blank_line(tmp_path: Path) -> None:
    home = _home_with(tmp_path, ".gemini")
    percorso = home / ".gemini" / "GEMINI.md"
    percorso.write_text("", encoding="utf-8")

    apply_to_file(percorso)

    assert percorso.read_text(encoding="utf-8").startswith(BEGIN_MARKER)


# ── il contenuto ──────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "strumento",
    ["memory_search", "memory_graph", "task_open", "task_step_add", "task_close", "memory_add"],
)
def test_every_tool_named_in_the_profile_exists(strumento: str) -> None:
    """Un ordine che la macchina non puo' eseguire e' peggio di nessun ordine.

    Insegna a chi legge che il blocco si puo' saltare. Questo test lega il
    testo alla superficie MCP reale: se un tool viene rinominato o rimosso, il
    profilo fallisce qui invece di mentire agli agenti.
    """

    from truenex_memory.mcp.server import _tool_definitions

    assert strumento in profile_text()
    assert strumento in {d["name"] for d in _tool_definitions()}


def test_the_profile_names_the_scope_parameter_it_asks_for() -> None:
    """Lo scope e' il guadagno misurato del 2026-08-21: da 2/32 a 8/32."""

    from truenex_memory.mcp.server import _tool_definitions

    ricerca = next(d for d in _tool_definitions() if d["name"] == "memory_search")

    assert "scope" in profile_text()
    assert "scope" in ricerca["inputSchema"]["properties"]


def test_the_profile_names_supersedes_and_the_tool_accepts_it() -> None:
    """La regola «chiudi il passato» esiste solo se il parametro esiste."""

    from truenex_memory.mcp.server import _tool_definitions

    aggiungi = next(d for d in _tool_definitions() if d["name"] == "memory_add")

    assert "supersedes" in profile_text()
    assert "supersedes" in aggiungi["inputSchema"]["properties"]


def test_the_block_has_no_project_specific_content() -> None:
    """Il profilo vale in ogni progetto, quindi non puo' nominarne uno.

    Sollevato da codex e minimax durante la consultazione: mescolare «come si
    lavora» con «cos'e' questo progetto» mette il contesto di un progetto nelle
    istruzioni di tutti gli altri.
    """

    testo = profile_text().lower()

    for nome in ("truenex-memory/", "meddesk", "qvac", "d:\\", "c:\\users"):
        assert nome not in testo


# ── chi si e' collegato: il segnale autorevole ─────────────────────────────

@pytest.mark.parametrize(
    "dichiarato, atteso",
    [
        ("claude-code", "Claude Code"),
        ("Claude Code", "Claude Code"),
        ("codex", "Codex"),
        ("Codex", "Codex"),
        ("cursor-vscode", "Cursor"),
        ("gemini-cli", "Gemini"),
        ("kimi-cli", "Kimi"),
    ],
)
def test_a_connecting_client_is_recognised_by_what_it_declares(
    dichiarato: str, atteso: str
) -> None:
    """Il nome nell'handshake batte la deduzione dalle cartelle.

    Una cartella dice «forse questo client esiste»; `clientInfo` dice «e'
    collegato adesso e mi sta parlando». Il confronto e' per sottostringa
    perche' i client aggiungono suffissi di variante che cambiano fra versioni.
    """

    from truenex_memory.adapters.profile import target_for_client_info

    trovato = target_for_client_info(dichiarato)

    assert trovato is not None and trovato.client == atteso


def test_an_unknown_client_is_recorded_not_guessed(tmp_path: Path) -> None:
    """Un nome che non conosciamo va scritto, non interpretato.

    E' l'unico modo di scoprire un client nuovo: l'assenza di istruzioni non
    produce nessun errore, quindi nessuno se ne accorgerebbe mai.
    """

    from truenex_memory.adapters.profile import record_client, target_for_client_info

    registro = tmp_path / "clients.json"
    voce = record_client("agente-mai-visto", "9.9", registro)

    assert target_for_client_info("agente-mai-visto") is None
    assert voce["recognised_as"] is None
    assert "agente-mai-visto" in registro.read_text(encoding="utf-8")


def test_the_registry_counts_connections_and_keeps_the_first_sighting(tmp_path: Path) -> None:
    from truenex_memory.adapters.profile import record_client

    registro = tmp_path / "clients.json"
    prima = record_client("codex", "1.0", registro)
    seconda = record_client("codex", "1.1", registro)

    assert seconda["connections"] == 2
    assert seconda["first_seen"] == prima["first_seen"]
    assert seconda["version"] == "1.1"


def test_a_corrupt_registry_does_not_break_the_handshake(tmp_path: Path) -> None:
    """Un handshake rotto e' molto peggio di un profilo non aggiornato."""

    from truenex_memory.adapters.profile import record_client

    registro = tmp_path / "clients.json"
    registro.write_text("{ questo non e' json", encoding="utf-8")

    voce = record_client("codex", "1.0", registro)

    assert voce["connections"] == 1


def test_by_default_the_first_insertion_is_never_automatic(tmp_path: Path) -> None:
    """Un programma non si scrive nel file di configurazione di qualcun altro.

    Il software e' pubblico: la prima inserzione la chiede una persona con
    `profile apply`. Aggiornare un blocco che c'e' gia' e' un'altra cosa —
    quel consenso e' stato dato quando il blocco e' stato messo.
    """

    from truenex_memory.adapters.profile import refresh_on_connect

    home = _home_with(tmp_path, ".claude")

    azione = refresh_on_connect("claude-code", home)

    assert azione == "skipped-first-insert"
    assert not (home / ".claude" / "CLAUDE.md").exists()


def test_an_existing_block_is_refreshed_automatically(tmp_path: Path) -> None:
    """Il caso per cui esiste tutto questo: chiesto una volta, mai piu' vecchio."""

    from truenex_memory.adapters.profile import refresh_on_connect

    home = _home_with(tmp_path, ".claude")
    percorso = home / ".claude" / "CLAUDE.md"
    percorso.write_text(
        "# mie regole\n\n<!-- truenex-memory:begin v0 -->\nvecchio\n<!-- truenex-memory:end -->\n",
        encoding="utf-8",
    )

    azione = refresh_on_connect("claude-code", home)

    contenuto = percorso.read_text(encoding="utf-8")
    assert azione == "updated"
    assert "vecchio" not in contenuto
    assert "# mie regole" in contenuto


def test_always_mode_inserts_on_first_connection(tmp_path: Path) -> None:
    from truenex_memory.adapters.profile import refresh_on_connect

    home = _home_with(tmp_path, ".codex")

    azione = refresh_on_connect("codex", home, mode="always")

    assert azione == "created"
    assert render_block() in (home / ".codex" / "AGENTS.md").read_text(encoding="utf-8")


def test_off_mode_touches_nothing(tmp_path: Path) -> None:
    from truenex_memory.adapters.profile import refresh_on_connect

    home = _home_with(tmp_path, ".codex")

    assert refresh_on_connect("codex", home, mode="off") == "off"
    assert not (home / ".codex" / "AGENTS.md").exists()


def test_an_unknown_client_writes_nothing(tmp_path: Path) -> None:
    from truenex_memory.adapters.profile import refresh_on_connect

    home = _home_with(tmp_path, ".claude")

    assert refresh_on_connect("agente-mai-visto", home, mode="always") == "unknown-client"


def test_the_default_mode_is_refresh(monkeypatch) -> None:
    from truenex_memory.adapters.profile import auto_profile_mode

    monkeypatch.delenv("TRUENEX_PROFILE_AUTO", raising=False)
    assert auto_profile_mode() == "refresh"
    monkeypatch.setenv("TRUENEX_PROFILE_AUTO", "valore-assurdo")
    assert auto_profile_mode() == "refresh", "un valore ignoto non deve spegnere ne' aprire"


# ── il testo vive in un file, e deve restare raggiungibile ────────────────

def test_the_profile_is_read_as_a_package_resource() -> None:
    """Il modo in cui il testo puo' sparire: non essere impacchettato.

    Installato come wheel il pacchetto puo' stare in uno zip, dove un percorso
    relativo a `__file__` non funziona e un file non dichiarato come dato del
    pacchetto semplicemente non c'e'. In quel caso il profilo non darebbe un
    errore chiaro: gli agenti resterebbero senza istruzioni, che e' invisibile.
    Questo test legge la risorsa nello stesso modo del codice.
    """

    from importlib import resources

    from truenex_memory.adapters.profile import PROFILE_RESOURCE

    testo = (
        resources.files("truenex_memory.adapters")
        .joinpath(PROFILE_RESOURCE)
        .read_text(encoding="utf-8")
    )

    assert testo.strip() == profile_text()
    assert testo.strip(), "il file esiste ma e' vuoto"


def test_the_markdown_carries_no_markers() -> None:
    """I marcatori li mette `render_block`, non il file.

    Se finissero anche nel `.md`, un file di client conterrebbe marcatori
    annidati e la sostituzione del blocco successivo taglierebbe nel posto
    sbagliato.
    """

    assert BEGIN_MARKER not in profile_text()
    assert "truenex-memory:end" not in profile_text()


# ── la guardia sulla cartella utente ─────────────────────────────────────

def test_the_home_can_be_redirected(tmp_path, monkeypatch) -> None:
    """Serve perche' un test ha scritto nel registro vero di chi sviluppa.

    La suite mandava un `clientInfo` finto e il server lo annotava nella home
    reale, quindi `profile clients` — il comando che serve a sapere quali
    client esistono davvero — ha cominciato a elencarne due inventati. La
    correzione non e' rattoppare i test che se ne sono accorti, e' rendere
    impossibile il caso: una guardia in un posto solo, attiva per tutti.
    """

    from truenex_memory.adapters.profile import PROFILE_HOME_ENV, client_registry_path, profile_home

    monkeypatch.setenv(PROFILE_HOME_ENV, str(tmp_path))

    assert profile_home() == tmp_path
    assert client_registry_path() == tmp_path / ".truenex-memory" / "clients.json"


def test_without_the_override_the_real_home_is_used(monkeypatch) -> None:
    from truenex_memory.adapters.profile import PROFILE_HOME_ENV, profile_home

    monkeypatch.delenv(PROFILE_HOME_ENV, raising=False)

    assert profile_home() == Path.home()


def test_the_handshake_writes_inside_the_redirected_home(tmp_path, monkeypatch) -> None:
    """Il test che avrebbe colto il difetto: l'handshake non deve uscire da qui."""

    from truenex_memory.adapters.profile import PROFILE_HOME_ENV
    from truenex_memory.mcp.server import handle_jsonrpc_message

    monkeypatch.setenv(PROFILE_HOME_ENV, str(tmp_path))

    handle_jsonrpc_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "clientInfo": {"name": "codex-mcp-client"}},
        }
    )

    registro = tmp_path / ".truenex-memory" / "clients.json"
    assert registro.exists(), "annotato nella home finta"
    assert "codex-mcp-client" in registro.read_text(encoding="utf-8")


def test_a_real_client_name_is_recognised_by_substring() -> None:
    """Verificato sui nomi VERI raccolti il 2026-08-22, non su ipotesi.

    Codex si presenta come `codex-mcp-client` e Cursor come `cursor-vscode`:
    un confronto esatto sui nomi che avevo immaginato («codex», «cursor»)
    avrebbe fallito su entrambi.
    """

    from truenex_memory.adapters.profile import target_for_client_info

    assert target_for_client_info("codex-mcp-client").client == "Codex"
    assert target_for_client_info("cursor-vscode").client == "Cursor"
    assert target_for_client_info("claude-code").client == "Claude Code"


# ── il profilo e' arrivato? lo dice il comportamento ──────────────────────

def _sessione(registro: Path, client: str, chiamate: list[tuple[str, dict]]) -> None:
    from truenex_memory.adapters.profile import record_client, record_tool_use

    record_client(client, "1.0", registro)
    for strumento, argomenti in chiamate:
        record_tool_use(client, strumento, argomenti, registro)


def test_a_client_that_never_uses_memory_is_the_strongest_signal(tmp_path: Path) -> None:
    """Il caso che questo meccanismo esiste per cogliere.

    Un client senza istruzioni non produce nessun errore: si collega, lavora
    leggendo trenta file, e da fuori e' indistinguibile da uno istruito. La sola
    traccia osservabile e' che non ha mai chiamato memoria.
    """

    from truenex_memory.adapters.profile import compliance, record_client

    registro = tmp_path / "clients.json"
    record_client("cursor-vscode", "1.0", registro)

    rapporto = compliance(registro)[0]

    assert rapporto["verdict"] == "no-usage"


def test_searching_without_scope_is_noticed(tmp_path: Path) -> None:
    """Lo scope e' il guadagno misurato piu' grande: da 2/32 a 8/32.

    Un client che cerca sempre senza scope sta interrogando ottanta volte il
    pagliaio necessario, e non lo sa nessuno se non lo si guarda.
    """

    from truenex_memory.adapters.profile import compliance

    registro = tmp_path / "clients.json"
    _sessione(registro, "codex-mcp-client", [("memory_search", {"query": "x"})] * 4)

    rapporto = compliance(registro)[0]

    assert rapporto["verdict"] == "ignores-scope"
    assert rapporto["scope_rate"] == 0.0


def test_half_the_profile_in_force_is_reported_as_such(tmp_path: Path) -> None:
    """Cerca bene ma non usa grafo ne' registra: e' un terzo stato, non un si/no."""

    from truenex_memory.adapters.profile import compliance

    registro = tmp_path / "clients.json"
    _sessione(registro, "claude-code", [("memory_search", {"query": "x", "scope": "p"})] * 3)

    assert compliance(registro)[0]["verdict"] == "search-only"


def test_a_compliant_client_is_recognised_as_such(tmp_path: Path) -> None:
    from truenex_memory.adapters.profile import compliance

    registro = tmp_path / "clients.json"
    _sessione(
        registro,
        "claude-code",
        [("memory_search", {"query": "x", "scope": "p"})] * 3
        + [("memory_graph", {"target": "f"}), ("task_step_add", {"text": "y"})],
    )

    rapporto = compliance(registro)[0]

    assert rapporto["verdict"] == "follows-profile"
    assert rapporto["scope_rate"] == 1.0
    assert rapporto["graph_calls"] == 1 and rapporto["task_steps"] == 1


def test_the_scope_threshold_tolerates_cross_project_questions(tmp_path: Path) -> None:
    """Cercare senza scope e' legittimo per «dove l'avevo risolto?».

    Per questo la soglia e' due su tre e non l'unanimita': un verdetto che
    accusa un comportamento corretto verrebbe ignorato, e un avviso ignorato
    non protegge da niente.
    """

    from truenex_memory.adapters.profile import compliance

    registro = tmp_path / "clients.json"
    _sessione(
        registro,
        "claude-code",
        [("memory_search", {"query": "x", "scope": "p"})] * 3
        + [("memory_search", {"query": "dove l'avevo risolto"})]
        + [("memory_graph", {"target": "f"})],
    )

    assert compliance(registro)[0]["verdict"] == "follows-profile"


def test_the_label_is_recomputed_not_frozen(tmp_path: Path) -> None:
    """Le etichette dei bersagli cambiano; il registro non va riscritto.

    «Codex CLI» e' diventato «Codex» perche' nominava una superficie invece di
    una cartella. Un valore congelato alla prima connessione mostrerebbe per
    sempre quello vecchio.
    """

    from truenex_memory.adapters.profile import compliance

    registro = tmp_path / "clients.json"
    registro.write_text(
        '{"codex-mcp-client": {"name": "codex-mcp-client", "recognised_as": "Etichetta Vecchia",'
        ' "behaviour": {"calls": 1, "searches": 1, "searches_with_scope": 1}}}',
        encoding="utf-8",
    )

    assert compliance(registro)[0]["recognised_as"] == "Codex"


def test_an_absent_registry_reports_nothing_rather_than_failing(tmp_path: Path) -> None:
    from truenex_memory.adapters.profile import compliance

    assert compliance(tmp_path / "non-esiste.json") == []


def test_recording_never_breaks_a_tool_call(tmp_path: Path, monkeypatch) -> None:
    """Un contatore mancato e' niente, una risposta perduta e' il lavoro di qualcuno."""

    from truenex_memory.adapters.profile import record_tool_use

    inesistente = tmp_path / "cartella" / "senza" / "permessi" / "clients.json"
    monkeypatch.setattr(Path, "mkdir", lambda *a, **k: (_ for _ in ()).throw(OSError("nope")))

    record_tool_use("claude-code", "memory_search", {"scope": "p"}, inesistente)  # non deve sollevare


def test_the_handshake_and_the_calls_share_one_registry(tmp_path, monkeypatch) -> None:
    """Il conteggio deve finire accanto al client che l'ha prodotto."""

    from truenex_memory.adapters.profile import PROFILE_HOME_ENV, compliance
    from truenex_memory.mcp.server import handle_jsonrpc_message

    monkeypatch.setenv(PROFILE_HOME_ENV, str(tmp_path))
    handle_jsonrpc_message(
        {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "clientInfo": {"name": "claude-code"}},
        }
    )
    handle_jsonrpc_message(
        {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "memory_search", "arguments": {"query": "x", "scope": "p"}},
        }
    )

    rapporti = compliance(tmp_path / ".truenex-memory" / "clients.json")
    claude = next(r for r in rapporti if r["recognised_as"] == "Claude Code")
    assert claude["searches"] == 1 and claude["searches_with_scope"] == 1

"""Identita' dell'archivio servito e diario del server MCP.

Da dove nasce questo file.

Una chiamata a `memory_search` ha risposto «Connection closed». Ripetuta, ha
funzionato, e io ne ho concluso che l'episodio fosse transitorio. La revisione di
Codex ha demolito la conclusione, e aveva ragione: sulla macchina erano vive TRE
istanze del server MCP, avviate con radici diverse, e due di quelle radici
aprivano archivi diversi — 18.426 documenti contro 171. Quindi «stessi parametri,
ora funziona» non dimostrava niente, perche' non avevo mostrato che le due
chiamate fossero servite dallo stesso processo sullo stesso archivio.

Due difetti veri sono emersi da quella correzione, e questi test li inchiodano.

Il primo: nessuno poteva sapere quale archivio serviva un'istanza. Un archivio
quasi vuoto risponde «non ho trovato niente», che e' indistinguibile da «non
esiste niente da trovare».

Il secondo: `global_status` guarda sempre la cartella utente, mentre le ricerche
guardano la radice di avvio. Un'istanza poteva cercare in un archivio e riferire
lo stato di un altro.

E un difetto di lettura mio: avevo scritto che il ciclo «esce solo su EOF». La
scrittura della risposta era fuori da ogni gestore, quindi una pipe chiusa
durante la scrittura uccideva il processo senza lasciare traccia.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

from truenex_memory.mcp.identity import (
    Cronometro,
    Diario,
    identita_archivio,
    istruzioni,
    riga_archivio,
)
from truenex_memory.mcp.server import handle_jsonrpc_line, run_stdio_server
from truenex_memory.store.sqlite import connect, initialize_schema


def _archivio(radice: Path, *, documenti: int = 0, progetti: tuple[str, ...] = ()) -> Path:
    db = radice / ".truenex-memory" / "truenex_memory.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db)
    initialize_schema(conn)
    for i in range(documenti):
        conn.execute(
            "INSERT INTO documents (id, project_id, path, filename, content_hash,"
            " created_at, updated_at)"
            " VALUES (?, 'p', ?, ?, ?, '2026-08-24', '2026-08-24')",
            (f"doc{i}", f"f{i}.md", f"f{i}.md", f"h{i}"),
        )
    for i, nome in enumerate(progetti):
        conn.execute(
            "INSERT INTO source_ledger (source_id, source_type, source_path_or_alias,"
            " project_name, status, created_at, updated_at)"
            " VALUES (?, 'document', ?, ?, 'active', '2026-08-24', '2026-08-24')",
            (f"s{i}", f"{nome}/f.md", nome),
        )
    conn.commit()
    conn.close()
    return db


# ─── l'identita' ───

def test_due_radici_diverse_hanno_identita_diverse(tmp_path: Path) -> None:
    """E' il fatto che rendeva la mia diagnosi indimostrabile."""

    a, b = tmp_path / "casa", tmp_path / "repo"
    _archivio(a, documenti=5, progetti=("alfa", "beta"))
    _archivio(b, documenti=1, progetti=("alfa",))

    ia, ib = identita_archivio(a), identita_archivio(b)

    assert ia["archive_id"] != ib["archive_id"]
    assert (ia["documents"], ia["projects"]) == (5, 2)
    assert (ib["documents"], ib["projects"]) == (1, 1)


def test_l_identita_del_file_e_stabile_fra_due_letture(tmp_path: Path) -> None:
    """`archive_id` identifica il FILE, quindi non deve cambiare da solo."""

    _archivio(tmp_path, documenti=2)
    assert identita_archivio(tmp_path)["archive_id"] == identita_archivio(tmp_path)["archive_id"]


def test_l_impronta_del_contenuto_cambia_quando_l_archivio_cambia(tmp_path: Path) -> None:
    """Serve ad attribuire due risposte incoerenti a due STATI, non a un difetto."""

    db = _archivio(tmp_path, documenti=2)
    prima = identita_archivio(tmp_path)["content_fingerprint"]

    conn = connect(db)
    conn.execute(
        "INSERT INTO documents (id, project_id, path, filename, content_hash,"
        " created_at, updated_at)"
        " VALUES ('nuovo', 'p', 'n.md', 'n.md', 'hn', '2026-08-24', '2026-08-24')"
    )
    conn.commit()
    conn.close()

    assert identita_archivio(tmp_path)["content_fingerprint"] != prima


def test_su_windows_le_maiuscole_non_fanno_due_archivi(tmp_path: Path) -> None:
    """Lo stesso file scritto in due modi e' lo stesso file.

    Senza questo, due istanze avviate con `C:\\Users\\marco` e `c:\\users\\marco`
    sembrerebbero servire archivi diversi e il confronto direbbe il falso.
    """

    _archivio(tmp_path, documenti=1)
    uno = identita_archivio(str(tmp_path))
    due = identita_archivio(str(tmp_path).upper() if tmp_path.drive else str(tmp_path))
    if tmp_path.drive:  # il confronto ha senso solo dove c'e' una lettera di unita'
        assert uno["archive_id"] == due["archive_id"]


def test_un_archivio_assente_lo_dichiara(tmp_path: Path) -> None:
    """Non «zero documenti»: ASSENTE. Sono due cose diverse."""

    i = identita_archivio(tmp_path / "mai-creato")

    assert i["db_exists"] is False
    assert i["documents"] is None
    assert "ATTENZIONE" in riga_archivio(i)
    assert "non c'e' niente da cercare" in riga_archivio(i)


def test_un_archivio_illeggibile_lo_dichiara(tmp_path: Path) -> None:
    db = tmp_path / ".truenex-memory" / "truenex_memory.db"
    db.parent.mkdir(parents=True)
    db.write_bytes(b"questo non e' un database sqlite")

    i = identita_archivio(tmp_path)

    assert i["db_exists"] is True
    assert i["unreadable"]
    assert "illeggibile" in riga_archivio(i)


def test_la_riga_nomina_il_percorso_e_i_conteggi(tmp_path: Path) -> None:
    _archivio(tmp_path, documenti=3, progetti=("alfa",))
    riga = riga_archivio(identita_archivio(tmp_path))

    assert "truenex_memory.db" in riga
    assert "3 documenti" in riga
    assert "1 progetti" in riga


# ─── l'handshake ───

def test_l_handshake_dichiara_l_archivio(tmp_path: Path) -> None:
    """Il posto dove chi fa le domande vede a chi le sta facendo."""

    _archivio(tmp_path, documenti=7, progetti=("alfa", "beta", "gamma"))

    r = handle_jsonrpc_line(
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"clientInfo": {"name": "prova"}}}),
        project_root=tmp_path,
    )

    risultato = r["result"]
    assert risultato["instructions"].splitlines()[0].startswith("Archivio servito")
    assert "7 documenti" in risultato["instructions"]
    a = risultato["_meta"]["truenex/archive"]
    assert a["documents"] == 7 and a["projects"] == 3
    assert a["db_path"].endswith("truenex_memory.db")


def test_l_handshake_non_perde_il_profilo(tmp_path: Path) -> None:
    """La riga dell'archivio si AGGIUNGE, non sostituisce."""

    _archivio(tmp_path, documenti=1)
    r = handle_jsonrpc_line(
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
        project_root=tmp_path,
    )
    testo = r["result"]["instructions"]
    assert testo.count("\n\n") >= 1
    assert len(testo) > len(riga_archivio(identita_archivio(tmp_path)))


def test_istruzioni_mette_l_archivio_prima_del_profilo() -> None:
    i = identita_archivio(Path("mai-esistito"))
    t = istruzioni(i, "PROFILO")
    assert t.index("ATTENZIONE") < t.index("PROFILO")


# ─── lo stato: archivio servito contro negozio globale ───

def test_global_status_dichiara_lo_scarto_fra_i_due_archivi(tmp_path: Path) -> None:
    """Il difetto: cercare in un archivio e riferire lo stato di un altro."""

    casa, repo = tmp_path / "casa", tmp_path / "repo"
    _archivio(casa, documenti=9, progetti=("alfa", "beta"))
    _archivio(repo, documenti=1, progetti=("alfa",))

    r = handle_jsonrpc_line(
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                    "params": {"name": "global_status", "arguments": {"home": str(casa)}}}),
        project_root=repo,
    )
    payload = json.loads(r["result"]["content"][0]["text"])

    assert payload["served_archive_differs"] is True
    assert payload["served_archive"]["documents"] == 1


def test_global_status_non_grida_quando_i_due_coincidono(tmp_path: Path) -> None:
    _archivio(tmp_path, documenti=4)

    r = handle_jsonrpc_line(
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                    "params": {"name": "global_status", "arguments": {"home": str(tmp_path)}}}),
        project_root=tmp_path,
    )
    payload = json.loads(r["result"]["content"][0]["text"])

    assert payload["served_archive_differs"] is False


# ─── il diario ───

def test_il_diario_scrive_una_riga_json_per_evento() -> None:
    flusso = io.StringIO()
    Diario(flusso=flusso).evento("prova", req=7, tool="memory_search")

    riga = json.loads(flusso.getvalue().strip())
    assert riga["evento"] == "prova" and riga["req"] == 7
    assert riga["pid"] > 0 and riga["t"]


def test_il_diario_non_fa_cadere_il_server_se_non_puo_scrivere() -> None:
    """Una riga di diagnostica persa non e' niente. Una risposta persa lo e'."""

    class Rotto(io.StringIO):
        def write(self, *_a, **_k):  # type: ignore[override]
            raise OSError("pipe chiusa")

    Diario(flusso=Rotto()).evento("prova")  # non deve alzare


def test_il_ciclo_registra_avvio_richiesta_risposta_e_uscita(tmp_path: Path, capsys) -> None:
    """La sequenza completa di una richiesta servita.

    Senza queste righe, «Connection closed» lasciava quattro spiegazioni
    plausibili e nessun modo di scegliere.
    """

    _archivio(tmp_path, documenti=2)
    richiesta = json.dumps({"jsonrpc": "2.0", "id": 42, "method": "ping"})

    import sys

    vecchio = sys.stdin
    sys.stdin = io.StringIO(richiesta + "\n")
    try:
        run_stdio_server(tmp_path)
    finally:
        sys.stdin = vecchio

    catturato = capsys.readouterr()
    eventi = [json.loads(r) for r in catturato.err.strip().splitlines() if r.startswith("{")]
    nomi = [e["evento"] for e in eventi]

    assert nomi == ["server_start", "request_in", "response_written", "server_exit"]
    assert eventi[0]["archive"]["documents"] == 2
    assert eventi[1]["req"] == 42 and eventi[1]["metodo"] == "ping"
    assert eventi[-1]["motivo"] == "stdin_eof"
    assert eventi[-1]["richieste_servite"] == 1
    # Il protocollo esce da stdout, la diagnostica da stderr: una riga di diario
    # su stdout corromperebbe il flusso JSON-RPC.
    assert json.loads(catturato.out.strip())["id"] == 42


def test_una_pipe_chiusa_in_scrittura_lascia_la_sua_traccia(tmp_path: Path, capsys, monkeypatch) -> None:
    """Il caso che uccideva il processo in silenzio.

    «Richiesta elaborata, risposta perduta» e «richiesta mai arrivata» sono due
    cose diverse: la prima dice che un ritentativo ripeterebbe il lavoro.
    """

    _archivio(tmp_path, documenti=1)

    import builtins
    import sys

    vero = builtins.print

    def print_rotto(*a, **k):
        # Solo la scrittura su STDOUT si rompe. Il diario passa `file=`, e
        # romperlo avrebbe cancellato le prove che questo test cerca — cosa che
        # e' successa alla prima stesura.
        if "file" not in k:
            raise BrokenPipeError("il client ha chiuso stdout")
        return vero(*a, **k)

    monkeypatch.setattr(builtins, "print", print_rotto)

    vecchio = sys.stdin
    sys.stdin = io.StringIO(json.dumps({"jsonrpc": "2.0", "id": 9, "method": "ping"}) + "\n")
    try:
        run_stdio_server(tmp_path)  # non deve propagare
    finally:
        sys.stdin = vecchio

    eventi = [
        json.loads(r) for r in capsys.readouterr().err.strip().splitlines() if r.startswith("{")
    ]
    nomi = [e["evento"] for e in eventi]

    assert "write_error" in nomi
    assert eventi[nomi.index("write_error")]["req"] == 9
    assert eventi[-1]["motivo"] == "stdout_chiuso"


def test_una_riga_non_json_non_abbatte_il_riferimento(tmp_path: Path, capsys) -> None:
    """La riga sbagliata la gestisce il parser JSON-RPC, non il diario."""

    _archivio(tmp_path)
    import sys

    vecchio = sys.stdin
    sys.stdin = io.StringIO("questa non e' JSON\n")
    try:
        run_stdio_server(tmp_path)
    finally:
        sys.stdin = vecchio

    fuori = capsys.readouterr()
    assert json.loads(fuori.out.strip())["error"]["code"] == -32700
    eventi = [json.loads(r) for r in fuori.err.strip().splitlines() if r.startswith("{")]
    assert [e["evento"] for e in eventi] == [
        "server_start", "request_in", "response_written", "server_exit",
    ]


def test_il_cronometro_misura_in_millisecondi() -> None:
    assert Cronometro().ms() >= 0

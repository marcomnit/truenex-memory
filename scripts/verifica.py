"""Verifica che truenex-memory funzioni, su questa macchina, adesso.

Un comando solo, un verdetto per riga. Nessuna virgoletta da comporre, nessun
`&&`, nessun `cd`:

    D:\\Project_sw\\ProjectPy\\truenex-memory\\.venv\\Scripts\\python.exe D:\\Project_sw\\ProjectPy\\truenex-memory\\scripts\\verifica.py

Perche' esiste. Le istruzioni di prova che avevo scritto a mano erano righe
composte con virgolette annidate, provate in PowerShell 7 e consegnate a chi usa
PowerShell 5.1: `&&` non esiste in 5.1 e le virgolette si sfaldano. Chi verifica
non deve combattere con la shell — deve leggere un verdetto.

Ogni riga dice ESITO, cosa e' stato misurato, e il numero. Le prove che non si
possono eseguire dicono SALTATA con la ragione: una prova saltata che si
presenta come superata e' peggio di una prova mancante.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# La console di Windows non e' UTF-8 per difetto: i caratteri accentati
# uscivano illeggibili. Un verdetto che non si legge non e' un verdetto.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # pragma: no cover - flussi che non si riconfigurano
    pass

ESITI: list[tuple[str, str]] = []


def _riga(esito: str, titolo: str, dettaglio: str) -> None:
    ESITI.append((esito, titolo))
    colore = {"OK": "  OK  ", "ROTTO": " ROTTO", "SALTATA": "SALTATA", "NOTO": " NOTO "}[esito]
    print(f"[{colore}] {titolo}")
    for linea in dettaglio.splitlines():
        print(f"          {linea}")


def _archivio_utente() -> dict:
    from truenex_memory.mcp.identity import identita_archivio

    return identita_archivio(Path.home())


def prova_archivio() -> dict:
    """C'e' un archivio, si legge, e contiene qualcosa."""

    a = _archivio_utente()
    if not a["db_exists"]:
        _riga("ROTTO", "L'archivio esiste", f"nessun file in {a['db_path']}")
        return a
    if a["unreadable"]:
        _riga("ROTTO", "L'archivio si legge", f"{a['db_path']}\n{a['unreadable']}")
        return a
    _riga(
        "OK",
        "L'archivio esiste e si legge",
        f"{a['db_path']}\n"
        f"{a['documents']} documenti, {a['chunks']} chunk, "
        f"{a['active_sources']} sorgenti su {a['projects']} progetti\n"
        f"archive_id {a['archive_id']}  impronta {a['content_fingerprint']}",
    )
    return a


def prova_grafi() -> list:
    """Quanti progetti hanno un grafo del codice in cache."""

    from truenex_memory.graph import default_cache_dirs, list_cached_graphs

    db = Path.home() / ".truenex-memory" / "truenex_memory.db"
    visti: dict[str, object] = {}
    for cartella in default_cache_dirs(db):
        for v in list_cached_graphs(cartella):
            visti.setdefault(v.cache_file, v)
    grafi = list(visti.values())
    if not grafi:
        _riga(
            "ROTTO",
            "Ci sono grafi del codice",
            "nessun grafo in cache. Costruiscine uno con:\n"
            "  truenex-mem graph build <cartella-del-progetto>",
        )
        return grafi

    rotti = [v for v in grafi if v.status != "ok"]
    file = sum(v.files for v in grafi)
    archi = sum(v.file_edges for v in grafi)
    dettaglio = f"{len(grafi)} grafi, {file} file, {archi} archi fra file"
    if rotti:
        dettaglio += "\nnon leggibili: " + ", ".join(f"{v.name} ({v.status})" for v in rotti[:5])
    _riga("ROTTO" if rotti else "OK", "Ci sono grafi del codice", dettaglio)
    return grafi


def prova_memory_graph(grafi: list) -> None:
    """La domanda piu' utile del prodotto: chi chiama questo file?

    Il bersaglio si SCEGLIE dai dati invece di essere scritto a mano: una prova
    che si rompe quando un progetto cambia nome misura la fixture, non il
    prodotto.

    La condizione era `chiamanti > 0 OPPURE entita > 0`, e con quella la prova
    e' passata su un bersaglio con ZERO chiamanti. Una prova che si dichiara
    superata senza aver misurato la cosa che le da' il nome e' peggio di una
    prova assente. Ora si richiedono chiamanti, e il bersaglio si cerca fra piu'
    candidati finche' uno risponde.
    """

    import sqlite3

    from truenex_memory.graph import load_file_graph
    from truenex_memory.mcp.tools import memory_graph

    # Quali progetti hanno documenti indicizzati: serve a DIRE, misurandolo, che
    # il grafo risponde anche dove la ricerca testuale non ha niente.
    indicizzati: set[str] = set()
    try:
        db = Path.home() / ".truenex-memory" / "truenex_memory.db"
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        try:
            indicizzati = {
                (r[0] or "").casefold()
                for r in conn.execute(
                    "SELECT DISTINCT project_name FROM source_ledger WHERE status = 'active'"
                )
            }
        finally:
            conn.close()
    except Exception:
        pass

    # Prima i progetti NON indicizzati, perche' e' su quelli che la risposta
    # dimostra qualcosa che nient'altro nel prodotto sa dare. A pari condizione,
    # quelli con piu' relazioni.
    candidati = sorted(
        (v for v in grafi if v.status == "ok" and v.file_edges > 0),
        key=lambda v: (v.name.casefold() in indicizzati, -v.file_edges),
    )
    if not candidati:
        _riga("SALTATA", 'Il grafo risponde a "chi chiama questo file?"',
              "nessun grafo con relazioni fra file")
        return

    esaminati: list[str] = []
    for voce in candidati[:8]:
        grafo = load_file_graph(Path(voce.cache_file).parent, Path(voce.root))
        if grafo is None or not grafo.edges:
            continue

        # Il file con piu' CHIAMANTI DISTINTI, non con piu' archi: un file
        # chiamato mille volte dallo stesso file non dimostra niente.
        chiamanti: dict[str, set[str]] = {}
        for arco in grafo.edges:
            chiamanti.setdefault(arco.target_file, set()).add(arco.source_file)
        for bersaglio, sorgenti in sorted(chiamanti.items(), key=lambda kv: -len(kv[1]))[:3]:
            r = memory_graph(bersaglio, scope=voce.name)
            t = r.get("totals", {})
            if t.get("callers", 0) > 0:
                stato = "no" if voce.name.casefold() not in indicizzati else "si"
                _riga(
                    "OK",
                    'Il grafo risponde a "chi chiama questo file?"',
                    f"progetto {voce.name} (documenti indicizzati: {stato})\n"
                    f"bersaglio {bersaglio}\n"
                    f"{t.get('callers', 0)} chiamanti, {t.get('matched', 0)} entita', "
                    f"{t.get('tests', 0)} test",
                )
                return
            esaminati.append(
                f"{voce.name}: {bersaglio[:56]} -> {t.get('callers', 0)} chiamanti"
                f" ({len(sorgenti)} attesi dal grafo)"
            )

    _riga(
        "ROTTO",
        'Il grafo risponde a "chi chiama questo file?"',
        "nessun bersaglio ha prodotto chiamanti, su "
        f"{len(esaminati)} tentativi:\n" + "\n".join(esaminati[:6]),
    )


def prova_ricerca() -> None:
    """La ricerca trova qualcosa nell'archivio utente."""

    from truenex_memory.mcp.tools import memory_search

    r = memory_search("routing system logs", top_k=3, project_root=Path.home())
    risultati = r.get("results", [])
    if not risultati:
        _riga("ROTTO", "La ricerca trova documenti", "zero risultati su una query generica")
        return
    percorsi = [(x.get("source_path") or x.get("title") or "")[:70] for x in risultati]
    _riga("OK", "La ricerca trova documenti",
          f"{len(risultati)} risultati\n" + "\n".join(percorsi))


def prova_scope() -> None:
    """DIFETTO NOTO: lo scope filtra i documenti e non le memorie.

    Non e' una prova che deve passare: e' una prova che deve DIRE la verita' su
    un difetto conosciuto, cosi' chi verifica non lo scopre da solo credendo di
    aver trovato qualcosa di nuovo. Il giorno che il difetto viene chiuso, questa
    riga cambia esito da sola.
    """

    from truenex_memory.mcp.tools import memory_search

    r = memory_search("routing system logs", top_k=3, scope="AI_Agent", project_root=Path.home())
    da = (r.get("scope") or {}).get("answered_from") or []
    applicato = (r.get("scope") or {}).get("applied")
    estranei = [p for p in da if p != "AI_Agent"]

    if applicato and estranei:
        _riga(
            "NOTO",
            "Lo scope restringe davvero la ricerca",
            f"chiesto AI_Agent, risposto anche da: {', '.join(estranei)}\n"
            f"la risposta dichiara applied={applicato}, ma il filtro copre solo i chunk\n"
            "dei documenti: _search_memories non ha il parametro scope.\n"
            "Difetto conosciuto, in attesa di decisione. Non e' una scoperta nuova.",
        )
        return
    if not estranei:
        _riga("OK", "Lo scope restringe davvero la ricerca",
              f"risposto solo da: {', '.join(da) or 'nessuna fonte'}")
        return
    _riga("OK", "Lo scope restringe davvero la ricerca",
          f"applied={applicato}, fonti dichiarate: {', '.join(da)}")


def prova_api(atteso: dict) -> None:
    """Il server che serve l'app guarda lo STESSO archivio."""

    # La 8000 per prima perche' e' quella che il frontend usa per difetto: era
    # l'errore di questa prova. Controllavo la 8777, dove avevo avviato io un
    # server sull'archivio giusto, e dichiaravo OK — mentre l'app leggeva la
    # 8000, dove ce n'era un altro sull'archivio sbagliato. Una prova che guarda
    # una porta diversa da quella del prodotto non misura il prodotto.
    for porta in (8000, 8777):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{porta}/api/stats", timeout=5) as risposta:
                d = json.loads(risposta.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, TimeoutError):
            continue

        negozio = d.get("store") or {}
        suo = str(negozio.get("db_path", ""))
        stesso = suo.replace("\\", "/").casefold() == atteso["db_path"].replace("\\", "/").casefold()
        dettaglio = (
            f"porta {porta}: {d.get('projects')} progetti, {d.get('documents')} documenti\n"
            f"negozio: {suo or 'non dichiarato'}"
        )
        if not stesso:
            dettaglio += f"\nDIVERSO da quello che usano gli agenti: {atteso['db_path']}"
        _riga("OK" if stesso else "ROTTO",
              "L'app guarda lo stesso archivio degli agenti", dettaglio)
        return

    _riga(
        "SALTATA",
        "L'app guarda lo stesso archivio degli agenti",
        "nessun server sulle porte 8777 e 8000. Avvialo con:\n"
        "  truenex-mem serve --port 8777 --project-root " + str(Path.home()),
    )


def prova_configurazione_mcp() -> None:
    """Le registrazioni MCP puntano a file che esistono."""

    trovate: list[tuple[str, str, bool]] = []
    for percorso in (
        Path.home() / ".claude.json",
        Path.home() / ".claude" / "settings.json",
    ):
        if not percorso.exists():
            continue
        try:
            dati = json.loads(percorso.read_text(encoding="utf-8"))
        except Exception:
            continue
        for blocco in (dati.get("mcpServers") or {}).items():
            nome, conf = blocco
            if "truenex" not in nome.lower():
                continue
            comando = str((conf or {}).get("command", ""))
            trovate.append((percorso.name, comando, Path(comando).exists() if comando else False))

    if not trovate:
        _riga("SALTATA", "Le registrazioni MCP puntano a file esistenti",
              "nessuna registrazione truenex trovata nei file di configurazione noti")
        return

    mancanti = [t for t in trovate if not t[2]]
    dettaglio = "\n".join(
        f"{'manca ' if not ok else 'ok    '} {file}: {cmd}" for file, cmd, ok in trovate
    )
    if len(trovate) > 1:
        dettaglio += (
            f"\n{len(trovate)} registrazioni con lo stesso nome in file diversi:"
            " quale vince dipende dal client."
        )
    _riga("ROTTO" if mancanti else "OK",
          "Le registrazioni MCP puntano a file esistenti", dettaglio)


def main() -> int:
    print("Verifica di truenex-memory su questa macchina")
    print("=" * 74)
    print()

    archivio = prova_archivio()
    print()
    if not archivio["db_exists"] or archivio["unreadable"]:
        print("L'archivio non e' utilizzabile: le altre prove non direbbero niente.")
        return 1

    grafi = prova_grafi()
    print()
    prova_memory_graph(grafi)
    print()
    prova_ricerca()
    print()
    prova_scope()
    print()
    prova_api(archivio)
    print()
    prova_configurazione_mcp()

    print()
    print("=" * 74)
    rotti = [t for e, t in ESITI if e == "ROTTO"]
    saltate = [t for e, t in ESITI if e == "SALTATA"]
    noti = [t for e, t in ESITI if e == "NOTO"]
    print(f"{len(ESITI)} prove: {len(ESITI) - len(rotti) - len(saltate) - len(noti)} superate,"
          f" {len(rotti)} rotte, {len(noti)} difetti noti, {len(saltate)} saltate")
    for t in rotti:
        print(f"  ROTTO   {t}")
    for t in noti:
        print(f"  NOTO    {t}")
    for t in saltate:
        print(f"  SALTATA {t}")
    return 1 if rotti else 0


if __name__ == "__main__":
    raise SystemExit(main())

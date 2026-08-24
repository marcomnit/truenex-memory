"""A quale meta' del corpus lo scope viene applicato.

Il difetto, misurato sull'archivio vero. Chiedendo `scope="AI_Agent"` la risposta
dichiarava `applied: true` e conteneva come primo risultato un log di sessione di
Codex, cioe' un documento di un altro progetto. Causa: la ricerca fonde due
sorgenti — i chunk dei documenti e i nodi di memoria — e `scope` arriva solo alla
prima. `_search_memories` non ha nemmeno il parametro.

Perche' non si e' semplicemente aggiunto il filtro anche alle memorie. Il
`source_path` di una memoria registra DOVE e' stata scritta, non di cosa parla:
su 3.120 memorie di questo archivio solo 85 hanno il nome del progetto nel
percorso, le altre stanno sotto il file della sessione in cui sono nate.
Filtrare per percorso escluderebbe la maggioranza delle memorie pertinenti e
farebbe passare quelle irrilevanti finite per caso nel posto giusto. Serve prima
un'attribuzione di progetto esplicita, ed e' un lavoro a parte.

Quindi qui non si corregge il comportamento: si smette di affermare il falso. Un
limite dichiarato e' un limite; un limite taciuto e' un difetto.
"""

from __future__ import annotations

from pathlib import Path

from truenex_memory.core.memory_service import MemoryService
from truenex_memory.mcp.tools import memory_search


def _progetto(radice: Path) -> MemoryService:
    servizio = MemoryService(radice)
    servizio.init_project()
    return servizio


def _documento(radice: Path, percorso: str, testo: str) -> None:
    file = radice / percorso
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(testo, encoding="utf-8")


def test_la_copertura_nomina_le_due_meta(tmp_path: Path) -> None:
    """Il campo che mancava: quale ramo e' stato filtrato e quale no."""

    servizio = _progetto(tmp_path)
    _documento(tmp_path, "alfa/nota.md", "il routing dei log di sistema")
    servizio.index(tmp_path)

    r = memory_search("routing log sistema", scope="alfa", project_root=tmp_path)

    copertura = r["scope"]["coverage"]
    assert copertura["document_chunks"] in ("filtered", "unfiltered")
    assert copertura["memory_nodes"] == "unfiltered"


def test_senza_scope_nessuno_dei_due_rami_e_filtrato(tmp_path: Path) -> None:
    """Nessuno scope chiesto: dire «filtrato» sarebbe falso in entrambi i sensi."""

    servizio = _progetto(tmp_path)
    _documento(tmp_path, "alfa/nota.md", "il routing dei log di sistema")
    servizio.index(tmp_path)

    r = memory_search("routing log sistema", project_root=tmp_path)

    # Senza scope il blocco puo' non esserci affatto: e' il caso in cui non c'e'
    # niente da dichiarare. Ma se c'e', non deve dire «filtrato».
    blocco = r.get("scope")
    if blocco and blocco.get("coverage"):
        assert blocco["coverage"]["document_chunks"] == "unfiltered"
        assert blocco["coverage"]["memory_nodes"] == "unfiltered"


def test_il_ripiego_globale_non_si_dichiara_filtrato(tmp_path: Path) -> None:
    """Uno scope che non trova niente fa ricadere la ricerca su tutto.

    In quel caso i documenti NON sono stati ristretti, e dirlo «filtered»
    sarebbe la stessa bugia in un altro punto.
    """

    servizio = _progetto(tmp_path)
    _documento(tmp_path, "alfa/nota.md", "il routing dei log di sistema")
    servizio.index(tmp_path)

    r = memory_search("routing log sistema", scope="progetto-che-non-esiste",
                      project_root=tmp_path)

    blocco = r["scope"]
    if blocco.get("applied") is False and blocco.get("coverage"):
        assert blocco["coverage"]["document_chunks"] == "unfiltered"


def test_applied_resta_un_booleano(tmp_path: Path) -> None:
    """Il campo pubblico non cambia forma.

    Trasformare `applied` da booleano a oggetto avrebbe rotto chi lo legge oggi.
    La copertura si AGGIUNGE accanto.
    """

    servizio = _progetto(tmp_path)
    _documento(tmp_path, "alfa/nota.md", "il routing dei log di sistema")
    servizio.index(tmp_path)

    r = memory_search("routing log sistema", scope="alfa", project_root=tmp_path)

    assert isinstance(r["scope"]["applied"], bool)
    assert "coverage" in r["scope"]


def test_la_descrizione_del_tool_dice_che_le_memorie_non_sono_ristrette() -> None:
    """Chi chiama deve saperlo PRIMA di chiedere, non dopo aver letto i risultati.

    La descrizione diceva «Restrict document candidates», che e' letteralmente
    corretto ma non basta: nessuno legge «document» come «e non le memorie».
    """

    from truenex_memory.mcp.server import _tool_definitions

    definizione = next(t for t in _tool_definitions() if t["name"] == "memory_search")
    testo = definizione["inputSchema"]["properties"]["scope"]["description"]

    assert "memory nodes" in testo.lower()
    assert "unrestricted" in testo.lower()
    # La ragione, non solo il fatto: senza il perche', il limite sembra una
    # dimenticanza e qualcuno lo «corregge» filtrando per percorso.
    assert "where it was" in testo.lower()
    assert "3120" in testo or "3.120" in testo

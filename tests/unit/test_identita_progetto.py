"""Cos'e' un progetto, e a quale appartiene un percorso.

Il difetto che questo modulo chiude. Il nome del progetto era l'ULTIMO SEGMENTO
del percorso, e su 17.053 sorgenti vere produceva 58 «progetti» di tre specie:
progetti veri, contenitori (`sessions` 8.296 sorgenti, `projects` 3.365, `D:`
2.684, `SOFWARE` 439) e nomi di file (`README.md`, `settings.json`,
`bz4tvl8ek.txt`).

Un nome di file non e' un progetto. Una cartella di sistema non e' un progetto.
`D:` non e' un progetto: e' un disco.

I test usano `esiste`/`elenca` iniettati, cosi' le regole si verificano senza
costruire un albero di file vero — e senza dipendere da com'e' fatta la macchina
su cui girano.
"""

from __future__ import annotations

from pathlib import Path

from truenex_memory.projects import (
    KIND_DATI_AGENTE,
    KIND_NON_ASSEGNATO,
    KIND_PROGETTO,
    attribuisci,
    chiave_radice,
    identificatore,
)


def _mondo(*presenti: str):
    """Un filesystem finto: solo i percorsi elencati esistono."""

    insieme = {chiave_radice(p) for p in presenti}

    def esiste(p: Path) -> bool:
        return chiave_radice(p) in insieme

    def elenca(p: Path):
        prefisso = chiave_radice(p) + "/"
        return [
            k[len(prefisso):] for k in insieme
            if k.startswith(prefisso) and "/" not in k[len(prefisso):]
        ]

    return esiste, elenca


AGENTI = frozenset({".claude", ".codex", ".truenex-memory"})


# ─── le tre specie ───

def test_una_radice_dichiarata_vince_su_tutto() -> None:
    esiste, elenca = _mondo("D:/p/App/.git", "D:/p/App/dep/.git")

    a = attribuisci(
        "D:/p/App/dep/src/x.py",
        radici_esplicite=["D:/p/App"],
        cartelle_agente=AGENTI, esiste=esiste, elenca=elenca,
    )

    assert a.kind == KIND_PROGETTO
    assert a.display_name == "App"
    assert a.discovery == "explicit"


def test_IL_DIFETTO_una_dipendenza_copiata_dentro_non_e_un_progetto() -> None:
    """`llama.cpp` con il suo `.git` dentro `AI_ApiFineTune_Chat`.

    La prima versione si fermava al primo candidato dal basso e su 17.053
    sorgenti produceva 209 progetti: ogni `.csproj` di sottocartella e ogni
    dipendenza copiata nel repo diventava un progetto a se'.
    """

    esiste, elenca = _mondo(
        "D:/p/AI_Chat/.git",
        "D:/p/AI_Chat/llama-cpp-python/.git",
        "D:/p/AI_Chat/llama-cpp-python/vendor/llama.cpp/.git",
    )

    a = attribuisci(
        "D:/p/AI_Chat/llama-cpp-python/vendor/llama.cpp/ggml/src/x.c",
        radici_esplicite=["D:/p/AI_Chat"],
        cartelle_agente=AGENTI, esiste=esiste, elenca=elenca,
    )

    assert a.display_name == "AI_Chat"
    # Cosa e' stato scartato viene DETTO: un progetto annidato che sparisce in
    # silenzio e' indistinguibile da un progetto che non esiste.
    assert "scartati 2 candidati" in a.reason
    assert "llama.cpp" in a.reason


def test_una_cartella_dati_di_un_client_non_e_un_progetto() -> None:
    esiste, elenca = _mondo()

    a = attribuisci(
        str(Path.home() / ".codex" / "sessions" / "2026" / "rollout.jsonl"),
        cartelle_agente=AGENTI, esiste=esiste, elenca=elenca,
    )

    assert a.kind == KIND_DATI_AGENTE
    assert a.display_name == ".codex"
    assert "non e' un progetto" in a.reason


def test_ma_la_cartella_client_DENTRO_un_progetto_appartiene_al_progetto() -> None:
    """`<progetto>/.claude/settings.json` e' del progetto, non del client.

    E' il caso che rende sbagliato controllare le cartelle dei client prima di
    risalire: su questo archivio sono decine di file di configurazione dentro
    progetti veri.
    """

    esiste, elenca = _mondo("D:/p/App/.git")

    a = attribuisci(
        "D:/p/App/.claude/settings.local.json",
        radici_esplicite=["D:/p/App"],
        cartelle_agente=AGENTI, esiste=esiste, elenca=elenca,
    )

    assert a.kind == KIND_PROGETTO
    assert a.display_name == "App"


def test_un_file_sperso_non_riceve_un_nome_inventato() -> None:
    """Prima diventava un progetto chiamato come il file."""

    esiste, elenca = _mondo()

    a = attribuisci("C:/tmp/neural-cortex-design.md",
                    cartelle_agente=AGENTI, esiste=esiste, elenca=elenca)

    assert a.kind == KIND_NON_ASSEGNATO
    assert a.display_name is None
    assert a.discovery == "none"
    assert "nessuna radice" in a.reason


def test_la_radice_di_un_disco_non_diventa_un_progetto() -> None:
    """«D:» era un progetto con 2.684 sorgenti."""

    esiste, elenca = _mondo()

    a = attribuisci("D:/qualcosa/file.md",
                    cartelle_agente=AGENTI, esiste=esiste, elenca=elenca)

    assert a.kind == KIND_NON_ASSEGNATO
    assert a.display_name != "D:"


def test_la_cartella_utente_non_diventa_un_progetto() -> None:
    esiste, elenca = _mondo()

    a = attribuisci(str(Path.home() / "appunti.md"),
                    cartelle_agente=AGENTI, esiste=esiste, elenca=elenca)

    assert a.kind == KIND_NON_ASSEGNATO


# ─── l'ordine di autorita' ───

def test_una_dichiarazione_batte_un_indizio_trovato_sul_disco() -> None:
    """`configured` sopra `marker`: dove l'utente tiene i progetti e' una sua
    affermazione, un marcatore e' un indizio."""

    esiste, elenca = _mondo("D:/lavoro/App/sotto/pyproject.toml")

    a = attribuisci(
        "D:/lavoro/App/sotto/modulo/x.py",
        contenitori=["D:/lavoro"],
        cartelle_agente=AGENTI, esiste=esiste, elenca=elenca,
    )

    assert a.display_name == "App"
    assert a.discovery == "configured"


def test_un_marcatore_basta_quando_non_c_e_niente_di_meglio() -> None:
    esiste, elenca = _mondo("D:/altrove/Progetto/pyproject.toml")

    a = attribuisci("D:/altrove/Progetto/src/x.py",
                    cartelle_agente=AGENTI, esiste=esiste, elenca=elenca)

    assert a.discovery == "marker"
    assert a.display_name == "Progetto"
    assert "pyproject.toml" in a.reason


def test_un_progetto_dotnet_si_riconosce_dal_suo_csproj() -> None:
    """Il marcatore .NET ha un nome diverso per ogni progetto: si riconosce
    dall'estensione, non da un elenco di nomi."""

    esiste, elenca = _mondo("D:/vs/MiaApp/MiaApp.csproj")

    a = attribuisci("D:/vs/MiaApp/Controllers/Home.cs",
                    cartelle_agente=AGENTI, esiste=esiste, elenca=elenca)

    assert a.discovery == "marker"
    assert a.display_name == "MiaApp"


def test_a_pari_autorita_vince_il_piu_vicino() -> None:
    esiste, elenca = _mondo("D:/a/pyproject.toml", "D:/a/b/pyproject.toml")

    a = attribuisci("D:/a/b/c/x.py", cartelle_agente=AGENTI, esiste=esiste, elenca=elenca)

    assert a.display_name == "b"


# ─── l'identita' ───

def test_l_identificatore_e_della_cartella_non_del_nome() -> None:
    """Due cartelle diverse con lo stesso nome sono due progetti."""

    assert identificatore("D:/uno/WebApp") != identificatore("D:/due/WebApp")


def test_su_windows_le_maiuscole_non_fanno_due_progetti() -> None:
    assert identificatore("D:/Progetti/App") == identificatore("d:/progetti/app")


def test_su_posix_le_maiuscole_contano() -> None:
    """Due cartelle che differiscono solo per le maiuscole sono due cartelle."""

    assert identificatore("/home/m/App") != identificatore("/home/m/app")


def test_le_barre_non_fanno_due_progetti() -> None:
    assert identificatore("D:\\p\\App") == identificatore("D:/p/App/")


def test_un_percorso_vuoto_non_e_un_errore() -> None:
    a = attribuisci("", cartelle_agente=AGENTI)
    assert a.kind == KIND_NON_ASSEGNATO
    assert "vuoto" in a.reason


def test_l_attribuzione_si_puo_serializzare() -> None:
    esiste, elenca = _mondo("D:/p/App/.git")
    d = attribuisci("D:/p/App/x.py", cartelle_agente=AGENTI,
                    esiste=esiste, elenca=elenca).to_dict()
    assert set(d) == {"kind", "project_id", "root", "display_name", "discovery", "reason"}
    # `discovery` e `reason` non sono decorazione: sono la differenza fra un
    # fatto e una supposizione, e chi legge deve poterla vedere.
    assert d["discovery"] and d["reason"]

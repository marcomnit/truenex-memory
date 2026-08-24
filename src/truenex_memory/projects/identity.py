"""Cos'e' un progetto, e come si attribuisce un file al suo.

Il difetto che questo modulo esiste per chiudere. Il nome del progetto era
`_infer_project_name(percorso)`, cioe' l'ULTIMO SEGMENTO del percorso. Per una
cartella di progetto funziona. Per tutto il resto no, e sull'archivio vero
produceva 58 "progetti" di tre specie diverse:

    progetti veri      AI_Agent, truenex-memory, SPACY, TRUENEX-WEB
    contenitori        sessions (8.296 sorgenti), projects (3.365), "D:" (2.684),
                       default (675), SOFWARE (439), daemon, web
    nomi di file       README.md, MEMORY.md, settings.json, bz4tvl8ek.txt

Un nome di file non e' un progetto. Una cartella di sistema non e' un progetto.
E `D:` non e' un progetto: e' un disco.

La conseguenza pratica non era estetica. `scope` filtra per sottostringa del
percorso, quindi cercare con `scope="AI_Agent"` competeva contro 8.296 sorgenti
etichettate `sessions`, e l'interfaccia disegnava trentaquattro palline che
progetti non erano.

L'IDENTITA'

Un progetto e' una RADICE sul filesystem, non un nome. Il nome visibile e' una
cosa separata, perche' due cartelle diverse possono chiamarsi allo stesso modo e
la stessa cartella puo' essere scritta in due modi.

    project_id      impronta della radice canonica: identifica LA CARTELLA
    root            la radice, in forma canonica
    display_name    come si chiama, per un umano
    discovery       COME lo si e' stabilito, dichiarato sempre
    reason          perche', in chiaro

`discovery` non e' decorazione: e' la differenza fra un fatto e una supposizione.
`vcs` significa che c'e' un `.git` la'; `inferred` significa che nessuno lo sa e
lo si e' dedotto. Chi legge deve poter distinguere, altrimenti un'euristica
travestita da fatto si propaga in ogni conteggio.

LE REGOLE, in ordine di autorita'

  1. explicit    la radice e' fra quelle che l'utente ha dichiarato
  2. vcs         la cartella contiene `.git`
  3. configured  la cartella e' figlia di un contenitore di progetti dichiarato
  4. marker      la cartella contiene un file di progetto (pyproject.toml,
                 package.json, *.sln, Cargo.toml, go.mod, ...)
  5. agent_data  il percorso vive nella cartella dati di un client (`.codex`,
                 `.claude`, ...): NON e' un progetto, ed e' una specie a se'
  6. unassigned  nessuna delle precedenti. Non si inventa un nome.

`configured` sta sopra `marker` perche' e' una dichiarazione dell'utente su dove
tiene i progetti, mentre un marcatore e' un indizio trovato sul disco: la
dichiarazione batte l'indizio. Questo elenco e il codice sono stati fuori
sincronia — l'elenco diceva marker prima di configured — e lo ha notato la
revisione di Codex: una documentazione che contraddice il codice e' peggio di una
documentazione assente, perche' viene creduta.

Il punto 6 e' il piu' importante. Prima ogni file riceveva un nome comunque, e
un nome inventato non si distingue da uno vero: `bz4tvl8ek.txt` sedeva
nell'elenco dei progetti accanto a `AI_Agent`. Meglio dichiarare di non sapere.

Due limiti che non possono essere superati salendo nel filesystem, e che
sarebbero i piu' dannosi: la radice di un disco e la cartella utente. Se la
ricerca arriva la', la risposta e' «non assegnato», non «D:».
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

#: File la cui presenza dichiara una cartella come radice di progetto. Non sono
#: indizi: sono file che si scrivono per dire «questo e' un progetto».
MARCATORI: tuple[str, ...] = (
    "pyproject.toml",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "setup.py",
    "CMakeLists.txt",
    "pom.xml",
    "build.gradle",
    "composer.json",
    "Gemfile",
)

#: Estensioni di file-progetto: un solo file con questa estensione nella
#: cartella basta. Sono i progetti dell'ecosistema .NET, dove il marcatore ha un
#: nome diverso per ogni progetto.
MARCATORI_PER_ESTENSIONE: tuple[str, ...] = (".sln", ".csproj", ".vbproj", ".fsproj")

#: Il numero massimo di cartelle da risalire. Non e' un limite di gusto: senza,
#: un percorso malformato (o un ciclo di collegamenti) girerebbe per sempre. E'
#: dichiarato perche' un limite invisibile e' un difetto: quando scatta, la
#: risposta e' `unassigned` con la ragione, non un silenzio.
RISALITA_MASSIMA = 40

KIND_PROGETTO = "project"
KIND_DATI_AGENTE = "agent_data"
KIND_NON_ASSEGNATO = "unassigned"


@dataclass(frozen=True)
class Attribuzione:
    """A chi appartiene un percorso, e su quale base lo si afferma."""

    kind: str
    project_id: str | None
    root: str | None
    display_name: str | None
    discovery: str
    reason: str

    def to_dict(self) -> dict[str, str | None]:
        return {
            "kind": self.kind,
            "project_id": self.project_id,
            "root": self.root,
            "display_name": self.display_name,
            "discovery": self.discovery,
            "reason": self.reason,
        }


def chiave_radice(percorso: str | Path) -> str:
    """La forma canonica di una radice, per confrontarla.

    Le maiuscole si ignorano SOLO per i percorsi in stile Windows (con la lettera
    di unita'): su un filesystem sensibile alle maiuscole due cartelle che
    differiscono solo per quelle sono due cartelle diverse, e fonderle sarebbe un
    errore.
    """

    barre = str(percorso).strip().replace("\\", "/").rstrip("/")
    if len(barre) == 2 and barre.endswith(":"):  # "D:" -> "D:/"
        barre += "/"
    return barre.lower() if _e_windows(barre) else barre


def _e_windows(percorso: str) -> bool:
    return len(percorso) >= 2 and percorso[1] == ":" and percorso[0].isalpha()


def identificatore(radice: str | Path) -> str:
    """Impronta della radice canonica. Identifica LA CARTELLA, non il nome."""

    return hashlib.sha256(chiave_radice(radice).encode("utf-8")).hexdigest()[:16]


def _e_radice_di_disco(p: Path) -> bool:
    return p.parent == p or (len(p.parts) <= 1)


def _dentro_cartella_agente(p: Path, cartelle_agente: frozenset[str]) -> str | None:
    """Il primo segmento del percorso che e' una cartella dati di un client."""

    for parte in p.parts:
        if parte in cartelle_agente:
            return parte
    return None


def _radice_fino_a(p: Path, segmento: str) -> Path:
    """Il percorso troncato al segmento indicato, incluso."""

    parti = list(p.parts)
    return Path(*parti[: parti.index(segmento) + 1])


def _ha_marcatore(cartella: Path, esiste: Callable[[Path], bool],
                  elenca: Callable[[Path], Iterable[str]]) -> str | None:
    for nome in MARCATORI:
        if esiste(cartella / nome):
            return nome
    for nome in elenca(cartella):
        for suffisso in MARCATORI_PER_ESTENSIONE:
            if nome.lower().endswith(suffisso):
                return nome
    return None


def _elenca_reale(cartella: Path) -> Iterable[str]:
    try:
        return [f.name for f in cartella.iterdir() if f.is_file()]
    except OSError:
        return ()


def attribuisci(
    percorso: str | Path,
    *,
    radici_esplicite: Iterable[str | Path] = (),
    contenitori: Iterable[str | Path] = (),
    cartelle_agente: frozenset[str] | None = None,
    esiste: Callable[[Path], bool] | None = None,
    elenca: Callable[[Path], Iterable[str]] | None = None,
) -> Attribuzione:
    """A quale progetto appartiene *percorso*, e su quale base.

    `radici_esplicite` sono le radici che l'utente (o un suo atto deliberato,
    come aver costruito un grafo del codice) ha dichiarato progetti: hanno
    l'autorita' piu' alta perche' non sono una deduzione.

    `contenitori` sono cartelle che CONTENGONO progetti — per esempio
    `D:/Project_sw/ProjectPy` — non progetti esse stesse. Un figlio diretto di un
    contenitore e' un progetto.

    `esiste` ed `elenca` esistono per i test: le regole restano verificabili
    senza costruire un albero di file vero.
    """

    from truenex_memory.adapters.profile import non_project_dirs

    _esiste = esiste if esiste is not None else Path.exists
    _elenca = elenca if elenca is not None else _elenca_reale
    _agente = cartelle_agente if cartelle_agente is not None else non_project_dirs()

    grezzo = str(percorso).strip()
    if not grezzo:
        return Attribuzione(
            KIND_NON_ASSEGNATO, None, None, None, "none", "percorso vuoto"
        )

    p = Path(grezzo.replace("\\", "/"))
    esplicite = {chiave_radice(r): Path(str(r).replace("\\", "/")) for r in radici_esplicite}
    contenitori_chiavi = {chiave_radice(c) for c in contenitori}

    # La cartella utente non e' un progetto e non lo diventa salendo: e' il
    # limite oltre il quale ogni attribuzione sarebbe inventata.
    casa = chiave_radice(Path.home())

    # UNA SCORCIATOIA SCARTATA, e vale scriverlo perche' sembrava gratuita.
    #
    # Un percorso sotto `~/.claude` o `~/.codex` non e' un progetto, e si
    # potrebbe rispondere senza toccare il disco: su questo archivio sono 12.333
    # sorgenti su 17.053, e la scorciatoia portava l'attribuzione dell'intero
    # archivio da 19,1 a 12,8 secondi.
    #
    # Ma dentro `~/.claude/plugins` ci sono repository VERI, col loro `.git`, e
    # la scorciatoia ne perdeva 7 — trasformava sette progetti reali in «dati di
    # un client». Sei secondi non valgono sette attribuzioni sbagliate, e il
    # posto giusto per pagare quel costo non e' qui: e' all'indicizzazione, una
    # volta per sorgente, invece che a ogni lettura.

    # Non si interroga il filesystem per sapere se `percorso` sia un file o una
    # cartella: puo' non esistere piu', e una sorgente indicizzata mesi fa spesso
    # non esiste. Si parte dal percorso stesso trattandolo come cartella e si
    # risale — una cartella con un marcatore dentro e' una radice sia che il
    # percorso puntasse a lei sia a un suo file.
    corrente = p

    # Tutti i candidati risalendo, poi si scegli per AUTORITA'.
    #
    # La prima versione si fermava al primo candidato partendo dal basso, e su
    # 17.053 sorgenti vere produceva 209 "progetti": in una soluzione .NET il
    # marcatore piu' vicino e' il `.csproj` di ogni sottocartella, quindi
    # `Server` e `Client` diventavano progetti dentro il progetto; e una
    # dipendenza copiata dentro il repo (`llama.cpp`, `llama-cpp-python`, col suo
    # `setup.py`) diventava un progetto a se'.
    #
    # Una radice DICHIARATA che sta piu' in alto batte un marcatore che sta piu'
    # in basso, perche' e' un atto deliberato contro un indizio. A pari autorita'
    # vince la piu' vicina: e' la piu' specifica fra due affermazioni dello
    # stesso peso.
    AUTORITA = {"explicit": 4, "vcs": 3, "configured": 2, "marker": 1}
    candidati: list[tuple[int, int, Path, str, str]] = []  # (autorita, -distanza, radice, metodo, ragione)

    salite = 0
    while salite < RISALITA_MASSIMA:
        k = chiave_radice(corrente)

        if k in esplicite:
            candidati.append((AUTORITA["explicit"], -salite, esplicite[k], "explicit",
                              "radice dichiarata dall'utente"))
        elif _esiste(corrente / ".git"):
            candidati.append((AUTORITA["vcs"], -salite, corrente, "vcs", "contiene .git"))
        elif chiave_radice(corrente.parent) in contenitori_chiavi:
            candidati.append((AUTORITA["configured"], -salite, corrente, "configured",
                              f"figlia del contenitore {corrente.parent}"))
        else:
            marcatore = _ha_marcatore(corrente, _esiste, _elenca)
            if marcatore:
                candidati.append((AUTORITA["marker"], -salite, corrente, "marker",
                                  f"contiene {marcatore}"))

        # Oltre la cartella utente e la radice del disco non si sale: sono i due
        # casi in cui l'attribuzione diventerebbe una bugia ("D:" era un
        # progetto con 2.684 sorgenti).
        if k == casa or _e_radice_di_disco(corrente):
            break

        corrente = corrente.parent
        salite += 1

    if candidati:
        autorita, distanza, radice, metodo, ragione = max(candidati)
        piu_interni = [c for c in candidati if c[0] < autorita]
        if piu_interni:
            # Cosa e' stato SCARTATO, e perche'. Senza questa riga, un progetto
            # annidato che spariva dall'elenco era indistinguibile da un progetto
            # che non esiste.
            ragione += (
                f"; scartati {len(piu_interni)} candidati di autorita' minore, il piu' vicino"
                f" {piu_interni[0][2].name} ({piu_interni[0][3]})"
            )
        return Attribuzione(
            KIND_PROGETTO, identificatore(radice), str(radice),
            radice.name or str(radice), metodo, ragione,
        )

    # Nessuna radice di progetto sopra il percorso. Prima di dire «non so»,
    # resta una cosa che si sa con certezza: se il percorso vive nella cartella
    # dati di un client, non e' un progetto ed e' una specie a se'. Questo
    # controllo viene DOPO la risalita, non prima: `<progetto>/.claude/x.json`
    # appartiene al progetto, non al client.
    segmento = _dentro_cartella_agente(p, _agente)
    if segmento:
        radice = _radice_fino_a(p, segmento)
        return Attribuzione(
            KIND_DATI_AGENTE, identificatore(radice), str(radice), segmento,
            "agent_dir",
            f"vive in {segmento}, la cartella dati di un client: non e' un progetto",
        )

    motivo = (
        f"nessuna radice di progetto risalendo da {p}"
        if salite < RISALITA_MASSIMA
        else f"risalita interrotta dopo {RISALITA_MASSIMA} cartelle"
    )
    return Attribuzione(KIND_NON_ASSEGNATO, None, None, None, "none", motivo)

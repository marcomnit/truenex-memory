"""The behavioural profile: one source, one block, every client.

## Why this exists

An agent that starts a session should already know that memory is here, that
searching costs less than reading files, that code questions are answered by
the graph, that decisions get recorded while the work happens, and that a note
superseded by a new implementation must be closed rather than left to
contradict the new one. Today none of that is known unless a person repeats it
every session — and a store nobody closes accumulates contradictory truths.

## Why one source and N generated files

There is NO single user-level file every client reads. Verified, not assumed:
Codex reads `~/.codex/AGENTS.md`, Claude Code reads `~/.claude/CLAUDE.md`,
Gemini CLI reads `~/.gemini/GEMINI.md`. A plain `~/AGENTS.md` is read by
nobody. The genuinely shared file is the *project* `AGENTS.md`, which is not
where this belongs: how to work with memory is true in every project, while
what a project is differs by project.

So the profile is written once here and rendered into every client found on
the machine. The alternative — a file per client maintained by hand — is what
exists today and it has already drifted: on this machine one client of six had
the full text, one was 32 lines behind, one file was empty, and three clients
had nothing at all. Nobody decided that; it is what hand-copying produces.

## Why the block is delimited

Everything between the markers belongs to this module and is replaced on
update. Everything outside is the user's and is never touched. Without the
markers an update would have to choose between clobbering hand-written rules
and never updating — and both are worse than being explicit.

## Why English

The reader is a model, not a person: instructions are followed most reliably
in English, and the gap widens on the small and cheap models this has to work
with. The software is also public, so a block in the author's language would
be unusable for everyone else. Content stored IN memory keeps its own language
— this module writes instructions, it never translates data.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any

# Bumped whenever the block text changes in a way worth re-applying. It travels
# inside the begin marker, so `profile status` can say "yours is 1, current is
# 2" instead of diffing prose — and so a client whose session cached an old
# block can be identified rather than guessed at.
PROFILE_VERSION = 1

BEGIN_MARKER = f"<!-- truenex-memory:begin v{PROFILE_VERSION} -->"
END_MARKER = "<!-- truenex-memory:end -->"

# Matches ANY version, so an older block is replaced rather than duplicated.
_BLOCK_PATTERN = re.compile(
    r"<!--\s*truenex-memory:begin[^>]*-->.*?<!--\s*truenex-memory:end\s*-->",
    re.DOTALL,
)


@dataclass(frozen=True)
class ClientTarget:
    """One client's user-level instruction file."""

    client: str
    relative: str  # relative to the user's home
    marker_dir: str  # the client's own directory; its presence means "installed"
    # Variabile che sposta la cartella del client altrove. Copilot ha
    # `COPILOT_HOME`: ignorarla vorrebbe dire scrivere nella home mentre il
    # client legge da un'altra parte — cioe' il difetto di ieri, un file
    # scritto dove nessuno guarda, che non da' nessun errore.
    home_env: str | None = None

    def _root(self, home: Path) -> Path:
        override = os.environ.get(self.home_env or "")
        if override and override.strip():
            return Path(override.strip())
        return home / self.marker_dir

    def path(self, home: Path) -> Path:
        return self._root(home) / Path(self.relative).name

    def installed(self, home: Path) -> bool:
        """True when the client's own directory exists.

        Presence of the directory, not of the file: a client installed but
        never given instructions is exactly the case worth fixing — on this
        machine three clients had a directory and no file, so they were
        running with no profile at all.
        """

        return self._root(home).is_dir()


# Where each client reads user-level instructions from. Verified against each
# client's own documentation rather than inferred from the AGENTS.md
# convention: the convention governs the PROJECT file, while the user-level
# location is each client's private choice and differs.
#
# I nomi qui sono quelli della CARTELLA di configurazione, non di una singola
# superficie del prodotto. Un prodotto ne ha spesso piu' di una — riga di
# comando, estensione dell'editor, applicazione — e quando condividono la
# stessa cartella condividono anche il profilo. Chiamare questo bersaglio
# «Codex CLI» sarebbe una precisione non verificata: il nome che il client
# dichiara nell'handshake (`codex-mcp-client`) e' quello della sua libreria
# MCP e non dice quale superficie lo stia usando. Se una superficie legge da
# un'altra cartella, serve una voce sua — e per ora non ne abbiamo la prova.
CLIENT_TARGETS: tuple[ClientTarget, ...] = (
    ClientTarget("Claude Code", ".claude/CLAUDE.md", ".claude"),
    ClientTarget("Codex", ".codex/AGENTS.md", ".codex"),
    ClientTarget("Gemini", ".gemini/GEMINI.md", ".gemini"),
    ClientTarget("Kimi", ".kimi/AGENTS.md", ".kimi"),
    ClientTarget("Cursor", ".cursor/AGENTS.md", ".cursor"),
    ClientTarget("Aider", ".aider/CONVENTIONS.md", ".aider"),
    # Copilot: la stessa cartella serve la CLI e le sessioni agente di VS Code
    # («harness-agnostic folders like ~/.copilot», documentazione Microsoft).
    # NON e' la cartella del profilo di VS Code: quella contiene impostazioni,
    # non istruzioni, e scriverci non avrebbe effetto.
    ClientTarget("Copilot", ".copilot/copilot-instructions.md", ".copilot", "COPILOT_HOME"),
)


PROFILE_RESOURCE = "profile.md"


@lru_cache(maxsize=1)
def profile_text() -> str:
    """Le istruzioni, lette dal file `profile.md` accanto a questo modulo.

    In un file di testo e non in una lista di stringhe Python perche' e' prosa:
    si modifica e si confronta meglio, e chi la legge vede quello che vedra'
    l'agente. Il legame con la realta' resta nei test, che verificano che ogni
    strumento nominato nel testo esista davvero nella superficie MCP — un
    ordine che la macchina non puo' eseguire insegna a chi legge che il blocco
    si puo' saltare.

    Letto con `importlib.resources` e non con un percorso relativo a
    `__file__`: installato come wheel il pacchetto puo' stare in uno zip, e li'
    `__file__` non e' un file vero. Il rischio di questa scelta e' che il
    `.md` non venga impacchettato e il profilo sparisca all'installazione —
    per questo c'e' un test che lo legge come risorsa.

    Se cambi il testo, alza `PROFILE_VERSION`: il confronto per aggiornare i
    file funziona comunque (si confronta il blocco intero), ma la versione e'
    cio' che permette di dire «il tuo e' v1, il corrente e' v2» invece di
    mettere due prose a confronto.
    """

    return (
        resources.files("truenex_memory.adapters")
        .joinpath(PROFILE_RESOURCE)
        .read_text(encoding="utf-8")
        .strip()
    )


def render_block() -> str:
    """The profile wrapped in its markers, ready to be written into a file."""

    return "\n".join([BEGIN_MARKER, profile_text(), END_MARKER])


def find_block(content: str) -> str | None:
    """The profile block already present in *content*, of any version."""

    match = _BLOCK_PATTERN.search(content)
    return match.group(0) if match else None


def block_version(content: str) -> int | None:
    """The version of the block present in *content*, if any."""

    block = find_block(content)
    if block is None:
        return None
    match = re.search(r"truenex-memory:begin\s+v(\d+)", block)
    return int(match.group(1)) if match else 0


def apply_to_text(content: str) -> tuple[str, str]:
    """Insert or refresh the block in *content*.

    Returns ``(new_content, action)`` where action is ``"created"``,
    ``"updated"`` or ``"unchanged"``. Everything outside the markers is carried
    over untouched, and the block is APPENDED when absent rather than
    prepended: a user's own rules usually open their file, and pushing them
    down would be a visible change nobody asked for.
    """

    block = render_block()
    existing = find_block(content)
    if existing is not None:
        if existing == block:
            return content, "unchanged"
        return _BLOCK_PATTERN.sub(lambda _: block, content, count=1), "updated"
    if not content.strip():
        return block + "\n", "created"
    return content.rstrip("\n") + "\n\n" + block + "\n", "created"


@dataclass(frozen=True)
class TargetReport:
    """What was found, or done, for one client."""

    client: str
    path: Path
    installed: bool
    action: str  # created | updated | unchanged | absent | client-not-installed
    present_version: int | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "client": self.client,
            "path": str(self.path),
            "installed": self.installed,
            "action": self.action,
            "present_version": self.present_version,
            "current_version": PROFILE_VERSION,
        }


def targets(home: Path | None = None, *, only_installed: bool = True) -> list[ClientTarget]:
    """The client files to write. Defaults to the clients actually present.

    Writing into a client that is not installed would create a directory the
    user never asked for; leaving out a client that IS installed is the defect
    this module exists to remove. Hence: presence decides.
    """

    root = profile_home() if home is None else home
    return [t for t in CLIENT_TARGETS if not only_installed or t.installed(root)]


def status(home: Path | None = None) -> list[TargetReport]:
    """What each client currently has, without changing anything."""

    root = profile_home() if home is None else home
    reports: list[TargetReport] = []
    for target in CLIENT_TARGETS:
        path = target.path(root)
        installed = target.installed(root)
        if not installed:
            reports.append(TargetReport(target.client, path, False, "client-not-installed"))
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            reports.append(TargetReport(target.client, path, True, "absent"))
            continue
        version = block_version(content)
        if version is None:
            action = "absent"
        elif version == PROFILE_VERSION and find_block(content) == render_block():
            action = "unchanged"
        else:
            action = "updated"  # cio' che accadrebbe applicando
        reports.append(TargetReport(target.client, path, True, action, version))
    return reports


def apply_to_file(path: Path) -> str:
    """Write or refresh the block in one file, creating it if needed.

    The write goes through a temporary file in the same directory and then a
    replace, so an interrupted run cannot leave a client with half a profile —
    a truncated instruction file is worse than an outdated one, because the
    agent would follow a sentence that stops mid-rule.
    """

    try:
        content = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        content = ""
    except UnicodeDecodeError:  # pragma: no cover - file non testuale
        raise

    new_content, action = apply_to_text(content)
    if action == "unchanged":
        return action

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".truenex-tmp")
    temporary.write_text(new_content, encoding="utf-8")
    temporary.replace(path)
    return action


def apply_all(home: Path | None = None) -> list[TargetReport]:
    """Write the profile into every installed client. Idempotent."""

    root = profile_home() if home is None else home
    reports: list[TargetReport] = []
    for target in CLIENT_TARGETS:
        path = target.path(root)
        if not target.installed(root):
            reports.append(TargetReport(target.client, path, False, "client-not-installed"))
            continue
        before = None
        try:
            before = block_version(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            pass
        action = apply_to_file(path)
        reports.append(TargetReport(target.client, path, True, action, before))
    return reports


# Come ciascun client si presenta nell'`initialize` MCP. E' il segnale
# AUTOREVOLE: non «forse e' installato» dedotto da una cartella, ma «e'
# collegato adesso e mi sta parlando». Le chiavi sono in minuscolo e il
# confronto e' per sottostringa, perche' i client aggiungono suffissi di
# variante (`cursor-vscode`, `claude-code-cli`) che cambiano fra le versioni.
CLIENT_INFO_ALIASES: tuple[tuple[str, str], ...] = (
    ("claude-code", "Claude Code"),
    ("claude code", "Claude Code"),
    ("claudecode", "Claude Code"),
    # Gli eseguibili si chiamano in modo piu' corto dei nomi dichiarati:
    # risalendo l'albero dei processi si trova `claude.exe`, non `claude-code`.
    ("claude", "Claude Code"),
    ("codex", "Codex"),
    ("gemini", "Gemini"),
    ("kimi", "Kimi"),
    ("cursor", "Cursor"),
    ("aider", "Aider"),
    ("copilot", "Copilot"),
)

# Cosa fa il server quando un client si presenta.
#   refresh (default) — aggiorna un blocco GIA' presente, non ne inserisce di
#     nuovi. Nessun file di nessuno cambia senza che l'abbia chiesto una volta,
#     e una volta chiesto non invecchia piu'.
#   always — inserisce anche la prima volta.
#   off — non tocca niente.
AUTO_PROFILE_ENV = "TRUENEX_PROFILE_AUTO"


# Ridirige TUTTO cio' che questo modulo scrive fuori dal progetto. Esiste per
# una ragione trovata sul campo: i test dell'handshake MCP scrivevano nel
# registro VERO dentro la home di chi sviluppa, lasciandoci due client
# inventati («test», «(non dichiarato)»). Un test con un effetto collaterale
# sulla macchina di qualcuno non e' un dettaglio: rende `profile clients` un
# elenco che mente su quali client esistono. La guardia sta qui, in un posto
# solo, invece che in ogni singolo punto di chiamata.
PROFILE_HOME_ENV = "TRUENEX_PROFILE_HOME"


def profile_home() -> Path:
    """La cartella utente su cui operare: quella vera, o quella ridiretta."""

    override = (os.environ.get(PROFILE_HOME_ENV) or "").strip()
    return Path(override) if override else Path.home()


def client_registry_path() -> Path:
    """Dove si annota chi si e' collegato."""

    return profile_home() / ".truenex-memory" / "clients.json"


def auto_profile_mode() -> str:
    """Modalita' di aggiornamento automatico del profilo alla connessione."""

    raw = (os.environ.get(AUTO_PROFILE_ENV) or "").strip().lower()
    return raw if raw in {"refresh", "always", "off"} else "refresh"


def target_for_client_info(name: str | None) -> ClientTarget | None:
    """Il file da aggiornare per il client che si e' appena presentato."""

    if not name:
        return None
    lowered = name.strip().lower()
    for alias, client in CLIENT_INFO_ALIASES:
        if alias in lowered:
            return next((t for t in CLIENT_TARGETS if t.client == client), None)
    return None


# Nomi che NON identificano niente. Kimi si presenta come `mcp` v0.1.0: e' il
# valore predefinito delle librerie MCP, non un prodotto. Mapparlo su Kimi
# sarebbe un errore che si scopre tardi — domani un altro client con lo stesso
# predefinito riceverebbe il profilo nella cartella di Kimi, e nessuno se ne
# accorgerebbe perche' un file scritto nel posto sbagliato non da' errori.
GENERIC_CLIENT_NAMES = frozenset({"", "mcp", "mcp-client", "mcpclient", "client", "unknown"})


def parent_process_name() -> str | None:
    """Il nome dell'eseguibile che ha lanciato questo processo.

    Il secondo segnale, e in pratica il piu' affidabile: un server MCP su stdio
    e' **avviato dal client**, quindi il padre e' il client, e il nome di un
    eseguibile non e' un campo che qualcuno dimentica di personalizzare.
    Serve quando `clientInfo.name` e' generico.

    Senza dipendenze nuove: `ctypes` su Windows, `/proc` su Linux, `ps` su
    macOS. Qualunque errore restituisce ``None`` — questa e' un'informazione in
    piu', non una condizione per funzionare.
    """

    try:
        parent = os.getppid()
    except (AttributeError, OSError):  # pragma: no cover - piattaforma esotica
        return None

    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, parent)
            if not handle:
                return None
            try:
                buffer = ctypes.create_unicode_buffer(1024)
                size = wintypes.DWORD(len(buffer))
                if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                    return None
                return Path(buffer.value).name or None
            finally:
                kernel32.CloseHandle(handle)
        except Exception:  # pragma: no cover - dipende dalla piattaforma
            return None

    try:
        return Path(os.readlink(f"/proc/{parent}/exe")).name
    except OSError:
        pass
    try:
        import subprocess

        out = subprocess.run(
            ["ps", "-o", "comm=", "-p", str(parent)],
            capture_output=True, text=True, timeout=2, check=False,
        )
        return Path(out.stdout.strip()).name or None
    except Exception:  # pragma: no cover - dipende dalla piattaforma
        return None


def process_ancestry(limit: int = 10) -> list[str]:
    """I nomi degli eseguibili che hanno portato a questo processo, dal piu' vicino.

    Un solo gradino non basta, e la prova e' sul campo: interrogando il padre si
    ottiene `python.exe`, che e' il lanciatore dello script console di questo
    stesso pacchetto — non il client. Lo stesso accadrebbe con `node.exe` per un
    client scritto in JavaScript. Il nome del prodotto compare piu' in alto,
    quindi si risale.

    Il limite di dieci gradini non e' prudenza generica: la catena reale ne ha
    tre o quattro, e un tetto evita che un ciclo nella tabella dei processi
    (pid riusati, padre morto e sostituito) faccia girare a vuoto.

    Nessuna dipendenza nuova. Su Windows una sola istantanea della tabella dei
    processi (`CreateToolhelp32Snapshot`) da' nome e padre di tutti, quindi la
    risalita non costa una chiamata per gradino.
    """

    try:
        start = os.getppid()
    except (AttributeError, OSError):  # pragma: no cover
        return []

    tabella = _process_table()
    if not tabella:
        nome = parent_process_name()
        return [nome] if nome else []

    catena: list[str] = []
    pid = start
    visti: set[int] = set()
    while pid and pid not in visti and len(catena) < limit:
        visti.add(pid)
        voce = tabella.get(pid)
        if voce is None:
            break
        nome, pid = voce
        if nome:
            catena.append(nome)
    return catena


def _process_table() -> dict[int, tuple[str, int]]:
    """``{pid: (nome, pid del padre)}`` per tutti i processi, o vuoto."""

    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            class PROCESSENTRY32W(ctypes.Structure):
                _fields_ = [
                    ("dwSize", wintypes.DWORD),
                    ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                    ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", ctypes.c_long),
                    ("dwFlags", wintypes.DWORD),
                    ("szExeFile", wintypes.WCHAR * 260),
                ]

            TH32CS_SNAPPROCESS = 0x00000002
            INVALID = ctypes.c_void_p(-1).value
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
            snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
            if not snapshot or snapshot == INVALID:
                return {}
            try:
                voce = PROCESSENTRY32W()
                voce.dwSize = ctypes.sizeof(PROCESSENTRY32W)
                tabella: dict[int, tuple[str, int]] = {}
                if not kernel32.Process32FirstW(snapshot, ctypes.byref(voce)):
                    return {}
                while True:
                    tabella[int(voce.th32ProcessID)] = (
                        str(voce.szExeFile),
                        int(voce.th32ParentProcessID),
                    )
                    if not kernel32.Process32NextW(snapshot, ctypes.byref(voce)):
                        break
                return tabella
            finally:
                kernel32.CloseHandle(snapshot)
        except Exception:  # pragma: no cover - dipende dalla piattaforma
            return {}

    tabella = {}
    try:
        for voce in Path("/proc").iterdir():
            if not voce.name.isdigit():
                continue
            try:
                testo = (voce / "stat").read_text(encoding="utf-8", errors="replace")
                nome = testo[testo.index("(") + 1 : testo.rindex(")")]
                resto = testo[testo.rindex(")") + 2 :].split()
                tabella[int(voce.name)] = (nome, int(resto[1]))
            except (OSError, ValueError, IndexError):
                continue
    except OSError:
        return {}
    return tabella


def identify_client(
    declared: str | None, process: str | None = None
) -> tuple[ClientTarget | None, str]:
    """Chi e' il client, e come lo si e' capito.

    Restituisce ``(bersaglio, segnale)`` dove segnale e' ``"declared"``,
    ``"process"`` o ``"none"``. Il nome dichiarato ha la precedenza quando dice
    qualcosa; se e' generico si guarda l'eseguibile che ha avviato il server.
    Registrare COME si e' capito e' importante quanto il risultato: un
    riconoscimento dedotto dal processo e' piu' fragile di uno dichiarato, e chi
    legge il registro deve poterlo distinguere.
    """

    lowered = (declared or "").strip().lower()
    if lowered not in GENERIC_CLIENT_NAMES:
        target = target_for_client_info(declared)
        if target is not None:
            return target, "declared"

    candidati = [process] if process is not None else process_ancestry()
    for nome in candidati:
        target = target_for_client_info(nome)
        if target is not None:
            return target, "process"
    return None, "none"


def identify_from_entry(
    name: str, entry: dict[str, Any]
) -> tuple[ClientTarget | None, str, str]:
    """Riconosce un client da una voce GIA' registrata.

    Esiste come funzione perche' la stessa risalita serviva a due comandi e
    l'avevo scritta due volte: la seconda copia era rimasta indietro e mostrava
    «ignoto» un client che l'altra riconosceva. Due copie di una regola sono due
    regole, qui come nel profilo.
    """

    lowered = (name or "").strip().lower()
    if lowered not in GENERIC_CLIENT_NAMES:
        target = target_for_client_info(name)
        if target is not None:
            return target, "declared", name
    for nome_processo in entry.get("ancestry") or [entry.get("process") or ""]:
        target = target_for_client_info(nome_processo)
        if target is not None:
            # Il nome che ha deciso, non tutta la catena: e' l'unica parte
            # interessante quando si legge il registro, e per un client ignoto
            # la catena resta comunque visibile.
            return target, "process", nome_processo
    return None, "none", ""


def record_client(name: str | None, version: str | None, registry: Path) -> dict[str, Any]:
    """Annota chi si e' collegato, riconosciuto o no.

    I nomi non riconosciuti vanno registrati, non ignorati: sono l'unico modo
    di scoprire che esiste un client nuovo da mappare. Senza questo elenco la
    mappatura si aggiornerebbe solo quando qualcuno si accorge, per caso, che
    un client non riceve il profilo — cioe' mai, perche' l'assenza di
    istruzioni non da' errore.
    """

    entry = {
        "name": name or "(non dichiarato)",
        "version": version or "",
        "last_seen": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    catena = process_ancestry()
    target, segnale = identify_client(name, None)
    # La catena intera, non solo il padre: e' cio' che serve per mappare un
    # client nuovo, e senza registrarla si dovrebbe chiedere all'utente di
    # riprodurre la connessione ogni volta.
    entry["process"] = catena[0] if catena else ""
    entry["ancestry"] = catena[:6]
    entry["recognised_as"] = target.client if target else None
    entry["signal"] = segnale

    known: dict[str, Any] = {}
    try:
        known = json.loads(registry.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        known = {}
    if not isinstance(known, dict):
        known = {}
    previous = known.get(entry["name"], {})
    entry["first_seen"] = previous.get("first_seen", entry["last_seen"])
    entry["connections"] = int(previous.get("connections", 0)) + 1
    known[entry["name"]] = entry
    try:
        registry.parent.mkdir(parents=True, exist_ok=True)
        temporary = registry.with_name(registry.name + ".truenex-tmp")
        temporary.write_text(json.dumps(known, indent=2, ensure_ascii=False), encoding="utf-8")
        temporary.replace(registry)
    except OSError:  # pragma: no cover - cache non scrivibile
        pass
    return entry


def refresh_on_connect(
    client_name: str | None, home: Path | None = None, *, mode: str | None = None
) -> str:
    """Aggiorna il profilo del client che si e' appena collegato.

    Restituisce l'azione compiuta: ``created``, ``updated``, ``unchanged``,
    ``skipped-first-insert``, ``unknown-client`` oppure ``off``.

    Una precisazione che va detta e non nascosta: il client ha gia' caricato le
    proprie istruzioni quando arriva a questo punto della connessione, quindi un
    blocco scritto ora vale dalla sessione SUCCESSIVA. Non e' un difetto
    dell'implementazione, e' come funziona il caricamento di quei file: per
    questo il canale principale resta il file e non l'handshake.
    """

    effective = auto_profile_mode() if mode is None else mode
    if effective == "off":
        return "off"
    target, _ = identify_client(client_name)
    if target is None:
        return "unknown-client"

    root = profile_home() if home is None else home
    path = target.path(root)
    if effective == "refresh":
        # Prima inserzione: solo su richiesta esplicita (`profile apply`). Un
        # programma che si scrive da solo dentro il file di configurazione di
        # qualcun altro al primo avvio e' invadente, e questo software e'
        # pubblico. Aggiornare un blocco che c'e' gia' e' un'altra cosa: quel
        # consenso e' stato dato quando il blocco e' stato messo.
        try:
            if find_block(path.read_text(encoding="utf-8")) is None:
                return "skipped-first-insert"
        except (OSError, UnicodeDecodeError):
            return "skipped-first-insert"
    try:
        return apply_to_file(path)
    except OSError:  # pragma: no cover - permessi
        return "unchanged"


# Quali contatori teniamo per capire se il profilo e' arrivato DAVVERO.
#
# Il problema che risolvono: sapere che un client si e' collegato non dice se ha
# letto le istruzioni. Chiedere all'agente di confermarlo (un marcatore da
# riportare, una chiamata di cortesia) dipende dalla sua collaborazione, cioe'
# misura la stessa cosa che si vuole verificare. Questi contatori guardano
# invece il COMPORTAMENTO, che il server osserva comunque: chi ha ricevuto il
# profilo cerca prima di leggere, passa lo `scope`, interroga il grafo per le
# domande strutturali e registra i passi. Chi non l'ha ricevuto no.
#
# Il verdetto e' un tasso, non un giudizio: cercare senza `scope` e' legittimo
# per una domanda trasversale, quindi un valore basso e' un indizio e non una
# prova.
COMPLIANCE_FIELDS = ("calls", "searches", "searches_with_scope", "graph_calls", "task_steps")


def record_tool_use(
    client_name: str | None,
    tool: str,
    arguments: dict[str, Any] | None,
    registry: Path | None = None,
) -> None:
    """Annota come il client sta usando memoria, per sessione.

    Chiamata a ogni `tools/call`, quindi deve costare quasi niente e non deve
    mai far cadere la chiamata: un errore qui e' un dettaglio, una risposta
    perduta e' il lavoro dell'utente.
    """

    path = client_registry_path() if registry is None else registry
    key = (client_name or "(non dichiarato)").strip() or "(non dichiarato)"
    try:
        known = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(known, dict):
            known = {}
    except (OSError, ValueError):
        known = {}

    entry = known.get(key) or {"name": key, "version": "", "connections": 0}
    entry.setdefault("recognised_as", None)
    behaviour = entry.get("behaviour") or {}
    behaviour["calls"] = int(behaviour.get("calls", 0)) + 1
    if tool == "memory_search":
        behaviour["searches"] = int(behaviour.get("searches", 0)) + 1
        if (arguments or {}).get("scope"):
            behaviour["searches_with_scope"] = int(behaviour.get("searches_with_scope", 0)) + 1
    elif tool == "memory_graph":
        behaviour["graph_calls"] = int(behaviour.get("graph_calls", 0)) + 1
    elif tool in {"task_open", "task_step_add", "task_close"}:
        behaviour["task_steps"] = int(behaviour.get("task_steps", 0)) + 1
    entry["behaviour"] = behaviour
    known[key] = entry

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".truenex-tmp")
        temporary.write_text(json.dumps(known, indent=2, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)
    except OSError:  # pragma: no cover - cache non scrivibile
        pass


def compliance(registry: Path | None = None) -> list[dict[str, Any]]:
    """Cosa dice il comportamento osservato, client per client.

    ``verdict`` e' uno di:

    - ``no-usage`` — collegato e non ha mai usato memoria. E' il segnale piu'
      forte che il profilo non e' arrivato: le istruzioni dicono di cercare
      prima di leggere, e non ha cercato mai.
    - ``ignores-scope`` — cerca ma passa lo `scope` in meno di due casi su tre.
      Lo scope e' il guadagno misurato piu' grande (da 2/32 a 8/32), quindi qui
      il profilo o non e' arrivato o non viene seguito.
    - ``search-only`` — cerca con lo scope ma non usa mai il grafo ne' registra
      niente: meta' del profilo e' in vigore.
    - ``follows-profile`` — cerca con lo scope e usa almeno una delle altre due
      porte.
    """

    path = client_registry_path() if registry is None else registry
    try:
        known = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(known, dict):
        return []

    rapporti: list[dict[str, Any]] = []
    for name, entry in sorted(known.items()):
        behaviour = entry.get("behaviour") or {}
        searches = int(behaviour.get("searches", 0))
        with_scope = int(behaviour.get("searches_with_scope", 0))
        rate = (with_scope / searches) if searches else None
        if not int(behaviour.get("calls", 0)):
            verdict = "no-usage"
        elif searches and rate is not None and rate < 2 / 3:
            verdict = "ignores-scope"
        elif not int(behaviour.get("graph_calls", 0)) and not int(behaviour.get("task_steps", 0)):
            verdict = "search-only"
        else:
            verdict = "follows-profile"
        # Ricalcolato dal nome, non letto dal registro: le etichette dei
        # bersagli possono cambiare (e sono cambiate: «Codex CLI» -> «Codex»,
        # perche' quel nome suggeriva una superficie e non una cartella), e un
        # valore congelato al momento della prima connessione mostrerebbe per
        # sempre l'etichetta vecchia.
        riconosciuto, segnale, _ = identify_from_entry(name, entry)
        rapporti.append(
            {
                "name": name,
                "recognised_as": riconosciuto.client if riconosciuto else None,
                "signal": segnale,
                "process": entry.get("process") or "",
                "connections": int(entry.get("connections", 0)),
                "verdict": verdict,
                "scope_rate": rate,
                **{campo: int(behaviour.get(campo, 0)) for campo in COMPLIANCE_FIELDS},
            }
        )
    return rapporti

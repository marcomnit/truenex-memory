"""Risolvere `ricevitore.metodo()` usando i tipi che il codice dichiara già.

## Il difetto che questo modulo attacca

Misurato il 2026-08-22 su un progetto Rust: delle funzioni con almeno un
chiamante in un ALTRO file, il grafo non ne trovava nessuno nell'83% dei casi
(19 su 23). I silenzi hanno tutti la stessa forma:

    // hub_connect.rs
    session.publish_vault_state(&id, snapshot, false).await;

    // hub_ipc.rs
    impl<S> AuthenticatedIpcSession<S> { pub async fn publish_vault_state(...) }

tree-sitter vede «chiamata al metodo `publish_vault_state` su un'espressione»,
ma non sa che quell'espressione è un `AuthenticatedIpcSession`, quindi l'arco
verso la definizione in un altro file non nasce. Il grafo risponde «lo chiama
solo un test», che è falso.

## Perché questa strada e non un resolver vero

Consultati codex e kimi: entrambi hanno scartato sia l'euristica del nome
univoco (`se esiste una sola funzione chiamata foo, l'arco va lì`) sia
rust-analyzer come primo passo. La prima mente in modo sistematico sui metodi
dei trait standard e sul dispatch dinamico; il secondo è una toolchain per
linguaggio con tempi di build, sproporzionata al problema.

Entrambi hanno proposto la stessa terza via: usare solo i tipi che il codice
**dichiara esplicitamente** — `let x: Tipo`, parametri di funzione, `self` —
e cercare il metodo solo negli `impl` di quel tipo. Non è completa come una
type inference vera, ma non richiede niente e copre esattamente la forma che
stiamo perdendo.

## Il patto che questo modulo rispetta

Gli archi che produce sono marcati `confidence="inferred"` e restano
distinguibili da quelli del parser per sempre. La ragione non è pudore: un
grafo che mente è peggio di un grafo incompleto, perché chi legge si fida. Dove
l'informazione non basta si rinuncia — un nome di variabile con due tipi diversi
nello stesso file non produce nessun arco, invece di produrne uno a caso.

## I limiti, dichiarati

- Solo Rust. Le altre lingue perdono le stesse chiamate e questo modulo non le
  aiuta: dirlo è meglio che estendere a caso una regola tarata su una sintassi.
- Nessuna nozione di visibilità, di crate, di `cfg` o di macro.
- I tipi dedotti sono quelli scritti a mano nel codice. Un `let x = Foo::new()`
  senza annotazione non viene risolto, benché il tipo sia ovvio per un umano.
- L'ambito è il file: una variabile omonima in due funzioni dello stesso file
  con tipi diversi viene scartata, non indovinata.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

# `impl<S> Session<S>` oppure `impl<S> Trait<S> for Session<S>`: in entrambi i
# casi i metodi appartengono all'ULTIMO tipo nominato, che con `for` è il tipo
# concreto e senza `for` è il tipo stesso.
_IMPL = re.compile(
    r"^\s*impl(?:\s*<[^>]*>)?\s+(?:[\w:]+(?:\s*<[^>]*>)?\s+for\s+)?([\w:]+)",
)
_FN = re.compile(r"^\s*(?:pub(?:\s*\([^)]*\))?\s+)?(?:const\s+|async\s+|unsafe\s+|extern\s+\"[^\"]*\"\s+)*fn\s+(\w+)")

# `let nome: Tipo`, `let mut nome: Tipo`, con riferimenti e generici.
_LET = re.compile(r"\blet\s+(?:mut\s+)?(\w+)\s*:\s*&?(?:mut\s+)?([\w:]+)")
# Parametri e campi: `nome: &mut Tipo`. Deliberatamente largo, poi filtrato.
_BINDING = re.compile(r"\b(\w+)\s*:\s*&?(?:'\w+\s+)?(?:mut\s+)?([\w:]+)")
# `let x = Tipo::new(...)` oppure `let x = Tipo { ... }`: il tipo NON e'
# annotato ma e' scritto comunque, nel costruttore. E' la forma che restava fuori
# dopo la prima misura — su tre casi persi guardati a mano, due erano questo
# (`let prompt_engine = PromptEngine::new()`), e chiamarlo «tipo non dichiarato»
# sarebbe stato falso: e' dichiarato, in un'altra posizione della sintassi.
_LET_CTOR = re.compile(
    r"\blet\s+(?:mut\s+)?(\w+)\s*=\s*&?\s*(?:[\w:]*::)?([A-Z]\w*)\s*(?:::\w+\s*\(|\s*\{)"
)

# `ricevitore.metodo(`
_METHOD_CALL = re.compile(r"\b(\w+)\s*\.\s*(\w+)\s*\(")

_COMMENT = re.compile(r"^\s*(//|/\*|\*)")

# Nomi che non sono tipi di questo progetto: comparirebbero come ricevitori
# plausibili e non lo sono.
_NOT_A_TYPE = frozenset(
    {
        "self", "Self", "String", "str", "Vec", "Option", "Result", "Box", "Arc",
        "Rc", "HashMap", "HashSet", "BTreeMap", "VecDeque", "RefCell", "Cell",
        "Mutex", "RwLock", "Duration", "Instant", "Path", "PathBuf", "bool",
        "char", "usize", "isize", "u8", "u16", "u32", "u64", "u128", "i8", "i16",
        "i32", "i64", "i128", "f32", "f64",
    }
)


@dataclass(frozen=True)
class InferredCall:
    """Una chiamata risolta usando un tipo dichiarato nel codice."""

    source_file: str
    source_name: str
    target_file: str
    target_name: str
    receiver_type: str


def _short(type_name: str) -> str:
    """`crate::mod::Session<S>` -> `Session`."""

    return type_name.split("::")[-1].split("<")[0].strip()


def collect_impl_methods(files: dict[str, str]) -> dict[str, set[tuple[str, str]]]:
    """``{Tipo: {(file, metodo), ...}}`` dai blocchi `impl`.

    Un tipo può avere `impl` in più file (inherent più trait): si tengono tutti,
    e l'ambiguità viene gestita da chi risolve, non nascosta qui.
    """

    per_tipo: dict[str, set[tuple[str, str]]] = {}
    for percorso, testo in files.items():
        tipo_corrente: str | None = None
        profondita = 0
        for riga in testo.splitlines():
            if _COMMENT.match(riga):
                continue
            impl = _IMPL.match(riga)
            if impl and profondita == 0:
                tipo_corrente = _short(impl.group(1))
            if tipo_corrente:
                metodo = _FN.match(riga)
                if metodo:
                    per_tipo.setdefault(tipo_corrente, set()).add((percorso, metodo.group(1)))
            profondita += riga.count("{") - riga.count("}")
            if profondita <= 0:
                profondita = 0
                if impl is None and tipo_corrente and riga.strip() == "}":
                    tipo_corrente = None
    return per_tipo


def collect_declared_types(testo: str) -> dict[str, str]:
    """``{nome_variabile: Tipo}`` dai tipi scritti a mano nel file.

    Un nome dichiarato con due tipi diversi viene **scartato**: preferire il
    silenzio a un arco indovinato è il punto di tutto il modulo.
    """

    candidati: dict[str, set[str]] = {}
    for riga in testo.splitlines():
        if _COMMENT.match(riga):
            continue
        # Il tipo INTERO anche per i parametri: `_BINDING` si ferma al `<` e da
        # `prompt_engine: State<'_, PromptEngine>` estraeva `State`, cioe' il
        # contenitore invece del contenuto. Stessa causa dei campi di struct.
        parametro = _FIELD.match(riga)
        if parametro:
            nome, tipo = parametro.groups()
            breve = _unwrap(tipo)
            if breve and breve not in _NOT_A_TYPE and breve[:1].isupper():
                candidati.setdefault(nome, set()).add(breve)
        # `_BINDING` si salta quando `_FIELD` ha gia' letto la riga: darebbero
        # due tipi per lo stesso nome (`State` contro `PromptEngine`) e la regola
        # dell'unicita' li scarterebbe entrambi — un filtro giusto che, per un
        # conflitto interno, cancellava l'informazione buona.
        for espressione in ((_LET, _LET_CTOR) if parametro else (_LET, _LET_CTOR, _BINDING)):
            for nome, tipo in espressione.findall(riga):
                breve = _short(tipo)
                if not breve or breve in _NOT_A_TYPE or not breve[0].isupper():
                    continue
                candidati.setdefault(nome, set()).add(breve)
    return {nome: next(iter(tipi)) for nome, tipi in candidati.items() if len(tipi) == 1}


def _enclosing_functions(testo: str) -> list[tuple[int, str]]:
    """``[(riga, nome_funzione)]`` per attribuire una chiamata a chi la contiene."""

    trovate = []
    for numero, riga in enumerate(testo.splitlines(), start=1):
        metodo = _FN.match(riga)
        if metodo:
            trovate.append((numero, metodo.group(1)))
    return trovate


def infer_receiver_calls(
    root: Path, files: Iterable[str], *, suffixes: frozenset[str] = frozenset({".rs"})
) -> list[InferredCall]:
    """Le chiamate `ricevitore.metodo()` che il parser non ha risolto.

    Legge i file una volta sola. Su ~150 file Rust costa qualche decina di
    millisecondi, quindi può stare nella costruzione del grafo senza pesare.
    """

    testi: dict[str, str] = {}
    for relativo in files:
        if Path(relativo).suffix.lower() not in suffixes:
            continue
        try:
            testi[relativo] = (root / relativo).read_text(encoding="utf-8", errors="replace")
        except OSError:  # pragma: no cover - file spartito durante la lettura
            continue
    if not testi:
        return []

    metodi = collect_impl_methods(testi)
    # Ambito di PROGETTO, non di file: un campo di struct e' dichiarato dove la
    # struct e' definita e usato altrove, ed era esattamente il caso che restava
    # fuori — `state.authority.next_publication()` ha come ricevitore un campo
    # dichiarato in un altro file.
    campi = collect_struct_fields(testi)
    ritorni = collect_return_types(testi)
    risultati: list[InferredCall] = []
    visti: set[tuple[str, str, str, str]] = set()

    for percorso, testo in testi.items():
        tipi = dict(campi)
        # I tipi locali vincono su quelli di progetto: nello stesso file una
        # dichiarazione esplicita e' piu' vicina alla verita' di un campo
        # omonimo definito altrove.
        tipi.update(collect_declared_types(testo))

        # `let a = state.authority.lock()`: il tipo di `a` non e' scritto, ma
        # discende da un campo noto attraverso un accessore di contenitore, e da
        # un tipo di ritorno negli altri casi.
        for nome, metodo in _LET_FROM_CALL.findall(testo):
            if nome in tipi:
                continue
            if metodo in _CONTAINER_ACCESSORS:
                continue  # gestito sotto, dove si conosce il ricevitore
            dedotto = ritorni.get(metodo)
            if dedotto:
                tipi[nome] = dedotto
        for riga in testo.splitlines():
            if _COMMENT.match(riga):
                continue
            catena = re.search(
                r"\blet\s+(?:mut\s+)?(\w+)\s*=\s*[\w.&*]*?\b(\w+)\s*\.\s*(\w+)\s*\(", riga
            )
            if not catena:
                continue
            variabile, ricevitore, accessore = catena.groups()
            if accessore not in _CONTAINER_ACCESSORS or variabile in collect_declared_types(riga):
                continue
            interno = campi.get(ricevitore) or tipi.get(ricevitore)
            if interno:
                tipi.setdefault(variabile, interno)

        if not tipi:
            continue
        funzioni = _enclosing_functions(testo)
        # Sul testo INTERO e non riga per riga: in Rust la chiamata idiomatica
        # spezza il ricevitore dal metodo, e cercarli sulla stessa riga perdeva
        # esattamente il caso da cui e' nato questo modulo —
        #
        #     session
        #         .publish_vault_state(&id, initial, true)
        #
        # Le righe di commento vengono svuotate invece che rimosse, cosi' i
        # numeri di riga restano quelli veri.
        pulito = chr(10).join(
            "" if _COMMENT.match(riga) else riga for riga in testo.splitlines()
        )
        for corrispondenza in _METHOD_CALL.finditer(pulito):
            ricevitore, metodo = corrispondenza.group(1), corrispondenza.group(2)
            numero = pulito.count(chr(10), 0, corrispondenza.start()) + 1
            if True:
                tipo = tipi.get(ricevitore)
                if tipo is None:
                    continue
                definizioni = {
                    (f, m) for (f, m) in metodi.get(tipo, set()) if m == metodo
                }
                if len(definizioni) != 1:
                    # Zero: il metodo non è di questo tipo (o è di un trait
                    # esterno). Più di uno: non sappiamo quale, e sceglierne uno
                    # sarebbe inventare.
                    continue
                file_bersaglio, nome_bersaglio = next(iter(definizioni))
                chiamante = next(
                    (nome for inizio, nome in reversed(funzioni) if inizio <= numero),
                    Path(percorso).name,
                )
                chiave = (percorso, chiamante, file_bersaglio, nome_bersaglio)
                if chiave in visti or file_bersaglio == percorso:
                    # Le chiamate nello stesso file le risolve già il parser.
                    continue
                visti.add(chiave)
                risultati.append(
                    InferredCall(
                        source_file=percorso,
                        source_name=chiamante,
                        target_file=file_bersaglio,
                        target_name=nome_bersaglio,
                        receiver_type=tipo,
                    )
                )
    return risultati


# `struct Nome {` — apre un blocco i cui `campo: Tipo` sono dichiarazioni valide
# in TUTTO il progetto, non solo nel file che le contiene. È la differenza che
# mancava: `state.authority.next_publication()` ha come ricevitore `authority`,
# che è un campo dichiarato dove la struct è definita, quasi sempre altrove.
# Il tipo di un campo va preso per intero, generici compresi: `_BINDING` si
# fermava al `<` e da `pub authority: Mutex<VaultAuthority>` estraeva `Mutex`,
# che finiva scartato come tipo del linguaggio. Il tipo utile era dentro.
_FIELD = re.compile(r"^\s*(?:pub(?:\s*\([^)]*\))?\s+)?(\w+)\s*:\s*(.+?)\s*,?\s*$")  # il tipo fino a fine riga:
# fermarsi alla prima virgola perdeva `State<'_, PromptEngine>`, dove la
# virgola sta DENTRO i generici.


_STRUCT = re.compile(r"^\s*(?:pub(?:\s*\([^)]*\))?\s+)?struct\s+(\w+)")

# `fn nome(...) -> Tipo`. Serve per `let conn = vault.conn_mut()`: il tipo di
# `conn` non è scritto lì, ma è scritto nella firma del metodo che lo produce.
_RETURNS = re.compile(
    r"\bfn\s+(\w+)\s*(?:<[^>]*>)?\s*\([^;{]*?\)\s*->\s*([\w:<>, &']+)"
)

# I contenitori che avvolgono il tipo utile: `-> Result<Vault, String>` produce
# un `Vault`, non un `Result`.
_WRAPPERS = (
    "Result", "Option", "Box", "Arc", "Rc", "Vec",
    # I contenitori di sincronizzazione: `pub authority: Mutex<VaultAuthority>`
    # e' il caso reale che restava fuori. Il tipo utile e' quello dentro, perche'
    # e' su quello che si chiamano i metodi del progetto — sul contenitore si
    # chiama solo `lock()`, che e' della libreria standard.
    "Mutex", "RwLock", "RefCell", "Cell", "MutexGuard", "RwLockReadGuard",
    "RwLockWriteGuard", "Ref", "RefMut",
    # `State<'_, PromptEngine>` di Tauri: tre degli otto casi che restavano
    # erano questo, e il tipo utile e' il secondo argomento perche' il primo e'
    # un lifetime.
    "State",
)

# Metodi che aprono un contenitore restituendo cio' che sta dentro. Non sono
# metodi del progetto, quindi non generano archi: servono a sapere che
# `let a = state.authority.lock()` da' un `VaultAuthority`, non un `Mutex`.
_CONTAINER_ACCESSORS = frozenset(
    {"lock", "borrow", "borrow_mut", "read", "write", "unwrap", "expect", "as_ref", "as_mut", "clone"}
)


def _unwrap(type_expr: str) -> str:
    """`Result<&mut Vault, String>` -> `Vault`."""

    testo = type_expr.strip()
    for _ in range(4):
        breve = testo.split("<")[0].split("::")[-1].strip()
        if breve in _WRAPPERS and "<" in testo:
            interno = testo[testo.index("<") + 1 :].rsplit(">", 1)[0]
            # Il primo argomento puo' essere un lifetime (`State<'_, T>`): si
            # prende il primo che sia un tipo.
            pezzi = [pezzo.strip() for pezzo in interno.split(",")]
            testo = next((pezzo for pezzo in pezzi if not pezzo.startswith("'")), pezzi[0])
            continue
        break
    return _short(testo.replace("&", "").replace("mut ", "").strip())


def collect_struct_fields(files: dict[str, str]) -> dict[str, str]:
    """``{nome_campo: Tipo}`` dalle definizioni di struct del progetto.

    Ambito di progetto e non di file, al contrario dei tipi locali: un campo è
    dichiarato una volta e usato altrove, che è esattamente il caso che restava
    fuori. La regola dell'unicità resta: un nome di campo con due tipi diversi
    in due struct viene scartato, non indovinato.
    """

    candidati: dict[str, set[str]] = {}
    for testo in files.values():
        dentro = False
        profondita = 0
        for riga in testo.splitlines():
            if _COMMENT.match(riga):
                continue
            if not dentro and _STRUCT.match(riga):
                dentro = "{" in riga
                profondita = riga.count("{") - riga.count("}")
                continue
            if dentro:
                campo_trovato = _FIELD.match(riga)
                for nome, tipo in ([campo_trovato.groups()] if campo_trovato else []):
                    breve = _unwrap(tipo)
                    if breve and breve not in _NOT_A_TYPE and breve[:1].isupper():
                        candidati.setdefault(nome, set()).add(breve)
                profondita += riga.count("{") - riga.count("}")
                if profondita <= 0:
                    dentro = False
    return {nome: next(iter(t)) for nome, t in candidati.items() if len(t) == 1}


def collect_return_types(files: dict[str, str]) -> dict[str, str]:
    """``{nome_metodo: Tipo restituito}``, scartando gli omonimi ambigui."""

    candidati: dict[str, set[str]] = {}
    for testo in files.values():
        for nome, tipo in _RETURNS.findall(testo):
            breve = _unwrap(tipo)
            if breve and breve not in _NOT_A_TYPE and breve[:1].isupper():
                candidati.setdefault(nome, set()).add(breve)
    return {nome: next(iter(t)) for nome, t in candidati.items() if len(t) == 1}


_LET_FROM_CALL = re.compile(r"\blet\s+(?:mut\s+)?(\w+)\s*=\s*[\w.\s?()&*]*?\.\s*(\w+)\s*\(")

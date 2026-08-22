"""Risolvere `ricevitore.metodo()` dai tipi che il codice dichiara già.

Perche' esiste. Misurato su un progetto Rust: delle funzioni con almeno un
chiamante in un altro file, il grafo non ne trovava nessuno nell'83% dei casi.
Tutti i silenzi avevano la stessa forma — una chiamata a metodo attraverso un
ricevitore, con l'`impl` in un altro file — perche' tree-sitter vede la chiamata
ma non risolve il tipo del ricevitore.

Consultati codex e kimi, entrambi hanno scartato l'euristica del nome univoco
(mente sui metodi dei trait standard) e rust-analyzer come primo passo (una
toolchain per linguaggio), proponendo la stessa terza via: usare SOLO i tipi che
il codice dichiara, e rinunciare dove non bastano.

La misura, sulle stesse 23 funzioni: **83% persi -> 65% -> 39%**, con 296 archi
dedotti che superano tutti il controllo di precisione. Il difetto non e' chiuso,
e' ridotto di due terzi.

Il patto che questi test difendono: dove l'informazione non basta si tace. Un
grafo incompleto e' un problema; un grafo che presenta una deduzione come una
lettura e' una trappola, perche' chi legge si fida.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from truenex_memory.graph.receiver_types import (
    collect_declared_types,
    collect_impl_methods,
    infer_receiver_calls,
)


def _progetto(tmp_path: Path, file: dict[str, str]) -> Path:
    """I percorsi arrivano come chiavi vere.

    La prima versione usava argomenti con nome e sostituiva `__` con `/`, quindi
    `src__hub_ipc__rs` diventava `src/hub_ipc/rs`: una cartella con un file
    senza estensione. Il test falliva mentre il codice funzionava — e per un
    po' ho cercato il difetto nel posto sbagliato.
    """

    for nome, contenuto in file.items():
        percorso = tmp_path / nome
        percorso.parent.mkdir(parents=True, exist_ok=True)
        percorso.write_text(contenuto, encoding="utf-8")
    return tmp_path


# ── i tipi dichiarati ─────────────────────────────────────────────────────

def test_a_parameter_type_is_a_declaration() -> None:
    """Il caso reale: `mut session: crate::mod::AuthenticatedIpcSession<S>`."""

    tipi = collect_declared_types(
        "async fn loop_it(\n"
        "    app: AppHandle,\n"
        "    mut session: crate::commands::hub_ipc::AuthenticatedIpcSession<S>,\n"
        ") {}\n"
    )

    assert tipi["session"] == "AuthenticatedIpcSession"


def test_a_constructor_declares_the_type_too() -> None:
    """`let x = PromptEngine::new()` non e' un tipo «non dichiarato».

    Era la forma che restava fuori dopo la prima misura: su tre casi persi
    guardati a mano, due erano questo. Il tipo e' scritto, solo in un'altra
    posizione della sintassi — e riconoscerlo ha portato i persi dal 65% al 39%.
    """

    tipi = collect_declared_types("let prompt_engine = PromptEngine::new();\n")

    assert tipi["prompt_engine"] == "PromptEngine"


def test_a_struct_literal_declares_the_type() -> None:
    tipi = collect_declared_types("let cfg = LicenseConfig { soglia: 3 };\n")

    assert tipi["cfg"] == "LicenseConfig"


def test_two_types_for_one_name_produce_nothing() -> None:
    """Preferire il silenzio a un arco indovinato e' il punto del modulo.

    Se lo stesso nome ha due tipi nel file, sceglierne uno sarebbe inventare —
    e un arco inventato in un grafo che dichiara di leggere il codice e' peggio
    di un arco mancante.
    """

    tipi = collect_declared_types(
        "fn uno(session: SessionA) {}\n"
        "fn due(session: SessionB) {}\n"
    )

    assert "session" not in tipi


def test_language_types_are_not_project_types() -> None:
    """`let x: String` non apre la caccia ai metodi di String nel progetto."""

    tipi = collect_declared_types("let nome: String = q();\nlet n: usize = 3;\n")

    assert tipi == {}


# ── i blocchi impl ────────────────────────────────────────────────────────

def test_methods_belong_to_the_concrete_type_with_and_without_for() -> None:
    """`impl<S> Session<S>` e `impl<S> Trait for Session<S>`: sempre Session."""

    metodi = collect_impl_methods(
        {
            "a.rs": "impl<S> Session<S> {\n    pub async fn invia(&self) {}\n}\n",
            "b.rs": "impl<S> Publisher for Session<S> {\n    fn chiudi(&self) {}\n}\n",
        }
    )

    assert ("a.rs", "invia") in metodi["Session"]
    assert ("b.rs", "chiudi") in metodi["Session"]


# ── la deduzione ──────────────────────────────────────────────────────────

def test_the_call_split_over_two_lines_is_found(tmp_path: Path) -> None:
    """La forma idiomatica di Rust, e il motivo per cui la prima versione fallì.

    Cercare ricevitore e metodo sulla stessa riga perdeva esattamente il caso da
    cui il modulo e' nato: 73 archi dedotti e zero verso il bersaglio noto.
    """

    radice = _progetto(
        tmp_path,
        {"src/hub_ipc.rs": "impl<S> Session<S> {\n    pub async fn publish(&mut self) {}\n}\n",
        "src/hub_connect.rs": (
            "async fn loop_it(mut session: Session<S>) {\n"
            "    session\n"
            "        .publish()\n"
            "        .await;\n"
            "}\n"
        ),},
    )

    dedotte = infer_receiver_calls(radice, ["src/hub_ipc.rs", "src/hub_connect.rs"])

    assert len(dedotte) == 1
    assert dedotte[0].source_name == "loop_it"
    assert dedotte[0].target_name == "publish"
    assert dedotte[0].receiver_type == "Session"


def test_a_method_the_type_does_not_have_is_not_linked(tmp_path: Path) -> None:
    """Il controllo che impedisce all'euristica di diventare una bugia."""

    radice = _progetto(
        tmp_path,
        {"src/a.rs": "impl Session {\n    fn invia(&self) {}\n}\n",
        "src/b.rs": "fn usa(session: Session) {\n    session.metodo_di_un_altro_tipo();\n}\n",},
    )

    assert infer_receiver_calls(radice, ["src/a.rs", "src/b.rs"]) == []


def test_two_types_defining_the_same_method_produce_nothing(tmp_path: Path) -> None:
    """Ambiguita' vera: due `impl` con lo stesso nome di metodo in due file.

    Sceglierne uno significherebbe indovinare. Kimi l'ha alzato come il rischio
    principale dell'euristica del nome, ed e' il motivo per cui qui si richiede
    UNA sola definizione per il tipo del ricevitore.
    """

    radice = _progetto(
        tmp_path,
        {"src/uno.rs": "impl Session {\n    fn invia(&self) {}\n}\n",
        "src/due.rs": "impl Session {\n    fn invia(&self) {}\n}\n",
        "src/usa.rs": "fn usa(session: Session) {\n    session.invia();\n}\n",},
    )

    assert infer_receiver_calls(radice, ["src/uno.rs", "src/due.rs", "src/usa.rs"]) == []


def test_an_unannotated_receiver_produces_nothing(tmp_path: Path) -> None:
    """Il limite dichiarato: senza tipo scritto non si deduce.

    `let x = qualcosa()` ha un tipo ovvio per un umano e ignoto per questo
    modulo. Fingere di saperlo sarebbe la trappola che stiamo evitando.
    """

    radice = _progetto(
        tmp_path,
        {"src/a.rs": "impl Session {\n    fn invia(&self) {}\n}\n",
        "src/b.rs": "fn usa() {\n    let s = fabbrica();\n    s.invia();\n}\n",},
    )

    assert infer_receiver_calls(radice, ["src/a.rs", "src/b.rs"]) == []


def test_same_file_calls_are_left_to_the_parser(tmp_path: Path) -> None:
    """Quelle le risolve gia' tree-sitter: duplicarle sporcherebbe il grafo."""

    radice = _progetto(
        tmp_path,
        {"src/solo.rs": (
            "impl Session {\n    fn invia(&self) {}\n}\n"
            "fn usa(session: Session) {\n    session.invia();\n}\n"
        ),},
    )

    assert infer_receiver_calls(radice, ["src/solo.rs"]) == []


def test_a_commented_call_is_not_a_call(tmp_path: Path) -> None:
    radice = _progetto(
        tmp_path,
        {"src/a.rs": "impl Session {\n    fn invia(&self) {}\n}\n",
        "src/b.rs": "fn usa(session: Session) {\n    // session.invia();\n}\n",},
    )

    assert infer_receiver_calls(radice, ["src/a.rs", "src/b.rs"]) == []


def test_only_rust_for_now(tmp_path: Path) -> None:
    """Il limite dichiarato nel docstring, fissato qui.

    Le altre lingue perdono le stesse chiamate e questo modulo non le aiuta.
    Estendere a caso una regola tarata su una sintassi produrrebbe archi
    plausibili e sbagliati proprio dove nessuno andrebbe a controllare.
    """

    radice = _progetto(
        tmp_path,
        {"src/a.py": "class Session:\n    def invia(self): pass\n",
        "src/b.py": "def usa(session: Session):\n    session.invia()\n",},
    )

    assert infer_receiver_calls(radice, ["src/a.py", "src/b.py"]) == []


def test_the_edge_is_marked_inferred_in_the_graph() -> None:
    """Il patto: una deduzione non si presenta mai come una lettura."""

    from truenex_memory.graph import EntityEdge

    letto = EntityEdge("a::x", "b::y", "calls", "a", "b")
    dedotto = EntityEdge("a::x", "b::y", "calls", "a", "b", confidence="inferred")

    assert letto.confidence == "resolved", "il default e' cio' che il parser produce"
    assert dedotto.to_dict()["confidence"] == "inferred"
    assert EntityEdge.from_dict(dedotto.to_dict()).confidence == "inferred"


def test_an_old_cache_reads_as_resolved() -> None:
    """Una cache scritta prima del campo non deve diventare «dedotta».

    Sarebbe il difetto simmetrico: declassare relazioni vere per prudenza rende
    inutile la distinzione appena introdotta.
    """

    from truenex_memory.graph import EntityEdge

    vecchio = {
        "source": "a::x", "target": "b::y", "relation_type": "calls",
        "source_file": "a", "target_file": "b",
    }

    assert EntityEdge.from_dict(vecchio).confidence == "resolved"


def test_the_answer_carries_the_confidence_of_each_caller() -> None:
    """Serve a chi legge la risposta, non al grafo.

    Entrambe le review hanno insistito: la provenienza deve viaggiare accanto al
    dato, altrimenti un modello tratta una deduzione come un fatto.
    """

    from truenex_memory.graph import EntityEdge, FileGraph, explain_entity

    grafo = FileGraph(
        root="/repo",
        entities=[
            EntityEdge("src/a.rs::letto", "src/c.rs::bersaglio", "calls", "src/a.rs", "src/c.rs"),
            EntityEdge(
                "src/b.rs::dedotto", "src/c.rs::bersaglio", "calls", "src/b.rs", "src/c.rs",
                confidence="inferred",
            ),
        ],
    )

    per_nome = {c["entity"]: c["confidence"] for c in explain_entity(grafo, "bersaglio")["callers"]}

    assert per_nome["src/a.rs::letto"] == "resolved"
    assert per_nome["src/b.rs::dedotto"] == "inferred"


# ── i tipi che stanno altrove: campi, ritorni, contenitori ────────────────

def test_a_struct_field_declares_a_type_for_the_whole_project() -> None:
    """Il campo e' dichiarato dove sta la struct e usato altrove.

    E' la differenza fra ambito di file e ambito di progetto:
    `state.authority.next_publication()` ha come ricevitore un campo dichiarato
    in un altro file, e cercarlo solo nel file chiamante non poteva funzionare.
    """

    from truenex_memory.graph.receiver_types import collect_struct_fields

    campi = collect_struct_fields(
        {"vault.rs": "pub struct VaultState {\n    pub authority: Mutex<VaultAuthority>,\n}\n"}
    )

    assert campi["authority"] == "VaultAuthority"


def test_a_container_is_unwrapped_to_what_is_inside() -> None:
    """Sul contenitore si chiama `lock()`, sul contenuto i metodi del progetto.

    `Mutex<VaultAuthority>` prima veniva letto come `Mutex` — il tipo del
    linguaggio — e scartato, perdendo l'informazione utile che stava dentro.
    """

    from truenex_memory.graph.receiver_types import _unwrap

    assert _unwrap("Mutex<VaultAuthority>") == "VaultAuthority"
    assert _unwrap("Result<&mut Vault, String>") == "Vault"
    assert _unwrap("Arc<Mutex<Engine>>") == "Engine"


def test_a_lifetime_argument_is_skipped() -> None:
    """`State<'_, PromptEngine>` di Tauri: il primo argomento non e' un tipo.

    Tre dei casi che restavano erano questo, e prendere il primo argomento
    generico avrebbe dato `'_`.
    """

    from truenex_memory.graph.receiver_types import _unwrap

    assert _unwrap("State<'_, PromptEngine>") == "PromptEngine"


def test_a_full_type_beats_a_truncated_one_on_the_same_line() -> None:
    """Due letture della stessa riga si annullavano a vicenda.

    Un riconoscitore si fermava al `<` e dava `State`, l'altro leggeva il tipo
    intero e dava `PromptEngine`: due tipi per un nome, e la regola
    dell'unicita' — giusta — cancellava anche quello buono. Un filtro corretto
    che, per un conflitto interno, distruggeva l'informazione.
    """

    tipi = collect_declared_types("    prompt_engine: State<'_, PromptEngine>,\n")

    assert tipi == {"prompt_engine": "PromptEngine"}


def test_a_return_type_declares_the_variable(tmp_path: Path) -> None:
    """`let conn = vault.conn_mut()`: il tipo e' nella firma del metodo."""

    from truenex_memory.graph.receiver_types import collect_return_types

    ritorni = collect_return_types(
        {"vault.rs": "impl Vault {\n    pub fn conn_mut(&mut self) -> &mut Connection {}\n}\n"}
    )

    assert ritorni["conn_mut"] == "Connection"


def test_two_methods_with_the_same_name_and_different_returns_are_dropped() -> None:
    """La regola dell'unicita' vale anche qui: meglio tacere che indovinare."""

    from truenex_memory.graph.receiver_types import collect_return_types

    ritorni = collect_return_types(
        {
            "a.rs": "fn apri(&self) -> Vault {}\n",
            "b.rs": "fn apri(&self) -> Sessione {}\n",
        }
    )

    assert "apri" not in ritorni


def test_the_chain_through_a_lock_resolves(tmp_path: Path) -> None:
    """Il caso segnalato da MiniMax come ancora rotto.

        let mut authority = state.authority.lock().unwrap();
        let snapshot = authority.next_publication();

    Il tipo di `authority` non e' scritto lì, ma il campo `authority` e'
    dichiarato `Mutex<VaultAuthority>` in un altro file, e `lock()` apre il
    contenitore. Due informazioni entrambe presenti nel codice.
    """

    radice = _progetto(
        tmp_path,
        {
            "src/vault.rs": (
                "pub struct VaultState {\n"
                "    pub authority: Mutex<VaultAuthority>,\n"
                "}\n"
            ),
            "src/authority.rs": (
                "impl VaultAuthority {\n    pub fn next_publication(&mut self) {}\n}\n"
            ),
            "src/connect.rs": (
                "fn pubblica(state: VaultState) {\n"
                "    let mut authority = state.authority.lock().unwrap();\n"
                "    let snapshot = authority.next_publication();\n"
                "}\n"
            ),
        },
    )

    dedotte = infer_receiver_calls(
        radice, ["src/vault.rs", "src/authority.rs", "src/connect.rs"]
    )

    assert [(d.source_name, d.target_name) for d in dedotte] == [
        ("pubblica", "next_publication")
    ]


def test_a_field_name_with_two_types_is_dropped() -> None:
    """Due struct, un nome di campo, due tipi: nessun arco."""

    from truenex_memory.graph.receiver_types import collect_struct_fields

    campi = collect_struct_fields(
        {
            "a.rs": "struct Uno {\n    motore: MotoreA,\n}\n",
            "b.rs": "struct Due {\n    motore: MotoreB,\n}\n",
        }
    )

    assert "motore" not in campi

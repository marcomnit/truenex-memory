"""MCP stdio server for Truenex Memory.

The stdio transport uses newline-delimited JSON-RPC messages. This module keeps
the implementation dependency-free so local tests can exercise the MCP contract
without installing the optional Python MCP SDK.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from truenex_memory import __version__
from truenex_memory.mcp.identity import (
    Cronometro, Diario, identita_archivio, istruzioni,
)
from truenex_memory.mcp.tools import (
    global_project_context, global_status, memory_add, memory_get, memory_resume,
    memory_search, task_open, task_step_add, task_close,
)


SUPPORTED_PROTOCOL_VERSIONS = (
    "2025-11-25",
    "2025-06-18",
    "2025-03-26",
    "2024-11-05",
)
DEFAULT_PROTOCOL_VERSION = SUPPORTED_PROTOCOL_VERSIONS[0]

JSONRPC_VERSION = "2.0"


def run_stdio_server(project_root: Path | str = ".") -> None:
    """Run the MCP stdio server.

    Il ciclo esce solo su EOF di stdin. Non ha timeout e non si chiude da se':
    quindi una chiusura viene sempre da fuori — il client, una terminazione, o
    la pipe rotta. Distinguere quei casi era impossibile, perche' il server non
    scriveva niente da nessuna parte: un tool che fallisce con «Connection
    closed» lasciava quattro spiegazioni ugualmente plausibili (richiesta non
    arrivata, arrivata e non elaborata, elaborata e risposta perduta, errore in
    scrittura) e nessun modo di scegliere. Ora ognuna lascia la sua traccia.

    La scrittura della risposta e' racchiusa: prima era fuori da qualsiasi
    gestore, quindi una pipe chiusa mentre si scriveva uccideva il processo
    senza lasciare niente. Lo ha fatto notare la revisione di Codex — l'avevo
    letto come «esce solo su EOF» e non era vero.
    """

    root = Path(project_root).resolve()
    diario = Diario()
    identita = identita_archivio(root)
    diario.avvio(identita)

    contate = 0
    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            contate += 1
            crono = Cronometro()
            richiesta = _riferimento_richiesta(line)
            diario.evento("request_in", **richiesta)

            response = handle_jsonrpc_line(line, project_root=root)
            if response is None:
                diario.evento("request_done", **richiesta, ms=crono.ms(), risposta="nessuna")
                continue

            try:
                print(json.dumps(response, separators=(",", ":"), sort_keys=True), flush=True)
            except (BrokenPipeError, OSError) as exc:
                # Il client ha chiuso stdout mentre rispondevamo. La richiesta
                # e' stata ELABORATA e la risposta e' andata perduta: e' un caso
                # diverso da «mai arrivata», e sapere quale dei due e' avvenuto
                # decide se un ritentativo e' sicuro.
                diario.evento(
                    "write_error",
                    **richiesta,
                    ms=crono.ms(),
                    errore=f"{type(exc).__name__}: {exc}",
                )
                diario.uscita("stdout_chiuso", richieste_servite=contate)
                return
            diario.evento("response_written", **richiesta, ms=crono.ms())
    except Exception as exc:  # pragma: no cover - confine del processo
        diario.uscita(
            "eccezione_nel_ciclo",
            errore=f"{type(exc).__name__}: {exc}",
            richieste_servite=contate,
        )
        raise

    # Fine naturale: stdin ha dato EOF. Il peer ha chiuso la pipe; da qui non si
    # puo' sapere CHI, e dirlo sarebbe un'invenzione.
    diario.uscita("stdin_eof", richieste_servite=contate)


def _riferimento_richiesta(line: str) -> dict[str, Any]:
    """Come chiamare questa richiesta nel diario.

    Il solo scopo e' correlare: il log del client parla di `Tool 'x' failed`, il
    nostro deve poter essere messo accanto. Se la riga non e' JSON valido non e'
    un problema di questa funzione — sara' `handle_jsonrpc_line` a rispondere
    con un errore di parsing — quindi qui si degrada senza lamentarsi.
    """

    try:
        m = json.loads(line)
    except Exception:
        return {"req": None, "metodo": None, "tool": None}
    if not isinstance(m, dict):
        return {"req": None, "metodo": "batch" if isinstance(m, list) else None, "tool": None}
    params = m.get("params")
    tool = params.get("name") if isinstance(params, dict) else None
    return {"req": m.get("id"), "metodo": m.get("method"), "tool": tool}


def handle_jsonrpc_line(line: str, *, project_root: Path | str = ".") -> dict[str, Any] | list[dict[str, Any]] | None:
    """Handle one newline-delimited JSON-RPC message or batch."""

    try:
        message = json.loads(line)
    except json.JSONDecodeError as exc:
        return _error_response(None, -32700, "Parse error", str(exc))

    if isinstance(message, list):
        responses = [
            response
            for item in message
            if (response := handle_jsonrpc_message(item, project_root=project_root)) is not None
        ]
        return responses or None
    return handle_jsonrpc_message(message, project_root=project_root)


def handle_jsonrpc_message(message: object, *, project_root: Path | str = ".") -> dict[str, Any] | None:
    """Handle one JSON-RPC request or notification."""

    if not isinstance(message, dict):
        return _error_response(None, -32600, "Invalid Request", "message must be an object")
    request_id = message.get("id")
    method = message.get("method")
    if message.get("jsonrpc") != JSONRPC_VERSION or not isinstance(method, str):
        return _error_response(request_id, -32600, "Invalid Request", "expected JSON-RPC 2.0 request")

    is_notification = "id" not in message
    params = message.get("params", {})
    if params is None:
        params = {}
    if not isinstance(params, dict):
        return None if is_notification else _error_response(request_id, -32602, "Invalid params")

    try:
        result = _dispatch(method, params, project_root=Path(project_root))
    except KeyError as exc:
        return None if is_notification else _error_response(request_id, -32601, "Method not found", str(exc))
    except ValueError as exc:
        return None if is_notification else _error_response(request_id, -32602, "Invalid params", str(exc))
    except Exception as exc:  # pragma: no cover - defensive protocol boundary
        return None if is_notification else _error_response(request_id, -32603, "Internal error", str(exc))

    if is_notification:
        return None
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result}


def _dispatch(method: str, params: dict[str, Any], *, project_root: Path) -> dict[str, Any]:
    if method == "initialize":
        return _initialize(params, project_root=project_root)
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": _tool_definitions()}
    if method == "tools/call":
        # Osservare come il client usa memoria e' il solo modo di sapere se il
        # profilo e' arrivato che non dipenda dalla collaborazione del client
        # stesso. Racchiuso in un try perche' una risposta perduta sarebbe un
        # danno vero, mentre un contatore mancato non e' niente.
        try:
            from truenex_memory.adapters.profile import record_tool_use

            record_tool_use(_CLIENT_NAME, params.get("name", ""), params.get("arguments"))
        except Exception:  # pragma: no cover - mai far cadere una chiamata
            pass
        return _call_tool(params, project_root=project_root)
    if method.startswith("notifications/"):
        return {}
    raise KeyError(method)


# Chi sta servendo questo processo. Un server stdio serve un solo client per
# tutta la sua vita, quindi una variabile di modulo e' esatta e non una
# scorciatoia: il nome arriva nell'handshake e vale per tutte le chiamate
# successive.
_CLIENT_NAME: str | None = None


def _initialize(params: dict[str, Any], *, project_root: Path | str = ".") -> dict[str, Any]:
    requested = params.get("protocolVersion")
    protocol_version = requested if requested in SUPPORTED_PROTOCOL_VERSIONS else DEFAULT_PROTOCOL_VERSION
    from truenex_memory.adapters.profile import (
        client_registry_path,
        profile_text,
        record_client,
        refresh_on_connect,
    )

    # Chi si e' collegato lo dice lui stesso, qui: `clientInfo` e' il segnale
    # autorevole su quali client esistono davvero su questa macchina — non una
    # deduzione da quali cartelle esistono. Annotarlo serve anche a scoprire i
    # client che ancora non sappiamo riconoscere: un nome non mappato viene
    # registrato invece di essere ignorato, perche' un client senza istruzioni
    # non da' nessun errore e altrimenti non lo scopriremmo mai.
    #
    # Racchiuso in un try: un handshake rotto e' molto peggio di un profilo non
    # aggiornato, e questo non e' il lavoro per cui il client si e' collegato.
    try:
        global _CLIENT_NAME
        info = params.get("clientInfo") or {}
        _CLIENT_NAME = info.get("name")
        record_client(info.get("name"), info.get("version"), client_registry_path())
        refresh_on_connect(info.get("name"))
    except Exception:  # pragma: no cover - mai far cadere l'handshake
        pass

    return {
        "protocolVersion": protocol_version,
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": {"name": "truenex-memory", "version": __version__},
        # Stesso testo che finisce nei file utente dei client, dalla stessa
        # sorgente. Va servito perche' costa zero, NON perche' ci si possa
        # contare: la specifica dice che il client «puo'» aggiungerlo al proprio
        # prompt di sistema, quindi un client conforme puo' ignorarlo — e ci
        # sono client che lo fanno (il gateway di LiteLLM lo perde, langchain4j
        # lo riceve e non lo espone). Il canale su cui si conta e' il file.
        #
        # In testa, QUALE archivio serve questa istanza. Su questa macchina
        # erano vive tre istanze con radici diverse — due sulla cartella utente,
        # una sul repo del sorgente — e chi faceva le domande non aveva modo di
        # sapere a quale archivio le stava facendo. Un archivio quasi vuoto
        # risponde «non ho trovato niente», che e' indistinguibile da «non
        # esiste niente da trovare».
        # Una riga davanti al profilo: la prima cosa che chi legge vede e'
        # a quale archivio sta parlando.
        # Prima QUALE archivio, poi il profilo: chi legge deve sapere a chi
        # sta parlando prima di sapere come parlargli.
        "instructions": istruzioni(identita_archivio(project_root), profile_text()),
        # La stessa cosa in forma leggibile da un programma, accanto a
        # `serverInfo` che identifica il software: il software e l'archivio sono
        # due identita' diverse e confonderle e' l'errore che ha aperto questa
        # indagine.
        "_meta": {"truenex/archive": identita_archivio(project_root)},
    }


def _tool_definitions() -> list[dict[str, Any]]:
    """Return the explicit stdio MCP tool surface.

    This is intentionally smaller than the local Python registry in
    `truenex_memory.mcp`: agent-facing stdio tools are limited to memory and
    read-only global bootstrap operations.
    """
    return [
        {
            "name": "memory_search",
            # Le descrizioni dei tool sono l'unico canale garantito dal
            # protocollo: `tools/list` e' obbligatorio, quindi nessun client puo'
            # chiamare uno strumento senza averne preso lo schema. Il profilo di
            # comportamento arriva invece per vie che dipendono dal client (un
            # file che deve leggere, o un campo `instructions` che «puo'»
            # incorporare), e il 2026-08-22 si e' visto cosa comporta: un client
            # ha recitato correttamente cosa fa lo `scope` e cos'e' il grafo —
            # informazioni che stanno QUI — e poi ha ricostruito una catena di
            # chiamate leggendo i file, perche' l'imperativo stava solo nel
            # profilo, che con ogni probabilita' non gli e' mai arrivato.
            #
            # Quindi le regole operative stanno anche qui, dove arrivano sempre.
            # La duplicazione col profilo e' voluta ma non lasciata al caso: un
            # test verifica che i due testi concordino sugli imperativi, cosi'
            # una modifica a uno solo dei due fa cadere la suite.
            "description": (
                "Search local Truenex Memory for project context and source-backed "
                "decisions. Returns excerpts plus a document_id/memory_id per result; "
                "call memory_get to read one in full. "
                "Call this BEFORE broadly reading or grepping a repository: it is "
                "what past decisions and constraints were written down in. "
                "Pass the folder you are working in as `scope` by default; omit it "
                "only for deliberately cross-project questions. "
                "For structural questions — who calls this, what uses this, which "
                "tests cover it — use memory_graph instead, and do not reconstruct "
                "a call chain by reading files."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural-language query for project memory.",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Maximum number of results to return.",
                        "minimum": 1,
                        "maximum": 50,
                        "default": 5,
                    },
                    "full_content": {
                        "type": "boolean",
                        "description": (
                            "Return verbatim bodies instead of excerpts. Costs "
                            "several kilobytes per call; prefer memory_get on the "
                            "one result you actually need."
                        ),
                        "default": False,
                    },
                    "scope": {
                        "type": "string",
                        "description": (
                            "Restrict document candidates to paths containing "
                            "this substring — normally the project you are "
                            "working in. The store holds every project at once, "
                            "so an unscoped search competes against roughly "
                            "eighty times more candidates than the relevant "
                            "ones: measured on questions phrased without the "
                            "target document's own words, scoping doubled the "
                            "answers found (2/32 -> 4/32 on a held-out set, MRR "
                            "0.042 -> 0.094). Omit it for genuinely "
                            "cross-project questions; a WRONG scope makes the "
                            "answer unreachable rather than merely worse. "
                            "IT RESTRICTS DOCUMENTS ONLY: memory nodes — the "
                            "decisions and notes written while working — are "
                            "searched unrestricted, because a memory's path "
                            "records where it was WRITTEN, not what it is "
                            "about (measured: 85 of 3120 memories carry their "
                            "project's name in the path; the rest sit under the "
                            "session file they were born in). So a result from "
                            "another project is a declared limit, not a bug: "
                            "the reply's `scope.coverage` names which half was "
                            "filtered, and `answered_from` names every project "
                            "the answer came from."
                        ),
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
        {
            "name": "memory_get",
            "description": (
                "Read one memory_search result in full, by the document_id or "
                "memory_id that result carried."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "document_id": {
                        "type": "string",
                        "description": "document_id from a memory_search result.",
                    },
                    "memory_id": {
                        "type": "string",
                        "description": "memory_id from a memory_search result.",
                    },
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "memory_graph",
            "description": (
                "Structural facts about one function, class or file, read from "
                "the code graph: who calls it, what it calls, which tests "
                "exercise it. A different door from memory_search — that one "
                "answers what was written and decided, this one how the code is "
                "wired. The "
                "relations are parsed from the source, so what it reports is "
                "accurate — but what it does NOT report proves nothing. "
                "Extraction is incomplete for method calls made through a "
                "receiver from another file: measured on a Rust project, 83% of "
                "cross-file callers were missing. The reply carries a `coverage` "
                "field with short codes for the limits that apply: "
                "`cross_file_method_calls` means callers in other files may be "
                "missing (the percentage is measured, not guessed); "
                "`no_caller_outside_defining_file` is the typical shape of that "
                "miss; `tests_detection: unknown` means an empty `tests` list is "
                "«unknown», never «none». Each caller carries `confidence`: "
                "`resolved` was read by the parser, `inferred` was deduced from a "
                "declared receiver type — open the file before relying on it. "
                "`candidate_callers_from_text` holds call sites found by text "
                "search: leads to open, never to be reported as graph-resolved "
                "relations. "
                "Call this BEFORE opening or grepping the files: a few hundred "
                "tokens here replace tens of thousands spent reconstructing the "
                "same chain by reading. Requires "
                "`truenex-mem graph build <path>` for that tree."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": (
                            "A function name, class name or file path fragment, "
                            "e.g. `_search_memories` or `store/repository.py`."
                        ),
                    },
                    "scope": {
                        "type": "string",
                        "description": (
                            "Which project's graph to consult, as a path "
                            "fragment. Omit when only one graph is built."
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 50,
                        "default": 12,
                        "description": "Maximum entries per group.",
                    },
                },
                "required": ["target"],
                "additionalProperties": False,
            },
        },
        {
            "name": "memory_add",
            "description": (
                "Add a local memory note or decision for the current project. "
                "Pass `project` to target a DIFFERENT project than the one this "
                "session is running in — a name, basename, or path already "
                "confirmed in the global source catalog — for a note "
                "discovered while working elsewhere but relevant there. "
                "Without it, the note always goes to the current project."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "Memory content to store locally.",
                    },
                    "supersedes": {
                        "type": "string",
                        "description": (
                            "Id of a memory this one replaces. That memory "
                            "becomes 'superseded' and leaves search, while "
                            "still pointing here. Pass it whenever this note "
                            "corrects or updates an earlier one."
                        ),
                    },
                    "memory_type": {
                        "type": "string",
                        "description": "Memory type, such as note or decision.",
                        "default": "note",
                    },
                    "project": {
                        "type": "string",
                        "description": (
                            "Write into this project instead of the current "
                            "session's own project. Resolved against the global "
                            "source catalog; an ambiguous or unknown name comes "
                            "back as an error naming the candidates instead of "
                            "guessing."
                        ),
                    },
                },
                "required": ["content"],
                "additionalProperties": False,
            },
        },
        {
            "name": "memory_resume",
            "description": (
                "Chronological recap of recent work on THIS project: what "
                "happened last, in what order, what tasks are still open. Use "
                "this for 'where did we leave off', 'what's the current "
                "status', 'what's next' — questions about RECENCY, not "
                "relevance. memory_search ranks content by similarity to a "
                "query, which measurably does not answer 'what happened last' "
                "(dense/lexical ranking both optimise for topical match, not "
                "time); memory_resume instead lists this project's own "
                "activity log, recent tasks, and recent notes/decisions, "
                "strictly ordered by time, with an explicit `coverage` field "
                "naming what each source can and cannot promise. Call this "
                "FIRST when resuming a session, before memory_search."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "limit_activity": {
                        "type": "integer",
                        "description": "Maximum recent tool-call log entries.",
                        "minimum": 1,
                        "maximum": 100,
                        "default": 20,
                    },
                    "limit_tasks": {
                        "type": "integer",
                        "description": "Maximum recent tasks (name-matched, best-effort).",
                        "minimum": 1,
                        "maximum": 50,
                        "default": 5,
                    },
                    "limit_memories": {
                        "type": "integer",
                        "description": "Maximum recent memory_add notes/decisions.",
                        "minimum": 1,
                        "maximum": 50,
                        "default": 10,
                    },
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "global_status",
            "description": "Read-only report of the Truenex Memory global store status (catalog, database, ledger, indexed, problems).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "home": {
                        "type": "string",
                        "description": "Override the global home directory (default: user home).",
                    },
                    "catalog": {
                        "type": "string",
                        "description": "Override path to sources.json catalog.",
                    },
                    "db": {
                        "type": "string",
                        "description": "Override path to SQLite global database.",
                    },
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "global_project_context",
            "description": "Read-only project context report from the Truenex Memory global store (catalog roots, ledger, indexed documents/chunks). Server aliases are hints only; no SSH/network/DB execution.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "project": {
                        "type": "string",
                        "description": "Project name, path, or alias to resolve.",
                    },
                    "home": {
                        "type": "string",
                        "description": "Override the global home directory (default: user home).",
                    },
                    "catalog": {
                        "type": "string",
                        "description": "Override path to sources.json catalog.",
                    },
                    "db": {
                        "type": "string",
                        "description": "Override path to SQLite global database.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum indexed documents/chunks to return.",
                        "minimum": 1,
                        "maximum": 100,
                        "default": 20,
                    },
                },
                "required": ["project"],
                "additionalProperties": False,
            },
        },
        {
            "name": "task_open",
            "description": "Open a new adaptive task record. Call at the start of every agent session to enable outcome tracking.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Short description of the task."},
                    "task_type": {"type": "string", "enum": ["bugfix", "feature", "refactor", "review", "query"], "default": "feature"},
                    "project": {"type": "string", "description": "Project name (e.g. AI_Agent)."},
                    "agent_session_id": {"type": "string", "description": "Current agent session ID."},
                },
                "required": ["title"],
                "additionalProperties": False,
            },
        },
        {
            "name": "task_step_add",
            "description": "Record a step taken within the current task (prompt, output, brain judgment, token usage).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "Task ID returned by task_open."},
                    "prompt_used": {"type": "string"},
                    "output": {"type": "string"},
                    "brain_judgment": {"type": "string", "enum": ["ok", "needs_revision", "rejected"]},
                    "tokens_used": {"type": "integer"},
                    "duration_s": {"type": "number"},
                    "model_used": {"type": "string"},
                },
                "required": ["task_id"],
                "additionalProperties": False,
            },
        },
        {
            "name": "task_close",
            "description": "Close the current task. Ask the human for a quality judgment (1=positive, 0=partial, -1=negative) before calling. Omit human_outcome if the human did not respond.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "human_outcome": {"type": "integer", "enum": [1, 0, -1]},
                    "human_comment": {"type": "string"},
                },
                "required": ["task_id"],
                "additionalProperties": False,
            },
        },
    ]


def _call_tool(params: dict[str, Any], *, project_root: Path) -> dict[str, Any]:
    name = params.get("name")
    arguments = params.get("arguments", {})
    if not isinstance(name, str) or not name:
        raise ValueError("tool name is required")
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        raise ValueError("tool arguments must be an object")

    ok = True
    try:
        if name == "memory_search":
            payload = _call_memory_search(arguments, project_root=project_root)
        elif name == "memory_get":
            payload = _call_memory_get(arguments, project_root=project_root)
        elif name == "memory_graph":
            payload = _call_memory_graph(arguments, project_root=project_root)
        elif name == "memory_add":
            payload = _call_memory_add(arguments, project_root=project_root)
        elif name == "memory_resume":
            payload = _call_memory_resume(arguments, project_root=project_root)
        elif name == "global_status":
            payload = _call_global_status(arguments, project_root=project_root)
        elif name == "global_project_context":
            payload = _call_global_project_context(arguments)
        elif name == "task_open":
            payload = _call_task_open(arguments)
        elif name == "task_step_add":
            payload = _call_task_step_add(arguments)
        elif name == "task_close":
            payload = _call_task_close(arguments)
        else:
            raise ValueError(f"unknown tool: {name}")
    except Exception as exc:
        ok = False
        payload = {"error": str(exc)}
    _log_activity(name, arguments, payload, ok=ok, project_root=project_root)
    return _tool_result(payload, is_error=not ok)


def _log_activity(
    name: str, arguments: dict[str, Any], payload: object, *, ok: bool, project_root: Path
) -> None:
    """Registra la chiamata nella cronologia automatica del progetto.

    Non deve MAI far fallire una risposta gia' calcolata: una riga di
    cronologia persa e' un'informazione in meno per `memory_resume`, un
    errore qui sarebbe un danno vero al chiamante che non c'entra niente con
    questa registrazione. Vedi `store.activity_log` per il perche' questo e'
    l'unico punto dove la scrittura non dipende dalla disciplina del client.
    """

    try:
        from truenex_memory.core.config import resolve_project_config
        from truenex_memory.store.activity_log import record

        config = resolve_project_config(project_root)
        import os

        project_id = os.environ.get("TRUENEX_PROJECT_ID") or config.project_root.name
        record(
            config.db_path,
            project_id=project_id,
            tool_name=name,
            summary=_activity_summary(arguments, payload),
            ok=ok,
        )
    except Exception:  # pragma: no cover - la cronologia non deve mai rompere una risposta
        pass


def _activity_summary(arguments: dict[str, Any], payload: object) -> str | None:
    for key in ("content", "query", "target", "title", "project"):
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    if isinstance(payload, dict) and isinstance(payload.get("error"), str):
        return f"error: {payload['error']}"
    return None


def _call_memory_graph(arguments: dict[str, Any], *, project_root: Path) -> dict[str, object]:
    from truenex_memory.mcp.tools import memory_graph

    target = arguments.get("target")
    if not isinstance(target, str) or not target.strip():
        raise ValueError("target must be a non-empty string")
    scope = arguments.get("scope")
    if scope is not None and not isinstance(scope, str):
        raise ValueError("scope must be a string")
    limit = arguments.get("limit", 12)
    if not isinstance(limit, int):
        raise ValueError("limit must be an integer")
    if limit < 1 or limit > 50:
        raise ValueError("limit must be between 1 and 50")
    return memory_graph(
        target.strip(),
        scope=scope.strip() if isinstance(scope, str) and scope.strip() else None,
        limit=limit,
        project_root=project_root,
    )


def _call_memory_search(arguments: dict[str, Any], *, project_root: Path) -> dict[str, object]:
    query = arguments.get("query")
    top_k = arguments.get("top_k", 5)
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a non-empty string")
    if not isinstance(top_k, int):
        raise ValueError("top_k must be an integer")
    if top_k < 1 or top_k > 50:
        raise ValueError("top_k must be between 1 and 50")
    full_content = arguments.get("full_content", False)
    if not isinstance(full_content, bool):
        raise ValueError("full_content must be a boolean")
    scope = arguments.get("scope")
    if scope is not None and not isinstance(scope, str):
        raise ValueError("scope must be a string")
    if isinstance(scope, str) and not scope.strip():
        scope = None
    return memory_search(
        query,
        top_k=top_k,
        full_content=full_content,
        project_root=project_root,
        scope=scope,
    )


def _call_memory_get(arguments: dict[str, Any], *, project_root: Path) -> dict[str, object]:
    document_id = arguments.get("document_id")
    memory_id = arguments.get("memory_id")
    for label, value in (("document_id", document_id), ("memory_id", memory_id)):
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError(f"{label} must be a non-empty string")
    if bool(document_id) == bool(memory_id):
        raise ValueError("provide exactly one of document_id or memory_id")
    return memory_get(
        document_id=document_id,
        memory_id=memory_id,
        project_root=project_root,
    )


def _call_memory_add(arguments: dict[str, Any], *, project_root: Path) -> dict[str, object]:
    content = arguments.get("content")
    memory_type = arguments.get("memory_type", "note")
    supersedes = arguments.get("supersedes")
    project = arguments.get("project")
    if supersedes is not None and (not isinstance(supersedes, str) or not supersedes.strip()):
        raise ValueError("supersedes must be a non-empty string")
    if project is not None and (not isinstance(project, str) or not project.strip()):
        raise ValueError("project must be a non-empty string")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("content must be a non-empty string")
    if not isinstance(memory_type, str) or not memory_type.strip():
        raise ValueError("memory_type must be a non-empty string")
    return memory_add(
        content,
        memory_type=memory_type,
        supersedes=supersedes,
        project=project.strip() if isinstance(project, str) else None,
        project_root=project_root,
    )


def _call_memory_resume(arguments: dict[str, Any], *, project_root: Path) -> dict[str, object]:
    limits: dict[str, int] = {}
    for key, default in (
        ("limit_activity", 20),
        ("limit_tasks", 5),
        ("limit_memories", 10),
    ):
        value = arguments.get(key, default)
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"{key} must be an integer")
        if value < 1 or value > 100:
            raise ValueError(f"{key} must be between 1 and 100")
        limits[key] = value
    return memory_resume(project_root=project_root, **limits)


def _call_global_status(arguments: dict[str, Any], *, project_root: Path) -> dict[str, object]:
    """Lo stato del negozio globale, PIU' l'archivio che questa istanza serve.

    Il difetto che questo chiude. `global_status` guarda sempre la cartella
    utente, mentre `memory_search` e `memory_graph` guardano la radice con cui il
    server e' stato avviato. Su questa macchina era vivo un server avviato sul
    repo del sorgente: quel processo cercava in un archivio con 171 documenti e
    riferiva lo stato di uno con 18.426. Chi chiedeva lo stato vedeva un archivio
    pieno e riceveva ricerche vuote, e le due cose non erano collegabili.

    Il contratto di `global_status` resta quello che dice il nome — il negozio
    globale — perche' cambiarlo sarebbe un'altra bugia. Ma la risposta porta
    accanto l'archivio SERVITO, e quando i due non coincidono lo dichiara: cosi'
    lo scarto e' leggibile invece di essere un compito di attenzione.
    """

    home = arguments.get("home")
    catalog = arguments.get("catalog")
    db = arguments.get("db")

    for name, val in (("home", home), ("catalog", catalog), ("db", db)):
        if val is not None and not isinstance(val, str):
            raise ValueError(f"{name} must be a string")

    report = global_status(home=home, catalog=catalog, db=db)

    servito = identita_archivio(project_root)
    globale = identita_archivio(Path(home) if home else Path.home())
    if isinstance(report, dict):
        report = {
            **report,
            "served_archive": servito,
            # Vero quando le ricerche di QUESTA istanza vanno in un archivio
            # diverso da quello descritto qui sopra.
            "served_archive_differs": servito["archive_id"] != globale["archive_id"],
        }
    return report


def _call_global_project_context(arguments: dict[str, Any]) -> dict[str, object]:
    project = arguments.get("project")
    home = arguments.get("home")
    catalog = arguments.get("catalog")
    db = arguments.get("db")
    limit = arguments.get("limit", 20)

    if not isinstance(project, str) or not project.strip():
        raise ValueError("project must be a non-empty string")
    for name, val in (("home", home), ("catalog", catalog), ("db", db)):
        if val is not None and not isinstance(val, str):
            raise ValueError(f"{name} must be a string")
    if not isinstance(limit, int) or limit < 1 or limit > 100:
        raise ValueError("limit must be an integer between 1 and 100")

    return global_project_context(
        project=project,
        home=home,
        catalog=catalog,
        db=db,
        limit=limit,
    )


def _call_task_open(arguments: dict[str, Any]) -> dict[str, object]:
    title = arguments.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("title must be a non-empty string")
    return task_open(
        title,
        arguments.get("task_type", "feature"),
        project=arguments.get("project"),
        agent_session_id=arguments.get("agent_session_id"),
    )


def _call_task_step_add(arguments: dict[str, Any]) -> dict[str, object]:
    task_id = arguments.get("task_id")
    if not isinstance(task_id, str) or not task_id.strip():
        raise ValueError("task_id must be a non-empty string")
    return task_step_add(
        task_id,
        prompt_used=arguments.get("prompt_used"),
        output=arguments.get("output"),
        brain_judgment=arguments.get("brain_judgment"),
        tokens_used=arguments.get("tokens_used"),
        duration_s=arguments.get("duration_s"),
        model_used=arguments.get("model_used"),
    )


def _call_task_close(arguments: dict[str, Any]) -> dict[str, object]:
    task_id = arguments.get("task_id")
    if not isinstance(task_id, str) or not task_id.strip():
        raise ValueError("task_id must be a non-empty string")
    human_outcome = arguments.get("human_outcome")
    if human_outcome is not None and human_outcome not in (1, 0, -1):
        raise ValueError("human_outcome must be 1, 0, or -1")
    return task_close(task_id, human_outcome=human_outcome, human_comment=arguments.get("human_comment"))


def _tool_result(payload: dict[str, object], *, is_error: bool) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(payload, indent=2, sort_keys=True)}],
        "isError": is_error,
    }


def _error_response(
    request_id: object,
    code: int,
    message: str,
    data: object | None = None,
) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "error": error}

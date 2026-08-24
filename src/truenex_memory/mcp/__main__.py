"""Invocare i tool di memoria da terminale, senza collegare un client MCP.

Perche' questo file conta. Chi non puo' provare una cosa da terminale la prova
in produzione. Due difetti lo rendevano inutilizzabile per verificare davvero:

Il primo: il registro conteneva quattro tool su nove. `memory_graph` — le
relazioni del codice, la cosa piu' utile del prodotto — non c'era.

Il secondo: nessun modo di dire QUALE archivio interrogare. I tool che accettano
`project_root` ricadevano su ".", cioe' l'archivio della cartella corrente.
Lanciato dentro il repo del sorgente, `memory_search` cercava in un archivio di
prova da 171 documenti invece di quello vero da 18.426, e la risposta non diceva
da dove veniva. E' costato un pomeriggio.

Adesso l'archivio interrogato viene sempre scritto su stderr, prima della
risposta. Su stderr perche' stdout deve restare JSON pulito, utilizzabile con
una pipe.
"""

from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path
from typing import Annotated, Any

import typer

from truenex_memory.mcp import TOOLS, call_tool, list_tools
from truenex_memory.mcp.identity import identita_archivio, riga_archivio

app = typer.Typer(help="Invoke local MCP-style tools without starting a server.")


@app.command("list")
def list_command() -> None:
    """Elenca i tool disponibili."""

    typer.echo(json.dumps(list_tools(), indent=2, sort_keys=True))


@app.command("call")
def call_command(
    name: Annotated[str, typer.Argument(help="Tool name.")],
    arguments: Annotated[str, typer.Option("--arguments", help="JSON object with tool arguments.")] = "{}",
    project_root: Annotated[
        str | None,
        typer.Option(
            "--project-root",
            help=(
                "Radice dell'archivio da interrogare. Per difetto la cartella utente,"
                " che e' l'archivio che usano gli agenti. Passare '.' per interrogare"
                " l'archivio della cartella corrente."
            ),
        ),
    ] = None,
) -> None:
    """Chiama un tool con argomenti JSON, dichiarando su quale archivio.

    La radice per difetto e' la cartella utente e non ".": e' l'archivio a cui
    parlano gli agenti, quindi e' l'unico su cui una verifica a mano dice
    qualcosa di utile. Chi vuole l'archivio locale lo chiede con `--project-root .`
    ed e' comunque scritto in chiaro quale sia.
    """

    parsed = json.loads(arguments)
    if not isinstance(parsed, dict):
        raise typer.BadParameter("--arguments must be a JSON object")

    radice = Path(project_root) if project_root is not None else Path.home()

    # `project_root` si passa SOLO ai tool che lo accettano: `global_status` e i
    # `task_*` hanno altre firme, e infilarglielo dentro sarebbe un errore di
    # chiamata al posto di un aiuto.
    tool = TOOLS.get(name)
    accetta = False
    if tool is not None:
        try:
            accetta = "project_root" in inspect.signature(tool).parameters
        except (TypeError, ValueError):  # pragma: no cover - builtin senza firma
            accetta = False
    if accetta and "project_root" not in parsed:
        parsed["project_root"] = str(radice)

    # Quale archivio, detto prima della risposta. Senza questa riga, una risposta
    # vuota e una risposta da un archivio sbagliato sono indistinguibili.
    print(
        riga_archivio(identita_archivio(radice))
        if accetta
        else f"Tool «{name}»: non interroga un archivio di progetto"
        f" (radice richiesta: {radice}).",
        file=sys.stderr,
    )

    typer.echo(json.dumps(call_tool(name, parsed), indent=2, sort_keys=True, default=str))


def main() -> None:  # pragma: no cover - punto di ingresso
    app()


if __name__ == "__main__":
    app()

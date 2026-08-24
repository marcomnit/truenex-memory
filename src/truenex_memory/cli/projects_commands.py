"""Comandi per l'identita' dei progetti.

    truenex-mem projects classify        mostra cosa cambierebbe, senza toccare niente
    truenex-mem projects classify --apply  scrive le attribuzioni
    truenex-mem projects status          cosa e' stato classificato

Perche' la prova a vuoto e' il comportamento predefinito. Riclassificare cambia
l'etichetta di ogni sorgente dell'archivio, e le regole sono cambiate due volte
in un'ora di lavoro: da 58 nomi a 209 e poi a 54. Un comando che riscrive 17.053
righe senza far vedere prima cosa fa non e' uno strumento, e' un rischio.
"""

from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path

import typer

from truenex_memory.projects import attribuisci
from truenex_memory.projects.store import Riga, leggi_tutte, riepilogo, scrivi

projects_app = typer.Typer(help="Project identity: which project a source belongs to.")


def _radice_archivio(project_root: str | None) -> Path:
    if project_root:
        return Path(project_root).resolve()
    dall_ambiente = os.environ.get("TRUENEX_PROJECT_ROOT", "").strip()
    return Path(dall_ambiente).resolve() if dall_ambiente else Path.cwd()


def _db(radice: Path) -> Path:
    from truenex_memory.core.config import DATA_DIR_NAME, DB_FILENAME

    return radice / DATA_DIR_NAME / DB_FILENAME


@projects_app.command("classify")
def classify(
    project_root: str = typer.Option(
        None, "--project-root",
        help="Radice dell'archivio. Per difetto TRUENEX_PROJECT_ROOT o la cartella corrente.",
    ),
    apply: bool = typer.Option(
        False, "--apply",
        help="Scrive le attribuzioni. Senza questa opzione mostra soltanto cosa cambierebbe.",
    ),
    limite_esempi: int = typer.Option(12, "--examples", help="Quanti esempi mostrare per specie."),
    json_out: bool = typer.Option(False, "--json", help="Uscita leggibile da un programma."),
) -> None:
    """Attribuisce ogni sorgente al suo progetto, o dichiara di non saperlo."""

    from truenex_memory.adapters.profile import known_project_roots
    from truenex_memory.store.sqlite import connect

    radice = _radice_archivio(project_root)
    db = _db(radice)
    if not db.exists():
        typer.echo(f"nessun archivio in {db}")
        raise typer.Exit(code=1)

    radici = known_project_roots()
    contenitori = [r.parent for r in radici]

    with connect(db) as conn:
        precedenti = leggi_tutte(conn)
        sorgenti = conn.execute(
            "SELECT source_id, source_path_or_alias, project_name"
            " FROM source_ledger WHERE status = 'active'"
        ).fetchall()

        # La stessa cartella ricorre migliaia di volte: attribuirla una volta.
        cache: dict[str, object] = {}
        righe: list[Riga] = []
        specie = Counter()
        cambi = Counter()
        esempi: dict[str, list[str]] = {}
        progetti = Counter()

        for source_id, percorso, nome_vecchio in sorgenti:
            chiave = str(Path(str(percorso).replace("\\", "/")).parent)
            if chiave not in cache:
                cache[chiave] = attribuisci(
                    percorso, radici_esplicite=radici, contenitori=contenitori
                )
            a = cache[chiave]  # type: ignore[assignment]
            righe.append(Riga(source_id, a))
            specie[a.kind] += 1
            if a.kind == "project":
                progetti[a.display_name] += 1

            nuovo = a.display_name or f"({a.kind})"
            if (nome_vecchio or "") != nuovo:
                cambi[f"{nome_vecchio or '(nullo)'} -> {nuovo}"] += 1
                esempi.setdefault(a.kind, [])
                if len(esempi[a.kind]) < limite_esempi:
                    esempi[a.kind].append(f"{nome_vecchio or '(nullo)'} -> {nuovo}: {percorso}")

        rapporto = {
            "archive": str(db),
            "sources": len(sorgenti),
            "already_classified": len(precedenti),
            "by_kind": dict(specie),
            "projects": len(progetti),
            "changes": sum(cambi.values()),
            "applied": apply,
        }

        if apply:
            scritte = scrivi(conn, righe)
            conn.commit()
            rapporto["written"] = scritte

    if json_out:
        typer.echo(json.dumps(rapporto, indent=2, ensure_ascii=False))
        return

    typer.echo(f"archivio: {db}")
    typer.echo(f"sorgenti attive: {len(sorgenti)}")
    typer.echo("")
    typer.echo("specie:")
    for k, n in specie.most_common():
        typer.echo(f"  {k:12s} {n:6d}")
    typer.echo("")
    typer.echo(f"progetti riconosciuti: {len(progetti)}")
    for n, q in progetti.most_common(limite_esempi):
        typer.echo(f"  {n:34s} {q:6d}")
    typer.echo("")
    typer.echo(f"etichette che cambierebbero: {sum(cambi.values())} sorgenti")
    for cambio, n in cambi.most_common(limite_esempi):
        typer.echo(f"  {n:6d}  {cambio}")

    if not apply:
        typer.echo("")
        typer.echo("PROVA A VUOTO: niente e' stato scritto.")
        typer.echo("Per scrivere:  truenex-mem projects classify --apply")
    else:
        typer.echo("")
        typer.echo(f"scritte {rapporto.get('written', 0)} attribuzioni.")


@projects_app.command("status")
def status(
    project_root: str = typer.Option(None, "--project-root"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Cosa e' stato classificato in questo archivio."""

    from truenex_memory.store.sqlite import connect

    db = _db(_radice_archivio(project_root))
    if not db.exists():
        typer.echo(f"nessun archivio in {db}")
        raise typer.Exit(code=1)

    with connect(db) as conn:
        r = riepilogo(conn)
    r["archive"] = str(db)

    if json_out:
        typer.echo(json.dumps(r, indent=2, ensure_ascii=False))
        return

    typer.echo(f"archivio: {db}")
    if not r.get("table"):
        typer.echo("nessuna classificazione in questo archivio.")
        typer.echo("Per farla:  truenex-mem projects classify --apply")
        return
    typer.echo(f"sorgenti classificate: {r['classified']}")
    typer.echo(f"specie: {r.get('by_kind')}")
    typer.echo(f"progetti distinti: {r.get('projects')}")
    if r.get("with_old_rules"):
        typer.echo(
            f"ATTENZIONE: {r['with_old_rules']} righe scritte con regole precedenti"
            f" (versione attuale: {r['rules_version']}). Riclassifica per allinearle."
        )

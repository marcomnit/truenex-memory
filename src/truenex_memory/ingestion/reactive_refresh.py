"""Tenere fresco l'indice globale senza un demone permanente.

Stesso disegno di ``graph.refresh``, applicato al corpus documentale globale
invece che al grafo del codice: truenex-memory e' un server MCP stdio, vive
solo quanto vive il client che lo lancia, quindi non esiste un processo
sempre attivo a cui affidare uno scheduler. Il rimedio e' lo stesso: un
controllo a costo pressoche' nullo (confronto di un timestamp) dentro le
porte che gia' leggono lo stato globale (``global_status``,
``global_project_context``), e una ricostruzione avviata in un processo
separato quando l'indice e' piu' vecchio della soglia — mai bloccando la
risposta corrente.

Diagnosi che ha motivato questo modulo (2026-09-17): ``global_status``
riportava un ``last_indexed_at`` fermo a settimane prima perche' il refresh
era raggiungibile solo a mano (``truenex-mem global refresh``). Un indice
derivato che non sa da solo di essere vecchio e' un compito lasciato alla
memoria di una persona — esattamente il difetto che ``graph.refresh`` aveva
gia' tolto dal grafo del codice.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Sotto quale eta' l'ultimo refresh globale e' considerato ancora valido.
# Sei ore: abbastanza per non rilanciare un refresh a ogni chiamata durante
# una sessione di lavoro, abbastanza poco perche' "settimane fa" non accada
# piu' a chi usa lo strumento almeno una volta al giorno.
MAX_AGE_ENV = "TRUENEX_GLOBAL_REFRESH_MAX_AGE_SECONDS"
DEFAULT_MAX_AGE_SECONDS = 6 * 60 * 60

# Spegne il meccanismo. Acceso per default, per lo stesso motivo per cui lo
# e' AUTO_REBUILD_ENV in graph.refresh: un artefatto derivato che si aggiorna
# solo se qualcuno lo configura torna a essere un compito per una persona.
AUTO_REFRESH_ENV = "TRUENEX_GLOBAL_AUTO_REFRESH"

# Quanto vale un lucchetto abbandonato prima di essere ignorato — vedi
# graph.refresh.LOCK_STALE_AFTER_SECONDS per il ragionamento completo.
LOCK_STALE_AFTER_SECONDS = 30 * 60


def auto_refresh_enabled() -> bool:
    """Vero se il refresh reattivo e' attivo (default: si').

    Si spegne con ``TRUENEX_GLOBAL_AUTO_REFRESH=0`` (o ``off``/``false``/``no``).
    """

    raw = (os.environ.get(AUTO_REFRESH_ENV) or "").strip().lower()
    return raw not in {"0", "off", "false", "no"}


def max_age_seconds() -> int:
    """Soglia di eta' oltre la quale l'indice globale e' considerato vecchio."""

    raw = os.environ.get(MAX_AGE_ENV)
    if not raw:
        return DEFAULT_MAX_AGE_SECONDS
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MAX_AGE_SECONDS
    return value if value > 0 else DEFAULT_MAX_AGE_SECONDS


def _lock_path(db_path: Path) -> Path:
    return db_path.parent / f"{db_path.stem}.refreshing"


def _claim(lock: Path) -> bool:
    """Prende il lucchetto se libero. Vero se il chiamante deve avviare il refresh.

    Stessa tecnica di ``graph.refresh._claim`` (creazione esclusiva, O_EXCL):
    se due client scoprono l'indice vecchio nello stesso istante, uno solo
    avvia il refresh, l'altro riceve "in corso".
    """

    try:
        if lock.exists() and time.time() - lock.stat().st_mtime > LOCK_STALE_AFTER_SECONDS:
            lock.unlink(missing_ok=True)
    except OSError:  # pragma: no cover - corsa con un altro processo
        pass
    try:
        lock.parent.mkdir(parents=True, exist_ok=True)
        handle = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    except OSError:  # pragma: no cover - cartella non scrivibile
        return False
    with os.fdopen(handle, "w", encoding="utf-8") as stream:
        stream.write(str(os.getpid()))
    return True


def _background_interpreter() -> str:
    """L'interprete senza console, quando esiste — vedi graph.refresh per il perche'."""

    if os.name != "nt":
        return sys.executable
    candidate = Path(sys.executable).with_name("pythonw.exe")
    return str(candidate) if candidate.exists() else sys.executable


def _spawn_refresh(catalog_path: Path, db_path: Path, lock: Path) -> bool:
    """Lancia `global refresh` in un processo separato e non lo aspetta."""

    command = [
        _background_interpreter(), "-m", "truenex_memory",
        "global", "refresh",
        "--catalog", str(catalog_path), "--db", str(db_path),
    ]
    creation = 0
    if os.name == "nt":  # pragma: no cover - dipende dalla piattaforma
        creation = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        subprocess.Popen(  # noqa: S603 - comando costruito qui, non da input
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creation,
            close_fds=True,
        )
    except OSError:
        lock.unlink(missing_ok=True)
        return False
    return True


def _age_seconds(last_indexed_at: str | None) -> float | None:
    if not last_indexed_at:
        return None
    try:
        parsed = datetime.fromisoformat(last_indexed_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - parsed).total_seconds()


def ensure_global_fresh(
    *,
    catalog_path: Path,
    db_path: Path,
    last_indexed_at: str | None,
    allow_refresh: bool | None = None,
) -> dict[str, Any]:
    """Confronta l'eta' dell'indice globale con la soglia e, se vecchio, lo rinfresca.

    Non blocca: torna sempre subito. Il valore va messo nella risposta della
    porta che l'ha chiamata, con lo stesso spirito di
    ``graph.refresh.ensure_current`` — dichiarare che una risposta parla del
    passato e' l'unico modo perche' chi chiama lo sappia senza doverselo
    ricordare da solo.

    ``refresh`` vale ``"non necessario"``, ``"avviata"``, ``"in corso"``,
    ``"disattivata"``, ``"non applicabile"`` (nessun catalogo) o
    ``"non avviata"`` (il lancio e' fallito).
    """

    age = _age_seconds(last_indexed_at)
    threshold = max_age_seconds()
    stale = age is None or age > threshold
    result: dict[str, Any] = {
        "last_indexed_at": last_indexed_at,
        "age_seconds": age,
        "max_age_seconds": threshold,
        "stale": stale,
    }
    if not stale:
        result["refresh"] = "non necessario"
        return result

    if not catalog_path.exists():
        result["refresh"] = "non applicabile"
        result["hint"] = "nessun catalogo sorgenti: truenex-mem global discover"
        return result

    enabled = auto_refresh_enabled() if allow_refresh is None else allow_refresh
    if not enabled:
        result["refresh"] = "disattivata"
        result["hint"] = "truenex-mem global refresh"
        return result

    lock = _lock_path(db_path)
    if not _claim(lock):
        result["refresh"] = "in corso"
        return result
    result["refresh"] = "avviata" if _spawn_refresh(catalog_path, db_path, lock) else "non avviata"
    if result["refresh"] == "non avviata":
        result["hint"] = "truenex-mem global refresh"
    return result


def release_lock(db_path: Path) -> None:
    """Toglie il lucchetto. Chiamato al termine di `global refresh`."""

    _lock_path(db_path).unlink(missing_ok=True)

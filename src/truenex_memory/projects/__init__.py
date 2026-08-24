"""Identita' dei progetti: cos'e' un progetto e a quale appartiene un percorso."""

from truenex_memory.projects.identity import (
    KIND_DATI_AGENTE,
    KIND_NON_ASSEGNATO,
    KIND_PROGETTO,
    Attribuzione,
    attribuisci,
    chiave_radice,
    identificatore,
)

__all__ = [
    "Attribuzione",
    "KIND_DATI_AGENTE",
    "KIND_NON_ASSEGNATO",
    "KIND_PROGETTO",
    "attribuisci",
    "chiave_radice",
    "identificatore",
]

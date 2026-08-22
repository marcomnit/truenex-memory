"""Nessun test puo' toccare la cartella utente vera.

Perche' questa guardia esiste: i test dell'handshake MCP mandano un
`clientInfo` finto (`{"name": "test"}`, o nessun nome), e il server annota chi
si collega nel registro dei client. Il registro stava nella home reale, quindi
la suite ha lasciato due client inventati nel file di chi sviluppa — e
`profile clients`, che serve a sapere quali client esistono davvero, ha
cominciato a mentire.

Un test con un effetto collaterale sulla macchina di qualcuno e' un difetto, e
la correzione giusta non e' rattoppare i due test che se ne sono accorti: e'
rendere impossibile il caso. Questa fixture e' `autouse`, quindi copre anche i
test che nessuno ha ancora scritto.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _profilo_in_una_home_finta(tmp_path_factory, monkeypatch) -> None:
    from truenex_memory.adapters.profile import PROFILE_HOME_ENV

    finta = tmp_path_factory.mktemp("home-finta")
    monkeypatch.setenv(PROFILE_HOME_ENV, str(finta))


@pytest.fixture(autouse=True)
def _nessun_indizio_dai_processi(monkeypatch) -> None:
    """Il riconoscimento non deve dipendere da chi ha avviato pytest.

    Il client si identifica anche risalendo l'albero dei processi, e in una
    suite quell'albero e' quello del terminale di chi la esegue: lanciata da
    Claude Code, un client «mai visto» risultava Claude Code, e tre test
    cambiavano esito in base allo strumento usato per eseguirli. Un test cosi'
    non misura il codice, misura l'ambiente.

    Chi vuole verificare la risalita passa una catena esplicita a
    `identify_from_entry`; qui la si azzera.
    """

    from truenex_memory.adapters import profile

    monkeypatch.setattr(profile, "process_ancestry", lambda *a, **k: [])

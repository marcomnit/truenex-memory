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

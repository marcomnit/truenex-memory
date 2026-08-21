"""Punto d'ingresso per `python -m truenex_memory`.

Esiste perche' il pacchetto deve poter rilanciare se stesso senza dipendere da
dove si trova lo script console `truenex-mem`: dentro un server MCP avviato da
un client qualunque, `sys.executable` e' noto e la cartella degli script no
(virtualenv, pipx, installazione utente, o un client che imposta un PATH
proprio). `python -m truenex_memory` funziona in tutti quei casi.
"""

from truenex_memory.cli.main import app

if __name__ == "__main__":
    app()

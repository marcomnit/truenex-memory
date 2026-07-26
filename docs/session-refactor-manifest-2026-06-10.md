# Sessione: Refactor Agent Discovery Manifest

**Data:** 2026-06-10
**Ticket:** AGENT-DISC-REFACTOR-2026-06-10
**Agente:** Kimi Code CLI

## Cosa è stato fatto

1. **Refactor di agent_discovery.py**: rimosso AGENT_ROOTS hardcoded, sostituito con manifest JSON esterno (`~/.truenex-memory/agent_manifest.json`).
2. **CLI commands aggiunti**: `truenex-mem agent list`, `agent add`, `agent remove`.
3. **Test**: 675 test passati, 7 nuovi test per il manifest.
4. **Verifica su PC reale**: il sistema ora scopre correttamente Kimi, Cursor, OpenClaw, Aider, Antigravity, Gemini (prima erano invisibili).

## Lezioni apprese (critico)

- **Il catalogo sources.json va tenuto pulito**. Confermare tutte le candidate con `--yes --limit 100` ha inondato il catalogo di 214 entries (Linux remote, `D:\`, `node_modules`, directory inesistenti). Il refresh è rimasto in esecuzione 90+ minuti.
- **Ripulito il catalogo**: da 214 a 127 entries. Il refresh torna a finire in pochi secondi.
- **Sessioni Kimi**: il file `context.jsonl` viene scritto su disco in tempo reale, ma NON viene indicizzato automaticamente. Serve `global refresh`.
- **Workflow corretto per salvare sessioni**:
  1. Scrivere riepilogo in un file del progetto
  2. Lanciare `truenex-mem add` per renderlo immediatamente ricercabile

## Stato finale

- Manifest: 9 agenti configurati (8 default + 1 aggiunto per test)
- Catalogo: 127 entries pulite
- Ledger: 38.206 righe
- Sessione corrente indicizzata: 355/384 exchange active

## Prossimi step

- Aggiornare sito web memory.truenex.ai con documentazione del nuovo sistema manifest.
- Configurare auto-refresh periodico (Task Scheduler Windows).

# Roadmap — Truenex Memory

> **Living document.** Questo file è la fonte di verità operativa.  
> Ogni task ha uno stato, un owner implicito (chi ci lavora), e un criterio di done.  
> Nuove idee vanno in fondo alla sezione **Backlog — Idee da Valutare**; non si saltano task in corso per inseguire idee nuove.

---

## Legenda

| Priorità | Significato | Quando farlo |
|----------|-------------|--------------|
| **P0** | Bloccante / bug che rompe il contratto con l'utente | Subito, interrompe tutto |
| **P1** | Importante per la milestone corrente | Questa settimana / sprint |
| **P2** | Nice-to-have, miglioramento qualitativo | Prossimo sprint |
| **P3** | Futuro, da valutare dopo le milestone chiave | Backlog |

| Stato | Icona |
|-------|-------|
| Da fare | `[-]` |
| In corso | `[>]` |
| Fatto | `[x]` |
| Scartato | `[~]` |

---

## Stato Attuale (Snapshot)

- **Versione**: `0.2.0a1` (backend) / `0.2.0` (desktop)
- **Test**: 612 passati, coverage **76%** (78% senza `serve.py`)
- **Working tree backend**: clean
- **Desktop**: no git repo, bug attivo su `AgentSessionList`

### Cosa funziona oggi
- CLI completo (`truenex-mem`), MCP server, SQLite store, BM25+semantic, export/import, migration, auto-memory lifecycle, agent discovery, orchestrator ricorsivo, file metadata API.

### Cosa NON funziona oggi
- PyPI: il pacchetto **non è pubblicato** (README promette `pip install truenex-memory` → fallisce).
- Endpoint `/api/file-analysis` mancante (frontend chiama API inesistente).
- Manifest update remoto fermo a `0.1.0` (chi ha `0.2.0a1` non riceve mai notifiche).
- Desktop GUI: `AgentSessionList` non renderizza, HMR rotto, chunk size ~887 kB.
- Coverage API HTTP (`serve.py`): **0%**.

---

## Fase 0 — Quick Wins (Settimana 1)

> Obiettivo: chiudere i bug P0 che rendono la documentazione bugiarda o rotto il contratto frontend/backend.

### 0.1 Pubblicare su PyPI (o correggere il README)
**Priorità: P0** | `[-]` | Stima: 2-4h

- [ ] Decidere: pubblicare `0.2.0a1` come pre-release su PyPI, oppure togliere `pip install truenex-memory` dal README.
- [ ] Se pubblicare:
  - [ ] Assicurarsi che `pip install -e .` e `pip install .` siano puliti (già verificato).
  - [ ] Usare `python -m build` + `twine upload --repository pypi dist/*`.
  - [ ] Verificare con `pip install truenex-memory` in ambiente pulito.
- [ ] Se NON pubblicare:
  - [ ] Rimuovere sezione "From PyPI" da README e docs/installation.md.
  - [ ] Sostituire con "Install from source only (alpha stage)".

**Criterio di done**: un utente con Python 3.12 pulito può installare seguendo il README senza errori.

---

### 0.2 Aggiungere endpoint `/api/file-analysis`
**Priorità: P0** | `[-]` | Stima: 30 min

- [ ] In `serve.py`, aggiungere:
  ```python
  @app.get("/api/file-analysis")
  def file_analysis(file_id: str = Query(...)):
      svc = _get_service()
      return svc.repository.analyze_file_content(file_id)
  ```
- [ ] Verificare che il frontend possa effettivamente chiamarlo.

**Criterio di done**: `curl "http://localhost:8000/api/file-analysis?file_id=<id>"` restituisce JSON valido.

---

### 0.3 Sincronizzare manifest update remoto
**Priorità: P1** | `[-]` | Stima: 15 min

- [ ] Aggiornare `version.json` in `marcomnit/truenex-memory-releases` a:
  ```json
  {
    "manifest_version": "1",
    "version": "0.2.0a1",
    "channel": "dev",
    "force_update": false,
    "update_full": false,
    "download_url": null,
    "release_notes_url": "https://github.com/marcomnit/truenex-memory/blob/main/CHANGELOG.md",
    "requires_migration": false,
    "min_supported_version": "0.1.0"
  }
  ```

**Criterio di done**: `truenex-mem update check` su installazione `0.2.0a1` riporta `"update_available": false` (stessa versione) e `"latest_version": "0.2.0a1"`.

---

### 0.4 Hardcoded version in `build_release.py`
**Priorità: P1** | `[-]` | Stima: 30 min

- [ ] Modificare `scripts/build_release.py` per leggere la versione da `pyproject.toml` (TOML parse o regex) invece di hardcoded `"0.2.0a1"`.

**Criterio di done**: cambiando versione in `pyproject.toml`, `build_release.py` produce artefatti con la nuova versione senza editare lo script.

---

## Fase 1 — v0.2.0 Stable Release (Settimane 2-4)

> Obiettivo: uscire dall'alpha e dichiarare `0.2.0` stabile.  
> Requisiti: tutti i P0 chiusi, coverage >80%, README allineato, manifest sync, release artifacts funzionanti.

### 1.1 Test coverage >80%
**Priorità: P1** | `[-]` | Stima: 1-2 giorni

- [ ] Aggiungere test per `serve.py`:
  - [ ] `GET /api/health`
  - [ ] `GET /api/sources`
  - [ ] `GET /api/stats`
  - [ ] `GET /api/file-metadata`
  - [ ] `GET /api/file-analysis` (da creare in 0.2)
  - [ ] `GET /api/settings`
  - [ ] Testare gestione errori (DB mancante, document_id invalido, etc.)
- [ ] Aggiungere test per `mcp/server.py` e `mcp/tools.py` (ora 69% / 71%).
- [ ] Aggiungere test per `semantic.py` (ora 67%).

**Criterio di done**: `pytest --cov=src/truenex_memory --ignore=tests/e2e` riporta **TOTAL ≥ 80%**.

---

### 1.2 README: documentare feature mancanti
**Priorità: P1** | `[-]` | Stima: 2-3h

- [ ] Aggiungere sezione o tabella per i comandi non menzionati:
  - `truenex-mem serve` — spiegare che è l'HTTP API per il desktop GUI.
  - `truenex-mem orchestrate run / converge-check` — breve descrizione con link a `docs/recursive-orchestrator-design.md`.
  - `truenex-mem task` — open/close/list/show/calibration.
  - `truenex-mem adapter` — generare file adapter per agenti.
- [ ] Aggiornare CLI reference nel README per includere i comandi mancanti.

**Criterio di done**: ogni sottocomando di `truenex-mem --help` ha almeno una riga di documentazione nel README o in un doc linkato.

---

### 1.3 Changelog & Versione
**Priorità: P1** | `[-]` | Stima: 30 min

- [ ] Aggiornare `CHANGELOG.md`: spostare `[Unreleased]` in `[0.2.0] — YYYY-MM-DD`.
- [ ] Aggiornare `pyproject.toml` a `version = "0.2.0"` (togliere `a1`).
- [ ] Aggiornare `src/truenex_memory/release/version.py` se necessario.
- [ ] Tag git `v0.2.0`.
- [ ] Build release artifacts: `python scripts/build_release.py`.
- [ ] Upload PyPI (se si è scelto di pubblicare in 0.1).
- [ ] Aggiornare manifest remoto a `0.2.0`.

**Criterio di done**: `pip install truenex-memory==0.2.0` funziona (o almeno `git checkout v0.2.0 && pip install -e .` è verificato).

---

### 1.4 Documentare i limiti noti
**Priorità: P2** | `[-]` | Stima: 1h

- [ ] Aggiungere sezione "Known Limitations" in README o `docs/troubleshooting.md`:
  - RAG non è ibrido (è semantic-with-BM25-fallback, non re-ranking combinato).
  - Scheduler auto-refresh è manuale (richiede cron/Task Scheduler manuale).
  - `update apply` non esiste; solo check manuale.
  - Desktop GUI è sperimentale e non inclusa nella distribuzione Python.

**Criterio di done**: un nuovo utente che legge il README sa cosa NON aspettarsi.

---

## Fase 2 — v0.3.0 Ecosystem (Mese 2-3)

> Obiettivo: rendere il motore estendibile e migliorare la qualità retrieval.

### 2.1 RAG Ibrido (3-phase retrieval)
**Priorità: P1** | `[-]` | Stima: 3-5 giorni

- [ ] Aggiungere colonna `embedding BLOB` alla tabella `chunks` in schema v5 (o v4 lazy).
- [ ] Implementare `MemoryRepository.search_hybrid(query, top_k=10)`:
  1. **BM25 recall**: top 50 candidati.
  2. **Semantic re-rank**: cosine similarity sui 50 candidati usando embedding locale.
  3. **Deduplication**: by `source_path`, adaptive threshold.
- [ ] Lazy migration: se un chunk non ha embedding BLOB, calcolarlo on-demand durante la query e salvarlo.
- [ ] CLI: `truenex-mem migrate embeddings` per batch migration esplicita.
- [ ] Test: verificare che query su termini tecnici specifici restituiscano risultati migliori di BM25 puro.
- [ ] Aggiornare `docs/semantic-rag-architecture-plan.md` con decisioni finali (sqlite-vec vs BLOB Python).

**Criterio di done**: test benchmark mostrano miglioramento precision@5 vs BM25 puro su almeno 10 query di test.

---

### 2.2 Plugin system (custom embedders)
**Priorità: P2** | `[-]` | Stima: 1 settimana

- [ ] Definire interfaccia `BaseEmbedder` (abstract class o protocol).
- [ ] Refactor `semantic.py` per usare `BaseEmbedder` inve di chiamate dirette.
- [ ] Supportare registrazione embedder via entry points Python (`setup.py` / `pyproject.toml` `[project.entry-points]`).
- [ ] Documentare come scrivere un plugin embedder.

**Criterio di done**: utente può installare un pacchetto `truenex-memory-embedder-openai` e usare `--embedder openai` senza modificare il core.

---

### 2.3 Performance: incremental indexing
**Priorità: P2** | `[-]` | Stima: 2-3 giorni

- [ ] `index` deve confrontare mtime/hash dei file prima di re-chunkare.
- [ ] Aggiungere colonna `file_hash` o `mtime` a `documents`.
- [ ] Skip file non modificati durante `global refresh`.

**Criterio di done**: `global refresh` su 8000 documenti già indicizzati impiega < 5 secondi se nessun file è cambiato.

---

### 2.4 Safer multi-project merge/import
**Priorità: P2** | `[-]` | Stima: 2-3 giorni

- [ ] `truenex-mem import` con modalità `--merge` vs `--replace`.
- [ ] Dedup su hash contenuto durante import.
- [ ] Preview dell'import (`--dry-run`) che mostra conflitti.

**Criterio di done**: import di due export diversi non crea duplicati e gestisce conflitti di `source_id`.

---

## Fase 3 — Desktop GUI Stabilizzazione (Parallela)

> Questo è un progetto separato (`truenex-memory-desktop`). Non blocca il backend.  
> Posizionamento: per ora **strumento personale di Marco**, da decidere se diventerà free, Pro, o rimarrà separato.

### 3.1 Creare repository Git
**Priorità: P1 (per Marco)** | `[-]` | Stima: 30 min

- [ ] `git init` nella directory desktop.
- [ ] `.gitignore` per `node_modules/`, `dist/`, `.truenex-memory/`, `src-tauri/target/`.
- [ ] Primo commit con lo stato attuale.
- [ ] (Opzionale) Creare repo remoto su GitHub (privato per ora).

**Criterio di done**: `git log` mostra almeno un commit.

---

### 3.2 Fix AgentSessionList
**Priorità: P1** | `[-]` | Stima: 2-4h

- [ ] Debuggare perché `graphView === "agents"` non attiva il rendering di `AgentSessionList`.
- [ ] Verificare stato `mode` (overview vs detail) quando si clicca su Agents.
- [ ] Assicurarsi che `AgentSessionList` riceva `sources` filtrati correttamente.

**Criterio di done**: cliccando su tab "Agents" si vede la lista gerarchica invece del grafo Cytoscape.

---

### 3.3 Ridurre chunk size build
**Priorità: P2** | `[-]` | Stima: 2-4h

- [ ] Analizzare con `vite-bundle-visualizer` cosa occupa ~887 kB.
- [ ] Code-splitting per `cytoscape` e `cytoscape-*` plugins (lazy load).
- [ ] Verificare se D3 è tree-shaken correttamente.

**Criterio di done**: `vite build` senza warning chunk size, o chunk principale < 500 kB.

---

### 3.4 HMR affidabile
**Priorità: P2** | `[-]` | Stima: da investigare

- [ ] Verificare se il problema è Vite, Tauri, o la configurazione proxy verso backend `:8000`.
- [ ] Testare senza Tauri (`npm run dev` standalone).

**Criterio di done**: modificare un file `.tsx` si riflette nel browser entro 2 secondi senza restart.

---

### 3.5 Build Tauri distribuibile
**Priorità: P3** | `[-]` | Stima: 1 giorno

- [ ] Configurare `tauri.conf.json` per Windows (e eventualmente macOS/Linux).
- [ ] Aggiungere auto-updater Tauri con endpoint manifest proprio (separato dal backend).
- [ ] Build CI/GitHub Actions per `.msi` / `.exe`.

**Criterio di done**: un utente Windows può scaricare `.exe` e usare la GUI senza installare Python.

---

## Backlog — Idee da Valutare (FIFO)

> **Regole del backlog**:
> 1. Nuove idee si aggiungono solo in fondo a questa lista.
> 2. Per portare un'idea in roadmap serve: motivazione chiara, stima, e accettazione che si rimanda una milestone attiva.
> 3. Non si saltano task in corso.

| # | Idea | Contesto | Priorità proposta |
|---|------|----------|-------------------|
| 1 | **Project Graph v2** con AST reale | Il vecchio `get_project_graph()` è stato rimosso per inaccurateria. Rifarlo con `get_file_metadata()` (ast.parse) per costruire grafo import/dependency reale. | P2 |
| 2 | **Client hooks** (Claude Code post-session, Codex hook) | Insieme allo scheduler OS, per refresh più frequente durante sessioni attive. Richiede timeout/fire-and-forget. | P2 |
| 3 | **Team sync / governance** | ROADMAP originale: separate distribution. Non prima di v1.0. | P3 |
| 4 | **sqlite-vec** backend | Alternativa a BLOB Python per embedding storage. Valutare quando scala >100k chunks. | P3 |
| 5 | **WebSocket per backend** | Notifiche push dal backend al frontend (es. "indexing completato"). | P2 |
| 6 | **Search filter per source type** nel desktop | Aggiungere filtri avanzati nella UI (per tipo file, data, provider). | P2 |
| 7 | **Dark mode** desktop | Tailwind supporta `dark:`, ma serve toggle e persistenza preferenza. | P3 |
| 8 | **Onboarding wizard** desktop | Primo avvio: configura path progetti, controlla backend, guida rapida. | P2 |

---

## Criteri di Done Generali

Per ogni task in questa roadmap:

1. **Il codice è scritto** e segue lo stile esistente (PEP8, TypeScript strict).
2. **I test passano**: `pytest -x` o `npm run build` senza errori.
3. **La coverage non scende** sotto la soglia attuale (target: sale, non scende).
4. **La documentazione è aggiornata**: README, CHANGELOG, o AGENTS.md se riguarda agenti.
5. **Il working tree è pulito**: commit atomici, messaggi descrittivi.
6. **È stato verificato manualmente**: almeno un test end-to-end manuale.

---

## Cronologia Aggiornamenti del Piano

| Data | Cambiamento |
|------|-------------|
| 2026-05-13 | v0.1.0-alpha.1 rilasciata |
| 2026-05-19 | v0.2.0a1, aggiunti orchestrator, file metadata, TODO.md |
| 2026-05-27 | Aggiornato TODO.md con RAG ibrido |
| **2026-05-29** | **Riscrittura completa di ROADMAP.md in formato operativo dettagliato** |

---

*Prossimo aggiornamento previsto: dopo chiusura Fase 0.*

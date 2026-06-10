# Ticket: Refactor Agent Discovery — Eliminare Hardcoded AGENT_ROOTS

| Field | Value |
|---|---|
| **Ticket ID** | AGENT-DISC-REFACTOR-2026-06-10 |
| **Data apertura** | 2026-06-10 |
| **Stato** | Analisi completata — pronto per implementazione |
| **Priorità** | Alta |
| **Tipo** | Refactor architetturale |
| **Assegnato** | Marco (truenex-memory) |

---

## 1. Problema

Il modulo `agent_discovery.py` contiene una lista `AGENT_ROOTS` hardcoded nel codice sorgente (riga 120-145). Questa lista definisce quali directory di agenti AI il software deve scansionare per estrarre progetti, documenti e alias server.

### 1.1 Perché è un difetto architetturale

- **Ogni nuovo agente richiede una release del software**: se esce un nuovo client AI (es. Windsurf, PearAI, Zed), l'utente deve aspettare una nuova versione PyPI.
- **I vendor cambiano layout senza preavviso**: es. Aider ha spostato da `input-history` a `caches`; il software continua a cercare il path vecchio.
- **Non c'è modo per l'utente di aggiungere un agente custom**: se un utente usa un tool interno o di nicchia, non può indicizzarlo.
- **Il software "conosce" prodotti terzi**: questo viola il principio di separation of concerns. Truenex Memory non dovrebbe sapere nulla dei path interni di Cursor, Kimi o Aider.

### 1.2 Impatto sul caso d'uso reale (PC Marco)

Scansione manuale eseguita il 2026-06-10 mostra che **molti agenti esistono fisicamente ma sono invisibili alla memoria**:

| Agente | Path hardcoded | Stato reale | File mancanti |
|---|---|---|---|
| codex-sessions | `~/.codex/sessions` | ❌ MISSING | 0 (directory spostata?) |
| claude-projects | `~/.claude/projects` | ✅ EXISTS | 227 |
| kimi-sessions | `~/.kimi/sessions` | ✅ EXISTS | **855** |
| cursor-projects | `~/.cursor/projects` | ✅ EXISTS | **1,894** |
| openclaw-workspace | `~/.kimi_openclaw/workspace` | ✅ EXISTS | 38 |
| aider-inputs | `~/.aider/input-history` | ❌ **MISSING** | 0 (path sbagliato: reale è `caches`) |
| antigravity-extensions | `~/.antigravity/extensions` | ✅ EXISTS | **2,961** |
| gemini-antigravity | `~/.gemini/antigravity` | ✅ EXISTS | 13 |

**Totale contenuto non indicizzato**: ~5,000+ file tra Kimi, Cursor, Antigravity, OpenClaw, Gemini.

Inoltre, il catalogo `sources.json` contiene solo 7 `agent_root` (tutti Codex/Claude), il che dimostra che il discovery è stato eseguito con una versione obsoleta del codice e non è mai stato aggiornato.

---

## 2. Root Cause

```python
# src/truenex_memory/discovery/agent_discovery.py  (riga 120-145)
AGENT_ROOTS = [
    ("codex-sessions", ".codex", "sessions"),
    ("codex-history", ".codex", "history.jsonl"),
    ...
    ("aider-inputs", ".aider", "input-history"),  # ← path sbagliato
    ...
]
```

1. **`AGENT_ROOTS` è una costante Python**: richiede modifiche al codice sorgente e nuova release.
2. **`heuristic_discovery()` dipende da `AGENT_ROOTS`** (riga 230) per filtrare i duplicati, quindi anche l'euristica è "avvelenata" dall'hardcoded.
3. **Nessun meccanismo di override runtime**: l'utente non può correggere un path sbagliato senza forkare il repo.

---

## 3. Soluzione Proposta

### 3.1 Principio guida

> *Il software distribuibile non deve contenere nomi di prodotti terzi hardcoded nel codice sorgente. I path degli agenti devono essere configurabili dall'utente e persistiti su disco.*

### 3.2 Componenti

#### A. Manifest esterno: `~/.truenex-memory/agent_manifest.json`

Nuovo file JSON che sostituisce `AGENT_ROOTS`. Viene creato automaticamente alla prima inizializzazione con un set di default ragionevole, ma è modificabile dall'utente.

```json
{
  "version": 1,
  "agents": [
    {
      "name": "codex",
      "roots": [
        {"label": "sessions", "subdir": "sessions"},
        {"label": "history", "subdir": "history.jsonl"},
        {"label": "memories", "subdir": "memories"}
      ]
    },
    {
      "name": "claude",
      "roots": [
        {"label": "projects", "subdir": "projects"},
        {"label": "skills", "subdir": "skills"}
      ]
    }
  ]
}
```

**Vantaggi**:
- L'utente vede la lista in un file leggibile
- Può aggiungere/rimuovere/modificare senza toccare il codice
- Il vendor cambia layout? L'utente modifica il JSON

#### B. Default embedded (fallback)

Il package include un `DEFAULT_AGENT_MANIFEST` come fallback. Se `agent_manifest.json` non esiste su disco, viene creato copiando il default. Questo garantisce che il software funzioni out-of-the-box, ma il default è **materializzato su disco**, non nascosto nel codice.

#### C. CLI per gestione manifest

```bash
# Lista agenti nel manifest
truenex-mem agent list

# Aggiunge un agente al manifest
truenex-mem agent add windsurf --dir .windsurf --subdir sessions

# Rimuove un agente
truenex-mem agent remove aider

# Riscansiona euristicamente la home per trovare nuovi agenti
truenex-mem agent scan
```

#### D. Refactor di `heuristic_discovery()`

Attualmente:
```python
known = {(rel, sub) for _, rel, sub in AGENT_ROOTS}
```

Deve diventare:
```python
manifest = load_agent_manifest()
known = {(a["dir"], r["subdir"]) for a in manifest["agents"] for r in a["roots"]}
```

L'euristica non deve più dipendere da costanti hardcoded.

#### E. Cosa rimane hardcoded (giustificato)

- **`EXCLUDED_SUBDIRS`**: nomi generici di directory da saltare (`logs`, `telemetry`, `cache`, `node_modules`...). Questi sono pattern universali, non nomi di prodotti.
- **`_AGENT_SUBDIR_SIGNALS`**: nomi generici che indicano un agente (`sessions`, `projects`, `skills`...). Sono segnali euristici, non binding a prodotti specifici.

---

## 4. File Coinvolti

| File | Azione |
|---|---|
| `src/truenex_memory/discovery/agent_discovery.py` | Rimuovere `AGENT_ROOTS`, aggiungere `load_agent_manifest()`, refactor `get_effective_agent_roots()`, refactor `heuristic_discovery()` |
| `src/truenex_memory/discovery/__init__.py` | Esportare nuove funzioni se necessario |
| `src/truenex_memory/cli/main.py` | Aggiungere comandi `agent list/add/remove/scan` |
| `src/truenex_memory/store/repository.py` | Eventualmente gestire migrazione del manifest |
| `docs/ticket-agent-discovery-refactor.md` | Questo documento |
| `tests/` | Aggiungere test per manifest, CLI, euristica |

---

## 5. Acceptance Criteria

- [ ] `AGENT_ROOTS` non esiste più come costante nel codice sorgente
- [ ] `~/.truenex-memory/agent_manifest.json` viene creato automaticamente al primo avvio
- [ ] Il manifest contiene un set di default ragionevole (Codex, Claude, Kimi, Cursor, OpenClaw, Aider, Antigravity, Gemini)
- [ ] L'utente può aggiungere un nuovo agente via CLI senza modificare il codice
- [ ] L'utente può rimuovere un agente dal manifest
- [ ] `heuristic_discovery()` funziona indipendentemente dall'hardcoded
- [ ] I test passano (CI verde)
- [ ] Il refactor non perde dati esistenti nel catalogo

---

## 6. Note

- Il problema è stato scoperto durante la verifica post-sync v0.3.0 (ticket precedente).
- Sul PC Marco, il discovery non ha mai scansionato Kimi, Cursor, OpenClaw, Antigravity, Gemini perché il catalogo è rimasto bloccato su una versione precedente del software.
- Il path Aider (`input-history`) è proprio sbagliato: la directory reale è `caches`.
- Questo ticket è prerequisito per qualsiasi futuro supporto a nuovi agenti AI.

---

## 7. Risultato implementazione

**Data completamento**: 2026-06-10
**Stato**: ✅ Completato

### Modifiche effettuate

| File | Modifica |
|---|---|
| `src/truenex_memory/discovery/agent_discovery.py` | Rimosse 16 tuple `AGENT_ROOTS` hardcoded. Aggiunto `DEFAULT_AGENT_MANIFEST`, `load_agent_manifest()`, `save_agent_manifest()`, `add_agent_to_manifest()`, `remove_agent_from_manifest()`. Refactor `get_effective_agent_roots()` e `heuristic_discovery()` per usare il manifest. |
| `src/truenex_memory/discovery/__init__.py` | Esportate nuove funzioni manifest. |
| `src/truenex_memory/cli/main.py` | Aggiunta `agent_app` con comandi `list`, `add`, `remove`. |
| `tests/unit/test_agent_manifest.py` | 7 nuovi test (TDD — scritti prima del codice). |
| `tests/unit/test_discovery.py` | Aggiornato test per riflettere path Aider corretto (`caches` invece di `input-history`). |

### Verifica sul PC reale

| Agente | Stato prima | Stato dopo | File |
|---|---|---|---|
| codex-sessions | MISSING | MISSING | 0 |
| claude-projects | EXISTS | ✅ EXISTS | 227 |
| **kimi-sessions** | INVISIBILE | ✅ EXISTS | **855** |
| **cursor-projects** | INVISIBILE | ✅ EXISTS | **1,894** |
| openclaw-workspace | INVISIBILE | ✅ EXISTS | 38 |
| **aider-caches** | MISSING (`input-history`) | ✅ EXISTS | — |
| **antigravity-extensions** | INVISIBILE | ✅ EXISTS | **2,961** |
| **gemini-antigravity** | INVISIBILE | ✅ EXISTS | 13 |

**Test suite**: 675 passed, 1 skipped, 0 fallimenti.

### Note post-implementazione

- Il manifest viene creato automaticamente con defaults embedded alla prima chiamata a `load_agent_manifest()`.
- L'utente può ora aggiungere un nuovo agente in 10 secondi con `truenex-mem agent add` senza aspettare una release.
- Il path Aider è stato corretto da `input-history` a `caches`.
- ~5,000 file precedentemente invisibili sono ora scopribili.

---

*Implementazione completata e verificata.*

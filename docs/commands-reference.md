# Truenex Memory — Guida completa ai comandi CLI

> Riferimento esaustivo per ogni comando di `truenex-mem`.  
> Se non sai cosa rispondere quando qualcuno ti chiede "a che serve X?", leggi qui.

---

## Indice rapido

| Categoria | Comandi |
|---|---|
| [Setup progetto](#setup-progetto) | `init`, `doctor`, `migrate` |
| [Memoria manuale](#memoria-manuale) | `add`, `list`, `status` |
| [Indicizzazione e ricerca](#indicizzazione-e-ricerca) | `index`, `search`, `logs`, `trace` |
| [Import/Export](#importexport) | `export`, `import` |
| [Integrazione agenti](#integrazione-agenti) | `adapter`, `mcp`, `serve` |
| [Git Bridge](#git-bridge) | `git` |
| [Global store](#global-store) | `global` |
| [Pipeline task](#pipeline-task) | `task` |
| [Orchestrazione](#orchestrazione) | `orchestrate` |
| [Licenza](#licenza) | `license` |
| [Utility](#utility) | `version`, `version-info`, `update` |

---

## Setup progetto

### `truenex-mem init`
**A cosa serve:** prepara la cartella nascosta `.truenex-memory/` nel progetto dove ti trovi.  
**Quando usarlo:** la prima volta che vuoi usare Truenex Memory in un nuovo progetto.

```bash
cd /percorso/del/tuo/progetto
truenex-mem init
```

**Output atteso:** `Initialized ./.truenex-memory`

---

### `truenex-mem doctor`
**A cosa serve:** diagnostica rapida. Ti dice se il database esiste, quanti documenti e memorie hai, che backend vettoriale stai usando.  
**Quando usarlo:** quando qualcosa non funziona e vuoi capire se il setup è a posto.

```bash
truenex-mem doctor
```

**Parametri:**
- `--privacy` — include anche diagnostica privacy (cosa viene inviato o meno)

---

### `truenex-mem migrate`
**A cosa serve:** gestisce le migrazioni dello schema del database SQLite. Quando aggiorni Truenex Memory a una nuova versione, il formato del DB potrebbe cambiare. Questo comando aggiorna lo schema senza perdere dati.  
**Quando usarlo:** dopo ogni aggiornamento di versione, prima di usare il tool.

**Sotto-comandi:**

| Sotto-comando | Descrizione | Esempio |
|---|---|---|
| `status` | Mostra versione schema attuale e ultima disponibile | `truenex-mem migrate status` |
| `apply` | Applica le migrazioni pendenti (fa backup automatico prima) | `truenex-mem migrate apply` |
| `backup-list` | Lista backup creati prima delle migrazioni | `truenex-mem migrate backup-list` |
| `restore NOME` | Ripristina un backup precedente | `truenex-mem migrate restore backup_20250602.db` |

---

## Memoria manuale

### `truenex-mem add`
**A cosa serve:** scrive una memoria a mano nel database locale del progetto.  
**Quando usarlo:** quando vuoi annotare una decisione, una nota, un problema o un pattern che l'agente AI deve ricordare.

```bash
truenex-mem add "Usiamo FastAPI e JWT per l'autenticazione" --type decision
```

**Parametri:**
- `CONTENT` (obbligatorio) — il testo della memoria
- `--type` — tipo di memoria: `note`, `decision`, `issue`, `pattern` (default: `note`)

**Tipi di memoria:**
- `note` — informazione generica
- `decision` — scelta architetturale o di progetto
- `issue` — problema o bug noto
- `pattern` — pattern ricorrente nel codice

---

### `truenex-mem list`
**A cosa serve:** mostra tutte le memorie manuali aggiunte con `add`.  
**Quando usarlo:** per vedere cosa hai annotato nel progetto.

```bash
truenex-mem list
```

**Parametri:**
- `--status` — filtra per stato: `active`, `obsolete`, `superseded`, `conflicting`, `unverified`
- `--json` — output in formato JSON

---

### `truenex-mem status`
**A cosa serve:** cambia lo stato di una memoria manuale nel suo ciclo di vita.  
**Quando usarlo:** quando una decisione non è più valida, o un problema è stato risolto.

**Sotto-comandi:**

| Sotto-comando | Descrizione | Esempio |
|---|---|---|
| `set ID STATO` | Cambia stato di un nodo | `truenex-mem status set mem_abc123 obsolete` |

**Stati possibili:**
- `active` — valida e attuale (default)
- `obsolete` — superata, non più valida
- `superseded` — sostituita da una memoria più recente
- `conflicting` — in conflitto con un'altra memoria
- `unverified` — in attesa di conferma

---

## Indicizzazione e ricerca

### `truenex-mem index`
**A cosa serve:** legge i file del progetto (codice sorgente, markdown, ecc.), li spezzetta in "chunks" e li memorizza nel database per la ricerca semantica.  
**Quando usarlo:** dopo aver creato/modificato file, o quando vuoi aggiornare la memoria del progetto.

```bash
truenex-mem index                    # indicizza tutto il progetto corrente
truenex-mem index src/main.py        # indicizza solo un file
truenex-mem index ./docs             # indicizza solo una cartella
```

**Parametri:**
- `PATH` (opzionale) — file o cartella da indicizzare (default: `.`)
- `--chunk-size` — caratteri massimi per chunk (default: usa config)
- `--chunk-overlap` — sovrapposizione tra chunk (default: 0)
- `--exclude` — pattern aggiuntivi da escludere

---

### `truenex-mem search`
**A cosa serve:** cerca nei documenti indicizzati con `index`. Usa ricerca semantica (embedding) + testuale.  
**Quando usarlo:** quando vuoi trovare informazioni nei file del progetto.

```bash
truenex-mem search "come funziona l'auth"
truenex-mem search "JWT token" --top-k 10
truenex-mem search "refactor" --include-inactive
```

**Parametri:**
- `QUERY` (obbligatorio) — cosa cerchi
- `--top-k` — numero massimo di risultati (1-50, default: 5)
- `--json` — output completo in JSON
- `--include-inactive` — include anche memorie obsolete/superseded

> ⚠️ **Known issue:** `search` attualmente cerca solo nei documenti indicizzati con `index`, **non** nelle memorie manuali aggiunte con `add`.

---

### `truenex-mem logs`
**A cosa serve:** mostra l'elenco delle ricerche recenti fatte con `search`. Ogni ricerca lascia una "traccia" con ID, query e risultati.  
**Quando usarlo:** per capire cosa hanno cercato gli agenti AI o per debug.

```bash
truenex-mem logs
truenex-mem logs -n 5        # ultime 5 ricerche
truenex-mem logs --json      # formato JSON
```

**Parametri:**
- `-n`, `--limit` — numero di log da mostrare (1-100, default: 20)
- `--json` — output JSON

---

### `truenex-mem trace`
**A cosa serve:** mostra i dettagli completi di una singola ricerca (trace), inclusi tutti i risultati trovati e i metadati.  
**Quando usarlo:** quando vuoi analizzare nel dettaglio cosa ha trovato una specifica ricerca.

**Sotto-comandi:**

| Sotto-comando | Descrizione | Esempio |
|---|---|---|
| `show ID` | Dettaglio completo di una trace | `truenex-mem trace show ret_abc123` |

L'ID della trace lo trovi con `truenex-mem logs`.

---

## Import/Export

### `truenex-mem export`
**A cosa serve:** salva tutto il contenuto del database locale in un file JSON. È il tuo backup completo.  
**Quando usarlo:** prima di una migrazione, per trasferire dati su un'altra macchina, o per archiviare.

```bash
truenex-mem export --output backup.json
```

**Parametri:**
- `-o`, `--output` (obbligatorio) — path del file JSON di output

---

### `truenex-mem import`
**A cosa serve:** ricarica nel database locale i dati da un file JSON precedentemente esportato.  
**Quando usarlo:** quando trasferisci un progetto su un'altra macchina o ripristini un backup.

```bash
truenex-mem import backup.json
```

**Parametri:**
- `INPUT_PATH` (obbligatorio) — path del file JSON da importare

---

## Integrazione agenti

### `truenex-mem adapter`
**A cosa serve:** stampa istruzioni da copiare negli agenti AI (Claude, Codex, ecc.) per farli usare Truenex Memory come tool.  
**Quando usarlo:** quando configuri un nuovo agente AI e vuoi che legga/ scriva nella memoria.

**Sotto-comandi:**

| Sotto-comando | Descrizione | Esempio |
|---|---|---|
| `claude-md` | Istruzioni per Claude (AGENTS.md / CLAUDE.md) | `truenex-mem adapter claude-md` |
| `agents-md` | Istruzioni generiche per agenti | `truenex-mem adapter agents-md` |

---

### `truenex-mem mcp`
**A cosa serve:** avvia un **server stdio** che parla il protocollo MCP (Model Context Protocol). È il modo in cui agenti AI compatibili (Claude Code, Codex, Cursor) possono "chiamare" Truenex Memory come se fosse un tool esterno.  
**Quando usarlo:** quando configuri l'integrazione MCP con un agente AI. Il server resta in ascolto di richieste JSON su stdin e risponde su stdout.

```bash
truenex-mem mcp
```

**Parametri:**
- `--project-root` — root del progetto (default: directory corrente)

> ⚠️ **Bloccante:** il processo resta attivo finché non premi `Ctrl+C`.

---

### `truenex-mem serve` 🔒 Pro
**A cosa serve:** avvia un **server HTTP locale** (API REST) sulla porta di default 8000. Serve per far comunicare la **GUI Desktop** di Truenex Memory con il backend.  
**Quando usarlo:** quando usi l'app desktop di Truenex Memory (richiede licenza Pro).

```bash
truenex-mem serve              # default: 127.0.0.1:8000
truenex-mem serve --port 9000  # porta personalizzata
```

**Parametri:**
- `--host` — indirizzo di binding (default: `127.0.0.1`)
- `-p`, `--port` — porta (default: `8000`)
- `--project-root` — root del progetto

> ⚠️ **Bloccante:** il processo resta attivo finché non premi `Ctrl+C`.

---

## Global store

### `truenex-mem global`
**A cosa serve:** gestisce la memoria **globale** (condivisa tra tutti i progetti), che sta in `~/.truenex-memory/`. Mentre `init/add/index` lavorano sul progetto locale, `global` lavora sul catalogo di tutti i progetti, documenti e server conosciuti.

**Sotto-comandi:**

| Sotto-comando | Descrizione | Esempio |
|---|---|---|
| `status` | Stato del global store (catalogo, ledger, indicizzati, problemi) | `truenex-mem global status` |
| `discover` | Scopre progetti e sessioni dai client agent installati | `truenex-mem global discover` |
| `refresh` | Aggiorna incrementale dal catalogo sorgenti confermato | `truenex-mem global refresh` |
| `context` | Contesto di un progetto confermato | `truenex-mem global context nome-progetto` |
| `search` | Cerca nel global store senza modificare log | `truenex-mem global search "query"` |
| `sources` | Gestisce il catalogo sorgenti (review, conferma) | `truenex-mem global sources` |
| `auto` | Manutenzione automatica memoria (Pro) | `truenex-mem global auto run` |

---

## Pipeline task

### `truenex-mem task`
**A cosa serve:** gestisce i record del task pipeline adattativo. Tiene traccia dei task che gli agenti AI eseguono, con step, giudizi umani e calibrazione.  
**Quando usarlo:** quando vuoi tracciare il lavoro degli agenti e misurarne la qualità nel tempo.

**Sotto-comandi:**

| Sotto-comando | Descrizione | Esempio |
|---|---|---|
| `open "Titolo"` | Apre un nuovo task | `truenex-mem task open "Refactor auth module"` |
| `close ID` | Chiude un task con giudizio umano | `truenex-mem task close task_abc123` |
| `list` | Lista task recenti | `truenex-mem task list` |
| `show ID` | Dettaglio task con step | `truenex-mem task show task_abc123` |
| `calibration` | Statistiche di calibrazione | `truenex-mem task calibration` |

---

## Orchestrazione

### `truenex-mem orchestrate`
**A cosa serve:** esegue un **loop ricorsivo multi-agente**. Più agenti AI lavorano in sequenza, convergendo verso una soluzione. Si ferma quando due round consecutivi producono lo stesso output (convergenza).  
**Quando usarlo:** per task complessi che richiedono più iterazioni e revisioni automatiche.

**Sotto-comandi:**

| Sotto-comando | Descrizione | Esempio |
|---|---|---|
| `run FILE.json` | Esegue il loop da un file di config JSON | `truenex-mem orchestrate run config.json` |
| `converge-check A B` | Verifica se due round sono identici byte-per-byte | `truenex-mem orchestrate converge-check round1.json round2.json` |

---

### `truenex-mem global auto` 🔒 Pro
**A cosa serve:** pipeline di manutenzione automatica della memoria globale. Genera, recensisce, approva e pota memorie derivate dai sorgenti indicizzati.  
**Quando usarlo:** in cron giornaliero o CI per tenere la memoria aggiornata senza intervento manuale.

**Sotto-comandi:**

| Sotto-comando | Descrizione | Esempio |
|---|---|---|
| `run` | Refresh + generazione memorie automatiche | `truenex-mem global auto run --auto-memory` |
| `status` | Stato della pipeline automatica | `truenex-mem global auto status` |
| `review` | Recensisci memorie generate | `truenex-mem global auto review` |
| `approve ID` | Approva una memoria generata | `truenex-mem global auto approve mem_xxx` |
| `reject ID` | Rifiuta una memoria generata | `truenex-mem global auto reject mem_xxx` |
| `promote ID` | Promuovi a memoria curata attiva | `truenex-mem global auto promote mem_xxx --title "..." --content "..."` |
| `prune` | Compatta memorie rifiutate | `truenex-mem global auto prune --yes` |

---

## Git Bridge

### `truenex-mem git` 🔒 Pro
**A cosa serve:** sincronizza la memoria di progetto su più PC usando Git. Esporta il database SQLite in JSON, committa in un repo git separato (`.truenex-memory/sync/`) e pusha su un remote condiviso (es. repo privata su GitHub).  
**Quando usarlo:** quando lavori su più macchine e vuoi che la memoria del progetto viaggi con te.

**Sotto-comandi:**

| Sotto-comando | Descrizione | Esempio |
|---|---|---|
| `init` | Inizializza il repo sync | `truenex-mem git init --url git@github.com:user/project-memory.git` |
| `push` | Esporta e pusha sul remote | `truenex-mem git push` |
| `pull` | Pulla e importa nel DB locale | `truenex-mem git pull` |
| `status` | Stato del sync | `truenex-mem git status --short` |
| `remote add NAME URL` | Aggiungi un remote | `truenex-mem git remote add origin <url>` |
| `remote remove NAME` | Rimuovi un remote | `truenex-mem git remote remove origin` |
| `remote list` | Elenca i remote | `truenex-mem git remote list` |
| `remote show NAME` | Dettagli di un remote | `truenex-mem git remote show origin` |

Tutti i comandi `git` supportano `--json` per output machine-readable e `--dry-run` (ove applicabile).

---

## Licenza

### `truenex-mem license`
**A cosa serve:** gestisce la licenza Truenex Memory Pro.

**Sotto-comandi:**

| Sotto-comando | Descrizione | Esempio |
|---|---|---|
| `status` | Mostra licenza attiva (tier, scadenza, feature) | `truenex-mem license status` |
| `activate CHIAVE` | Attiva una licenza Pro (online di default) | `truenex-mem license activate trxn-pro-XXXX` |
| `deactivate` | Rimuove la licenza locale | `truenex-mem license deactivate --yes` |
| `require TIER` | Verifica se il tier corrente soddisfa il minimo | `truenex-mem license require pro` |

**Parametri di `activate`:**
- `KEY` (obbligatorio) — la chiave di licenza
- `--offline` — attiva localmente senza contattare il server

---

## Utility

### `truenex-mem version`
**A cosa serve:** stampa la versione di Truenex Memory.

```bash
truenex-mem version
```

---

### `truenex-mem version-info`
**A cosa serve:** stampa tutte le versioni dei componenti in formato JSON.

```bash
truenex-mem version-info
```

---

### `truenex-mem update`
**A cosa serve:** controlla manualmente se esistono aggiornamenti del software.

**Sotto-comandi:**

| Sotto-comando | Descrizione | Esempio |
|---|---|---|
| `check` | Controlla aggiornamenti senza inviare dati del progetto | `truenex-mem update check` |

---

## Flusso di lavoro tipico

```bash
# 1. Setup progetto
cd mio-progetto
truenex-mem init

# 2. Annota decisioni importanti
truenex-mem add "Usiamo PostgreSQL, non MySQL" --type decision

# 3. Indicizza il codice
truenex-mem index

# 4. Cerca nella memoria
truenex-mem search "database choice"

# 5. Verifica tutto sia a posto
truenex-mem doctor

# 6. Attiva licenza Pro (se acquistata)
truenex-mem license activate trxn-pro-XXXXXXXXXXXXXXXX
```

---

## Note e known issues

- `search` non include le memorie manuali aggiunte con `add` (solo documenti indicizzati)
- `serve` richiede `httpx` che non è incluso nelle dipendenze PyPI (bug da fixare)
- `mcp` e `serve` sono comandi bloccanti — restano in esecuzione fino a `Ctrl+C`

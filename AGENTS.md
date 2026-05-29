# 🚨 IDENTITÀ E WORKFLOW OBBLIGATORIO

**Tu sei KIMI. Non sei Codex. Non sei DeepSeek. Non sei Claude.**

Ogni task di codice su questo progetto DEVE seguire il workflow a 5 fasi:
1. **Kimi** — Architettura e piano tecnico (non scrivere codice)
2. **DeepSeek (claude-ds)** — Sviluppo patch bounded
3. **Kimi** — Verifica, integrazione, test
4. **Codex (OpenAI o4-mini)** — Review incrociata (task delicati)
5. **Kimi** — Commit e salvataggio memoria

**Regola d'oro:** NON scrivere codice da solo al posto di DeepSeek. NON commitare senza test verdi.

Se il contesto viene compattato e dimentichi chi sei, rileggi questo file.

---

# Truenex Memory — Agent Developer Guide

> Questo file contiene le informazioni essenziali per sviluppare, avviare e testare il backend Truenex Memory.

---

## 🚀 Avvio del backend (FastAPI)

Il backend espone un server HTTP FastAPI sulla porta **8000**.

### Prerequisiti

```bash
# Installa le dipendenze (incluso FastAPI, Uvicorn, ecc.)
pip install -e ".[dev]"
```

### Avvio

```bash
# Modo 1: via CLI ufficiale (consigliato)
truenex-mem serve

# Modo 2: diretto via Python module
python -m truenex_memory.serve

# Modo 3: con host/porta custom
truenex-mem serve --host 0.0.0.0 --port 8000
```

Il server risponde su `http://127.0.0.1:8000`.

### Health check

```bash
curl http://localhost:8000/api/health
```

Risposta attesa: `{"status":"ok"}`

---

## 🧪 Test rapido degli endpoint

### Stats globali

```bash
curl http://localhost:8000/api/stats
```

### Lista fonti

```bash
curl http://localhost:8000/api/sources
```

### File metadata (analisi strutturale di un file)

```bash
# 1. Trova un document_id dal DB
curl http://localhost:8000/api/sources | jq '.[0].documents[0].id'

# 2. Chiedi i metadati
curl "http://localhost:8000/api/file-metadata?document_id=DOC_ID_QUI"
```

### Project graph (se attivo)

```bash
curl "http://localhost:8000/api/project-graph?project_name=NOME_PROGETTO"
```

---

## 🗄️ Database

- **Path globale default:** `C:\Users\marco\.truenex-memory\truenex_memory.db` (Windows)
- **SQLite** — nessun servizio esterno richiesto
- **Qdrant** (opzionale) — vector store per semantic search, default disabilitato

---

## 🔧 Variabili d'ambiente

| Variabile | Default | Descrizione |
|-----------|---------|-------------|
| `TRUENEX_PROJECT_ROOT` | `.` | Root del progetto corrente per CLI |

---

## 🏗️ Architettura moduli chiave

```
src/truenex_memory/
├── serve.py              # FastAPI app + endpoint HTTP
├── cli/main.py           # CLI commands (truenex-mem ...)
├── core/
│   ├── chat_engine.py    # Retrieval ibrido (BM25 + semantic)
│   └── embedder.py       # HashingEmbedder + SentenceTransformerEmbedder
├── store/
│   ├── repository.py     # MemoryRepository — queries SQL, metadati file
│   └── sqlite.py         # Schema e low-level SQLite
└── retrieval/
    └── semantic.py       # Vector store abstractions
```

---

## 📝 Note per agenti

- **Non committare codice non testato.** Testare sempre l'endpoint prima del commit.
- **Pulire `__pycache__`** se i cambiamenti non vengono riflessi: `find . -type d -name __pycache__ -exec rm -rf {} +`
- **Il package deve essere installato in editable mode** (`pip install -e .`) altrimenti Python carica la versione installata in `site-packages` invece del sorgente locale.
- **Porte:** backend 8000, Qdrant 6333, Vite (frontend) 1420.

---

## 🆘 Troubleshooting

### Le modifiche al codice non vengono riflesse dopo il riavvio

**Sintomo:** Hai modificato un file `.py`, riavviato il backend, ma il comportamento è identico a prima. Gli endpoint restituiscono lo stesso risultato o `AttributeError` su metodi che hai appena aggiunto.

**Cause comuni:**

1. **Cache `__pycache__` stale** — Python ha compilato un `.pyc` che è più recente del `.py` per qualche motivo (timestamp corrotto, copia del file, ecc.)
2. **Package non in editable mode** — Se `truenex-memory` è installato in `site-packages` (non `-e`), Python carica quello invece del sorgente locale
3. **Processo zombie** — Un vecchio processo Uvicorn è ancora in ascolto sulla porta 8000
4. **Metodo fuori dalla classe** — Se aggiungi un metodo con indentazione sbagliata (es. dopo la fine della classe), Python lo vede come funzione a livello di modulo, non come metodo di istanza

**Diagnosi passo-passo:**

```bash
# 1. Verifica che non ci siano processi zombie sulla porta 8000
netstat -ano | grep ':8000'

# 2. Verifica che il package sia in editable mode
pip show truenex-memory
# Deve mostrare: Editable project location: D:\Project_sw\ProjectPy\truenex-memory

# 3. Verifica che Python carichi il file giusto
python -c "import truenex_memory.store.repository as r; print(r.__file__)"

# 4. Verifica che il metodo esista nella classe
python -c "import truenex_memory.store.repository as r; print(hasattr(r.MemoryRepository, 'NOME_METODO'))"

# 5. Se il metodo non esiste, controlla l'indentazione:
python -c "
import ast
with open(r'D:\Project_sw\ProjectPy\truenex-memory\src\truenex_memory\store\repository.py') as f:
    tree = ast.parse(f.read())
for node in ast.walk(tree):
    if isinstance(node, ast.ClassDef) and node.name == 'MemoryRepository':
        methods = [n.name for n in node.body if isinstance(n, ast.FunctionDef)]
        print('Metodi nella classe:', methods)
"
```

**Soluzione definitiva:**

```bash
# 1. Uccidi tutti i processi Python/Uvicorn sulla porta 8000
taskkill //F //PID <PID>

# 2. Cancella TUTTE le cache Python nel progetto
find src -type d -name __pycache__ -exec rm -rf {} +

# 3. Avvia ignorando la scrittura di nuovi .pyc
set PYTHONDONTWRITEBYTECODE=1
python -m truenex_memory.serve

# 4. Se ancora non funziona, reinstalla in editable mode
pip install -e .
```

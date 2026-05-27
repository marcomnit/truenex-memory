# Truenex Memory — TODO / Roadmap

> Questo file traccia i task aperti e le prossime milestone. Aggiornarlo dopo ogni sessione di lavoro.

---

## 🖥️ Frontend (Desktop App)

### In corso / Bloccato
- [ ] **Fix lista Sessioni Agent** — `AgentSessionList.tsx` è creato ma il grafo Cytoscape continua ad apparire invece della lista. Verificare condizione `graphView === "agents"` in `MemoryGraph.tsx`.
- [ ] **Path cliccabile + "Apri cartella"** — aggiunti nel sidebar della source, verificare che funzionino correttamente.

### Fatto
- [x] P1–P7: token metrics, index validation, tabella file, tab switch robusto, label raggruppamento, nomi sessioni
- [x] Bug fix: infinite fetch loop, macro click vuoto, highlight persistence
- [x] P4 sidebar: file analysis nel sidebar `project-file`
- [x] P8 sidebar: day cluster gerarchico (`DayClusterSidebar`)

---

## 🔧 Backend (Motore)

### Commit in sospeso (da fare prima del RAG)
- [ ] **Project Graph API** — `get_project_graph()` in `repository.py` + `GET /api/project-graph` in `serve.py`
- [ ] **File Metadata API** — `get_file_metadata()` in `repository.py` + `GET /api/file-metadata` in `serve.py`
- [ ] **File Analysis API** — `analyze_file_content()` esiste in `repository.py` ma **manca l'endpoint** `/api/file-analysis` in `serve.py` (il frontend lo chiama ma il backend non lo serve ancora)

### Prossima milestone: RAG Ibrido
- [ ] Implementare **3-phase retrieval** in `MemoryRepository.search()`:
  1. BM25 lexical recall (top 50 candidati)
  2. Semantic cosine re-ranking sui 50 candidati
  3. Deduplication by source_path + adaptive threshold
- [ ] Aggiungere colonna `embedding BLOB` alla tabella `chunks`
- [ ] Implementare **lazy migration** — calcola embedding mancanti on-demand durante la query
- [ ] Aggiungere CLI command `truenex-mem migrate embeddings` per batch migration
- [ ] Store embedding in SQLite BLOB invece di dipendere solo da Qdrant
- [ ] Test: verificare che query su termini tecnici specifici restituiscano risultati migliori

---

## 📅 Decisioni pending

| Decisione | Contesto |
|-----------|----------|
| **RAG ibrido: sì/no?** | Migliora chat/search ma non la navigazione grafica. Da fare dopo aver stabilizzato la UI. |
| **sqlite-vec vs BLOB Python?** | Piano originale suggeriva BLOB Python per semplicità, sqlite-vec per scalabilità futura. |

---

*Ultimo aggiornamento: 2026-05-24*

# Prompt per Generare Immagini — Truenex Memory

Prompt pronti per ChatGPT (o qualsiasi generatore di immagini) organizzati per le 4 tipologie di descrizione.

---

# TIPOLOGIA 1: ONE-LINER

**Obiettivo:** 1-2 immagini iconiche, immediate, che comunicano il concetto in un colpo d'occhio. Ideali per hero image di README, thumbnail social, copertina.

## Immagine 1A — Il Secondo Cervello

Genera un'illustrazione minimalista e moderna: un cervello umano stilizzato (fatto di circuiti o nodi luminosi) affiancato dal simbolo di un database locale (un cilindro con l'etichetta "SQLite"). Tra i due, una freccia bidirezionale. In basso, il testo "Truenex Memory — Il secondo cervello per i tuoi agenti AI". Sfondo scuro (blu notte o nero), palette di colori: ciano, viola, bianco. Stile tech pulito, senza ingombri. Il messaggio deve essere: memoria + AI + locale.

## Immagine 1B — Query, Don't Re-read

Genera un'immagine concettuale divisa in due metà. A sinistra: una pila disordinata di documenti (50+ fogli) con un agente AI (rappresentato come un robot o un chip) che li legge tutti, uno per uno, con l'espressione "sopraffatta". Sopra la pila, l'etichetta "50.000 token sprecati". A destra: lo stesso agente che lancia una query verso un database luminoso e ordinato, ricevendo indietro un singolo foglio con informazioni precise. Sopra, l'etichetta "500 token". Lo stile è moderno, tech, con palette ciano e viola su sfondo scuro. In basso il claim: "Query, don't re-read."

---

# TIPOLOGIA 2: TECNICA

**Obiettivo:** 5-7 diagrammi architetturali precisi per documentazione tecnica, README, paper. Devono essere chiari, ben strutturati, adatti a un pubblico di sviluppatori.

## Immagine 2A — Architettura a 9 Strati

Genera un diagramma architetturale a stack verticale con 9 livelli, dal basso verso l'alto. Ogni livello è un rettangolo con etichetta e breve descrizione. Stile da documentazione tecnica, colori distinti per ogni layer, frecce di dipendenza verso l'alto. I livelli sono:

**L1 — SQLite Database** (base, colore grigio scuro): "Storage persistente locale. 9 tabelle: documents, chunks, memory_nodes, edges, retrieval_logs, source_ledger, tasks, task_steps, verifier_rounds."

**L2 — Ingestion Pipeline** (sopra, colore blu): "Parser per documenti testuali (.md, .py, .yaml) e sessioni JSONL agenti. Estrae solo contenuto rilevante, esclude tool call e messaggi di sistema."

**L3 — Chunking & Embedding** (sopra, colore indaco): "Chunking deterministo Markdown-aware (1200 char). HashingEmbedder 384 dimensioni, zero model download. Target: intfloat/multilingual-e5-base. Qdrant opzionale."

**L4 — Retrieval Engine** (sopra, colore viola): "Ricerca ibrida a cascata: semantica (cosine similarity) → BM25 → keyword matching. Filtro ledger-aware. Trace ID su ogni query."

**L5 — Agent Discovery** (sopra, colore magenta): "Scoperta progetti da .codex/ e .claude/. Regex-based, confidence-scored. Zero blind disk scan."

**L6 — Global Refresh** (sopra, colore rosa): "Indicizzazione incrementale. State machine a 5 stati. Preserva versione attiva su errore. Stabilità JSONL: 120s."

**L7 — Auto Memory** (sopra, colore arancione): "Generazione automatica memorie. Deduplicazione per content hash. Soglia confidenza 0.50. Solo l'utente promuove a active."

**L8 — MCP Server** (sopra, colore verde): "JSON-RPC 2.0 via stdio. Tool: memory_search, memory_add, global_project_context, global_status."

**L9 — CLI** (cima, colore ciano): "Interfaccia completa: init, add, search, index, export, import, migrate, global discover/refresh/auto."

Sul lato sinistro dello stack, un'etichetta verticale: "LOCAL-FIRST — PRIVACY-FIRST — AGENT-FIRST". Sul lato destro: "100% Locale · Zero Cloud · Zero Telemetria · Export JSON". Sfondo bianco o grigio chiaro, adatto a documentazione. Titolo in alto: "Truenex Memory — Architettura a 9 Strati".

## Immagine 2B — Flusso di Discovery e Refresh

Genera un diagramma di flusso orizzontale che mostra il processo di discovery e indicizzazione di Truenex Memory. Da sinistra a destra:

1. **Agent Roots** (box iniziale): icone di cartelle `.codex/` e `.claude/`, con file JSONL e MD che escono.

2. **Regex Extraction** (box successivo): lente di ingrandimento su testo, con etichette "path assoluti", "alias SSH", "documenti".

3. **DiscoveryReport** (box): lista di candidati con confidence score (es. "truenex-engine: 4.0", "AI_Agent: 3.0").

4. **Human Confirmation** (box con icona utente e checkmark): "Revisione e conferma".

5. **sources.json** (box): icona file JSON, "Source Catalog — 95 fonti confermate".

6. **Parser** (box): due rami — "text_docs" (MD, PY, YAML) e "jsonl_sessions" (agente).

7. **Chunking** (box): testo diviso in blocchi con heading_path visibile.

8. **Embedding** (box): vettori 384-dim, icona rete neurale stilizzata.

9. **Storage** (box finale): cilindro SQLite + icona Qdrant (opzionale).

Frecce che collegano tutti i box. Palette di colori: blu, viola, ciano. Sfondo bianco. Titolo: "Truenex Memory — Discovery & Refresh Flow".

## Immagine 2C — State Machine del Source Ledger

Genera un diagramma a stati UML per il Source Ledger di Truenex Memory. Cinque stati rappresentati come rettangoli arrotondati:

- **pending** (grigio, in alto a sinistra): "In coda / migrazione"
- **active** (verde, al centro): "Indicizzato e disponibile per retrieval"
- **missing** (rosso, a destra): "File non più esistente"
- **skipped** (giallo, in basso a sinistra): "Non indicizzabile / JSONL instabile"
- **error** (arancione, in basso a destra): "Errore parsing o indexing"

Frecce di transizione etichettate:
- nuovo → active: "Indicizzato con successo"
- nuovo → skipped: "Non indicizzabile"
- nuovo → missing: "Path inesistente"
- nuovo → error: "Parse fallito"
- active → active: "File modificato, re-indicizzato"
- active → missing: "File scomparso"
- active → error: "Parse fallito (versione precedente preservata)"
- active → skipped: "JSONL instabile"
- skipped → active: "Diventato stabile"
- error → active: "Riprovato con successo"
- missing → active: "File ricomparso"

In un riquadro in evidenza (con bordo spesso o sfondo diverso), la regola critica: "Un errore di re-indicizzazione NON distrugge mai l'ultima versione attiva valida." Stile tecnico pulito, sfondo bianco, adatto a documentazione. Titolo: "Source Ledger — State Machine".

## Immagine 2D — Strategia di Retrieval a Cascata

Genera un diagramma di flusso verticale che illustra la strategia di ricerca a tre livelli di Truenex Memory. Dall'alto verso il basso:

**INPUT:** Query dell'agente (box in cima): `memory_search("bug router dual-model")`

**LIVELLO 1 — Ricerca Semantica** (box largo, colore viola):
- "Embedding query → cosine similarity su vettori"
- Due rami: "Qdrant (se disponibile)" e "SQLite fallback"
- "JOIN con source_ledger: esclude missing e skipped"
- Freccia verso il basso con etichetta: "Se risultati trovati → RETURN"
- Freccia laterale con etichetta: "Se 0 risultati →"

**LIVELLO 2 — BM25 Fallback** (box largo, colore blu):
- "Tokenizzazione query e documenti"
- "BM25 scoring con source_boost (project_docs > agent_session)"
- "JOIN con source_ledger"
- Freccia verso il basso: "Se risultati → RETURN"
- Freccia laterale: "Se 0 risultati →"

**LIVELLO 3 — Keyword Matching** (box largo, colore ciano):
- "Ricerca su memory_nodes (active + unverified)"
- "Jaccard-like token overlap scoring"
- Freccia verso il basso: "RETURN"

**OUTPUT:** (box in fondo, colore verde):
- "SearchHit[] con: title, content, source_path, heading_path, memory_type, status, score"
- "RetrievalLog creato con trace_id per audit"

Stile tecnico pulito, sfondo bianco, icone minimali. Titolo: "Truenex Memory — Retrieval Cascade".

## Immagine 2E — Flusso Auto Memory

Genera un diagramma di flusso che illustra il processo di Auto Memory di Truenex Memory. Flusso dall'alto verso il basso:

1. **Indexed Chunks** (box iniziale): "Contenuto candidato da chunk indicizzati"

2. **Content Hash** (box): "SHA-256 del contenuto candidato"

3. **Deduplication Check** (rombo di decisione): "Esiste nodo active con stesso hash?" → SÌ: "Skip (duplicate reported)" / NO: prosegue

4. **Tombstone Check** (rombo): "Esiste nodo obsolete con stesso hash?" → SÌ: "Skip (tombstone)" / NO: prosegue

5. **Confidence Threshold** (rombo): "Confidence ≥ 0.50?" → NO: "Skip (low confidence)" / SÌ: prosegue

6. **Classification** (box): "Classificazione deterministica: decision (wording chiaro) / note (default) / pattern (approccio ricorrente)"

7. **Create Unverified** (box giallo): "Nuovo MemoryNode: status='unverified', source_kind='auto', created_by='auto'"

8. **Human Review** (tre rami finali):
   - **approve** → (box verde) "status='active'"
   - **reject** → (box rosso) "status='obsolete' + tombstone hash"
   - **promote** → (box blu) "nuovo nodo curated_auto + originale → obsolete"

Stile tecnico pulito, sfondo bianco. Titolo: "Truenex Memory — Auto Memory Flow".

## Immagine 2F — MCP Server e Toolkit

Genera un diagramma che mostra l'MCP Server di Truenex Memory e i suoi tool. Layout:

Al centro, un box grande: **"MCP Server — JSON-RPC 2.0 via stdio"**

A sinistra, tre client che si connettono al server tramite frecce:
- Claude Code (icona quadrato arancione)
- Codex (icona rombo blu)
- Cursor (icona rettangolo verde)

A destra, i tool esposti dal server, ciascuno in un box con descrizione:
- **memory_search** — "Ricerca semantica locale con score e provenance"
- **memory_add** — "Aggiunge memoria/decisione nel progetto corrente"
- **global_project_context** — "Contesto completo di un progetto dal global store"
- **global_status** — "Report stato: catalog, ledger, chunks, warnings"
- **task_open / task_step_add / task_close** — "Pipeline adattiva multi-agente"

In basso, un box che rappresenta lo storage: **"SQLite + Qdrant (opzionale)"**, connesso al MCP Server.

In alto, un'etichetta: "100% Locale — Zero Cloud — Privacy-First". Stile tecnico, sfondo bianco.

## Immagine 2G — Strutture Dati Chiave

Genera un diagramma che mostra le tre strutture dati principali di Truenex Memory, affiancate orizzontalmente. Ogni struttura è un box con campi elencati:

**SourceLedgerRecord** (box sinistro, colore blu):
- source_id: "hash(tipo + path)"
- source_path_or_alias
- project_name
- source_type: "agent_session | project_docs | server_alias"
- content_hash: "SHA-256"
- last_modified_at
- last_indexed_at
- status: "active | missing | skipped | error | pending"
- error_message
- chunk_count

**MemoryNode** (box centrale, colore viola):
- id: "mem_<uuid>"
- type: "decision | note | issue | pattern"
- title: "prima linea, max 80 char"
- content
- status: "active | obsolete | superseded | conflicting | unverified"
- source_kind: "manual | auto | curated_auto"
- content_hash: "SHA-256 per dedup"
- confidence: "0.0 – 1.0"
- source_path, source_document_id, source_chunk_id

**TextChunk** (box destro, colore ciano):
- index: "posizione nel documento"
- content: "testo del chunk"
- heading_path: "es. Architettura > Database"
- content_hash: "SHA-256"
- token_count: "stima deterministica"

Frecce che mostrano le relazioni: SourceLedgerRecord.source_path → MemoryNode.source_path. MemoryNode.source_chunk_id → TextChunk. TextChunk → MemoryNode (per auto-memory generation).

Stile tecnico pulito, sfondo bianco. Titolo: "Truenex Memory — Core Data Structures".

---

# TIPOLOGIA 3: NARRATIVA

**Obiettivo:** 3-4 immagini che raccontano una storia: il problema, la soluzione, il risultato. Per blog post, landing page, presentazioni.

## Immagine 3A — Il Problema: Amnesia

Genera un'illustrazione narrativa che mostra il problema che Truenex Memory risolve. Una scrivania di sviluppatore con un monitor. Sul monitor, una chat con un agente AI. La scena è calda e frustrante — lo sviluppatore ha le mani tra i capelli. Sulla scrivania, una pila enorme di fogli (documentazione, log, note) che l'agente AI (rappresentato come un'entità luminosa confusa) sta cercando di leggere tutti in una volta. Un orologio sul muro mostra le lancette che girano veloci. In sovrimpressione, numeri che salgono: "Token usati: 12.547... 28.903... 51.442...". In basso, il testo: "Ogni nuova sessione, il tuo agente riparte da zero. E tu paghi per la sua amnesia." Stile illustrazione moderna, palette calda (arancioni, rossi, grigi). Non troppo cartoon, professionale ma emotivo.

## Immagine 3B — La Soluzione: Il Secondo Cervello

Genera un'illustrazione narrativa che mostra la soluzione di Truenex Memory. stessa scrivania, stesso sviluppatore, ma l'atmosfera è completamente diversa: luminosa, colori freddi e puliti (ciano, viola, blu). Sotto la scrivania o accanto al monitor, un "secondo cervello" — un database luminoso a forma di cervello stilizzato, da cui partono connessioni ordinate verso il monitor. Sul monitor, l'agente AI riceve esattamente 5 "carte" informative pulite, ognuna con un'etichetta (es. "Architettura DB", "Bug noto #3", "Server SSH"). L'orologio ora è fermo o mostra un tempo brevissimo. In sovrimpressione: "Token usati: 487". Lo sviluppatore è rilassato, sorride. In basso: "Truenex Memory — Una query. Il contesto giusto. Fine." Stile coerente con l'immagine precedente ma palette opposta.

## Immagine 3C — Before/After Split

Genera un'immagine divisa a metà, stile "prima e dopo". La metà sinistra (PRIMA, senza Truenex Memory) ha toni caldi/rossi, mostra un agente AI sommerso da una valanga di documenti, con etichette: "60+ file letti", "50.000 token sprecati", "2 minuti per iniziare", "Context window satura". La metà destra (DOPO, con Truenex Memory) ha toni freddi/blu/ciano, mostra lo stesso agente che lancia una query verso un database ordinato, con etichette: "5 chunk rilevanti", "500 token", "0.3 secondi", "Context window libera". Al centro, la linea di divisione con il logo Truenex Memory (un cervello stilizzato + cilindro database). In alto, il titolo: "Non stai pagando per la memoria del tuo agente. Stai pagando per la sua amnesia."

## Immagine 3D — Il Viaggio dell'Agente

Genera un'illustrazione orizzontale in stile "hero journey" che racconta il flusso completo in 4 vignette collegate da una strada curva:

**Vignetta 1 — Discovery:** Un'esploratrice (l'agente) con una lente di ingrandimento che esamina le cartelle `.codex/` e `.claude/`. Trova icone di progetti, documenti, server. Testo: "1. Scopre i tuoi progetti."

**Vignetta 2 — Index:** L'agente organizza tutto in un database cristallino e luminoso. I documenti diventano piccoli cristalli (chunk) ordinati. Testo: "2. Indicizza tutto, una volta sola."

**Vignetta 3 — Query:** Una nuova sessione. L'agente fa una domanda e dal database esce un raggio di luce preciso che porta esattamente 5 cristalli. Testo: "3. L'agente chiede. Riceve solo ciò che serve."

**Vignetta 4 — Learn:** I cristalli crescono e si organizzano da soli nel tempo. L'agente sorride. Testo: "4. Il sistema impara. Tu approvi."

Stile illustrazione moderna, palette ciano-viola-bianco su sfondo scuro. Titolo in alto: "Truenex Memory — Il Viaggio dell'Agente". In basso: "Query, don't re-read."

---

# TIPOLOGIA 4: MARKETING

**Obiettivo:** 3-4 immagini ad alto impatto visivo per Product Hunt, social media, pitch deck. Devono fermare lo scroll e comunicare il valore in 2 secondi.

## Immagine 4A — Hero Image per Product Hunt

Genera un'hero image per il lancio su Product Hunt di "Truenex Memory". Dimensioni orizzontali (1200x630 o simili). Stile moderno, tech, pulito, con forte impatto visivo.

Sfondo: gradiente scuro dal blu notte al viola profondo, con sottili griglie geometriche che suggeriscono circuiti o connessioni neurali.

Al centro, un'illustrazione potente: un cervello stilizzato metà organico (a sinistra, toni caldi/rosa) e metà digitale (a destra, toni ciano/blu, composto da nodi e connessioni luminose). Dal lato digitale, un fascio di luce si trasforma in una query che colpisce un database (cilindro luminoso), e dal database parte una risposta precisa (5 raggi laser) che tornano al cervello.

Testo in alto, grande e bold: "TRUENEX MEMORY". Sotto, più piccolo ma leggibile: "Your AI agents finally remember. Local. Private. Yours."

In basso, tre badge orizzontali: "100% LOCAL" · "ZERO CLOUD" · "OPEN SOURCE".

In un angolo, un piccolo box con numeri: "42K+ chunks indexed · 7.6K+ documents · 1 SQLite file".

Nessun testo lungo. Impatto visivo prima di tutto.

## Immagine 4B — Il Conto dei Token (Social Media)

Genera un'immagine quadrata (1080x1080) per social media (LinkedIn, Twitter/X, Instagram). Design audace, simile a un poster tech.

Sfondo nero. Al centro, in caratteri enormi e bold, un numero che si trasforma:
"50.000" scritto in rosso, che viene barrato con una X gigante.
Sotto, "500" scritto in ciano brillante, circondato da un glow.

Tra i due numeri, un'etichetta: "Token per sessione".

In basso, il logo Truenex Memory e il claim: "Query, don't re-read."

In alto, una domanda in caratteri più piccoli ma leggibili: "Quanto ti costa l'amnesia del tuo agente AI?"

Stile: bold, minimal, tipografico. Colori: nero, rosso, ciano, bianco. Altamente condivisibile.

## Immagine 4C — Confronto Visivo per Pitch Deck

Genera una slide di confronto per un pitch deck. Layout orizzontale, stile professionale da presentazione startup.

Due colonne affiancate:

**Colonna SINISTRA — "Senza Truenex Memory"** (sfondo rosso tenue):
- Icona di un robot che legge una pila di documenti
- "50.000+ token sprecati/sessione"
- "60+ file letti a ogni chat"
- "Context window satura di rumore"
- "Tempo perso: 2+ minuti"
- "Nessuna persistenza tra sessioni"

**Colonna DESTRA — "Con Truenex Memory"** (sfondo verde tenue):
- Icona di un robot che interroga un database ordinato
- "~500 token per contesto"
- "5 chunk rilevanti, non 60 file"
- "Context window libera per il ragionamento"
- "Tempo: 0.3 secondi"
- "Memoria persistente che impara nel tempo"

In basso, centrato: "Truenex Memory — Query, don't re-read." con sotto "Local-first · Privacy-first · Agent-first · Open Source (Apache 2.0)"

Stile pulito, professionale, adatto a una slide da VC o stakeholder tecnico.

## Immagine 4D — Infografica "Il Risparmio"

Genera un'infografica verticale per social media o post blog. Stile moderno, data-driven, visivamente accattivante.

Sfondo: gradiente blu scuro → viola.

Sezioni verticali:

**SEZIONE 1 (in alto):** "Costo Annuale dell'Amnesia"
Due barre orizzontali:
- Barra rossa lunga: "Senza TM: ~1.2 MILIONI di token sprecati/anno"
- Barra ciano corta: "Con TM: ~12.000 token/anno per query"

**SEZIONE 2 (centro):** "Tempo Risparmiato"
- Orologio stilizzato: "120+ ore/anno risparmiate in letture"

**SEZIONE 3 (in basso):** "Il Sistema"
Tre icone in fila con frecce:
- 🧠 "Indicizza" → 🔍 "Query" → ✅ "Ricorda"

In fondo: logo Truenex Memory. "Local. Private. Yours. — truenex.ai"

Tipografia moderna, numeri grandi, palette ciano-viola-bianco.

---

# RIEPILOGO

| Tipologia | Immagini | Uso |
|---|---|---|
| **1. One-Liner** | 1A, 1B | Hero README, thumbnail, copertina |
| **2. Tecnica** | 2A–2G (7 immagini) | Documentazione, architettura, paper tecnico |
| **3. Narrativa** | 3A–3D (4 immagini) | Blog post, landing page, presentazioni |
| **4. Marketing** | 4A–4D (4 immagini) | Product Hunt, social, pitch deck |

**Totale: 17 prompt**, ciascuno autonomo e pronto da copiare in ChatGPT.

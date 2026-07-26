# Le memorie scritte con `memory_add` non sono mai recuperabili da `memory_search`

Data: 2026-07-26
Segnalato da: sessione Claude Code sul progetto MedDesk (`D:\Project_sw\ProjectPy\truenex-local-QVAC_MedPsy`)
Versione osservata: store locale `C:\Users\marco\.truenex-memory\truenex_memory.db`, 42.916 documenti / 478.423 chunk indicizzati

## Riepilogo

`memory_add` scrive correttamente. `memory_search` interroga correttamente la tabella giusta. Ciononostante **una memoria appena scritta non compare mai tra i risultati**, nemmeno cercando una frase presa alla lettera dal suo contenuto.

La causa non è né la scrittura né una tabella sbagliata: è che i punteggi delle memorie e quelli dei chunk documentali vivono su **due scale numeriche incomparabili** e vengono ordinati insieme in un'unica classifica. Le memorie perdono sempre.

Impatto: un agente che salva conoscenza con `memory_add` riceve `{"status":"active"}` e non ha modo di accorgersi che quella conoscenza è irrecuperabile. È un guasto silenzioso, e rende inaffidabile l'uso di Truenex Memory come memoria condivisa fra agenti — che è il suo scopo.

## Riproduzione

1. Scrivere una memoria con una frase distintiva:

```
memory_add(content="... un backup che non hai mai provato a ripristinare non è un backup, è un auspicio ...", memory_type="decision")
→ {"id":"mem_7dd1233ce38945dd85f49ff3f4401728","memory_type":"decision","status":"active"}
```

2. Cercare quella frase quasi verbatim:

```
memory_search(query="un backup che non hai mai provato a ripristinare non è un backup è un auspicio", top_k=5)
```

3. Risultato osservato: 5 risultati, **tutti `memory_type: document_chunk`**, nessuno pertinente. Il primo è un promemoria di un altro progetto (score 374), il secondo è il file di localizzazione italiana di Steam `steamui_italian.txt` (score 335). La memoria non compare.

Prove aggiuntive raccolte nella stessa sessione: 4 query distinte, costruite su stringhe letterali presenti nelle memorie appena scritte (fra cui l'identificatore raro `patient_external_ids` e il nome di funzione `stripParentSessionEnv`). **20 risultati su 20 erano `document_chunk`. Zero memorie, in nessuna query.**

## Causa esatta

In `src/truenex_memory/store/repository.py`, `MemoryRepository.search()` (riga 226):

```python
hits = _search_memories(conn, tokens, include_inactive, self.project_id)   # riga 235
hits.extend(_search_chunks(conn, tokens, self.project_id, limit=max(top_k * 20, 100)))  # riga 237
hits = [hit for hit in hits if _is_searchable_source_path(hit.source_path)]  # riga 244
...
hits.sort(key=lambda item: item.score, reverse=True)   # riga 248  ← il difetto
results = _deduplicate_search_hits(hits)[:top_k]       # riga 249
```

Le due sorgenti producono punteggi su scale diverse:

| Sorgente | Funzione | Formula | Intervallo |
|---|---|---|---|
| Memorie | `_search_memories`, riga 892 | `len(overlap) / len(tokens)` | **0.0 – 1.0** |
| Chunk documentali | `_search_chunks_fts`, riga 955 | `bm25(chunks_fts, 1.0, 2.0)` riscalato | **~160 – 400** osservati |

La riga 248 ordina l'unione delle due liste per `score` grezzo. Una memoria con corrispondenza **perfetta** vale al massimo `1.0`; un chunk con corrispondenza marginale vale centinaia. Di conseguenza ogni memoria si colloca sotto ogni singolo chunk trovato, e il taglio `[:top_k]` alla riga 249 la elimina.

Con `limit=max(top_k * 20, 100)` sui chunk, servirebbero **meno di `top_k` chunk con almeno un token in comune** perché una memoria diventi visibile. Su un indice da 478.423 chunk questo non accade praticamente mai.

### Ipotesi scartate durante l'analisi

Elencate perché non vengano riesplorate:

- **«`search()` non interroga `memory_nodes`»** — falso. `_search_memories` (riga 874) fa `SELECT * FROM memory_nodes WHERE status IN (?, ?)` alla riga 886.
- **«le memorie vengono filtrate perché prive di `source_path`»** — falso. `_is_searchable_source_path` (riga 1076) ritorna `True` quando `source_path` è vuoto o `None`: filtra solo cestino di sistema e `System Volume Information`.
- **«è colpa dell'indice non aggiornato»** — no. `memory_add` non passa dall'indicizzatore; le memorie sono in tabella e leggibili con `list_memory_nodes()`. (L'indice documentale *è* comunque fermo al 2026-07-16, ma è un problema separato — vedi in fondo.)
- **«è il `project_id` che non combacia»** — no. `_search_memories` non filtra per progetto: lo usa solo come valore di ripiego per l'etichetta `project` del risultato (riga 894).

## Correzione proposta

Il problema è la fusione di ranker eterogenei, quindi va risolto nella fusione, non nelle singole funzioni di ricerca. Tre opzioni, in ordine di robustezza:

1. **Reciprocal Rank Fusion (raccomandata).** Ordinare separatamente memorie e chunk, poi fondere per *posizione* invece che per punteggio: `score_finale = Σ 1/(k + rank_i)` con `k ≈ 60`. È la soluzione standard per combinare ranker con scale non confrontabili (lessicale, denso, strutturato) e non richiede nessuna taratura delle formule esistenti. Rende inoltre innocuo l'inserimento futuro di un terzo ranker.

2. **Normalizzazione dei punteggi.** Riportare entrambe le sorgenti su `0.0–1.0` (min-max sul batch corrente, o normalizzazione BM25) prima della riga 248. Più semplice, ma la normalizzazione min-max su un batch è instabile: cambia i risultati al variare del numero di chunk trovati.

3. **Quote riservate.** Garantire un numero minimo di posizioni alle memorie nel `top_k`. Funziona, ma è arbitrario e nasconde il vero problema di ranking.

In tutti i casi, le memorie vanno considerate **evidenza di prima classe**: sono conoscenza curata e scritta esplicitamente da un agente o da una persona, non testo estratto automaticamente da un file. A parità di pertinenza dovrebbero stare *sopra* un chunk documentale, non sotto.

### Criteri di accettazione

- Scrivere una memoria con una frase distintiva e cercare quella frase la restituisce in prima posizione.
- Una query che corrisponde sia a una memoria sia a chunk documentali restituisce entrambi, con la memoria non penalizzata dalla scala di punteggio.
- I punteggi esposti nella risposta MCP sono su una scala unica e documentata, così che un consumatore possa applicare una soglia di confidenza. Oggi convivono valori `0.6` e `374` nel medesimo campo `score`, e sono incommensurabili.
- Test di regressione che scriva una memoria e ne verifichi il recupero: oggi manca, ed è la ragione per cui il difetto è passato inosservato.

## Secondo problema, indipendente: il retrieval non è semantico

Non è un difetto, è una migrazione non completata — ma spiega la qualità dei risultati e conviene decidere quando chiuderla.

`MemoryService.__init__` (`core/memory_service.py`, riga 21) usa `HashingEmbedder()`. Il suo stesso docstring (`core/embedder.py`, riga 42) lo dichiara:

> *Deterministic local embedder that never downloads model weights. The metadata names `intfloat/multilingual-e5-base` as the target model so persisted vectors can declare their intended production replacement, while tests keep a small dependency-free backend.*

Quindi i vettori persistiti dichiarano già il modello che dovrebbe sostituirlo. Finché resta l'embedder per hashing, non esiste alcuna comprensione semantica: la ricerca si regge su sovrapposizione di token via FTS5/BM25.

Effetti osservati sul campo:

- la query *«un backup che non hai mai provato a ripristinare»* ha restituito le stringhe di localizzazione italiana di Steam, per sovrapposizione di parole funzionali italiane;
- la query *«...sovrascrive correzioni manuali»* ha restituito un documento ECM contenente la parola «manuali»;
- cercando *«vault backup import GPU offload»*, il chunk concettualmente più pertinente (la sezione «Utility di manutenzione del database — Backup, Repair, VACUUM e controllo integrità» del ROADMAP di MedDesk) è arrivato **settimo**, sotto panoramiche architetturali generiche. Con `top_k=5` sarebbe stato invisibile.

Da notare anche che il ramo denso è quasi irraggiungibile: `_search_semantic_chunks` viene invocato solo se le ricerche lessicali non hanno prodotto **nessun** risultato (righe 245-247). Con un indice grande, qualche corrispondenza lessicale esiste quasi sempre, quindi il percorso semantico in pratica non si esercita. Sostituire l'embedder senza rivedere quella condizione non cambierebbe i risultati.

L'aggancio per farlo bene esiste già: il backend Qdrant è supportato (`vector_backend == "qdrant"`), ma di default resta `sqlite` con `available: false`.

Raccomandazione: misurare prima e dopo su un insieme fisso di query, altrimenti non è verificabile se il cambio migliori davvero.

## Terzo punto, di manutenzione

Da `global_status`:

- `last_indexed_at`: **2026-07-16**, dieci giorni prima della segnalazione. Tutto il lavoro recente non è indicizzato.
- **620 sorgenti in stato `missing`**, quasi tutte dentro `.claude\worktrees\agent-*\` — copie in worktree cancellati. Sono un rischio concreto: una ricerca può restituire la versione *vecchia* di un file che nel repository è cambiato (osservati `soap.md`, `manifest.toml`, `Cargo.toml` in questo stato). Andrebbero rimosse dal ledger, e i percorsi di worktree degli agenti probabilmente esclusi dall'indicizzazione.
- **10.586 righe `agent_session`** nel ledger. Le trascrizioni di sessioni passate competono in classifica con i documenti autorevoli: fra i risultati sono comparsi `.kimi\sessions\...jsonl::exchange_33` e `.codex\sessions\...jsonl::exchange_27`. C'è un rischio di autoconferma — un agente recupera cose dette da un agente, non fatti verificati. Vale la pena decidere se le trascrizioni debbano avere un peso inferiore rispetto ai documenti, o una sorgente separata.

## Priorità suggerita

1. **Fusione del ranking** (primo problema). Piccolo, delimitato, e sblocca l'uso di `memory_add` come memoria condivisa. Finché non è chiuso, ogni scrittura è persa in lettura pur rispondendo `active`.
2. **Pulizia del ledger e reindicizzazione** (terzo punto). Manutenzione, effetto immediato sulla qualità.
3. **Embedder semantico** (secondo problema), con misurazione prima/dopo e revisione della condizione che rende irraggiungibile il ramo denso.

Nota: il primo punto è quello che rende il sistema *scorretto*. Gli altri due lo rendono *migliore*. Non vanno confusi in un unico intervento.

---

## Appendice — Risoluzione (sera del 2026-07-26, sessione Kimi)

### Conferma sperimentale aggiuntiva (riportata dal campo)

Dopo un refresh completo dell'indice, il retrieval documentale funzionava bene ma quello sulle memorie restava rotto: **14 risultati su 14 senza nemmeno una memoria, punteggi invariati al decimale**. Questo dato, raccolto *dopo* che il fix di ranking era già committato, ha portato alla scoperta della vera causa residua (vedi sotto, «gap di deployment»).

### Fix 1 — Fusione del ranking (commit `adfda27`)

Implementata l'opzione 1 (Reciprocal Rank Fusion) in `MemoryRepository.search()`:

- nuova `_fuse_ranked_hits()` in `store/repository.py`: memorie e chunk ordinate separatamente per rank, fuse con `Σ weight/(RRF_K + rank)`, `RRF_K = 60`, `MEMORY_SOURCE_WEIGHT = 1.5` (le memorie, conoscenza curata, a parità di rank precedono i chunk);
- chiave di identità `(memory_type, source_path o document_id, title, contenuto normalizzato)`: chunk distinti dello stesso file restano separati, i duplicati veri si fondono;
- score esposti su scala unica documentata `(0, ~0.040984]`; il ramo fallback semantico resta su scala coseno (limitazione documentata);
- 5 test di regressione in `tests/unit/test_memory_ranking_fusion.py`, verificati **rossi sul codice pre-patch e verdi post-patch** (incluso un corpus 200 filler + 12 matcher che riproduce il taglio `top_k` della produzione). Suite completa: 716 test verdi.

**Residuo noto:** il percorso CLI `build_global_search` (`ingestion/global_search.py`) non passa dalla fusione RRF e mantiene il merge di score grezzi — il bug di scala è ancora vivo lì. Follow-up da pianificare.

### Fix 2 — Pulizia ledger + manutenzione (commit `cf4d0fc`)

- Nuovo comando `truenex-mem global sources purge-missing` (dry-run di default): cancella le righe ledger `missing` con documenti/chunk associati (FTS via trigger), con guard anti-twin su path normalizzati, transazione atomica, report delle `memory_nodes` con riferimenti penzolanti (conservate: sono conoscenza curata). 10 test in `tests/unit/test_ledger_purge.py`.
- Escluse le directory `worktrees` dall'indicizzazione (`core/exclusions.py`) — copie effimere `.claude/worktrees/agent-*`.
- Boost `agent_session` 0.75 → 0.5: le trascrizioni non sono fatti verificati (rischio autoconferma); restano recuperabili ma sotto i documenti autorevoli.

**Eseguito sul DB globale** (backup preventivo `truenex_memory.db.bak-2026-07-26`): 620 righe ledger missing, 616 documenti e 3.515 chunk cancellati; zero documenti worktree residui; FTS riallineato. Le 316 memorie che referenziavano contenuto purgato sono state conservate.

### La causa residua trovata dopo il fix: gap di deployment

I punteggi «invariati al decimale» erano la prova che il server MCP in produzione **non eseguiva il codice patchato**. Causa:

- esistevano **due installazioni**: il venv del progetto (editable, patchato) e una copia **non editable della 0.4.0** in `C:\Users\marco\AppData\Roaming\Python\Python313\site-packages`, antecedente ai fix;
- la configurazione MCP **radice** di Claude Code (`C:\Users\marco\.claude.json`) puntava all'eseguibile globale (`...\Python313\Scripts\truenex-mem.exe`), mentre le configurazioni project-scoped e quella di Codex puntavano al venv. Le sessioni su altri progetti (es. MedDesk) ereditavano la config radice → codice vecchio;
- i `retrieval_logs` mostravano le due scale mescolate nelle stesse ore, confermando due versioni del codice attive in parallelo.

**Risoluzione:** terminati i processi `truenex-mem.exe` del Python globale, disinstallata la copia stale e reinstallato il pacchetto in **editable** anche nel Python globale (`py -m pip install -e .`). Ora tutti i consumer (Claude root config, project config, Codex) eseguono lo stesso sorgente.

**Verifica end-to-end sul DB globale** con la query esatta del presente report:

```
1 decision | score 0.0246 | MedDesk — Archivio clinico (vault): stato verificato...
2 decision | score 0.0242 | MedDesk — Backup verificato dell'archivio clinico...
...
```

La memoria `mem_7dd1233ce38945dd85f49ff3f4401728` è in **prima posizione** (score `1.5/61 ≈ 0.0246`, scala RRF). Criterio di accettazione principale: **soddisfatto in produzione**.

### Nota operativa per il team

- Dopo ogni modifica al codice, verificare **quale installazione** eseguono i server MCP (`pip show truenex-memory` in ogni interprete referenziato dalle config MCP) e riavviare le sessioni agente: i server MCP stdio vengono spawnati all'avvio della sessione e non ricaricano il codice a caldo.
- Istanze MCP già avviate prima di questo intervento devono essere riavviate per eseguire il codice corretto.

### Stato delle priorità

1. ~~Fusione del ranking~~ — **chiusa** (fix + deployment + verifica in produzione).
2. ~~Pulizia ledger e reindicizzazione~~ — purge **eseguito**; reindicizzazione (`global refresh`) avviata dall'utente il 2026-07-26.
3. **Embedder semantico** — aperta, con misurazione prima/dopo e revisione della condizione del ramo denso; si aggiunge il follow-up RRF per `build_global_search`.

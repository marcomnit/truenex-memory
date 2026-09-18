# Changelog

## [Unreleased]

## [0.7.1] — 2026-09-18

Fast-follow su 0.7.0, trovato lo stesso giorno da una domanda vera contro
l'archivio reale ("dove siamo arrivati con l'ultima sessione"): la ricerca
restituiva dump grezzi di chiamate a tool invece del documento che
rispondeva davvero.

### Fixed

- **Indicizzazione**: `~/.claude/projects/<sessione>/tool-results/*.txt` —
  output grezzo di chiamate a tool passate (dump SQL, risultati di grep) —
  non era mai escluso. Trovati 17-23 file cosi' su un archivio reale,
  capaci di vincere il ranking su query generiche perche' un dump condivide
  piu' parole con una domanda vaga di quante ne condivida il documento
  specifico che risponde davvero. `tool-results` e' ora in
  `DEFAULT_EXCLUDED_DIRS`.
  Nota di processo: una prima versione di questo fix escludeva l'intera
  cartella `.claude`. Un dry-run del comando di pulizia sotto contro una
  COPIA dell'archivio reale ha mostrato che avrebbe cancellato 4690
  documenti — quasi tutti trascrizioni di sessione (`.claude/projects/*.jsonl`,
  13.299 righe nel ledger), che questo progetto indicizza di proposito. La
  prova sul campo ha bloccato la regressione prima che venisse pubblicata.

### Added

- **`truenex-mem sources purge-path --path-filter <substring>`**: rimuove
  documenti/chunk gia' indicizzati il cui percorso combacia un filtro,
  indipendentemente dallo stato nel ledger (a differenza di
  `sources purge-missing`, che tocca solo sorgenti gia' segnate `missing`).
  Serve perche' una nuova esclusione impedisce solo le indicizzazioni
  FUTURE: non rimuove da sola cio' che era gia' stato indicizzato prima
  del fix. Dry-run per default; con `--yes` esegue prima un backup
  timestampato dell'intero database, poi cancella. Non tocca mai
  `memory_nodes` (le note curate restano valide anche se il documento
  sorgente sparisce). Verificato su una copia del database reale di
  produzione: conteggi prima/dopo esatti, backup integro, idempotente,
  `global_status`/ricerca funzionanti dopo la pulizia.

## [0.7.0] — 2026-09-18

Quattro difetti diagnosticati usando lo strumento su un progetto reale, non
scoperti leggendo il codice: un indice globale che invecchiava in silenzio,
una nota impossibile da scrivere nel progetto giusto quando non era quello
della sessione corrente, un `memory_graph` che poteva agganciarsi al
progetto sbagliato quando lo scope era una sottostringa condivisa da piu'
radici (es. "truenex" contenuto sia in "truenex-memory" che in
"truenex-engine" — confermato su 11 progetti reali durante il collaudo), e
l'assenza di un modo affidabile per rispondere a "dove eravamo rimasti".
Analisi indipendente di tre modelli diversi (Codex, Kimi, DeepSeek), DUE
volte — la seconda dopo che il collaudo sul campo ha smentito la prima
ipotesi sul quarto punto — per lo stesso motivo per cui il progetto gia'
separa `memory_graph` da `memory_search`: un secondo parere costa poco e un
difetto silenzioso su un layer di memoria costa la fiducia in tutto quello
che ha registrato.

**Nota sul quarto punto, per chi si aspettava un fix sull'embedder.** La
prima ipotesi — che la ricerca "sembra lessicale" perche' l'embedder di
default e' `hashing` — era incompleta. Verificato sul campo: il ranking
denso/e5 e' gia' spento da un flag SEPARATO e gia' esistente,
`TRUENEX_DENSE` (default `off`, indipendente da `TRUENEX_EMBEDDER`), spento
il 21/08/2026 dopo una misurazione reale (201.282 chunk, 53 casi) che ha
mostrato che il ranking per cosine-similarity NON migliora i risultati su
questo corpus — non e' un difetto, e' una decisione gia' presa e gia'
corretta. Il problema vero, isolato con l'aiuto dei tre modelli consultati
una seconda volta: "dove eravamo rimasti" e' una domanda sulla RECENZA
dello stato di un progetto, non sulla RILEVANZA di un testo rispetto a una
query — nessun ranking, lessicale o semantico, per quanto lo si ottimizzi,
risponde a "cosa e' successo per ultimo". Il fix e' quindi un tool nuovo,
non un tuning del ranking esistente: vedi `memory_resume` sotto.

### Added

- **`memory_resume`**: risposta deterministica, non di ranking, a "dove
  eravamo rimasti / qual e' lo stato attuale / prossimi passi". Aggrega tre
  fonti ordinate per tempo: la cronologia automatica delle chiamate a questo
  progetto (vedi punto successivo), i task recenti del pipeline adattivo
  (`task_open`/`task_step_add`/`task_close`, associati per nome — limite
  dichiarato in `coverage`, non nascosto), e le ultime note/decisioni
  (`memory_add`) di questo progetto. Nessun punteggio, nessuna soglia:
  ordinamento cronologico puro. Ogni risposta porta un campo `coverage` che
  dichiara cosa ciascuna fonte puo' e non puo' promettere, cosi' un recap
  vuoto si legge come "poco e' stato registrato", mai come "non e'
  successo niente" quando invece qualcosa e' sfuggito alle tre fonti.
- **Cronologia automatica delle chiamate** (`store/activity_log.py`): il
  server stdio registra da solo, per ogni `tools/call` che gli arriva —
  qualunque tool, qualunque client (Claude, Codex, Cursor, Kimi) — una riga
  con progetto, strumento, breve riassunto ed esito. E' il punto che chiude
  davvero il problema: prima, "sapere cosa e' successo" dipendeva
  dall'agente che si ricordava di chiamare `task_open`/`task_step_add`; ora
  non dipende da nessuna cooperazione del client, perche' il dispatcher e'
  l'unico punto per cui passano tutte le chiamate. Una riga di cronologia
  persa non fa mai fallire la risposta del tool che l'ha generata.
- **Reindex reattivo del corpus globale** (`ingestion/reactive_refresh.py`):
  `global_status` e `global_project_context` ora avviano da soli un refresh
  in background quando l'indice e' piu' vecchio di
  `TRUENEX_GLOBAL_REFRESH_MAX_AGE_SECONDS` (default 6h), con lo stesso
  meccanismo gia' validato in `graph.refresh` — lock esclusivo, processo
  senza console su Windows, risposta mai bloccata. Si spegne con
  `TRUENEX_GLOBAL_AUTO_REFRESH=0`. Prima esisteva solo il refresh manuale
  (`truenex-mem global refresh`), e un indice che nessuno ricordava di
  aggiornare restava fermo a settimane fa — verificato sul campo: passato da
  20 giorni di eta' a "aggiornato ora" alla prima chiamata di `global_status`.
- **`memory_add(project=...)`**: scrive in un progetto diverso da quello
  della cwd della sessione corrente, risolvendolo per nome/basename/percorso
  contro il catalogo sorgenti confermato (la stessa risoluzione gia' usata
  da `global_project_context`). Un nome ambiguo torna un errore con la lista
  dei percorsi candidati invece di scegliere in silenzio. Esposto anche sul
  server stdio (schema e dispatcher) — la sola funzione Python non bastava,
  perche' nessun client MCP reale poteva raggiungerla.

### Fixed

- **`memory_graph`**: uno scope che e' sottostringa di piu' radici cache
  (es. `truenex` per `truenex-memory` e `truenex-engine`) restituiva in
  silenzio il primo grafo letto dal disco. Ora un nome di cartella esatto ha
  la precedenza sulla sottostringa, e una sottostringa che corrisponde a piu'
  radici distinte torna un errore con l'elenco dei candidati invece di
  indovinare.

### Migration

Nessuna azione richiesta per chi aggiorna: tutti i fix sono additivi (nuovo
parametro opzionale, nuovo tool, nuova tabella creata al bisogno, nuovo
comportamento di background che non cambia la forma delle risposte
esistenti se non aggiungendo campi) e nessuno schema di embedding e'
cambiato. Ne' `TRUENEX_EMBEDDER` ne' `TRUENEX_DENSE` cambiano default in
questa release: la decisione di tenere il ranking denso spento resta quella
misurata il 21/08/2026, e non e' in discussione qui.

## [0.6.2] — 2026-08-23

Quattro difetti mostrati dai 24 grafi di una macchina vera, tutti della
stessa famiglia di 0.6.1: righe identiche per cause opposte.

Il punto di partenza e' stato un `upgrade` reale su 24 progetti .NET, dove
otto righe dicevano «ricostruito» con la colonna dei file vuota. Dietro
quelle otto righe c'erano quattro situazioni diverse: due cartelle di
configurazione nostre, due cartelle di soli binari, tre progetti in VB.NET
che non sappiamo leggere, e una cartella vuota. La tabella le mostrava
tutte allo stesso modo.

### Fixed

- **Le nostre cartelle non sono piu' progetti.** `.claude` e
  `.truenex-memory` erano finite nella cache dei grafi, e da quel momento
  `known_project_roots` le considerava progetti per sempre: ogni `upgrade`
  le ripercorreva. La seconda contiene l'archivio e la cache dei grafi
  stessi. Il filtro (`is_project_root`) prende i nomi dai `marker_dir` dei
  client che conosciamo — la stessa fonte che dice se un client e'
  installato — piu' la costante della cartella dati, quindi un client
  aggiunto domani e' coperto senza che nessuno se ne ricordi.
- **Un grafo vuoto non sembra piu' un successo.** La tabella di `upgrade`
  scriveva il conteggio solo se diverso da zero, quindi «0 file» compariva
  come colonna vuota — che si legge «non lo so», non «niente dentro».
- **Il linguaggio viene nominato.** `unsupported_languages_seen` esisteva
  gia' e distingue «non so leggere questo linguaggio» da «non ho trovato
  relazioni», ma la usava solo `graph build`, dove scorre via fra decine di
  progetti. Ora `upgrade` scrive `0 file — 79 .vb (VB.NET): grammatica
  assente`.
- **Un test che misurava l'ambiente.** `test_upgrade_says_the_graph_does_
  not_exist_yet` verificava quale ramo capitasse a seconda di come era
  installata la macchina: verde in sviluppo, rosso in CI, che e' il modo
  peggiore di sbagliare un test.

### Added

- **`graph forget`.** Un grafo si poteva costruire e non rimuovere: una
  cartella graficata per sbaglio restava nell'elenco per sempre, e l'unica
  via era cancellare a mano dei file JSON in un percorso che l'utente non
  deve conoscere. Accetta nomi o percorsi, ha `--not-projects` per togliere
  in un colpo le voci che non sono progetti, e `--dry-run` per guardare
  prima.
- **`graph status` segnala le voci che non sono progetti** e dice quale
  comando le toglie.

### Removed

- `releases/version.json`, copia locale di un manifest che vive in un altro
  repository e che nessuna riga di codice leggeva. Era ferma a 0.5.0 mentre
  il manifest servito annunciava 0.6.0: due copie della stessa verita' senza
  niente che le tenga insieme divergono, e questa aveva gia' divergiato di
  due versioni senza che nulla lo segnalasse.

### Known limits

- **VB.NET resta fuori dal grafo.** L'estrattore (`graphifyy`, di terzi,
  pre-1.0) non elenca `.vb` fra le estensioni di codice e nessuna delle 25
  grammatiche installate e' VB. Su PyPI esiste `tree-sitter-vbnet`, ma a
  v0.1.0. La misura: 93 file su tre progetti su ventiquattro. La ricerca
  testuale su quei file continua a funzionare — cio' che manca e' solo il
  «chi chiama chi». Ora il grafo lo dichiara invece di tacerlo.

## [0.6.1] — 2026-08-23

Tre difetti trovati aggiornando una macchina vera, nessuno dei quali era
visibile in sviluppo. Tutti della stessa famiglia: un limite che non si
dichiarava.

### Fixed

- **Le esclusioni erano tarate su un ecosistema.** `bin` e `obj` non erano
  escluse, quindi su codice .NET il grafo analizzava i `.cs` generati dalla
  compilazione — AssemblyInfo, file di designer, output di Razor. Misurato su
  quattordici progetti reali: **844 file C# su 1.851 stavano dentro `bin` o
  `obj`, il 46%**; su un progetto Blazor erano 81 su 95. Il sintomo che ha fatto
  guardare: un progetto con 1.132 file sorgente e 82 soli archi, un rapporto che
  non appartiene a codice scritto da qualcuno. Dopo la correzione lo stesso
  Blazor analizza 14 file e produce 184 archi fra entita', e un'applicazione web
  di 168 file ne produce 4.035.

  `packages` NON e' stata aggiunta benche' sia la cartella di NuGet: nei
  monorepo JavaScript e' esattamente dove vive il codice, ed escluderla per
  aiutare .NET cancellerebbe il progetto di qualcun altro.

- **Il pacchetto del grafo non era dichiarato da nessuna parte.** Il grafo e' la
  capacita' principale della 0.6.0 e dipende da un extra opzionale, ma niente lo
  diceva prima di provare a costruirlo — un requisito che si scopre da un errore
  e' un requisito nascosto. Ora `upgrade` lo verifica e lo nomina, e il messaggio
  porta le **virgolette** attorno al nome: senza, PowerShell interpreta le
  parentesi quadre e il rimedio suggerito fallisce senza nominare la causa.

- **Uno zero muto sui linguaggi che non sappiamo leggere.** Un progetto di otto
  file VB.NET produceva «0 file sorgente», che chi legge intende come «nessuna
  relazione» mentre significa «non ho guardato niente»: due risposte opposte
  scritte allo stesso modo. Ora il comando nomina il linguaggio e dice che la
  grammatica non esiste — per VB.NET, ASP.NET Web Forms, Razor e SQL non e'
  una questione di configurazione.

### Added

- Estensioni analizzate: `.ps1`, `.psm1`, `.groovy`, `.ex`, `.exs`, `.jl`.
  Scelte guardando le grammatiche **davvero installate**, non a memoria. Restano
  fuori `.vb`, `.aspx`, `.cshtml` e `.sql`, che su codice .NET servirebbero: il
  parser non esiste, e dichiarare un'estensione senza parser darebbe un elenco
  piu' lungo e nessun arco in piu'. Fuori anche `.m`, che e' Objective-C e anche
  MATLAB: analizzare MATLAB come Objective-C produrrebbe entita' plausibili e
  sbagliate.
- Venticinque test sui comandi `profile`, `upgrade` e `graph`, che erano provati
  soltanto a mano. Il rilascio 0.6.0 era fallito in CI per la copertura, e la
  soglia aveva ragione: un comando che nessun test esegue e' un comando che si
  scopre rotto dall'utente.

### Note

- Chi ha gia' costruito i grafi con la 0.6.0 li rifaccia: la cache precedente
  contiene i file generati. `truenex-mem graph build <cartella>` per ciascun
  progetto.

## [0.6.0] — 2026-08-22

Due giorni su una domanda sola: perche' un agente che ha la memoria a
disposizione continua a leggere trenta file. La risposta e' stata tre difetti
distinti — la ricerca guardava nel posto sbagliato, il grafo del codice taceva
meta' delle relazioni, e nessuno aveva mai detto agli agenti che quegli
strumenti esistono.

### Added

- **`scope` nella ricerca.** `memory_search`, `MemoryService.search()` e la CLI
  accettano una cartella su cui restringere. La condizione vive dentro l'SQL di
  FTS5, non come filtro successivo: filtrare dopo spenderebbe il bacino dei
  candidati sul resto del corpus. Lo scope corrisponde a **segmenti interi** del
  percorso, quindi `truenex-memory` non prende `truenex-memory-dev` — un progetto
  vicino e sbagliato e' il caso peggiore, perche' la risposta e' plausibile.
  Misurato sull'insieme cieco: **da 2/32 a 8/32** (MRR 0,042 -> 0,128), zero
  regressioni sui 53 casi committati.
- **Grafo del codice** (`memory_graph`, `truenex-mem graph`). Risponde a «chi
  chiama questa funzione», «cosa chiama», «quali test la coprono» leggendo le
  relazioni estratte dal sorgente con tree-sitter, aggregate su due livelli.
  Costo misurato di una risposta: 1.604 caratteri contro 108.707 leggendo i
  quattro file coinvolti.
- **Deduzione del tipo del ricevitore** per le chiamate che tree-sitter non
  risolve: parametri, `let x: T`, costruttori, campi di struct, contenitori
  (`Mutex<T>`, `State<'_, T>`) e tipi di ritorno. Chiamanti cross-file trovati:
  **da 4/23 a 18/22**, con 323 archi dedotti su 323 che superano il controllo di
  precisione. Gli archi dedotti portano `confidence: "inferred"` e restano
  distinguibili da quelli letti dal parser.
- **Riconoscimento dei test in Rust** dall'attributo `#[test]` e dai moduli
  `#[cfg(test)]`. Prima il campo «quali test coprono X» era vuoto **sempre** su
  Rust, perche' l'euristica guardava il percorso del file e li' i test stanno
  accanto al codice. 583 funzioni di test riconosciute su un progetto reale, 365
  funzioni con almeno un test attribuito.
- **Profilo di comportamento per gli agenti**, da una sorgente sola
  (`adapters/profile.md`) verso i file utente degli otto client riconosciuti, gli
  `AGENTS.md` di progetto e il campo `instructions` dell'handshake MCP. Comandi
  `truenex-mem profile show | status | apply | clients | check`.
- **`truenex-mem upgrade`**: migrazione con backup, ricostruzione dei grafi,
  scrittura del profilo, in un comando. Dopo un aggiornamento restavano quattro
  passi da fare a mano, e quattro passi scritti in un manuale sono un compito
  affidato alla memoria di una persona.
- **`truenex-mem profile check`**: misura se i client seguono il profilo
  guardando come usano gli strumenti, invece di fidarsi del fatto che il testo
  sia arrivato.

### Changed

- **Nessun aggiornamento silenzioso dello schema.** `initialize_schema` gira a
  ogni apertura — una ricerca, un handshake MCP — e applicava le migrazioni
  senza backup: chi installava una versione nuova si trovava lo schema cambiato
  e nessun punto di ripristino. Ora un archivio **con contenuto** e schema piu'
  vecchio viene rifiutato con il comando da eseguire; `truenex-mem migrate` e
  `upgrade` restano le sole porte autorizzate, e prendono il backup prima. Gli
  archivi nuovi o vuoti non sono toccati, e un archivio piu' NUOVO del codice non
  viene bloccato — le migrazioni sono additive, quindi si prosegue registrando
  un avviso.
- **Il grafo si accorge da solo di essere invecchiato**, confrontando mtime e
  dimensione dei sorgenti in 4 ms, e si ricostruisce in disparte. La logica sta
  in libreria e la usano le tre porte che leggono il grafo — tool MCP, CLI, API —
  perche' una regola scritta nella configurazione di un client lascerebbe gli
  altri con un grafo vecchio.
- **`MAX_CHUNKS_PER_DOCUMENT = 1`**: la deduplica per contenuto non vedeva tre
  chunk *diversi* dello stesso file, che occupavano tutte le prime posizioni.
  Sweep monotono sull'insieme cieco: nessun cap 4/32, cap 3 -> 4, cap 2 -> 5,
  **cap 1 -> 6**.
- **Nella fusione RRF un'identita' contribuisce una volta sola** al suo rango
  migliore, invece di sommare ogni occorrenza. Un export di chat da 2.559 chunk
  con 399 gruppi identici accumulava sei volte il punteggio di qualunque
  risposta vera. **+3 casi.**
- **Il ramo denso e' spento per default** (`TRUENEX_DENSE=on` per riaccenderlo):
  alla soglia 0,90 non portava nulla, e spento guadagna 2 casi.
- **I limiti dello strumento sono dichiarati nella risposta**, non nella
  documentazione: `totals` accanto agli elenchi tagliati, `coverage` con la
  percentuale misurata di relazioni ancora mancanti, `tests` che dice «non lo so»
  invece di «nessuno» quando non puo' sapere, e `answered_from` con i progetti da
  cui la risposta viene davvero.

### Fixed

- Il ripiego globale dello scope era registrato solo nel log del server: chi
  chiamava riceveva risultati di tutto il corpus credendoli ristretti.
- 579 archi `references` verso una finta entita' `String` — il 10,2% del grafo —
  ora scartati insieme agli altri tipi del linguaggio.
- Il confronto esatto dei nomi falliva sul punto iniziale dei metodi
  (`.verify_token`) e restituiva anche gli omonimi piu' lunghi
  (`verify_token_for_device`).
- Le esclusioni: `.venv` come **prefisso** (il punto iniziale annullava la regola,
  quindi `venv310` era escluso e `.venv310` no), piu' `.cargo` e `.archive`.
  Indice da 201.282 a 170.285 chunk, da 4,05 a 3,47 GB.
- L'harness di valutazione confrontava i percorsi come sottostringhe letterali,
  quindi ogni bersaglio scritto con `/` falliva in silenzio su Windows.
- `serve.py` non aveva un logger: il ramo di errore avrebbe sollevato
  `NameError` dentro il gestore della richiesta.
- La deduplica delle memorie non filtrate per stato scartava 111 nodi approvati
  da una persona, rendendo `global auto approve` un comando senza effetto.

### Note

- La cache del grafo del codice ha cambiato formato (versione 5). Le cache
  esistenti vengono ricostruite: `truenex-mem upgrade` lo fa per i progetti che
  conosce.
- Sui client: il profilo arriva in modo **verificato** su Claude Code, Codex e
  Kimi CLI. Su Cursor, Gemini, Copilot e Aider il file e' nel percorso che la
  loro documentazione dichiara, ma non e' stato provato sul campo. MiniMax non
  legge nessun file di istruzioni: per lui l'unica strada e' l'`AGENTS.md` di
  progetto o la sua memoria interna.

## [0.5.3] — 2026-08-05

### Fixed

- `truenex-mem update self` now passes `--no-cache-dir` to pip. Right after a fresh release, pip's local HTTP cache could keep serving a stale index response and report "already satisfied" on the old version even though a newer one was published, forcing users to run pip manually with `--no-cache-dir`. The command now does this itself.

## [0.5.2] — 2026-08-05

### Note

- No functional changes. Patch release to exercise the `update self` self-overwrite path (fixed in 0.5.1) against a real newer version on a live installation.

## [0.5.1] — 2026-08-05

### Fixed

- `truenex-mem update self` on Windows failed with `WinError 32` because pip tried to overwrite the running `truenex-mem.exe` in place (a process can never overwrite its own executable image, regardless of other processes). The command now renames the running launcher stub aside before invoking pip, which frees the path for the upgrade; the rename is restored if pip fails, and stale renames from interrupted runs are swept up on the next invocation.

## [0.5.0] — 2026-07-28

### Added

- Reciprocal Rank Fusion (RRF) of memory and chunk rankings: curated memories are first-class evidence and exact memory recalls rank first again instead of being buried under BM25 chunk scores.
- Retrieval evaluation harness (`scripts/eval_retrieval.py` with the 25-case eval set `scripts/eval/queries.json`): hit@1, hit@k, MRR and expected-absent metrics, with dated reports under `docs/eval/`.
- Optional semantic ranker (opt-in via `TRUENEX_EMBEDDER=e5`): multilingual-e5-base embeddings act as a third RRF ranker, with asymmetric query/passage prefixes, the `global reindex-embeddings` CLI command (resumable, dry-run by default), database schema v7, and an in-process numpy vector index (one BLAS matvec over ~479k vectors, GPU-accelerated encoding).
- Persistent memmap cache for the vector index (`~/.truenex-memory/vector_cache/`): process cold-start drops from a ~150s JSON rebuild to a ~0.1s memmap open, with a `MAX(updated_at)`-stamped sidecar for validation and atomic, best-effort writes.
- `purge-missing` maintenance for ledger entries whose files disappeared; agent worktrees are excluded from source discovery.
- `TRUENEX_DENSE=off` kill-switch to disable the dense ranker without changing the configured embedder.

### Changed

- Relevance gates before fusion (RRF merges by rank and ignores raw scores): memories below `MEMORY_FUSION_MIN_OVERLAP = 0.5` token-overlap and dense candidates below `DENSE_FUSION_MIN_COSINE = 0.90` cosine are filtered as noise. Both thresholds were tuned empirically on the eval set (memory-recall hit@k 0.93 and overall hit@k 0.88 preserved, expected-absent 3/3).
- Dense candidate fetching applies the cosine gate before row hydration (`min_score` pre-filter): the dominant dense cost (~1.4s under disk pressure) drops to tens of milliseconds when no candidate passes the gate.
- The CLI global search path uses the same RRF fusion as the MCP search path.
- Agent-session source boost lowered to 0.5.
- Database schema v6 (secondary indexes) and v7 (`qdrant_point_id` and `(embedding_model, updated_at)` covering indexes); purge deletes are batched to keep write locks short.

### Fixed

- A long tail of weak single-token memories no longer pushes every relevant document chunk out of the fused top_k.

### Upgrade notes

- Run `truenex-mem migrate apply` to upgrade the database schema to v7 (a backup is taken automatically before migrating).
- The e5 semantic ranker is strictly opt-in: without `TRUENEX_EMBEDDER=e5` production behavior is unchanged (hashing embedder, dense ranker off). To enable it, re-embed once with `truenex-mem global reindex-embeddings --yes` (GPU recommended, ~43 min for ~479k chunks). On NVIDIA GPUs the per-query encoding costs ~61ms; on CPU-only machines consider leaving the dense ranker disabled.

## [0.4.0] — 2026-07-17

### Added

- Persistent SQLite FTS5 index for document chunks, including automatic backfill and insert/update/delete triggers.
- Database schema v5 with safe migration and backup support.
- Search API filters and richer source, project, and timestamp metadata in results.
- Optional local LLM bootstrap tooling, Docker configuration, and setup documentation.
- Release workflow for tagged builds, GitHub Releases, and PyPI Trusted Publishing.

### Changed

- Lexical evidence is evaluated before dense fallback so partially embedded databases cannot hide exact matches.
- Global and project search use the persistent FTS5 index, with the previous Python BM25 implementation retained as a compatibility fallback.
- Agent discovery resolves manifests and preferences from the explicitly selected home directory.

### Fixed

- `memory_search` no longer scans the entire chunk corpus in Python for every query.
- Ingestion metadata preambles are no longer exposed as search results.
- Search results suppress operating-system trash paths and collapse identical mirrored chunks.
- Retrieval gating now follows the most recent ledger status for each source path.
- Chat returns a controlled service-unavailable response when the local memory database cannot be opened.

### Security

- Local activation databases, `.env` files, and private signing keys are explicitly excluded from version control.

## [0.2.7] — 2026-06-03

### Fixed

- Git Bridge `pull` now uses `--allow-unrelated-histories` to support syncing between two independently initialized PCs.

## [0.2.6] — 2026-06-03

### Fixed

- Git Bridge `push` and `pull` now run with `capture_output=False`, allowing Git Credential Manager to prompt for credentials interactively (previously stdout/stderr were captured by Python, blocking credential prompts).

## [0.2.5] — 2026-06-03

### Fixed

- Git Bridge bug: `MemoryRepository(str(db_path))` caused `AttributeError` because `sqlite.connect()` expected a `Path` object. Removed unnecessary `str()` calls in `git_commands.py` and made `sqlite.connect()` robust to both `Path` and `str` inputs.

## [0.2.4] — 2026-06-03

### Fixed

- `__init__.py` now reads `__version__` dynamically from package metadata (`importlib.metadata`) instead of a hardcoded string.
- `licensing.py` uses server-provided `expires_at` during activation instead of locally decoding the JWT token. Fixes cases where `expires` was incorrectly reported as `never` on some Windows machines.

## [0.2.3] — 2026-06-03

### Fixed

- Bump `__version__` to match package metadata (was still reporting 0.2.1 internally).

## [0.2.2] — 2026-06-03

### Fixed

- **License enforcement is now real**: previously a license key could be copied to unlimited devices. This was a critical commercial bug.
  - Added online license server (`memory.truenex.ai/api/v1/license`) with device binding (max 3 devices per Pro key).
  - Activation now issues an RS256 JWT token tied to a stable device fingerprint.
  - Offline validation works for 30 days; 7-day grace period after expiry.
  - `truenex-mem license deactivate` frees a device slot on the server.

### Changed

- `truenex-mem license activate` no longer supports `--offline`, `--tier`, `--expires-at`, or `--feature` flags. Tier and expiry are server-side.

## [0.2.1] — 2026-06-02

### Added

- `truenex-mem update self` — auto-detects pipx/pip and upgrades to latest version.
- Auto-update notice at CLI startup — checks PyPI every 24h (cache + 2s timeout) and warns if a newer version exists.
- Upgrade instructions in README and docs/installation.md.

### Fixed

- Include missing `self_update.py` and `auto_update_check.py` in the package (were untracked during v0.2.0 build).

## [0.2.0] — 2026-06-02

### Added

- **Pro license enforcement**: commands now require a Pro license (activated via `truenex-mem license activate`):
  - `truenex-mem serve` — Desktop App backend
  - `truenex-mem global auto run|status|review|approve|reject|promote|prune`
  - `truenex-mem git init|push|pull|status|remote`
- **Git Bridge** (`truenex-mem git *`): sync project memory across multiple PCs via Git.
  - `git init` — initialise sync repo with `.gitignore`
  - `git push` — export DB → JSON and push to remote
  - `git pull` — pull from remote and import JSON → DB
  - `git status` — show sync state (DB vs exported JSON vs git dirty)
  - `git remote add|remove|list|show` — manage remotes
  - All git commands support `--json` and `--dry-run`.
- **MCP setup guide** (`docs/mcp-setup.md`): copy-paste configuration snippets for Claude Code, Kimi, Cursor, and Codex.
- **Landing page** (`memory.truenex.ai`): quickstart with pipx, commands reference, and MCP setup link.

### Fixed

- Added missing `httpx>=0.27` to main `project.dependencies`. `truenex-mem serve` no longer crashes with `ModuleNotFoundError: No module named 'httpx'` on a clean install.

## [0.2.0a1] — 2026-05-19

### Fixed

- README/docs: corrected installation instructions (alpha is source-only, PyPI coming with v0.2.0 stable).
- `serve.py`: added missing `GET /api/file-analysis` endpoint.
- `scripts/build_release.py`: version now read dynamically from `pyproject.toml`.
- `ROADMAP.md`: rewritten as operational execution plan with priorities and criteria of done.

### Stabilization

- Test coverage expanded to >80% for all CLI commands.
- Package build hardened: `pip install -e .` and `pip install .` verified clean.
- Release artifact script with SHA-256 hashes (`scripts/build_release.py`).
- Qdrant integration hardened: configurable timeout and retries, graceful fallback to SQLite with logging.
- Chunking configurability: `--chunk-size` and `--chunk-overlap` CLI options, plus `TRUENEX_MEMORY_CHUNK_SIZE` / `TRUENEX_MEMORY_CHUNK_OVERLAP` env vars.
- Unified source exclusion presets (node_modules, .git, __pycache__, build/, dist/) with `.gitignore` awareness.
- Expanded documentation: PyPI install instructions, troubleshooting guide.

## [0.1.0-alpha.1] — 2026-05-13

### Core

- Local project memory with add, search, list, and lifecycle status
  (active / obsolete / superseded / conflicting / unverified).
- CLI entry point `truenex-mem` via Typer with full `--help` tree.
- MCP stdio server exposing `memory_search`, `memory_add`,
  `global_status`, and `global_project_context`.

# Validazione label real-logs — 2026-07-28

**Per Marco — 3 domande, ~5 minuti.**

Questi sono 3 casi di ricerca *reali* (fatti dagli agent in produzione, presi dai log).
Per misurare se il retrieval semantico funziona dobbiamo sapere **quale documento
era la risposta giusta**. La nostra ipotesi ("label proposta") va confermata da te.

Per ogni caso: leggi la domanda, apri il file proposto, e decidi se chi faceva
quella domanda voleva trovare proprio quel documento. Rispondi a Kimi anche solo
così: `r01 sì, r02 sì, r05 no — quello giusto è <nome file>`.

---

## r01 — domanda reale:

```
MedDesk visita medica SOAP workflow UX design briefing nota
```

**Label proposta (da validare):**

`D:\Project_sw\ProjectPy\truenex-local-QVAC_MedPsy\docs\unified\04-runtime-tauri\plans\2026-07-24-ux-flow-and-verifiability-roadmap.md`

**Cosa trova oggi il sistema:** MISS — il documento giusto non arriva nei primi 5.
Al suo posto restituisce note MedDesk correlate (roadmap UX post-collaudo,
difetti sicurezza clinica, fix allucinazioni briefing) ma non il file sopra.

**Domanda per te:** chi cercava "visita medica SOAP workflow UX design briefing"
voleva trovare *quel* file della roadmap UX? Oppure voleva trovare una **nota di
memoria** (di quelle che il sistema già trova)?

- [ ] SÌ, il file proposto è la risposta giusta
- [ ] NO — la risposta giusta è: ______________________

---

## r02 — domanda reale:

```
MedDesk Windows tauri dev llama-cpp-sys-2 CMake build failure local target D:\truenex-target Vulkan Ninja
```

**Label proposta (da validare):**

`D:\Project_sw\ProjectPy\truenex-local-QVAC_MedPsy\runtime\tauri-app\docs\BUILD-GPU.md`

**Cosa trova oggi il sistema:** il documento giusto arriva, ma solo al **5° posto**
(il primo che conta per hit@1 non è). Con il ranker semantico attivo la posizione
migliora rispetto a prima ma resta fuori dal podio.

**Domanda per te:** chi aveva il build failure CMake/Vulkan su Windows cercava
proprio la guida BUILD-GPU.md?

- [ ] SÌ, il file proposto è la risposta giusta
- [ ] NO — la risposta giusta è: ______________________

---

## r05 — domanda reale:

```
MedPsy-4B modello estrazione clinica second reader prompt medico
```

**Label proposta (da validare):** un file `models.md` — ne esistono due:

1. `D:\Project_sw\ProjectPy\truenex-local-QVAC_MedPsy\docs\models.md`
2. `D:\Project_sw\ProjectPy\truenex-local-QVAC_MedPsy\docs\unified\02-architettura-tecnica\models.md`

**Cosa trova oggi il sistema:** MISS — nessuno dei due `models.md` nei primi 5.
Al primo posto arriva una vecchia conversazione recuperata del 2026-07-22.

**Domande per te:**
a) il documento giusto è uno dei due `models.md`? Se sì, **quale dei due**?
b) oppure la risposta giusta era proprio quella conversazione storica
   (e allora il sistema aveva già ragione e il caso va chiuso come "non un errore")?

- [ ] SÌ — il giusto è il n. __ (1 o 2)
- [ ] NO — la risposta giusta è: ______________________

---

## ESITO — validazione completata il 2026-07-28 (owner)

- **r01 → VALIDA.** Il file roadmap è la risposta giusta. Nota: la memoria curata che
  cita quel documento arriva al rank 1, quindi l'informazione era comunque servita
  anche quando il file esatto era fuori dal top-5 (verificato dal vivo con
  `truenex-mem global search`: file al rank 10, nota al rank 1).
- **r02 → VALIDA.** `BUILD-GPU.md` è inequivocabilmente la risposta per un build
  failure CMake/Vulkan su Windows.
- **r05 → VALIDA**, specificata: il file giusto è
  `docs/unified/02-architettura-tecnica/models.md` (non l'altro `docs/models.md`).
  Label in `queries.json` aggiornata di conseguenza.

Le metriche delle eval già prodotte non cambiano per costruzione (gli expected di
r01/r02 sono invariati; per r05 nessun `models.md` era nei top-hit, quindi resta
MISS anche col path più specifico). Condizione Codex "validare le label entro
2-4 settimane" → **CHIUSA**. Il ranker denso esce dallo stato "in prova".

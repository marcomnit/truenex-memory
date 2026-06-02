# Truenex Memory — Guida per l'utente

> Questa guida è pensata per chi usa Truenex Memory, non per chi lo sviluppa.

---

## 1. Cosa è Truenex Memory

**Truenex Memory** è uno strato di memoria locale per gli agenti di coding AI (Claude Code, Codex, Cursor, Kimi, ecc.).

Invece di perdere il contesto a ogni nuova sessione, i tuoi agenti possono interrogare una memoria persistente sul tuo PC che contiene decisioni di progetto, documenti indicizzati, architettura e contesto accumulato.

- **Totalmente locale** — i tuoi dati restano sul tuo computer
- **Open core** — la versione base è gratuita e open-source
- **Pro** — licenza a pagamento (€39 una tantum) per funzionalità avanzate

---

## 2. Installazione

### Requisiti

- **Python** 3.12 o superiore
- Windows, macOS o Linux
- Circa 50 MB di spazio disco + spazio per i documenti che indicizzerai

### Installazione rapida (1 comando)

Apri un terminale (PowerShell su Windows, Terminal su macOS/Linux) e scrivi:

```bash
pip install truenex-memory
```

Se preferisci un ambiente isolato (consigliato), usa **pipx**:

```bash
pipx install truenex-memory
```

### Verifica

Dopo l'installazione, il comando `truenex-mem` è disponibile **da qualsiasi cartella**:

```bash
truenex-mem --help
```

Se vedi la lista dei comandi, l'installazione è riuscita.

---

## 3. Primi passi (versione gratuita)

Non serve comprare nulla per iniziare. La versione gratuita include tutto il core:

```bash
# Inizializza la memoria nel tuo progetto
cd /percorso/del/tuo/progetto
truenex-mem init

# Indicizza i file del progetto nella memoria
truenex-mem index

# Cerca nella memoria
truenex-mem search "come funziona l'autenticazione"

# Controlla lo stato
truenex-mem doctor
```

### Cosa include la versione gratuita

- Memoria locale SQLite
- Indicizzazione file di progetto
- Ricerca semantica e testuale
- Comandi manuali: add, list, search, status
- Tracce di retrieval e log
- Export/import dati
- Server MCP per integrazione con agenti
- Aggiornamento globale e refresh
- Auto-memory candidata (con review manuale)

---

## 4. Passare a Pro — Cosa cambia

La licenza **Pro** costa **€39 una tantum** (nessun abbonamento) e si acquista su:

👉 [memory.truenex.ai](https://memory.truenex.ai)

### Dopo l'acquisto

1. Ricevi un'email da `hello@truenex.ai` con la tua chiave:
   ```
   trxn-pro-XXXXXXXXXXXXXXXX
   ```

2. Attivala con un solo comando:
   ```bash
   truenex-mem license activate trxn-pro-XXXXXXXXXXXXXXXX
   ```

3. Verifica:
   ```bash
   truenex-mem license status
   ```
   Output atteso:
   ```
   Tier:    pro
   Status:  active
   Expires: 2027-06-02T...
   Features: advanced_auto_memory, knowledge_dashboard, cross_project_ui, priority_support
   ```

### Licenza multi-dispositivo

Ogni chiave Pro supporta fino a **3 attivazioni**. Puoi usare la stessa chiave su:
- PC fisso
- Laptop
- Macchina virtuale / WSL

Se superi le 3 attivazioni, contatta il supporto.

---

## 5. Feature Pro (Free vs Pro)

| Funzionalità | Free | Pro |
|---|---|---|
| Memoria locale SQLite | ✅ | ✅ |
| Indicizzazione file | ✅ | ✅ |
| Ricerca semantica | ✅ | ✅ |
| MCP server | ✅ | ✅ |
| Export/import | ✅ | ✅ |
| Auto-memory candidata (review manuale) | ✅ | ✅ |
| **Advanced Auto Memory** (generazione automatica senza review) | ❌ | ✅ |
| **Knowledge Dashboard** (UI web per esplorare la memoria) | ❌ | ✅ |
| **Cross-Project UI** (vista unificata memoria multi-progetto) | ❌ | ✅ |
| **Priority Support** (supporto prioritario via email) | ❌ | ✅ |

> **Nota:** Le feature Pro sono attualmente disponibili come *flag di licenza*. Alcune funzionalità (come la Knowledge Dashboard) sono in fase di rilascio e saranno accessibili automaticamente non appena pubblicate, senza costi aggiuntivi.

---

## 6. Scenario: "Ho comprato la licenza ma non ho ancora installato nulla"

Nessun problema. L'ordine non conta:

1. **Compra** su [memory.truenex.ai](https://memory.truenex.ai) — ricevi la chiave via email
2. **Installa** quando vuoi:
   ```bash
   pip install truenex-memory
   ```
3. **Attiva** la chiave:
   ```bash
   truenex-mem license activate trxn-pro-XXXXXXXXXXXXXXXX
   ```
4. **Inizia a usare** tutto, incluso le feature Pro quando saranno rilasciate

La licenza resta valida per **1 anno** dalla data di acquisto. Puoi sempre rinnovarla.

---

## 7. Comandi utili

| Comando | Scopo |
|---|---|
| `truenex-mem --help` | Lista completa comandi |
| `truenex-mem license status` | Stato licenza attiva |
| `truenex-mem license activate CHIAVE` | Attiva licenza Pro |
| `truenex-mem init` | Inizializza memoria nel progetto corrente |
| `truenex-mem index` | Indicizza file del progetto |
| `truenex-mem search "query"` | Cerca nella memoria |
| `truenex-mem doctor` | Diagnostica e verifica salute |

---

## 8. Supporto

- **Documentazione tecnica:** [docs/](../)
- **Sito web:** [memory.truenex.ai](https://memory.truenex.ai)
- **Email:** hello@truenex.ai
- **GitHub Issues:** [marcomnit/truenex-memory](https://github.com/marcomnit/truenex-memory/issues)

Gli utenti **Pro** hanno priorità nella risposta via email.

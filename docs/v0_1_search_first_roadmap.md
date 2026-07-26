# Truenex Memory Desktop — v0.1 Roadmap
## "Search-First Memory Explorer" (Integration Sprint)

**Approach:** Option C+ (Compromised Enhanced)  
**Base Review:** Codex Architecture Review `agent-nayos95s`  
**Last Updated:** 2026-06-10

---

## 1. Executive Summary

v0.1 is an **integration sprint**, not an invention sprint. We unify existing mature components (Cytoscape graph engine, Search cards, DayClusterSidebar) under a single search-first layout with a global search bar, three-pane explorer UI, and simulated client-side QIL (prefix parsing). A Dockerized llama-server is prepared and running on localhost:9081 but **not yet wired into the search path** — it serves only as a health-checkable foundation for v0.2.

**Goal:** Ship a usable search-first memory explorer in **2 weeks** that demonstrates the "externalized memory retrieval" paradigm without requiring backend LLM inference.

---

## 2. Guiding Principles (from Codex Review)

1. **Integration, not invention.** Reuse `MemoryGraph.tsx`, `Search.tsx` cards, `DayClusterSidebar.tsx`, `ProjectTreemap.tsx`.
2. **No chat in navigation.** `Chat.tsx` file is preserved but route and nav item are removed.
3. **Results = objects, not text.** No generative LLM output in the core flow.
4. **No scope creep.** If it is not in the "IN" list below, it is v0.2.
5. **Zustand for global UI state.** `query`, `filters`, `selectedNode`, `viewMode`, `rightPanelOpen`.
6. **Backend stays native Python.** No Docker for the main backend; Docker is used **only** for the optional llama-server sidecar.

---

## 3. Architecture v0.1

```
┌─────────────────────────────────────────────┐
│  DESKTOP (Tauri 2 + React 18 + Zustand)     │
│  ├── Topbar: GlobalSearchBar                │
│  ├── Left: FilterPanel + DayClusterSidebar  │
│  ├── Main: SearchResults (List | Graph)     │
│  └── Right: ContextPanel                    │
└─────────────────────────────────────────────┘
              │ HTTP REST
┌─────────────────────────────────────────────┐
│  BACKEND (Python native, truenex-mem serve) │
│  ├── POST /api/search    (+filters)         │
│  ├── GET  /api/source/:id                   │
│  ├── GET  /api/catalog/status               │
│  └── GET  /api/health/llm  (proxy)          │
└─────────────────────────────────────────────┘
              │ HTTP localhost:9081
┌─────────────────────────────────────────────┐
│  LLM SIDECAR (Docker, prepared NOT wired)   │
│  ├── llama-server container                 │
│  └── Accessible via /props for health only  │
└─────────────────────────────────────────────┘
```

---

## 4. Scope v0.1 — IN

### Frontend (Desktop)
- [ ] **F1** Remove `/chat` route and nav item (preserve file).
- [ ] **F2** Add Zustand store `src/store/uiStore.ts`.
- [ ] **F3** Global search input in `Topbar.tsx` (always visible, placeholder: "Search your memory…").
- [ ] **F4** Restructure `AppShell.tsx` to 3-pane layout (Left | Main | Right).
- [ ] **F5** `FilterPanel.tsx` (left): type, status, project, date filters.
- [ ] **F6** `ContextPanel.tsx` (right): metadata, relations preview, open-in-OS for selected node/result.
- [ ] **F7** `StatusBar.tsx`: backend health, catalog last refresh, chunk count.
- [ ] **F8** Unify `Search.tsx` with view toggle: **List** (existing cards) | **Graph** (embed `MemoryGraph.tsx`).
- [ ] **F9** Extract `DayClusterSidebar.tsx` from inside `MemoryGraph.tsx` to standalone left-panel component.
- [ ] **F10** Client-side QIL: parse prefixes (`project:`, `type:`, `status:`, `after:`) before sending to backend.
- [ ] **F11** Simplify `Dashboard.tsx` to landing page: stats + recent searches + quick actions.

### Backend (Python)
- [ ] **B1** Extend `POST /api/search` to accept optional `filters` object (`project`, `type`, `status`, `date_after`).
- [ ] **B2** Add `GET /api/source/:id` — full source node with documents, relations, chunk preview.
- [ ] **B3** Add `GET /api/catalog/status` — indexing health, last refresh, warnings, total sources/chunks.
- [ ] **B4** Add `GET /api/health/llm` — proxy to llama-server `/props` or return `{"status":"not_ready"}`.

### Infrastructure (Docker)
- [ ] **D1** Add `docker-compose.llm.yml` with llama-server (CUDA image for Win/Linux, CPU fallback).
- [ ] **D2** Add `scripts/start_llm.py` helper to bootstrap the container (check Docker, pull image, start).
- [ ] **D3** Document manual model placement in `./models/` (no auto-download in v0.1).

---

## 5. Scope v0.1 — OUT (Scope-Creep Firewall)

| Feature | Why Out | When |
|---------|---------|------|
| Backend QIL (LLM query expansion) | Invention, not integration | v0.2 |
| Entity extraction via LLM | Requires wired llama-server | v0.2 |
| Reranking via mini-LLM | Requires wired llama-server | v0.2 |
| Multi-search (full-text + vector + graph) | Backend invention | v0.2 |
| Timeline view | No backend event stream | v0.2 |
| Chat UI in navigation | Product differentiation | v0.2+ or never |
| Generative chat responses | Violates "results = objects" | v0.2+ or never |
| GPU selection UI | Docker handles it opaquely | v0.2 |
| API key for remote LLM | Local-first principle | Future |
| Auto-download model GGUF | Complexity | v0.2 |
| Collaborative memory graphs | No multi-user backend | v0.3 |

### Contingency Plan

If **F8a (MemoryGraph embeddability spike)** fails:
- **Pivot:** Graph mode opens in a dedicated full-screen overlay/modal instead of an inline toggle.
- **Impact:** UX degradation (no split-screen list+graph), but preserves ship date.
- **Trigger:** Decision at end of F8a (Week 1, Day 1).

---

## 6. Task Breakdown & Estimates

### Frontend
| ID | Task | Est | Files |
|----|------|-----|-------|
| F1 | Remove chat from nav | 10m | `App.tsx`, `Sidebar.tsx` |
| F2 | Zustand uiStore | 2h | `src/store/uiStore.ts` |
| F3 | GlobalSearchBar | 3h | `Topbar.tsx`, `GlobalSearchBar.tsx` |
| F4 | AppShell 3-pane | 4h | `AppShell.tsx` |
| F5 | FilterPanel | 4h | `src/components/FilterPanel.tsx` |
| F6 | ContextPanel | 4h | `src/components/ContextPanel.tsx` |
| F7 | StatusBar | 2h | `src/components/StatusBar.tsx` |
| F8a | **SPIKE:** Audit MemoryGraph.tsx embeddability (decouple routing, viewport) | 4h | `src/components/MemoryGraph.tsx` |
| F8 | Search List+Graph toggle | 12h | `src/pages/Search.tsx`, `src/components/MemoryGraph.tsx` |
| F9 | Generalize DayClusterSidebar for left-panel integration | 4h | `src/components/DayClusterSidebar.tsx` |
| F10 | Client-side prefix parser | 2h | `src/utils/queryParser.ts` |
| ~~F11~~ | ~~Dashboard simplify~~ | ~~2h~~ | ~~`src/pages/Dashboard.tsx`~~ |
| | **Frontend Subtotal** | **~4–5 dev days** | |

### Backend
| ID | Task | Est | Files |
|----|------|-----|-------|
| B1 | Search + filters | 4h | Search endpoint |
| B2 | Source by ID | 6h | Source endpoint |
| B3 | Catalog status | 3h | Catalog endpoint |
| B4 | LLM health proxy | 2h | Health endpoint |
| | **Backend Subtotal** | **~2 dev days** | |

### Docker / LLM Prep
| ID | Task | Est | Files |
|----|------|-----|-------|
| D1 | docker-compose.llm.yml | 2h | `docker-compose.llm.yml` |
| D2 | start_llm.py helper | 2h | `scripts/start_llm.py` |
| D3 | Documentation | 1h | `docs/LLM_SETUP.md` |
| | **Docker Subtotal** | **~1 dev day** | |

### Integration & Test
| ID | Task | Est |
|----|------|-----|
| I1 | End-to-end search flow | 4h |
| I2 | Graph mode in new layout | 4h |
| I3 | Filter → Search → ContextPanel | 4h |
| I4 | Docker smoke test | 2h |
| | **Integration Subtotal** | **~2 dev days** |

**Total v0.1 Estimate: 11–12.5 dev days (~2.5–3 weeks)**

*If the team must hit 2 weeks, cut F6 (ContextPanel) and show metadata inline instead.*

---

## 7. API Contract v0.1

### `POST /api/search` (extended)
Request:
```json
{
  "query": "authentication logic",
  "top_k": 10,
  "filters": {
    "project": "truenex-memory",
    "type": "document_chunk",
    "status": "active",
    "date_after": "2026-05-01"
  }
}
```

Response (v0.1 — **MUST include `source_id` and `document_id`**):
```json
[
  {
    "source_id": "src_abc123",
    "document_id": "doc_xyz789",
    "title": "Authentication Refactor",
    "content": "...",
    "source_path": "/projects/truenex-memory/src/auth.py",
    "memory_type": "code",
    "status": "active",
    "score": 0.92
  }
]
```

### `GET /api/source/:id`
```json
{
  "id": "src_abc123",
  "path": "/home/marco/projects/truenex-memory/README.md",
  "project": "truenex-memory",
  "documents": [...],
  "relations": [
    {"target": "src_def456", "type": "references"}
  ],
  "chunk_count": 12,
  "last_indexed": "2026-06-01T10:00:00Z"
}
```

### `GET /api/catalog/status`
```json
{
  "status": "healthy",
  "last_refresh": "2026-06-08T20:00:00Z",
  "warnings": [],
  "total_sources": 142,
  "total_chunks": 8902
}
```

### `GET /api/health/llm`
```json
{
  "status": "ready",
  "model": "qwen2.5-7b-instruct-q4_k_m.gguf",
  "server_url": "http://localhost:9081"
}
// OR if container not running:
{
  "status": "not_ready",
  "message": "llama-server container not running"
}
```

---

## 8. Component Mapping

### Reuse (minimal changes)
| Component | Action |
|-----------|--------|
| `MemoryGraph.tsx` | Embed as Main-view "Graph" mode; add `highlightQuery` prop. |
| `Search.tsx` result cards | Reuse card layout, type badges, score formatting. |
| `ProjectTreemap.tsx` | Keep; trigger from project selection. |
| `DayClusterSidebar.tsx` | Extract from `MemoryGraph.tsx` to standalone left panel. |
| `AgentSessionList.tsx` | Merge into left panel or keep embedded in DayCluster. |
| `AddMemory.tsx` | Keep as-is; link from Dashboard and ContextPanel. |
| `Settings.tsx` | Keep as-is; add llama-server status indicator only. |

### Build New
| Component | Purpose |
|-----------|---------|
| `GlobalSearchBar.tsx` | Lives in Topbar; debounced input; Enter to execute. |
| `FilterPanel.tsx` | Left panel; type/status/project/date filters. |
| `ContextPanel.tsx` | Right panel; selected node/result metadata and relations preview. |
| `StatusBar.tsx` | Footer; backend health, catalog status, chunk count. |
| `uiStore.ts` | Zustand store for query, filters, viewMode, selection. |
| `queryParser.ts` | Client-side prefix parser for simulated QIL. |

### Deprecate from UI (keep files)
| Component | Action |
|-----------|--------|
| `Chat.tsx` | Remove route and nav item. File stays. |

---

## 9. Docker llama-server — Preparation Only

**Purpose:** Have a running local LLM that can be health-checked, ready to be wired into the search path in v0.2.

**Image:** `ghcr.io/ggerganov/llama.cpp:server-cuda` (Win/Linux with NVIDIA Container Toolkit) or `ghcr.io/ggerganov/llama.cpp:server` (CPU fallback).

**Ports:** `9081:9081`

**Volumes:** `./models:/models:ro`

**CUDA Profile (Windows/Linux with GPU):**
```yaml
command: >
  --host 0.0.0.0
  --port 9081
  --model /models/qwen2.5-7b-instruct-q4_k_m.gguf
  --ctx-size 8192
  --parallel 2
  --n-gpu-layers ${N_GPU_LAYERS:-999}
  --metrics
```

**CPU Profile (macOS or CPU-only fallback):**
```yaml
command: >
  --host 0.0.0.0
  --port 9081
  --model /models/qwen2.5-7b-instruct-q4_k_m.gguf
  --ctx-size 4096
  --parallel 2
  --n-gpu-layers 0
  --metrics
```

**macOS Note:** NVIDIA Container Toolkit is unavailable on macOS. macOS users **must** use the CPU profile. GPU acceleration on macOS will be supported natively in v0.2 via Metal backend.

**v0.1 Constraint:** The frontend and backend do **not** call this container for search. Only `GET /api/health/llm` probes it.

---

## 10. Review Gates (Codex Checkpoints)

Before proceeding past each gate, a Codex review is required.

| Gate | Trigger | What to Review |
|------|---------|----------------|
| **G1** | F1–F4 complete | AppShell layout, routing changes, Zustand schema. |
| **G3** | B1–B3 complete | API contract, filter logic, catalog status accuracy. |
| **G2** | F8 complete | Search+Graph toggle integration, Cytoscape embedding. |
| **G4** | D1–D3 complete | Docker compose correctness, helper script robustness. |
| **G5** | Pre-release | End-to-end flow, performance, scope adherence. |

**Note:** API contract (G3) is reviewed **before** graph-toggle integration (G2) so the frontend consumes a stable contract.

---

## 11. Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Cytoscape breaks in new layout | High | Test each graph mode (overview, zone, cluster, detail) individually. |
| Docker llama-server fails on user's machine | Medium | Clear setup docs; fallback to CPU image; `StatusBar` shows "LLM not ready" without breaking search. |
| Backend filter logic slows search | Medium | Filters applied post-vector-search on top_k results (fast path). |
| 3-pane layout feels cramped on small screens | Low | Make left/right panels collapsible; default closed on <1280px. |
| Scope creep during implementation | High | Strict adherence to Section 5 "OUT" list; any addition requires explicit approval. |

---

## 12. Definition of Done for v0.1

- [ ] User opens app → sees Dashboard with stats and global search bar.
- [ ] User types query → sees results in List mode with type badges and scores.
- [ ] User toggles to Graph mode → sees Cytoscape graph with query-relevant nodes.
- [ ] User clicks a result → ContextPanel shows metadata and relations.
- [ ] User applies filters (type, project) → results refine.
- [ ] User sees catalog status in StatusBar (last refresh, chunk count).
- [ ] `GET /api/health/llm` returns status (ready or not_ready) — no functional impact on search.
- [ ] No chat UI accessible from navigation.
- [ ] No generative text output in the core flow.

---

*Document ready for Codex review.*

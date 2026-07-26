# Codex Review: Truenex Memory Desktop — Search-First v1 Spec

> **From:** Kimi (current session)  
> **To:** Codex (review & architecture validation)  
> **Date:** 2026-06-10  
> **Ticket:** AGENT-DISC-REFACTOR-2026-06-10 (completed) → Next: DESKTOP-SEARCH-FIRST-v1

---

## 1. What Already Exists (Desktop Context)

**Repository:** `D:\Project_sw\ProjectPy\truenex-memory-desktop`  
**Stack:** Tauri v2 (Rust) + React 18 + TypeScript + Tailwind CSS v3 + React Router v6 + Lucide React  
**Communication:** HTTP REST to local Python backend (`truenex-mem serve`)

### Current Pages
| Page | Status | Notes |
|---|---|---|
| `Dashboard.tsx` | ✅ Exists | Stats, quick actions |
| `Search.tsx` | ✅ Exists | Semantic search (text input + results list) |
| `MemoryViewer.tsx` | ✅ Exists | Browse/manage memory nodes |
| `Settings.tsx` | ✅ Exists | Backend, Qdrant, theme, license |
| `Chat.tsx` | ✅ Exists | **Conversational interface** (⚠️ conflicts with new spec) |
| `AddMemory.tsx` | ✅ Exists | Manual memory entry |
| `AgentSessionList.tsx` | ✅ Exists | Browse agent sessions |
| `ConnectionScreen.tsx` | ✅ Exists | Backend connection UI |
| `Onboarding.tsx` | ✅ Exists | First-run experience |
| `ProjectDashboard.tsx` | ✅ Exists | Project overview |

### Current Components
| Component | Status | Notes |
|---|---|---|
| `MemoryGraph.tsx` | ✅ Exists | Network graph visualization |
| `ProjectTreemap.tsx` | ✅ Exists | Treemap view of projects |
| `ProjectDashboardCard.tsx` | ✅ Exists | Project card component |
| `DayClusterSidebar.tsx` | ✅ Exists | Timeline sidebar by day |
| `Sidebar.tsx` | ✅ Exists | Navigation sidebar |
| `StatusBar.tsx` | ✅ Exists | Footer status bar |

### Current API Contract (backend exposes)
```
GET  /api/health                  → { status, version }
GET  /api/projects                → [ { id, name, path, indexed_at } ]
POST /api/search                  → { query, top_k } → [ results ]
GET  /api/memory                  → [ { id, content, type, status, created_at } ]
POST /api/memory                  → { content, type } → { id }
GET  /api/stats                   → { projects, documents, chunks, memory_nodes }
GET  /api/settings                → current config
POST /api/settings                → update config
POST /api/shutdown                → graceful backend shutdown
```

---

## 2. The Proposal (from Marco / User)

**Core Principle:**
> Truenex Memory Desktop is **A search-driven visual operating system for structured memory and knowledge graphs.**
> 
> ❌ Not a chat  
> ❌ Not a conversational assistant  
> ✅ A visual search and exploration system

### Key Architectural Elements

```
┌────────────────────────────────────────────┐
│ TOP BAR: SEARCH INPUT (always visible)     │
├────────────────────────────────────────────┤
│ LEFT PANEL  │ MAIN VIEW                    │
│ - History   │ - Results / Graph            │
│ - Filters   │ - Timeline                   │
│ - Projects  │ - Document View              │
├────────────────────────────────────────────┤
│ RIGHT PANEL (Context Inspector)            │
│ - Entity details                           │
│ - Relations                                │
│ - Metadata                                 │
└────────────────────────────────────────────┘
```

### Search Bar (Single Entry Point)
- Always visible, NOT a chat input
- No conversation threads
- Each ENTER = independent query
- Placeholder: "Search your memory…"

### Main View Modes
1. **Document View** — For files, code, notes (preview cards)
2. **Entity View** — For system entities (clients, modules, projects)
3. **Graph View** — Network relational visualization (CORE)
4. **Timeline View** — Chronological decision/event stream

### Query Execution Flow
```
User Query
    ↓
Query Intelligence Layer (QIL)
    ↓
Structured Query DSL
    ↓
Search Orchestrator
    ↓
Memory Graph / Vector / Index
    ↓
UI Rendering Layer
```

### UX Rules
- ❌ No chat bubbles
- ❌ No assistant/user messages
- ❌ No long textual responses
- ✅ Structured cards
- ✅ Graph nodes
- ✅ Timeline events
- ✅ Entity panels
- ✅ Document previews

---

## 3. Kimi's Analysis

### What's Right
1. **"Not a chat"** — Correct and differentiating. All competitors (Mem0, Zep, etc.) are chat-centric.
2. **Search-first** — Users want to *find* things, not "talk" to memory.
3. **Graph as differentiator** — Visualizing relationships between projects, decisions, entities is a real gap.
4. **Knowledge visibility** — "See" memory, don't "read" it.

### Risks / Concerns
1. **Too ambitious for v1** — The proposal defines Document + Entity + Graph + Timeline + Context Inspector + QIL. That's a 6-9 month scope.
2. **Missing "Management" operations** — The spec is 100% read. Daily ops (refresh, catalog health, approve/reject auto-memory, agent manifest management) are missing.
3. **Chat.tsx conflicts** — We already HAVE a `Chat.tsx` page. The spec says "no chat". Do we remove it? Deprecate it?
4. **QIL/DSL undefined** — The Query Intelligence Layer and Structured Query DSL are hand-wavy. We need concrete API specs.

### MVP Recommendation (Kimi)
| Phase | Features | Est. Time |
|---|---|---|
| **v0.1 MVP** | Search bar + results list + document preview + catalog status | 2-3 weeks |
| **v0.2** | Entity cards + Timeline view | 2-3 weeks |
| **v0.3** | Graph view (most complex) | 1-2 months |
| **v0.4** | Query builder + collaborative | 1-2 months |

**Keep the "no chat" positioning but add a "synthesis card" output mode** — not conversation, but a structured summary card of results.

---

## 4. Questions for Codex

### Architecture
1. **Should we remove `Chat.tsx` entirely** or keep it behind a feature flag / "legacy mode"?
2. **How should the Search bar integrate with existing `Search.tsx`** — replace it or evolve it?
3. **Is the proposed QIL/DSL a new backend module** or can it be implemented client-side with the existing `/api/search` endpoint?

### Technology
4. **Graph library:** We already have some graph in `MemoryGraph.tsx`. Should we standardize on **Cytoscape.js**, **D3**, **vis-network**, or **React Flow**?
5. **State management:** Currently using hooks (`useApi.ts`). For the multi-panel layout (left/main/right), do we need **Zustand** / **Jotai** / **Context**?

### API
6. **Missing endpoints for v1:** The current API has no endpoints for:
   - Catalog status (`global status` equivalent)
   - Auto-memory review lifecycle
   - Agent manifest management
   - Source ledger inspection
   - **Which backend endpoints should we add first?**

### Scope
7. **Given we ALREADY have** `MemoryGraph.tsx`, `ProjectTreemap.tsx`, `DayClusterSidebar.tsx` — **should v0.1 focus on unifying these existing components** under the new search-first layout rather than building new ones?

---

## 5. Deliverables Expected from Codex

Please provide:
1. **Architecture review** — Feasibility of the spec with current stack
2. **Component decomposition** — What to build, what to reuse, what to remove
3. **API gap analysis** — Which backend endpoints are missing for v0.1
4. **Recommended v0.1 scope** — Your opinion on what the MVP should actually include
5. **Next action items** — What should be coded first

---

*Context: The backend (`truenex-memory` Python) has just been refactored with an external agent manifest system. The desktop app is the next priority. We want to avoid scope creep while maintaining the "search-first, not chat-first" differentiation.*

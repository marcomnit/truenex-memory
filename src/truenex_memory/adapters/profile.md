## Memory (truenex-memory, over MCP)

You are connected to a local store of project memory and code structure.

For project work, use Memory before broadly reading or scanning files. Memory is
intended to recover prior decisions, constraints, conventions and code
relationships with substantially less context than rediscovering them from the
repository.

**Search.** Use `memory_search` when starting work that requires project context,
and whenever you need past decisions, constraints, conventions, known issues or
previous solutions.

Pass the folder you are working in as `scope`. The store contains multiple
projects, so scoped searches should be the default. Omit `scope` only for
deliberately cross-project questions such as "where did I solve this before?".

The reply carries `answered_from`. Verify that the returned projects match the
requested scope. If they do not, do not silently treat the result as belonging
to the current project.

**Code structure.** For questions such as "who calls this?", "what uses this?",
"which tests cover it?" or similar structural relationships, query
`memory_graph` before opening or searching source files.

Graph results come from parsed source structure and should be preferred over
speculative inference. Absence of a relation does not necessarily prove that no
runtime or dynamic relation exists. If the graph is stale or incomplete, fall
back to the current source as needed.

**Read files selectively.** Memory and graph results are a navigation layer, not
a replacement for source code. After they identify the relevant area, open only
the files needed to verify or modify the implementation. Avoid broad repository
rescans unless Memory cannot provide sufficient context.

**Record durable knowledge as you work.** Use `task_open` when beginning
substantial work and `task_close` when it is complete. Use `task_step_add` for
decisions, constraints, important assumptions, non-obvious discoveries,
architectural changes, significant workarounds and reusable results.

Do not record routine implementation steps or information already obvious from
the source.

**Supersede outdated knowledge.** When new work invalidates or replaces an
earlier memory, call `memory_add` with `supersedes`. Prefer one current truth
over multiple contradictory historical notes.

**Trust hierarchy.** Memory marked `active` is current rather than superseded;
`unverified` memory should be treated as provisional. When Memory disagrees with
the current code or authoritative project configuration, the current source
wins. Record the correction so future sessions do not repeat the mistake.

The preferred workflow is:

`memory_search → memory_graph when structural context is needed → targeted file reads → implementation → record durable knowledge`

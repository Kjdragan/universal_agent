# Codebase Recommendations and Cleanup Agenda

**Date:** 2026-04-16  
**Status:** Pending Review / Action Required

Based on an ad-hoc heuristic audit and cross-referencing architectural rules in `AGENTS.md` and `project-variables-and-secrets.md`, the following three P0/P1 issues have been identified as high-value cleanup targets.

---

## 1. Direct `os.getenv` Secret Retrieval (Rule Violation)
**Risk Level:** High (Security/Compliance)  
**Context:** Repository rules mandate standardizing all secret fetching through the programmatic Infisical Secret Service pipeline (`project-variables-and-secrets.md`). 

However, there are still dozens of locations specifically calling `os.getenv()` natively for critical tokens, bypassing internal credential wrappers. Examples include:
- `COMPOSIO_WEBHOOK_SECRET`
- `DISCORD_BOT_TOKEN`
- `UA_INTERNAL_API_TOKEN`
- `NOTEBOOKLM_AUTH_COOKIE_HEADER`

**The Issue:** Running under `infisical run --env=production` technically injects these directly into the process environment allowing `os.getenv` to capture them, but this fundamentally violates the canonical design pattern. It makes the application brittle by allowing developers to silently slip back into bad `.env` habits locally.
**Recommendation:** Standardizing all of these through a central `secrets` fetcher library or strict `InfisicalClient` wrapper to natively enforce the single source of truth.

---

## 2. Blocking I/O in Async Contexts
**Risk Level:** Medium (Performance/Concurrency)  
**Context:** The repository relies heavily on SQLite3 for tasks, metadata, memory, and orchestration. `AGENTS.md` has a very strict mandate against executing blocking DB operations during an `async` event loop.

**The Issue:** Because Python's native `sqlite3` driver is strictly synchronous, any direct database calls inside `gateway_server.py` FastAPI routes or nested inside `awaitable` loops without proper thread offloading (via `run_in_executor`) or an async wrapper (like `aiosqlite`) will critically choke network concurrency under load.
**Recommendation:** Perform a full audit of where DB execution overlaps with `async def` routing. Isolate these blocking DB calls into offloaded IO thread executors or migrate the internal query buses to `aiosqlite`.

---

## 3. Orphaned Artifacts & Decommissioned Sub-routines
**Risk Level:** Low (Technical Debt)  
**Context:** Given recent architectural migrations establishing VPS-only ingestors and durable orchestrated workspaces, several remnant baseline files actively exist in the root module space.

**The Issue:** Files like `desktop_transcript_worker.py` and old dashboard baseline layouts are technically "dead code." Keeping them clutters the deployment structure and distracts LLM context windows.
**Recommendation:** Cody's overnight cron (1:30 AM scheduled trigger) now runs exclusively to prune this "low-hanging fruit." We should review the generated PRs from Cody to see if the autonomous agent successfully identifies and strips out this dead code natively. We can manually step in for deeper purges later if required.

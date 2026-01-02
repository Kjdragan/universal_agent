# Phase 2 Progress Log (Continuation)

**Date:** 2026-01-02

1) Tightened resume behavior and Ctrl-C reliability.
   - Added SIGINT handler to always save interrupt checkpoints.
   - Added fallback to last step_id in DB if current_step_id missing.
   - Updated: `src/universal_agent/main.py`

2) Filtered research corpus pipeline refined.
   - Relaxed filter thresholds to keep more files.
   - Explicitly mark filtered-only usage and dropped files in `research_overview.md`.
   - Updated: `src/mcp_server.py`

3) Report sub-agent prompt unified to filtered corpus only.
   - Enforces `finalize_research` + filtered corpus reads.
   - Prevents raw `search_results/crawl_*.md` reads.
   - Updated: `src/universal_agent/main.py`

4) MCP server syntax fix and stability.
   - Fixed indentation error in `_crawl_core` async helper.
   - Removed duplicate import.
   - Updated: `src/mcp_server.py`

5) Durable job demo prompt updated to natural language.
   - Ensures normal complex-query decomposition path.
   - Updated: `src/universal_agent/durable_demo.json`

6) Added project context summary for handoff.
   - Updated: `Project_Documentation/000_CURRENT_CONTEXT.md`

Known Issues / Next Steps:
- Resume currently loads checkpoint but does not auto-continue; requires manual prompt entry after resume.
- Multiple local-toolkit trace IDs per run window; correlation still via time window.
- Consider adding SIGTERM + atexit checkpoint save for additional safety.
- Decide whether to auto-replay last prompt on resume or keep manual confirmation.

# MCP Toolkits Inventory Report (In‑Process vs Out‑of‑Process)

**Date:** 2026-02-02

## Purpose
Provide a clear inventory of all MCP toolkits used by the Universal Agent system, grouped by **in‑process** vs **out‑of‑process** execution. This is intended to help reduce confusion and identify toolkits that may be unused or overlapping.

---

## 1) In‑Process MCP Toolkits (SDK‑embedded)
These run **inside the agent process** via `create_sdk_mcp_server` and do not launch separate subprocesses.

### A) `internal` MCP server
**Defined in:**
- `src/universal_agent/agent_setup.py` → `_build_mcp_servers()`
- `src/universal_agent/main.py` → `setup_session()`

**Type:** `create_sdk_mcp_server(...)`

**Tools exposed (current):**
- `run_research_pipeline_wrapper`
- `run_research_phase_wrapper`
- `crawl_parallel_wrapper`
- `run_report_generation_wrapper`
- `generate_outline_wrapper`
- `draft_report_parallel_wrapper`
- `cleanup_report_wrapper`
- `compile_report_wrapper`
- `upload_to_composio_wrapper`
- `ua_memory_get` (if memory enabled)

**Implementation sources:**
- Wrappers in `src/universal_agent/tools/research_bridge.py`
- Upload wrapper in `src/universal_agent/tools/local_toolkit_bridge.py`

**Notes:**
- This internal MCP is meant to guarantee availability of key research/report tools regardless of external MCP availability.

---

## 2) Out‑of‑Process MCP Toolkits (Subprocess / Remote)
These run **outside the agent process** and connect via stdio or HTTP.

### A) `local_toolkit` MCP server (stdio)
**Defined in:**
- `src/universal_agent/agent_setup.py` → `_build_mcp_servers()`
- `src/universal_agent/main.py` → `setup_session()`

**Type:** stdio subprocess
**Command:** `python -u src/mcp_server.py`

**Common tools (hardcoded list):**
- `mcp__local_toolkit__crawl_parallel`
- `mcp__local_toolkit__finalize_research`
- `mcp__local_toolkit__list_directory`
- `mcp__local_toolkit__upload_to_composio`
- `mcp__local_toolkit__append_to_file`
- `mcp__local_toolkit__generate_image`

**Inventory source:**
- `src/universal_agent/utils/composio_discovery.py` (LOCAL_MCP_TOOLS)

**Notes:**
- This is the **primary local tool server**. If it fails to register, you will see “No such tool available”.

---

### B) `composio` MCP server (HTTP)
**Defined in:**
- `src/universal_agent/agent_setup.py` → `_build_mcp_servers()`
- `src/universal_agent/main.py` → `setup_session()`

**Type:** HTTP MCP
**URL:** `session.mcp.url` (Composio tool router)

**Purpose:**
- Exposes all Composio toolkits (Gmail, search, etc.) via MCP calls.

---

### C) External MCP servers (stdio)
Configured in `setup_session()` and (partially) in `agent_setup.py`.

| Name | Type | Command | Purpose |
|------|------|---------|---------|
| `edgartools` | stdio | `python -m edgar.ai` | SEC/EDGAR research |
| `video_audio` | stdio | `python external_mcps/video-audio-mcp/server.py` | Video/audio editing |
| `youtube` | stdio | `python -m mcp_youtube` | YouTube/video download |
| `zai_vision` | stdio (npx) | `npx -y @z_ai/mcp-server` | Z.AI vision model |
| `taskwarrior` | stdio | `python src/universal_agent/mcp_server_taskwarrior.py` | Taskwarrior integration |
| `telegram` | stdio | `python src/universal_agent/mcp_server_telegram.py` | Telegram bot tools |

**Notes:**
- In `agent_setup.py`, some external servers (`edgartools`, `video_audio`, `youtube`) are commented out, while `taskwarrior` and `telegram` are enabled.
- In `main.py`, all of the above are present in the `mcp_servers` block.

---

## 3) Other MCP‑like Components

### A) `interview` MCP server (URW)
**Defined in:** `src/universal_agent/urw/interview.py`
**Type:** `create_sdk_mcp_server(...)`
**Scope:** URW harness only

---

## 4) Where MCP Tool Registry is Reported

- CLI startup prints tool visibility:
  - “Active Local MCP Tools” (from `composio_discovery.get_local_tools()`)
  - “External MCP Servers” (hardcoded list in `main.py`)

- Gateway startup (via `gateway.log`) **does not print** the same explicit tool list, which makes parity debugging harder.

---

## 5) Potential Confusion / Overlap Areas

1. **`local_toolkit` vs `internal` MCP**
   - Both can offer overlapping functionality (e.g., research pipeline wrappers vs native `finalize_research`).
   - The internal MCP is in‑process; local_toolkit is subprocess.

2. **Multiple places define MCP servers**
   - `agent_setup.py` and `main.py` both define `mcp_servers` for different entrypoints.
   - They should remain aligned to prevent drift.

3. **Hardcoded local tool list**
   - `LOCAL_MCP_TOOLS` is hardcoded and may become stale if `mcp_server.py` changes.

4. **External MCPs configured but unused**
   - Some are commented out in `agent_setup.py` but active in `main.py`.
   - This can lead to inconsistent environments across entrypoints.

---

## 6) Recommendations (No Changes Implemented)

- Add a single authoritative MCP registry listing and make both CLI and Gateway log it on startup.
- Eliminate or consolidate unused external MCPs if they’re no longer needed.
- Make `LOCAL_MCP_TOOLS` auto‑generated from `mcp_server.py` to avoid staleness.
- Ensure `agent_setup.py` and `main.py` use the same MCP server list for parity.

---

## Appendix: Key Files
- `src/mcp_server.py` — Local tool server implementation
- `src/universal_agent/tools/research_bridge.py` — In‑process research wrappers
- `src/universal_agent/tools/local_toolkit_bridge.py` — In‑process upload wrapper
- `src/universal_agent/agent_setup.py` — MCP server config for API/URW
- `src/universal_agent/main.py` — MCP server config for CLI
- `src/universal_agent/utils/composio_discovery.py` — Local tool inventory
- `src/universal_agent/mcp_server_taskwarrior.py` — Taskwarrior MCP
- `src/universal_agent/mcp_server_telegram.py` — Telegram MCP


# 101 — VP Agent Identity, Prompt Architecture, and Mission Briefing Source of Truth

**Last verified against source code:** 2026-03-21
**Status:** Canonical — this document is the authoritative reference for VP agent identity and prompt architecture.

---

## 1. Overview

Universal Agent runs two VP (Virtual Primary) worker agents that execute delegated work on behalf of Simone (the coordinator agent). Each VP has its own **identity** (soul), **streamlined system prompt**, and can receive **mission-specific briefings** from dispatchers.

For the mission lifecycle, delegation flow, and infrastructure details, see [03_VP_Workers_And_Delegation](../01_Architecture/03_VP_Workers_And_Delegation.md).

---

## 2. VP Agent Roster

| VP ID | Agent Name | Soul File | Client Kind | Primary Scope |
|-------|-----------|-----------|-------------|---------------|
| `vp.coder.primary` | **CODIE** | `CODIE_SOUL.md` | `claude_code` | Code implementation, refactoring, doc maintenance |
| `vp.general.primary` | **ATLAS** | `ATLAS_SOUL.md` | `claude_generalist` | Research, analysis, content creation, system ops |

Both agents run as separate `systemd` services with their own Claude Agent SDK session, using the **Opus** model.

---

## 3. VP Identity Architecture

### 3.1 Identity Is Not Simone

VP workers have their own souls — they are **not** Simone. They do not orchestrate the UA project, manage cron schedules, or handle email routing. They receive mission objectives and execute them autonomously.

### 3.2 Soul Seeding

At mission start, `worker_loop.py` copies the VP's soul file from `prompt_assets/` into the mission workspace as `SOUL.md`. The existing `_load_soul_context()` in `agent_setup.py` picks up the workspace `SOUL.md` (priority 1) — meaning the VP gets its own identity instead of Simone's.

```
prompt_assets/CODIE_SOUL.md → {workspace}/SOUL.md  (for coder VP missions)
prompt_assets/ATLAS_SOUL.md → {workspace}/SOUL.md  (for general VP missions)
```

### 3.3 Soul File Location

Soul files live at:
```
src/universal_agent/prompt_assets/
├── SOUL.md              # Simone (coordinator)
├── CODIE_SOUL.md        # CODIE (VP Coder)
└── ATLAS_SOUL.md        # ATLAS (VP General)
```

> [!IMPORTANT]
> `GENERALIST_VP_SOUL.md` was removed on 2026-03-21. `ATLAS_SOUL.md` is the replacement.

---

## 4. VP Prompt Architecture

### 4.1 Streamlined VP System Prompt

VP workers use `build_vp_system_prompt()` (in `prompt_builder.py`) instead of the full `build_system_prompt()` used by Simone. This reduces the system prompt from ~90K chars (~22K tokens) to ~20K chars (~5K tokens).

**What the VP prompt keeps:**
- Soul / identity (CODIE or ATLAS)
- Mission briefing (if provided)
- Workspace key files
- Temporal context
- Recovery handoff
- Capabilities registry
- Memory context
- Simplified architecture & tool usage
- Capability domains (condensed)
- ZAI vision tools
- Autonomous behavior
- Infisical secrets (condensed)
- Memory management (condensed)
- Skills

**What the VP prompt strips (Simone-specific):**
- Coordinator role identity (§2)
- Showcase guidance (§8)
- Search hygiene details (§9)
- Data flow policy (§10)
- Workbench restrictions (§11)
- Artifact output policy (§12)
- Email routing (§13)
- Todoist task queue execution (§14b)
- Report delegation (§15)
- System configuration delegation (§16)

### 4.2 VP Detection

`agent_setup.py` detects a VP worker by checking the loaded soul content for markers:
```python
is_vp_worker = any(
    marker in (self._soul_context or "")
    for marker in ("CODIE", "ATLAS", "VP Coder Agent", "VP General Agent")
)
```

When detected, it uses `build_vp_system_prompt()` and logs prompt size for observability.

### 4.3 Prompt Size Logging

Both VP and Simone paths log prompt size:
```
📦 VP system prompt built (19483 chars, ~4870 tokens)
📦 System prompt built (89214 chars, ~22303 tokens)
```

---

## 5. Mission Briefing Injection

### 5.1 How It Works

Dispatchers can include a `system_prompt_injection` field in the mission payload. At mission start, `worker_loop.py` extracts this field and writes it to `{workspace}/MISSION_BRIEFING.md`. The VP prompt builder loads this file and includes it as a "MISSION BRIEFING" section between the soul and capabilities.

### 5.2 Payload Schema

```json
{
  "vp_id": "vp.coder.primary",
  "objective": "Fix documentation drift issues...",
  "mission_type": "doc-maintenance",
  "execution_mode": "sdk",
  "system_prompt_injection": "## Documentation Maintenance Mission\n\nYou are operating as a Documentation Maintenance Agent..."
}
```

### 5.3 Current Mission Briefings

| Dispatcher | VP Target | Briefing Content |
|-----------|-----------|------------------|
| `doc_maintenance_agent.py` | `vp.coder.primary` | Verification rules, commit discipline, drift fix methodology |

---

## 6. Sub-Agent Capability

Both VP workers are **full Claude Code agents** with sub-agent capability enabled (`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`). They can delegate to specialist sub-agents:

- `research-specialist` — deep search pipelines
- `report-writer` — HTML/PDF report generation
- `code-writer` — parallel code changes
- `image-expert`, `video-creation-expert` — media

### 6.1 When to Delegate vs Self-Execute

VP agents should delegate when:
- Parallel work across different domains would speed up the mission
- A specialist has domain-specific tools (e.g., image generation)

VP agents should self-execute when:
- Work is sequential and file-by-file
- The overhead of delegation exceeds the benefit

---

## 7. VP Memory

Each VP workspace has its own memory scaffold (`MEMORY.md` + `memory/` directory), isolated from Simone's memory space. Memory persists across missions within the same workspace root.

---

## 8. Mission Execution Pattern

Both VP agents follow a structured decomposition pattern for non-trivial missions:

1. **Analyze** the objective — identify concrete deliverables
2. **Create PLAN.md** — numbered checklist of steps
3. **Execute sequentially** — update PLAN.md as each step completes
4. **Verify** each step before marking done
5. **Handle blocks** — document and skip to next parallelizable step
6. **Summarize** — write completion summary and produce deliverables

---

## 9. Key Implementation Files

| File | Purpose |
|------|---------|
| `prompt_assets/CODIE_SOUL.md` | CODIE agent identity |
| `prompt_assets/ATLAS_SOUL.md` | ATLAS agent identity |
| `vp/profiles.py` | VP profile definitions (soul_file, client_kind) |
| `vp/worker_loop.py` | Soul seeding, mission briefing, claim-execute loop |
| `prompt_builder.py` | `build_vp_system_prompt()` |
| `agent_setup.py` | VP detection and prompt routing |
| `vp/clients/claude_code_client.py` | CODIE SDK client |
| `vp/clients/claude_generalist_client.py` | ATLAS SDK client |
| `scripts/doc_maintenance_agent.py` | Example dispatcher with mission briefing |

---

## 10. Related Documents

- [VP Workers & Delegation Architecture](../01_Architecture/03_VP_Workers_And_Delegation.md) — mission lifecycle, routing, factory heartbeat
- [Factory Delegation, Heartbeat & Registry](88_Factory_Delegation_Heartbeat_And_Registry_Source_Of_Truth_2026-03-06.md) — cross-machine delegation infrastructure
- [Documentation Drift Maintenance Pipeline](99_Documentation_Drift_Maintenance_Pipeline.md) — primary consumer of CODIE VP missions
- [Agent Skills Directory](98_Agent_Skills_Directory.md) — includes `vp-orchestration` skill

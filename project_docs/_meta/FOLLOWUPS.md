# Documentation Rebuild — Follow-ups

Open items surfaced during the code-first reconstruction (Phases 1–2). None block the rebuilt
corpus; each is a decision or a scoped addition for after this PR merges.

## Coverage gaps — RESOLVED (PR-C)

1. ✅ **SDK lifecycle hooks — DONE.** Added `02_execution_core/06_sdk_lifecycle_hooks_and_guardrails.md`
   (code-first from `hooks.py` + `guardrails/workspace_guard.py` + `constants.py`): PreToolUse/PostToolUse
   gating, `DISALLOWED_TOOLS`, workspace guard, heartbeat write allowlist, subagent detection, TaskStop
   rejection, event emission. Distinct from `05_hook_system` (webhook ingress).

1b. ✅ **`/btw` — DONE (clarified, no dedicated doc needed).** It is UA's own minor in-memory sidebar-session
   command (`session_hub.py`), **unrelated to Claude Code's native `/btw`**. Documented where it lives
   (`05_channels/05_web_ui_communication.md`); the `agents-simone` note now states the distinction. No
   separate doc — it's a one-line feature.

2. ✅ **Mission Control — DONE (scope expanded).** `04_intelligence/11_mission_control_intelligence.md` now
   covers the full tiered stack (sweeper, dedicated DB, tier-0 tiles, tier-1 cards, tier-2 Chief-of-Staff,
   event titles, dashboard endpoints) — not just the `supervisors/` snapshots. Flagged inline: tiered-stack
   endpoints lack the ops-auth/HQ gate the supervisor endpoints enforce (`> [VERIFY]`).

## Dispose / revive decisions (operator)

3. **Agent College is vestigial.** Not started by the production VPS deploy (only Railway `start.sh` launches
   its FastAPI wrapper); `Scribe`, `setup_agent_college`, `runner.py` have no in-repo callers. The doc
   (`03_agents/06_agent_college.md`) describes it honestly as vestigial. **Decide:** dispose (delete code +
   doc) or revive.

4. **Discord MCP bridge** (`mcp_bridge.py`) is registered in `.mcp.json` but has no in-repo consumer/launcher.
   Registered-but-unwired. **Decide:** wire or remove.

## Engine / nightly transport — RESOLVED (ZAI)

5. **LLM accuracy auditor — DONE (routed through ZAI/GLM).** `scripts/doc_accuracy_sweep.py` is the
   LLM-judge step: it takes the oldest-verified batch (`doc_audit.build_accuracy_batch`), reads each doc +
   the code its `code_paths` claims to document, and judges drift via the **ZAI proxy / GLM models**
   (`resolve_sonnet` → `glm-5-turbo`, Anthropic-emulation client pointed at `ANTHROPIC_BASE_URL`, creds from
   Infisical — no Anthropic spend, no new secret). Wired as the `accuracy-sweep` job in `doc-nightly.yml`
   (opens a GH issue on drift; never fails the run). Verified locally against a real ZAI call.

   Remaining refinements (minor): (a) **rotation** — `last_verified` is not auto-stamped yet, so the sweep
   re-audits the same oldest-N until a docfix PR bumps dates; true rotation arrives once stamping is wired.
   (b) **GHA env confirmation** — run one manual `workflow_dispatch` of "Nightly Documentation Health" to
   confirm Infisical→ZAI bootstrap resolves on the GHA runner (it's proven locally; the job skips gracefully
   if creds don't load).

## Cutover items deliberately deferred (to keep this PR low-risk)

6. **Physical rename `docs/` → `docs_archive/` deferred.** ~5 production Python scripts reference
   `docs/` paths at runtime (`openclaw_release_scanner.py`, `scheduling_v2_soak.py`,
   `csi_vault_cleanup_grounding_hallucinations.py`, the legacy drift scripts). A rename would break them.
   This PR instead **search-excludes** `docs/` via `.rgignore` + an ARCHIVED banner (achieves "out of
   flow / not in searches"). Do the physical rename after remapping those code refs.

7. **Legacy drift pipeline not yet deleted.** `doc_drift_auditor.py`, `doc_maintenance_agent.py`,
   `nightly-doc-drift-audit.yml` are superseded by `scripts/doc_audit.py` + `.github/workflows/doc-audit.yml`
   + `doc-nightly.yml`, but they have tendrils (`update_cron.py`, `doc_drift_health_check.py`, a dormancy
   guard test, `test_todo_dispatch_executing_sessions.py`). Removing them cleanly (without a red CI) is a
   focused follow-up. They are left in place (dormant) for now.

## Stale gotcha-inventory entries corrected during reconstruction (already handled, logged for traceability)

- Legacy claim `logfire.instrument_anthropic()` — not in code (only mcp/httpx/sqlite3). Corrected in `arch-events-tracing`.
- Legacy `tools.py` with `lcm_grep`/`lcm_expand` — does not exist. Omitted from `intel-lossless`.
- Legacy `composio_youtube_transform.py` — does not exist (only `manual_youtube`). Corrected in `chan-webhooks`.
- Legacy `youtube_playlist_watcher_state.json` — does not exist (actual state is a `processed_videos` SQLite table). Flagged in `intel-youtube`.
- LLM Wiki `resolve_vault_path` "ignores vault_kind" bug — **fixed in current code**; not carried as a live gotcha.

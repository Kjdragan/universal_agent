# 130 — Production Verification Rules (Extended Reference)

**Last updated:** 2026-05-13 (extracted from CLAUDE.md trim)

This document is the long-form reference for the production-verification discipline summarized in `CLAUDE.md` § "Production Verification Rules — DO NOT SKIP" and the anti-pattern catalog summarized in `CLAUDE.md` § "Pre-Implementation Reading — DO NOT SKIP". Read CLAUDE.md first; this doc holds the postmortem context, the Ship-then-Verify cadence (Rules A–D), and the full anti-patterns list.

---

## Why this discipline exists (postmortem context)

Between 2026-04-15 and 2026-05-06 the v2 ClaudeDevs intel rebuild shipped 17 PRs with 439 passing unit tests and a "shakedown log" that declared the system green. After all of that, an operator pulled production state and found:

1. `/opt/ua_demos/` had only the smoke workspace — Phase 2/3 had never executed end-to-end despite Simone (the principal who owns Phase 2) being live and the `cody-scaffold-builder` skill being deployed. Reason: `memory/HEARTBEAT.md` never directed her to scan new vault entities.
2. The linked-doc fetcher was silently dropping the actual documentation downstream phases were supposed to consume, because an LLM judge gate was tagging official-handle links as "promotional".
3. Two consecutive sessions of "everything looks green" had concealed the gap.

439 unit tests caught none of these because each test stubbed the boundary it didn't own. The architecture diagram is not the system. Skill files on disk are not the same as the heartbeat directives that invoke them. Mocked end-to-end loops are not the same as production end-to-end runs.

Earlier, on 2026-05-06, an agent was minutes away from shipping ~50 lines of new orchestration logic into `memory/HEARTBEAT.md` (claim tasks, route to Simone, enforce concurrency cap, reset orphaned in-progress tasks) before the operator stopped them and asked "doesn't Task Hub already do this?" It did. Every line of the proposed addition was redundant with `services/dispatch_service.py` + `task_hub.py`, which the agent had not read. The actual missing piece was a 30-line *producer* change — the consumer side was already wired through `dispatch_sweep` + `route_all_to_simone`. Same class of error.

The rules below exist so neither shape of failure recurs.

---

## Ship-then-Verify cadence — Rules A through D

These extend the eight numbered rules in CLAUDE.md. Apply when work touches gateway endpoints, DB queries, scoring/ranking logic, or service-layer code AND end-to-end browser confirmation is desired.

### Rule A — Verify the right artifact

Before any browser-based or HTTP-based end-to-end check against `app.clearspringcg.com` or `127.0.0.1:8002`, the agent MUST first hit `GET /api/v1/version` (no auth) and log the returned `commit_sha`, `branch`, and `process_started_at`.

If the live SHA does not contain the change being verified, **STOP** — do not run the browser pass, do not declare anything verified. Acceptable response: "live SHA is X, my change is on SHA Y which has not deployed yet — verification deferred until ship completes." Unacceptable: burning a 5-minute browser session against stale code and reporting its findings as if they reflect the new behavior.

### Rule B — Backend logic vs. UI rendering have different verification paths

- **Backend logic changes** (DB queries, scoring/ranking, route handlers, service-layer functions) → authoritative verification is direct Python invocation in dev:
  ```
  PYTHONPATH=src uv run python -c "from universal_agent.services.X import Y; print(Y(...))"
  ```
  This is conclusive because it exercises the new code in-process. Browser verification of backend logic against production is only meaningful AFTER ship.
- **UI rendering** (drawer layouts, hover states, optimistic updates, CSS, component composition) → verified post-deploy against `app.clearspringcg.com` via the agent-browser sub-agent. Pre-deploy local browser checks are acceptable when the change is `web-ui/` only (Next.js hot reload).
- **Anti-pattern:** dispatching a browser agent against production to "verify the dedup I just wrote" before the dedup is on `main` and deployed. The browser is looking at the old code; its findings about the new behavior are noise.

### Rule C — Ship-then-verify cadence for backend-touching work

1. Land code on `feature/latest2` with local Python verification of the new behavior (Rule B).
2. Operator runs `/ship`.
3. Wait for GH Actions deploy to go green AND `GET /api/v1/version` on production returns the new SHA.
4. Then dispatch the browser agent against `app.clearspringcg.com`.
5. Browser agent's first action is Rule A: hit `/api/v1/version`, log SHA, confirm it includes the change. If not, abort.

### Rule D — Deploy-time service restart is already part of the workflow (verified, not a gap)

`.github/workflows/deploy.yml` runs `sudo systemctl restart universal-agent-gateway universal-agent-api universal-agent-webui ...` after the rsync + venv sync. Gateway picks up Python changes on the next deploy by construction. There is NO separate "remember to restart the gateway" step. If a backend change is on `main` and the deploy workflow is green, the new code is live. Confirm via Rule A's `/api/v1/version` check, not by guessing.

---

## Anti-patterns shipped (or nearly shipped) that must not recur

These belong in muscle memory. Each is a real incident.

1. **Writing "Simone, check Task Hub for source_kind X" in HEARTBEAT.md.** Task Hub already routes every claimed task to Simone. The missing piece is always *producing* the task.
2. **Writing "concurrency cap of N" in a directive.** `claim_next_dispatch_tasks(limit=N)` is the cap.
3. **Inventing a fallback artifact path.** `artifacts.resolve_artifacts_dir` is canonical.
4. **Adding catch-up / backfill logic per-cron.** `_register_system_cron_job` already handles it.
5. **Adding orphan-reset directives.** The stale-task policy in Task Hub is the right knob.
6. **Inlining a literal token into `.mcp.json` to satisfy "Claude Code Doctor says MCP needs `<TOKEN>`."** Doctor is correctly diagnosing that the env var is unset in the parent process; the fix is to launch `claude` via `scripts/claude_with_mcp_env.sh` (or its alias), which runs the canonical Infisical bootstrap. See `docs/operations/2026-05-08_hostinger_token_remediation.md` for the cautionary tale.
7. **Wrapping `claude` with `infisical run --env=… -- claude` (the CLI).** The CLI requires its own interactive `infisical login` session that doesn't exist on the VPS for user `ua`; falls into a tty-only login prompt and fails non-tty. Use the Python SDK path (`initialize_runtime_secrets()`) instead — that's what `scripts/claude_with_mcp_env.sh` does.
8. **Letting a background tool (Doctor / IDE plugin) auto-resolve `${VAR}` in `.mcp.json` to a literal value and then committing the diff.** If `git status` ever shows `.mcp.json` modified with a `${VAR}` → literal substitution, `git checkout -- .mcp.json` immediately. Never commit the substitution.

---

## See also

- `CLAUDE.md` § "Pre-Implementation Reading — DO NOT SKIP" — the canonical-service-module table and 30-second pre-flight grep.
- `CLAUDE.md` § "Production Verification Rules — DO NOT SKIP" — Rules 1–8 (skill-deployed-≠-invoked, real-artifact, canonical-resolver, no-conflation, prove-the-claim, end-of-PR smoke, sandbox honesty, branch-vs-deploy honesty).
- `docs/03_Operations/129_Task_Hub_Observability_Protocol.md` — the six-rule observability protocol for any new async unit of work.
- `docs/deployment/ci_cd_pipeline.md` — deploy workflow internals.

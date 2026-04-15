# Debugging Lessons Living Document

**Started:** 2026-03-23  
**Purpose:** Preserve high-value debugging lessons from complex incidents so
future investigations start from stronger operational instincts instead of
repeating the same mistakes.

## How To Use This Document

Add new entries when an incident teaches a lesson that is broader than the
single bug that triggered it.

Good entries usually capture:

1. the false assumption that wasted time
2. the verification step that broke the false assumption
3. the reusable lesson that applies to future incidents
4. any code or operational pattern that should become standard

This is a living operational reference, not a single-incident report.

---

## 2026-04-05: Dashboard Return Crash Was A Browser-State Incident, Not Just A Hydration Bug

### Incident Summary

The production `/dashboard` showed the generic Next.js message "Application error: a client-side exception has occurred" for one real authenticated browser profile during navigation back to the dashboard from `/dashboard/todolist`.

The investigation had three phases:

1. **Initial code review found real hydration hazards**:
   - client-only state initializers in the dashboard and To Do pages
   - render-phase mutation inside the embedded Agent Flow widget
   - navigation-sensitive state derived from `localStorage`
2. **A convincing but wrong intermediate theory** was that production had not received the UI fix. This was disproven by checking the deployed `HEAD` SHA directly on VPS: production was already running the same fix SHA as `develop`.
3. **The decisive production-only signal** was browser-local state:
   - the crashing browser profile had a large `localStorage["ua.agent-flow-spotlight.v1"]` payload containing archived Agent Flow session timelines
   - a clean browser session did not reproduce the crash
   - clearing that key stopped the crash in the affected browser
   - the permanent code fix changed spotlight persistence so only lightweight control state survives rehydrate

The durable lesson is that the earlier hydration-only explanation was incomplete. There were legitimate render/hydration hardening fixes, but the remaining production incident was driven by persisted browser state for an always-mounted widget.

### Lesson 1: When One Browser Profile Fails And Another Does Not, Inspect Browser State First

Reusable rule:

1. compare the real authenticated user browser with a clean browser profile or automation run
2. inspect `localStorage` and `sessionStorage` before assuming the fault is server-side
3. clear the smallest plausible key first rather than wiping all site data immediately

For this incident, the `ua.agent-flow-spotlight.v1` key was the differentiator.

### Lesson 2: Do Not Persist Large Archive Graphs For Always-Mounted Widgets

Reusable rule:

- persist lightweight control state only
- do not persist large event timelines, archive graphs, or replay payloads into browser storage for widgets that mount on high-traffic routes
- when legacy persisted shape is already in the field, add an explicit migration that discards heavy historical payloads during rehydrate

The permanent fix here was to trim Agent Flow spotlight persistence to mode/selection/replay control fields only.

### Lesson 3: Verify Production Releases By Deployed SHA, Not Branch Assumption

Reusable rule:

1. check the deployed checkout `HEAD` SHA first
2. only then infer whether production is missing a fix
3. do not use local branch position or a checkout label like `develop` as the primary proof of what is deployed

In this incident, "production must still be on an older branch" looked plausible and was wrong. The live VPS checkout already contained the fix chain; the remaining issue was browser state.

### Lesson 4: Fix The Render Path Anyway, But Do Not Stop There

Reusable rule:

- when a navigation crash is reported, still harden render-phase behavior:
  - remove client-only initializers from first render
  - move local-only state hydration into `useEffect`
  - eliminate render-phase ref mutation
- but if the browser-specific crash remains, continue until you have explained the browser-specific difference

---

## 2026-03-31: Logfire Import Failure During Production Restart

### Incident Summary

Production services entered restart loops during deploy because startup hit this
path:

1. `import logfire`
2. `import opentelemetry.context`
3. `StopIteration` while OpenTelemetry tried to resolve its runtime context entry point

The same damaged environment also later proved to be missing core runtime
packages such as `uvicorn` and `pydantic-settings`, which confirmed the real
problem was the integrity of the deployed `.venv`, not merely one tracing
toggle.

### Lesson 1: Optional Observability Must Never Be A Hard Startup Dependency

The correct runtime behavior is fail-open.

Reusable rule:

1. keep the application available even if tracing libraries are broken
2. isolate observability bootstrap from the main startup path
3. prefer an explicit degraded-mode signal over a crash loop

In this incident, package bootstrap moved Logfire behind a fail-open no-op stub
so gateway/API/telegram/VP workers could still boot.

### Lesson 2: Deploy Success Must Prove Real Imports In The Actual Service Venv

A runtime safety stub is not a deployment success condition.

Reusable rule:

- run import validation inside the exact `.venv` that systemd services will use

The deploy contract now validates, in order:

1. runtime bootstrap identity and secret access
2. real OpenTelemetry + Logfire imports
3. service entrypoint imports

If any of those fail after the first `uv sync`, deploy deletes `.venv`, does
one clean rebuild, reruns validation, and aborts before restart if the runtime
is still broken.

### Lesson 3: A Broken Tracing Environment Can Coexist With Partial Observability

Seeing spans in Logfire does not prove every deployed service is using the real
SDK path.

Reusable rule:

- distinguish "the project is receiving some traces" from "this specific
  service process is running with real tracing"

That is why health endpoints now expose an explicit `observability.mode`
(`real`, `stub`, or `disabled`) instead of treating observability as invisible
background state.

### Lesson 4: Rebuild The Environment, Do Not Paper Over Missing Runtime Packages

Once `uvicorn` and `pydantic-settings` were missing from the production venv,
the right fix was to rebuild `.venv` cleanly, not to keep adding package-by-
package repairs.

Reusable rule:

- when the deployed virtualenv shows multiple independent import failures,
  assume environment corruption first and prefer one clean rebuild over manual
  incremental patching

## 2026-03-23: Tutorial Pipeline Proxy Failure

### Incident Summary

The YouTube tutorial ingest path on the production VPS returned:

- `error = "proxy_not_configured"`
- `proxy_mode = "disabled"`

The final code fix was in `src/universal_agent/execution_engine.py`: parent
process env sanitization was stripping Infisical-loaded proxy secrets from the
live gateway process after child-process setup.

### Lesson 1: Verify The Exact Host Before Building The Story

The incident spent meaningful time on the theory that the VPS was stuck in
`local_workstation`. That theory was built from curl results against
`100.95.187.38`, which was Kevin's desktop, not the VPS.

Reusable rule:

1. identify the exact target host first
2. verify its hostname or Tailscale identity
3. only then trust health or behavior observations from that machine

When multiple machines expose the same port and service shape, "the endpoint
answered" is not enough evidence.

### Lesson 2: Fresh-Process Success Does Not Prove Live-Service Health

A fresh one-off Python process on the VPS could bootstrap proxy secrets
correctly while the long-running gateway process still behaved as if those
secrets were missing.

Reusable rule:

- always compare fresh-process behavior with live-service behavior

If they disagree, the problem is often:

1. stale process state
2. post-bootstrap mutation
3. long-lived cache or singleton behavior
4. service-local configuration drift

### Lesson 3: Bootstrap Is A Moment, Not A Guarantee

Runtime bootstrap loads secrets and establishes initial process state. That does
not guarantee later code will preserve that state.

In this incident:

1. Infisical bootstrap succeeded
2. proxy secrets existed
3. later runtime code mutated `os.environ`
4. downstream code then misclassified the system as "proxy not configured"

Reusable rule:

- if downstream code reads from `os.environ`, audit every later code path that
  mutates `os.environ`

### Lesson 4: Child-Process Compatibility Fixes Must Not Damage The Parent

`sanitize_env_for_subprocess()` existed for a legitimate reason: preventing
`E2BIG` when spawning the Claude subprocess.

The bug was not the existence of env sanitization. The bug was letting it alter
the long-running gateway process.

Reusable rule:

1. scope compatibility workarounds to the narrowest possible boundary
2. snapshot and restore parent state around child-process setup
3. never use process-global state as a convenient scratch buffer in a service

### Lesson 5: `/proc/<pid>/environ` Is Useful But Not Final Truth

For this incident, `/proc/<pid>/environ` was not decisive evidence about what
the application observed later at runtime.

Reusable rule:

- treat `/proc/<pid>/environ` as a useful signal, not as the sole source of
  truth for late runtime behavior

When the question is "what did the app see when it handled the request?", an
in-process diagnostic or end-to-end endpoint check is usually stronger evidence.

### Lesson 6: Temporary Diagnostics Are Fine If They Are Controlled

The incident was unblocked by adding a short-lived, ops-authenticated debug path
that exposed only the presence of proxy env variables, not their values.

Reusable rule:

Temporary production diagnostics are acceptable when they are:

1. tightly scoped
2. authenticated
3. non-secret-bearing
4. removed immediately after verification

### Lesson 7: Keep The False Leads In The Write-Up

The wrong-host theory and the deployment-profile theory were not the final
answer, but they still belong in the incident record.

Reusable rule:

- preserve the false leads that looked convincing and explain why they were
  wrong

That prevents the next debugger from reopening the same dead ends.

## Preferred Debugging Checklist For Similar Incidents

When a production service claims a secret-backed feature is "not configured":

1. verify the exact host identity first
2. compare live endpoint behavior with a fresh process on that same host
3. check whether the failing feature reads directly from `os.environ`
4. inspect post-bootstrap code for process-global env mutation
5. add minimal temporary diagnostics only if the existing signals are still
   ambiguous
6. remove those diagnostics immediately after confirmation

---

## 2026-03-28: Transcript Generation Duplication & Gateway Port Collisions

### Incident Summary 1: Run Transcript Iteration Duplication

The `transcript.md` log for runs progressively repeated "Iteration 1" over and over, dumping all historical tool calls cumulatively into a single block instead of creating progressive turns.

The underlying issue was tracking loops: the central Agent SDK engine starts each `process_turn()` CLI execution loop with a local counter initialized to `iteration = 1`. The `transcript_builder.py` assumed this `iteration` value uniquely mapped to a step, so when it compiled the trace log, it grabbed *every* tool call previously generated with `iteration = 1`, resulting in massive redundant output dumps.

### Lesson 1: Local Loop Variables Do Not Ensure Global Sequencing

When processing multi-turn, stateful traces over independent programmatic executions, local counters like `iteration` cannot uniquely map events.

Reusable rule:

- Always map events across a continuous trace back to universally unique boundary signatures (`step_id` or `trace_id`), rather than relying on local procedural loops.

### Incident Summary 2: Gateway Server Crashing on Restart (Address in Use)

During fast inner-loop development, terminating and restarting the `gateway_server.py` FastAPI service frequently caused it to crash on startup with `OSError: [Errno 98] Address already in use`, requiring manual intervention to wait out the `TIME_WAIT` TCP flush state.

### Lesson 2: Production Services Need Bonded Startup Retries

Generic application runners like `uvicorn.run()` die immediately if the socket binding fails. Fast, programmatic redeploys or restarts usually encounter kernel socket flush races.

Reusable rule:

1. Never boot the core entrypoint of a primary system service completely unprotected against socket race conditions.
2. Wrap the API runner inside a bounded, small-interval retry loop that explicitly catches `Errno 98 / Address already in use` to gracefully wait out the flush before aborting.

---

## 2026-03-25: Nightly Documentation Pipeline Audit

### How The Pipeline Works

The nightly doc maintenance pipeline has two stages triggered by the
`nightly-doc-drift-audit.yml` GitHub Actions workflow (runs at 08:17 UTC):

1. **Stage 1 — Drift Auditor** (GHA runner, deterministic Python):
   - Runs `doc_drift_auditor.py` which scans last 24h of git history
   - Produces `artifacts/doc-drift-reports/<YYYY-MM-DD>/drift_report.json`
   - Auto-creates and squash-merges a PR with the report into `develop`
   - Sets `exit_code` output: 0 = no issues, 1 = issues found

2. **Stage 2 — VP Mission Dispatch** (GHA → VPS via Tailscale SSH):
   - Only runs if Stage 1 found issues (`exit_code != 0`)
   - Copies drift report to VPS via SCP
   - Runs `doc_maintenance_agent.py` on VPS
   - This script dispatches VP missions to `vp.coder.primary` via gateway API
   - Missions are batched by severity (P0 → P1 → P2, max 15 issues/batch)

3. **VP Worker Loop** (VPS background service):
   - `worker_loop.py` polls the `vp_missions` SQLite table
   - Claims and executes missions via `ClaudeCodeClient` (SDK mode)
   - On completion, `_post_mission_push_pr_merge()` checks if the agent
     created a `docs/*` branch with commits and pushes + creates + merges a PR

### Audit Checklist

When investigating whether the nightly doc pipeline worked:

1. **Check GHA run status:**
   ```bash
   gh run list --workflow="nightly-doc-drift-audit.yml" --limit 3 \
     --json status,conclusion,createdAt,databaseId
   ```

2. **Check all steps completed (including Stage 2):**
   ```bash
   gh api repos/Kjdragan/universal_agent/actions/runs/<RUN_ID>/jobs \
     --jq '.jobs[] | {name, conclusion, steps: [.steps[] | {name, conclusion}]}'
   ```

3. **Read the Stage 2 dispatch log (VP missions dispatched):**
   ```bash
   gh api repos/Kjdragan/universal_agent/actions/jobs/<JOB_ID>/logs 2>&1 \
     | grep -iE "dispatch|mission|issues|batch|✅|❌"
   ```

4. **Check drift report content (what issues were found):**
   ```bash
   git show origin/chore/drift-report-<YYYY-MM-DD>:artifacts/doc-drift-reports/<YYYY-MM-DD>/drift_report.json \
     | python3 -c "import json,sys; r=json.load(sys.stdin); print(f'Issues: {r[\"total_issues\"]}'); [print(f'  {i[\"severity\"]} {i[\"category\"]}: {i[\"file\"]}') for i in r['issues']]"
   ```

5. **Check VP mission status via gateway API:**
   ```bash
   curl -s "http://uaonvps:8002/api/v1/ops/vp/missions?vp_id=vp.coder.primary&limit=5" \
     -H "Authorization: Bearer $TOKEN"
   ```
   Look for: `status`, `duration_seconds`, `result_ref`.

6. **Check if doc fix PRs exist:**
   ```bash
   gh pr list --search "nightly drift <YYYY-MM-DD>" --state all \
     --json number,title,state,mergedAt
   ```

7. **Check remote branches for VP-created doc branches:**
   ```bash
   git fetch origin --prune && git branch -r | grep "docs.*<YYYY-MM-DD>"
   ```

### Why No Doc PRs May Be Created (Correct Behavior)

The VP agent is instructed to "verify before fixing." If all flagged issues
turn out to be false positives (docs are already accurate), the agent
correctly skips all changes. The post-mission hook then sees "not on a docs/
branch" or "no new commits vs develop" and skips the push/PR/merge step.

Common false-positive scenarios:
- `glossary_candidate` for generic programming terms (SQL keywords, etc.)
- `agentic_drift` when AGENTS.md was already updated manually
- `code_doc_drift` when docs were updated in a separate PR outside the audit
  window

### Lesson: VP Worker Loop Has No Logfire Instrumentation

The VP worker loop (`vp/worker_loop.py`) and VP client modules
(`vp/clients/`) contain **zero Logfire spans**. All VP logging goes through
Python's `logging` module only, which means:

- Logfire queries for VP mission execution return **nothing**
- Mission status must be checked via the gateway API (`/api/v1/ops/vp/missions`)
- Mission execution traces are only available through the gateway's
  `gateway_request` span in `execution_engine.py`, which is the outermost
  wrapper around `process_turn()`
- The actual VP worker lifecycle (claim, heartbeat, finalize, post-mission hook)
  is invisible to Logfire

**Implication for future debugging:** If VP mission observability is needed,
either check the VP mission DB via API, or consider adding minimal Logfire
spans to the worker loop for critical lifecycle events.

### Lesson: Logfire Query Patterns For This System

When querying Logfire for system telemetry:

1. **Logfire primarily instruments**: `execution_engine.py` (gateway_request
   spans), `agent_core.py` (conversation turns), `hooks.py` (tool calls),
   and HTTP request/response spans.

2. **Logfire does NOT instrument**: VP worker loop, VP clients, cron service
   lifecycle, heartbeat scheduling, doc maintenance dispatch, or notification
   dispatch.

3. **Effective query patterns**:
   - Filter by `service_name = 'universal-agent'`
   - Filter by `span_name` for specific operations (e.g., `gateway_request`,
     `tool_use`, `claude.assistant.turn`)
   - Use `attributes->>'trace_id'` to follow request chains
   - Messages are generic span names, not descriptive — use `start_timestamp`
     ranges to narrow scope

4. **What to use instead of Logfire** for uninstrumented subsystems:
   - VP missions: gateway API (`/api/v1/ops/vp/missions`)
   - Heartbeats: gateway API or check `heartbeat_findings_latest.json`
   - Cron/scheduling: gateway health endpoint or VPS service logs

---

## 2026-04-03: Agent Tool Name Misdirection and Discovery Thrashing

### Incident Summary

The coding agent was tasked with investigating why a specific MCP tool (`mcp__internal__task_hub_task_action`) was "missing" from its accessible tool list. The agent spent significant time debugging the Python registration pipeline, resolving symlinks, and launching sprawling background `grep` commands across the codebase.

The root cause was simple: The SDK stripped the `mcp__internal__` prefix when natively exposing the tools (so it appeared as `task_hub_task_action`), but the agent's system prompts explicitly instructed the agent to "strictly use `mcp__internal__task_hub_task_action`". The agent strictly followed the false prompt text, failed to find the exact string match, assumed the tool was missing, and fell back to executing raw database scripts.

### Lesson 1: Inspect Available Tools First, Distrust Stale Prompts

When an agent or developer operates under the assumption that a tool is missing, the very first step should be to inspect the *currently available* tools in the environment using native SDK endpoints or reflection (e.g., listing tools explicitly), rather than blindly trusting static prompt text or documentation.

Reusable rule:
- Assume prompt text about tool names might be stale or misaligned with the SDK integration layer. 
- Always rely on the dynamic framework-provided tool list as the single source of truth for capabilities.

### Lesson 2: Prefer Native Search Over Heavy Shell Commands

During the discovery phase, the agent executed poorly-scoped `grep -R` commands natively in the shell, which led to timeouts and long-running background processes parsing untracked directories like `__pycache__`.

Reusable rule:
- Avoid broad, blocking shell searches when native semantic or indexed search tools (`grep_search` via MCP, ripgrep, etc.) are available.
- If a shell search is absolutely necessary, scope it tightly to the subsystem in question, exclude `__pycache__` and object files, and use strict limits to prevent background freezing.

### Lesson 3: Code Registration and Prompt Sync Are Distinct Layers

The incident highlighted that Python internal function names (like `mcp__internal__send_agentmail`) are decoupled from the SDK's registered tool names (e.g., `@tool(name="send_agentmail")`), which are in turn decoupled from the markdown prompts used to guide the agent logic.

Reusable rule:
- When renaming or altering existing tool namespaces, update the Python function, the `@tool` decorator, AND the human-readable Markdown prompts simultaneously. Failure to sync the prompting layer breaks autonomous discovery.

---

## 2026-04-11: Next.js Turbopack HMR WebSocket Proxy Bypassing

### Incident Summary

During local development with Next.js App Router and Turbopack, the dashboard experienced continuous `ECONNRESET` loops. 
The issue originated from Next.js Client WebSockets failing to establish stable connections through standard proxy setups or path-based routing.

### Lesson 1: Turbopack HMR Websockets Ignore Standard Path Routing

Turbopack fundamentally changes how Next.js handles Hot Module Replacement (HMR) websocket connections. 
Standard proxy configs (like rewriting `/api` to a backend) do not reliably intercept the HMR websocket upgrade requests.

Reusable rule:
- Do not rely on path routing or Next.js `rewrites` to handle backend websocket connections during local development with Turbopack.

### Lesson 2: Direct Target Assignment Prevent `ECONNRESET` Loops

To prevent connection resets and ensure stable websocket connections during local dev, the client must directly target the backend service port.

Reusable rule:
- Configure local environment variables (like `UA_API_PORT` or `NEXT_PUBLIC_API_URL`) to explicitly point Next.js client websocket connections to the direct backend target (e.g., `localhost:8001`), bypassing the Next.js proxy layer entirely.

---

## 2026-04-14: Gemini Image Generation 403 — Org-Level API Keys vs AI Studio Keys

### Incident Summary

The `generate_image` MCP tool repeatedly returned HTTP 403 errors when Simone tried to generate infographics as part of the KB→Report→Infographic pipeline. Text generation with the same `GEMINI_API_KEY` worked fine. The investigation spiraled through model name checks, API enablement audits, and Vertex AI permission reviews before isolating the root cause.

The `GEMINI_API_KEY` was a Google Cloud project-level key (Cloud Console → API & Credentials). Google Cloud org-level policies silently block the `imagen` / image generation models even when the Generative Language API is enabled. AI Studio keys (`aistudio.google.com`) bypass org restrictions because they go through `generativelanguage.googleapis.com` without org policy enforcement.

### Lesson 1: Google Cloud API Keys Are Not Universal Across Gemini Capabilities

Reusable rule:

- A Cloud Console API key that works for text generation will **not** necessarily work for image generation.
- Imagen-family endpoints have additional org-level policy enforcement that standard Generative Language API enablement does not control.
- When image generation returns 403 but text works, suspect org policy before checking permissions or enablement.

### Lesson 2: Maintain Separate Infisical Keys for Different Capability Tiers

Reusable rule:

- Store capability-specific API keys separately in Infisical (e.g., `GEMINI_IMAGE_API_KEY` for image-capable endpoints, `GEMINI_API_KEY` for general text).
- Code should prefer the capability-specific key and fall back to the general key.
- The `generate_image` tool now uses `GEMINI_IMAGE_API_KEY` (AI Studio key) before falling back to `GEMINI_API_KEY`.

### Lesson 3: 403 Errors From Google APIs Need Org-Level Investigation

Reusable rule:

- When a Google API returns 403 despite the API being "enabled" in the project, check **org-level policies** and **domain-wide restrictions** before spending time on IAM roles or service account changes.
- AI Studio keys are the fastest unblock path for individual developer usage.

---

## 2026-04-14: Knowledge Base Pipeline Must Explicitly Mandate NLM for Artifact Generation

### Incident Summary

The nightly wiki agent was designed to build knowledge bases from proactive signal cards, generate reports, and create infographics. During execution, the VP agent used `generate_image` (a generic Gemini image generation tool) for infographics instead of NotebookLM's native `studio_create(type="infographic")`. This hit the 403 API key issue above, but revealed a deeper architectural problem: the pipeline prompt never explicitly told the agent to use NLM's artifact generation capabilities.

### Lesson 1: Agents Will Use Generic Tools Unless Explicitly Directed to Specialized Ones

Reusable rule:

- When a pipeline has access to both a generic capability (e.g., `generate_image`) and a specialized one (e.g., NLM `studio_create`), agents will default to the generic capability unless the prompt **explicitly mandates** the specialized tool.
- Pipeline prompts must name the specific the tool and describe the expected invocation pattern, not just the desired outcome.

### Lesson 2: KB Pipelines Should Follow the NLM-First Pattern

Reusable rule for knowledge base artifact generation:

1. **Research corpus** → NLM `research_start` + `research_import` (not generic web scraping)
2. **Reports** → NLM `studio_create(type="report")` (not LLM-generated markdown)
3. **Infographics** → NLM `studio_create(type="infographic")` (not `generate_image`)
4. **Audio overviews** → NLM `studio_create(type="audio")` (only option)
5. **Download** → NLM `download_artifact` to a known path

Generic tools should only be used as an explicit fallback when NLM is unavailable.

### Lesson 3: System Prompts Should Include Universal Anti-Patterns

Reusable rule:

- When a class of error recurs across multiple pipelines (e.g., agents using `generate_image` instead of NLM, or agents calling AgentMail SDK instead of `agentmail_send_with_local_attachments`), the fix belongs in the **system prompt** as a universal routing rule, not just in the individual pipeline prompt.
- Section 13 of the Simone system prompt now routes large binary attachments to the correct handler; a similar universal rule should eventually route all KB artifact generation through NLM.

---

## 2026-04-13: Python Execution Scope and Dictionary Initialization

### Incident Summary

During the deployment of a new feature involving large attachment bypassing for AgentMail, the pipeline's integration tests (like `py_compile`) passed successfully, but the actual VPS `gateway` service crashed immediately on restart.

The Github Action pipeline showed a `NameError: name 'finalize_research_wrapper' is not defined` traceback originating from `gateway_server.py`. The root cause was located in `local_toolkit_bridge.py`: a dictionary (`LOCAL_TOOLS`) was initialized at the top of the file grouping all the tool wrapper functions *before* those `__wrapper` functions were actually defined later in the file. 

Because `py_compile` only checks for *SyntaxErrors* and not runtime *NameErrors* relating to variable scope binding during module load, the invalid initialization order successfully compiled.

### Lesson 1: Scope Initialization Matters in Python Module Loading

Variables and dictionary references in the main body of a module are evaluated immediately when the module is imported.

Reusable rule:
- Ensure all functions mapping to a dictionary or registry declared at the global sequence level are defined *before* the mapping dictionary itself is declared.
- Place registry/mapping dictionaries at the bottom of the file to ensure all references exist.

### Lesson 2: py_compile is Insufficient for Runtime Load Checking

`python -m py_compile` checks for syntax errors, but does not capture execution-order scope binding errors. 

Reusable rule:
- Don't overly rely on compilation tools as a golden metric of correctness. For simple scripts or files acting as modules, explicitly load the file natively using `uv run python file.py` or a linter (like `mypy` or `ruff`) set to strict mode to identify scope or unresolved variable references before pushing to `main` for deployment.

---

## 2026-04-15: Swallowed API 403s Masking Core Auth Bugs in Production Hydration

### Incident Summary

When clicking to open completed cron jobs on the production dashboard, the UI showed `NO RUN HISTORY FOUND FOR CRON_X`. Initially, this appeared to be a file-path issue since the UI expected `cron_output.md` but the backend generated `cron_result.md`. However, even after fixing the filename typo, the UI continued to show no run history on production. 

Local tests worked fine, but fetching the API endpoint explicitly on the live server returned a `403 Forbidden` JSON error ("Access denied: session owner mismatch"). `cron` jobs automatically execute under an implicitly assigned `cron` system ownership tag. When the Dashboard owner fetched these files on the UI, the Next.js Client fired a request to the backend with their credentials, and the API `_enforce_session_owner` check rejected it.

The primary issue was that the UI's fallback handling was checking if `cronResp.status === 404`, and if it *wasn't* 404 but simply `!response.ok` (like a 403), it merely swallowed the exception, returning an empty string. This misleadingly confirmed the initial "file missing" hypothesis.

### Lesson 1: Swallowed Exceptions on the Frontend Send You on Wild Goose Chases

If the frontend silences specific error codes and forces them into a generic functional fallback, standard debugging becomes impossible. The UI checking specifically for a `404` and ignoring a `403` meant the actual failure was never signaled. 

Reusable rule:
- Do not let `!response.ok` checks silently swallow potentially non-standard HTTP errors (like `403`, `500`). 
- Log unexpected HTTP errors to the console explicitly before taking the fallback, so developers inspecting the site locally or via screenshots know immediately that an API failure occurred instead of a file missing.

### Lesson 2: Do Not Use Dev / Localhost Environments to Debug Production-Specific Edge Cases

When the bug was reported *on the production app*, testing it locally first provided a misleading "it works" result. Local development environments can easily have missing or overly permissive simulated auth bypasses compared to strict Live deployments.

Reusable rule:
- If a bug is observed on production, always trace the root cause against the production configuration, including checking production logs and raw cURL responses under production restrictions, before attempting to simulate it locally to confirm the fix.

### Lesson 3: Automated Machine Identities Require Explicit Admin Dashboard Bypasses

When generating sessions or workflows tied to machine/system accounts (`session_hook_`, `cron_`), those sessions inherit different ownership records than normal Interactive Developer accounts. 

Reusable rule:
- Always ensure that your primary UI/API auth mechanism (like `_enforce_session_owner`) explicitly maintains read-access parity for the developer/dashboard owner if those system sessions are supposed to be monitored within internal UI. 

---

## Seed Questions For Future Entries

When adding a new lesson, answer these:

1. What did we believe too early?
2. What evidence finally disproved it?
3. What boundary actually failed: host, process, transport, cache, credential,
   or code path?
4. What verification step should become standard next time?
5. What code or deployment pattern should we standardize to prevent recurrence?

---
title: "ADR: Deploy-Restart Resilience for the Gateway (cut the churn, protect in-process work)"
status: active
canonical: true
subsystem: plat-deploy-restart-resilience
code_paths:
  - scripts/deploy/remote_deploy.sh
  - scripts/deploy/deploy_coalesce.py
  - .github/workflows/deploy.yml
  - src/universal_agent/gateway_server.py
  - src/universal_agent/heartbeat_service.py
  - src/universal_agent/vp/worker_loop.py
  - deployment/systemd/
  - deploy/nginx/universal-agent-app
last_verified: 2026-06-11
---

# ADR: Deploy-Restart Resilience for the Gateway

> **ADR status: ACCEPTED — phased implementation.** The operator approved the
> hybrid recommendation **B → C1 → C2, defer A** (2026-06-11). Phases land as
> separate PRs, each with a rollback, verified between phases.
>
> - **Phase B (coalesce redundant deploys): ✅ IMPLEMENTED** — `scripts/deploy/deploy_coalesce.py`
>   (unit-tested decision) + a fail-safe gate step in `.github/workflows/deploy.yml`.
>   See the **As-built** note under §4.
> - **Phase C1 (slim lifespan): PENDING.** · **Phase C2 (extract heartbeat service): PENDING.**
> - **Lever A (zero-downtime): DEFERRED.**
>
> Deploy handling is 24/7 P0 infra (exempt from dormancy). This ADR is a sibling to
> [`08_scheduling_substrate_adr.md`](08_scheduling_substrate_adr.md) — Lever C2
> below is the same "extract a singleton out of the gateway into its own systemd
> service" pattern that ADR's Decision 2 used for the Mission Control sweeper.

## 1. Context

Every merge to `main` triggers `.github/workflows/deploy.yml`, which SSHes to the
VPS and runs `scripts/deploy/remote_deploy.sh`. The production stack restart is a
**blunt** `systemctl restart` of six units:

```
universal-agent-gateway universal-agent-api universal-agent-webui \
universal-agent-telegram ua-discord-cc-bot ua-discord-intelligence
```

(There are **three copies** of this list in `remote_deploy.sh` — sudo, non-sudo,
and a `service`-manager fallback — which must be kept in sync.)

At the current cadence (~19 deploys/day) this produces **~141 gateway
restart journal lines / 7 days**. PR #941 quieted the *symptom* on the dashboard
(`ServiceStatusBanner` now needs 2 consecutive failed `/api/v1/version` probes
before going red). This ADR addresses the *cause*: the restart itself.

### 1.1 Two distinct harms (do not conflate them)

| # | Harm | Mechanism | Who feels it |
|---|------|-----------|--------------|
| **H1** | **Dead `:8002` window** | `gateway_server.py::lifespan` runs ~734 lines of **synchronous pre-yield init** (factory registry, runtime-DB schema migration, heartbeat/daemon session seed, task-lifecycle reconcile, autonomous-cron registration, session reaper, workspace archiver, `_reconcile_stale_vp_missions_on_startup`) **before** FastAPI begins serving. `remote_deploy.sh::check_local_health` budgets up to **8 min** (96×5s) for the gateway to answer `/api/v1/health`. During that window `:8002` refuses connections. | Dashboard banner; any external `:8002` hit 502s; in-flight HTTP requests at SIGTERM time. |
| **H2** | **In-process autonomous work SIGTERM'd** | Daemon **Simone heartbeat/todo** iterations run *inside the gateway process* (`heartbeat_service`, `process_heartbeat`). The unit is `Type=simple`, `Restart=always`, default `KillMode=control-group` → a restart sends **SIGTERM to the whole cgroup**, interrupting any in-flight heartbeat iteration. | Autonomous Simone work mid-iteration ("deploy-restart casualty"). |

### 1.2 What is NOT harmed (corrects a common framing)

**VP worker missions are already protected.** They run in separate
`universal-agent-vp-worker@*.service` processes, are **deliberately not
restarted** by `remote_deploy.sh`, and pick up new code by self-restarting
**between** missions: `worker_loop._should_restart_for_code_currency()` + the
unit's `Restart=always`. A running mission keeps heartbeating its claim lease
from its own process, so the gateway's startup reconciler sees a live claim and
leaves it alone. **The gateway restart does not kill VP missions** — it kills
in-process daemon work (H2), a narrower blast radius than "deploys kill Cody/Simone work."

### 1.3 Frequency driver

`deploy.yml` **serializes** deploys (`concurrency: { group: deploy-production,
cancel-in-progress: false }` — queues, never cancels) but does **not coalesce**.
N merges in a burst = **N full restarts**, even though only the last commit's
code matters.

### 1.4 The central constraint: the gateway is a stateful singleton

The lifespan seeds heartbeat/daemon **sessions**, registers **autonomous crons**,
and runs **reconcilers** (task lifecycle, stale VP missions), a **session reaper**,
and a **workspace archiver**. Two gateway instances running at once would
**double-run** all of this (duplicate cron registration, duplicate reconcile,
duplicate daemon sessions). **This is why naive blue-green / two-instance
zero-downtime is unsafe today** — and it is the precondition every "zero-downtime"
idea must satisfy first.

```mermaid
flowchart TD
    PR[PR merged to main] --> DY[deploy.yml: push trigger]
    DY -->|concurrency: serialize, NO coalesce| RD[remote_deploy.sh on VPS]
    RD --> B1[git reset --hard origin/main + uv sync + webui build]
    B1 --> RST[systemctl restart x6 units]
    RST --> H1[H1: gateway :8002 dead until lifespan finishes\n~734 lines sync init, up to 8min health budget]
    RST --> H2[H2: SIGTERM kills in-process Simone heartbeat iteration]
    H1 --> HG[check_local_health gates deploy success]
    RST -. NOT restarted .-> VPW[VP workers: self-restart between missions]
    style H1 fill:#ffe0e0,stroke:#c0392b
    style H2 fill:#ffe0e0,stroke:#c0392b
    style VPW fill:#e0ffe0,stroke:#27ae60
```

## 2. The three levers (orthogonal — combine, don't choose one)

### Lever A — Zero-downtime gateway handoff
Eliminate the dead `:8002` window so in-flight requests **drain** instead of being
refused. Mechanisms: socket-activated handoff, or blue-green behind nginx
(`deploy/nginx/universal-agent-app` already fronts the app; gateway `:8002`,
webui `:3000`, tailnet TLS via `tailscale serve`).
**Blocked by §1.4** — requires a "serving" vs "scheduling/daemon" split so only
one instance owns the singletons before two instances can coexist. Highest effort.
Fully fixes H1 for HTTP; only **partially** fixes H2 (drains HTTP, but in-process
daemon work still has to migrate to the surviving instance).

### Lever B — Coalesce / debounce deploys
Collapse N rapid merges into **one** restart that deploys the **latest** commit.
Cheapest lever. Reduces the **frequency** of H1 and H2; does not reduce per-restart
cost. Natural hook: a short debounce/batch window keyed on the existing
`concurrency: deploy-production` guard so queued runs collapse to HEAD.
**Must preserve:** latest code always deploys, and the health-gate stays intact.

### Lever C — Cut per-restart cost (at the source)
- **C1 — Slim the synchronous pre-yield lifespan.** Make the ~734 lines of
  `gateway_server.py::lifespan` init lazy / post-yield / backgrounded so cold-start
  drops from **minutes to seconds**. This is the code's own stated
  "right architectural fix" (the comment above the 8-min health budget in
  `remote_deploy.sh::check_local_health`). Directly shrinks H1.
- **C2 — Decouple in-process daemon Simone from the gateway lifecycle.** Move the
  heartbeat/daemon-session loop into its **own systemd service** (mirrors
  `08_scheduling_substrate_adr.md` Decision 2, which extracted the Mission Control
  sweeper). A gateway restart then no longer SIGTERMs autonomous work. **Removes H2
  at the source**, and is the first half of the serving/scheduling split that
  Lever A needs.

## 3. Lever comparison

| | Fixes H1 (dead window) | Fixes H2 (SIGTERM casualty) | Cuts frequency | Effort | Risk | Precondition |
|---|:---:|:---:|:---:|:---:|:---:|---|
| **A** zero-downtime | ✅ (HTTP) | ⚠️ partial | — | **High** | High (singleton double-run if rushed) | §1.4 split (started by C2) |
| **B** coalesce | indirect (fewer) | indirect (fewer) | ✅ | **Low** | Med (must keep latest+health-gate) | none |
| **C1** slim lifespan | ✅ (sec not min) | — | — | **Med** | Med (init ordering bugs) | none |
| **C2** extract heartbeat svc | — | ✅ | — | **Med** | Med (singleton ownership, lease handoff) | none |

## 4. Recommendation — hybrid, phased: **B → C1 → C2, defer A**

```mermaid
flowchart LR
    subgraph P1[Phase 1 - Lever B]
      B[Coalesce rapid merges to 1 restart at HEAD]
    end
    subgraph P2[Phase 2 - Lever C1]
      C1[Slim lifespan: lazy/post-yield init -> seconds]
    end
    subgraph P3[Phase 3 - Lever C2]
      C2[Extract daemon Simone heartbeat to own systemd service]
    end
    subgraph P4[Deferred - Lever A]
      A[Zero-downtime handoff behind nginx]
    end
    B --> C1 --> C2 -.enables.-> A
    style P1 fill:#e8f5e9,stroke:#27ae60
    style P2 fill:#e3f2fd,stroke:#2196f3
    style P3 fill:#e3f2fd,stroke:#2196f3
    style P4 fill:#f5f5f5,stroke:#9e9e9e,stroke-dasharray: 5 5
```

**Why this order.** B is the cheapest cut and **de-risks everything else** (fewer
restarts = fewer chances to trip any bug, immediately). C1 shrinks the dead window
to seconds per the codebase's own stated direction. C2 removes the actual
autonomous-work casualty (H2) and **delivers the serving/scheduling split** that A
requires. A is a large project whose marginal gain is small once B+C have made
restarts cheap and casualty-free — so it is naturally last, and only if still
warranted.

```mermaid
sequenceDiagram
    participant M as 3 merges in 4 min
    participant GHA as deploy.yml
    participant VPS as gateway :8002
    Note over M,VPS: TODAY (no coalesce)
    M->>GHA: merge #1, #2, #3
    GHA->>VPS: restart (dead window 1)
    GHA->>VPS: restart (dead window 2)
    GHA->>VPS: restart (dead window 3)
    Note over M,VPS: AFTER Lever B (coalesce to HEAD)
    M->>GHA: merge #1, #2, #3
    GHA->>VPS: ONE restart at commit #3
```

### Phase B — As-built (deploy coalescing)

Implemented as a unit-tested decision script plus a minimal, fail-safe gate in the
deploy job (the risky logic lives in tested Python, not YAML — keeping the
`deploy.yml` change small to dodge the parser quirk in §5.1):

- `scripts/deploy/deploy_coalesce.py::should_skip_redundant_deploy` — pure decision:
  skip iff a **strictly-newer** Deploy run (higher monotonic `run_id`) is still in an
  **active** state (`queued`/`in_progress`/…). `deploy_coalesce.py::main` is the CLI
  the workflow calls (runs JSON on stdin, `--my-run-id`, prints `skip=true|false`).
- `.github/workflows/deploy.yml` — a `Coalesce redundant deploys` step (`id: coalesce`,
  `if: github.event_name == 'push'`) fetches the script at the deploy SHA, feeds it
  `gh run list --workflow=deploy.yml --json databaseId,status,event`, and writes the
  decision to `$GITHUB_OUTPUT`. The Tailscale + Deploy steps gate on
  `steps.coalesce.outputs.skip != 'true'`.
- **Safety invariants** (covered by `tests/unit/test_deploy_coalesce.py`): the newest
  run never skips itself (latest code always ships); only active newer runs supersede
  (a newer *completed* run never causes a skip); and any error / missing token scope /
  malformed input ⇒ `skip=false` (proceed) — coalescing can never *block* a deploy,
  only no-op. `workflow_dispatch` runs always proceed (the step is push-only).
- **Net effect:** a burst of N merges collapses to **2 restarts** (the already-running
  first + the newest), not N. **Known residual:** if the newest run later *fails*, an
  intermediate coalesced run won't have shipped its (older-but-newer-than-first) code;
  the existing deploy-failure email surfaces this, and the next merge re-deploys HEAD.

## 5. Landmines (carry into every phase)

1. **deploy.yml YAML-parser quirk** — the GHA validator has silently rejected
   `deploy.yml` on `check_local_health`-adjacent edits even when `actionlint` +
   `pyyaml` pass (see `memory/project_2026-05-27_deployyml_parser_quirk.md`).
   **Smoke-test any `deploy.yml` change on a feature branch first** (escaped `+`
   in the branches filter). `actionlint` alone is NOT sufficient.
2. **Health-gate integrity** — `check_local_health` is the only pre-deploy gate.
   Coalescing must not let a bad commit skip the gate, and must still deploy the
   *latest* commit, not a stale queued one.
3. **Singleton double-run (§1.4)** — never run two gateway instances, or two
   heartbeat owners, without an explicit single-owner lease. C2 and A both must
   define who owns crons/reconcilers/daemon sessions.
4. **Do not fold VP workers into a restart scheme** — they self-restart between
   missions on purpose (`worker_loop._should_restart_for_code_currency`).
   Restarting them mid-deploy was the original mission-casualty bug.
5. **24/7 P0** — deploy handling is exempt from dormancy. A broken deploy pipeline
   is a production outage. Stage carefully; each phase ships with a rollback.
6. **`.env` is clobbered every deploy** (`deploy.yml` rewrites `/opt/universal_agent/.env`)
   — any new durable config goes in the deploy bootstrap dict or code defaults,
   not a VPS-side hand-edit (see `memory/feedback_env_clobbered_by_deploy.md`).

## 6. Operator decision points (only Kevin can decide)

- **D1 — Start with Phase 1 (Lever B coalescing)?** Cheapest, immediate, lowest
  risk. Recommended first ship.
- **D2 — Coalesce mechanism:** a debounce window in `deploy.yml` keyed on the
  concurrency guard, vs. a "deploy at most every N minutes / collapse-to-HEAD"
  batch. (Recommend the concurrency-guard debounce — least new machinery.)
- **D3 — Appetite for C1 (slim lifespan)?** Medium effort, touches gateway startup
  ordering. Biggest single reducer of the dead window.
- **D4 — Approve the serving/scheduling split (C2 → eventually A)?** This is the
  architectural commitment; it's consistent with the scheduling-substrate ADR's
  direction but is real work.

## 7. References

- Sibling ADR: [`08_scheduling_substrate_adr.md`](08_scheduling_substrate_adr.md) (Decision 2 = the extract-a-service pattern C2 reuses).
- `scripts/deploy/remote_deploy.sh` — restart block, `check_local_health`, `ensure_current_venv_interpreter`.
- `.github/workflows/deploy.yml` — `concurrency: deploy-production`, `paths-ignore`, push trigger.
- `src/universal_agent/gateway_server.py::lifespan` — the synchronous pre-yield init.
- `src/universal_agent/vp/worker_loop.py::_should_restart_for_code_currency` — VP worker self-restart contract.
- Memories: `project_2026-05-27_deployyml_parser_quirk`, `feedback_env_clobbered_by_deploy`, `project_2026-05-26_gateway_eventloop_starvation` (in-process daemon work starving the loop is the same surface H2 lives on).

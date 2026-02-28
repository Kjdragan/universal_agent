# Handoff & Current Architecture Explanation

**Written:** 2026-02-28
**Context:** Handoff documentation and Q&A covering the architectural progress built in Phase 2 for the transition to Universal Agent Corporation architecture.

---

## What was Accomplished During This Session

1. **Architecture Consolidation:**
   We successfully moved legacy isolated documents from `docs/` and created the new authoritative structural document: `corporation/docs/design/003_IMPLEMENTATION_PLAN.md`.
2. **Phase 2 `.env` Parameterization (`agent_setup.py`):**
   Injected parsing logic and configuration rules for `FACTORY_ROLE` and `ENABLE_VP_CODER` into the Agent. This guarantees the local agent conditionally filters the `capabilities.md` registry based on its assigned environmental role.
3. **Phase 3 Preparation (Redis Message Bus):**
   Established the basic Docker Compose files (`docker-compose.yml` and `redis.conf`) inside `corporation/infrastructure/redis/` to bootstrap the Redis message stream on the VPS.

---

## Answers to Handoff Questions

**Q: What is the exact handoff baseline commit/branch I should treat as source of truth?**
**A:** The current unstaged working directory (as of `2026-02-28`). Please commit the current state as a baseline before proceeding to avoid local conflicts.

**Q: Are there any uncommitted local changes or stashed work not reflected in 003_IMPLEMENTATION_PLAN.md?**
**A:** Yes. The most recent uncommitted changes involve the parameterization edits made to `src/universal_agent/agent_setup.py` and the newly drafted Phase 3 Redis files located in `corporation/infrastructure/redis/`. Furthermore, tactical tracking artifacts (`task.md` and locally scoped `implementation_plan.md`) have been updated.

**Q: Confirm the canonical env contract for Phase 2: exact var names, allowed values, and defaults (FACTORY_ROLE, ENABLE_*, LLM_PROVIDER, etc.).**
**A:**

- `FACTORY_ROLE`: (String) Determines operational identity. Default: `"HEADQUARTERS"`. Allowed values: `"HEADQUARTERS"`, `"LOCAL_WORKER"`, etc.
- `ENABLE_VP_CODER`: (Boolean string) Dictates whether the VP internal routines and tools are appended to the agent capabilities. Default: `"true"`.
- `LLM_PROVIDER_OVERRIDE`: (String) Controls inference provider fallback logic for cost-saving local polls. Default: empty.

**Q: There are naming variants across docs (IS_HEADQUARTERS vs FACTORY_ROLE): which naming is final for code?**
**A:** `FACTORY_ROLE` is the final standardized variable chosen for capability-gating inside the codebase.

**Q: Which runtime components must be capability-gated first (priority order): heartbeat loop, Telegram polling, VP agent loading, UI server, delegation consumer?**
**A:**

1. **VP Agent Loading** - Has been gated successfully inside `agent_setup.py` `capabilities.md` generation.
2. **UI Server & API Endpoints** - Needs to be gated so that generic `LOCAL_WORKER` variants do not expose an open administration dashboard.
3. **Telegram Polling & Native Heartbeats** - Only specific roles should talk externally or manage global queues.
4. **Delegation Consumers** - Built directly as capability-aware consumers during Phase 3.

**Q: What is the expected capabilities.md contract: file location, schema, and who consumes it (HQ endpoint, heartbeat payload, or both)?**
**A:** `capabilities.md` is generated dynamically and physically dumped to the session's `workspace_dir` upon `initialize()`. It is currently loaded into the local component's `system_prompt`. While currently consumed only locally by the SDK agent, future iterations might scrape this registry to use during HQ registration payload handshakes.

**Q: Should capabilities be generated only at startup, or recomputed dynamically on config refresh?**
**A:** Currently generated exclusively at runtime startup inside `AgentSetup.initialize()`. It acts as a fail-closed, statically assigned sandbox for that session.

**Q: For HQ/local state split, what is the authoritative boundary now: which entities remain local SQLite-only vs HQ-global?**
**A:** Ephemeral scratchpads, operational runtime logs, and execution states (like `vp_state.db`) remain strictly local SQLite databases to avoid network saturation. Only formalized "Memos", summaries, and final task artifacts cross the boundary into the HQ-global knowledge graph (Phase 5).

**Q: For delegation architecture, has Redis Streams vs NATS been finalized? If Redis, provide stream names and consumer-group naming convention.**
**A:** Redis is the architecture of choice. The base Docker Compose files are drafted. However, the explicit schema for **stream names and consumer groups** must still be defined as the primary design action item for the developer taking over Phase 3.

**Q: Do you already have a draft mission payload schema (required fields, idempotency key, retries, timeout, result envelope)?**
**A:** No, this payload schema design is the next requirement in Phase 3 stateless delegation.

**Q: For security, what are the finalized Ops token TTL/rotation rules and validation source (gateway-only or shared middleware)?**
**A:** The system relies on Infisical's machine identity infrastructure for root API keys. Internal factory communication requires short-lived Ops Tokens, but the specific validation middleware and TTL limits are to be solidified during the Phase 3 stream logic development.

**Q: For Infisical runtime, are there any remaining known gaps beyond the “ignore #1 for now” item?**
**A:** `infisical_loader.py` is safely instantiated inside `main.py` and `agent_setup.py`. Be cautious about rogue legacy python scripts executing outside of the standard entry-points, as they might bypass the unified loader.

**Q: For “Corporation View” MVP, what exact fields are required in first release (factory identity, heartbeat latency, capabilities, workload, cost)?**
**A:** The required fields are: Factory Identity ID, Heartbeat Latency, current Active Capabilities Profile (`FACTORY_ROLE`), and ZAI API Cost metrics if available (Phase 4).

**Q: What are the hard acceptance criteria for Phase 2 done (tests, manual checks, deployment checks)?**
**A:** Phase 2 (Parameterization) is functionally complete on the core startup component. Acceptance requires verifying that a node spun up with `FACTORY_ROLE=LOCAL_WORKER` actively discards VP constraints and operates entirely headless when queried locally.

**Q: List any known bugs, risky shortcuts, or TODOs not captured in docs.**
**A:** **Risky Shortcut:** The parameterization currently heavily filters the prompt boundaries in `agent_setup.py`, but you must still explicitly gate the FastAPI server (`gateway_server.py`) so a `LOCAL_WORKER` does not unintentionally run the admin Web UI.

**Q: Should old copies under docs/ be considered deprecated now that architecture docs live in corporation/docs/?**
**A:** Yes. We consolidated these explicitly. Any legacy architecture documentation located outside of `corporation/docs/design/` should be treated as deprecated or historical references.

---

## Additional Final Clarifications

**Q: Confirm the intended behavior for import-time secret bootstrapping (remove all import-time calls and keep bootstrap only inside explicit startup entrypoints?)**
**A:** Yes. Having `initialize_runtime_secrets()` at the module import level causes all integrations to trigger HTTP requests to Infisical even when testing or importing functions. You should refactor this to only occur inside the `if __name__ == "__main__":` block or explicit initialization functions (e.g., FastAPI lifespan).

**Q: Provide the exact final FACTORY_ROLE enum and defaults. Which roles are valid now, and what should happen for unknown values?**
**A:** The final valid roles are explicitly:

1. `HEADQUARTERS` (Default if unconfigured)
2. `LOCAL_WORKER`
3. `STANDALONE_NODE`

If an unknown value is supplied, the system must fallback to `LOCAL_WORKER` (the most restricted fail-safe headless mode) and log a critical warning.

**Q: Provide a role-to-runtime behavior matrix (must be explicit): (start FastAPI gateway, start Next.js UI, enable Telegram polling, enable heartbeat, accept delegations, allow VP coder)**
**A:**

| Component / Role | `HEADQUARTERS` | `LOCAL_WORKER` | `STANDALONE_NODE` |
| --- | --- | --- | --- |
| **FastAPI Gateway** | Yes | No (Healthcheck only) | Yes |
| **Next.js UI** | Yes | No | Yes |
| **Telegram Polling** | Yes | No | Optional |
| **Heartbeat Loop** | Yes (Global) | Yes (Local) | Yes (Local) |
| **Delegations** | Publish & Listen | Listen & Process Only | No (Internal loop only) |
| **VP Coder** | If `ENABLE_VP_CODER` | If `ENABLE_VP_CODER` | If `ENABLE_VP_CODER` |

**Q: Clarify current capability gating scope (agent_setup.py line 531). Do you want hard enforcement at tool/agent registration level now, or prompt-level filtering only for this phase?**
**A:** For Phase 2/3, prompt-level filtering inside the generated `capabilities.md` is sufficient for Agent LLM steering. However, **network boundary enforcement** is mandatory: the system must have hard-enforcement to ensure a `LOCAL_WORKER` cannot accidentally start the API dashboard routes or Telegram queues.

**Q: Confirm LLM_PROVIDER_OVERRIDE contract: Exact allowed values and precedence vs existing config. Should this be role-driven automatically, or manual env override?**
**A:**

- **Precedence:** `LLM_PROVIDER_OVERRIDE` > Default system provider.
- **Allowed Values:** `ZAI`, `ANTHROPIC`, `OPENAI`, `OLLAMA`.
- **Behavior:** This is a *manual `.env` horizontal override*. A user should be able to explicitly configure a `LOCAL_WORKER` to run cheap polls via `OLLAMA`, regardless of what `HEADQUARTERS` is using.

**Q: Redis security/deployment final decision: Private vs Internet-exposed? ACL/TLS? Who owns secret injection?**
**A:** Redis is deployed **internet-exposed** because factories operate across networks (e.g. VPS HQ to local desktop workers).

- **Security:** We rely strictly on `requirepass` inside `redis.conf` AND a network firewall rule (UFW on VPS) limiting port 6379 access to only known Factory IPs. No ACL/TLS is mandated for Phase 3.
- **Secret Injection:** The secret is owned by Infisical under the key `REDIS_PASSWORD`.

**Q: Redis stream naming conventions:**
**A:** Please enforce the following canonical taxonomy:

- **Stream Name:** `ua:missions:delegation`
- **Consumer Group:** `ua_workers`
- **Dead-Letter Stream:** `ua:missions:dlq`
- **Consumer Names:** `worker_{FACTORY_ID}` (where `FACTORY_ID` is ideally the Infisical Machine Identity name or a generated UUID).

**Q: Mission envelope schema decision:**
**A:** Headquarters assigns the Job ID upon publishing. The basic schema contract must be:

```json
{
  "job_id": "uuid-v4",
  "idempotency_key": "uuid_or_hash",
  "priority": 1, 
  "timeout_seconds": 3600,
  "max_retries": 3,
  "payload": {
    "task": "User string description",
    "context": {}
  }
}
```

**Result Envelope:** `{"job_id": "uuid", "status": "SUCCESS|FAILED", "result": "...", "error": "..."}`.

**Q: Ops token policy finalization: TTL, issuance endpoint, validation mechanism, centralized vs route-local.**
**A:**

- **TTL:** 1 hour.
- **Issuance:** A dedicated endpoint on the Headquarters FastAPI server (`/auth/ops-token`).
- **Signature:** JWTs symmetrically signed using a dedicated Infisical operational secret.
- **Validation:** Must be validated via centralized FastAPI dependency middleware so no routes are accidentally left exposed.

**Q: Baseline control point: What branch/commit should I branch from now?**
**A:** The explicit baseline commit to branch from is: `b5059c97aab7775a7b1a42bb3adf537fb5b6e2fa`

---

## Action Item for Next Coder

We are ready to deploy the Redis message bus to begin Phase 3.
To proceed, please review `corporation/infrastructure/redis/docker-compose.yml`.

*Note from Antigravity to User:*
"Do you have an SSH configuration or a deployment script I can use to deploy these Docker configurations directly from here? Or would you prefer to deploy them manually on the VPS yourself?"

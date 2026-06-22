# GPU Demo Desktop-Build Approval — Implementation Notes

## Summary

All 5 components implemented (Component 6 — expiry cron — deferred; see below).
Every changed `.py` passes `py_compile` + `ruff check`. 143 unit tests pass.

---

## Files Changed

### 1. `src/universal_agent/feature_flags.py`
Added `gpu_demo_desktop_approval_enabled(default=False)` at the bottom.
Mirrors the existing simple disable/enable pattern (e.g. `task_hub_missions_enabled`).
Env vars: `UA_DISABLE_GPU_DEMO_DESKTOP_APPROVAL` (kill-switch), `UA_GPU_DEMO_DESKTOP_APPROVAL_ENABLED` (opt-in).

### 2. `src/universal_agent/services/cron_artifact_notifier.py`
Added `sign_gpu_demo_token` / `verify_gpu_demo_token` after the `verify_ideation_token` block.
Reuses `_ack_secret()` exactly like `sign_ideation_token` / `sign_feedback_token`.
Payload prefix `"gpu_demo:"` prevents cross-token replay.
Both added to `__all__`.

### 3. `src/universal_agent/gateway_server.py`
Added `@app.get("/api/v1/gpu_demo/{task_id}/approve")` route `gpu_demo_approve_get`
inserted immediately before `@app.get("/briefs/{artifact_id}")`.
- 404 when `gpu_demo_desktop_approval_enabled()` is False.
- HMAC (`verify_gpu_demo_token`) is the auth — no bearer header (links land in mail client).
- On "approve": stamps `metadata.gpu_approval.state="approve"` via `task_hub.upsert_item` under `_activity_store_lock`; returns `_brief_chrome` HTML with `/gpu-demo-build <task_id>` command.
- On "reject": stamps "rejected" + short page.
- Confirmed signatures match: `_activity_connect`, `_ensure_activity_schema`, `_activity_store_lock`, `_brief_chrome`, `task_hub.get_item`, `task_hub.upsert_item`.

### 4. `src/universal_agent/services/proactive_tutorial_builds.py`
Added:
- `_GPU_BOUND_KEYWORDS` tuple + `_GPU_DEFAULT_MODEL` constant.
- `gpu_bound_from_candidate(candidate) -> dict` — pure deterministic keyword classifier.
  Has `__main__` assert self-check (run with `PYTHONPATH=src python proactive_tutorial_builds.py`).
- `classify_and_gate_gpu_demo(conn, *, candidate, source) -> dict | None` — the gate.
  Flag OFF → None. GPU-bound → queue with `agent_ready=False`, stamp `endpoint_required="ollama_local"` + `gpu_approval={state:"pending",...}`, fire `_send_gpu_demo_approval_email`, return task.
- `async _send_gpu_demo_approval_email(*, task_id, candidate, verdict)` — mirrors `proactive_health_notifier.send_critical_digest`. Uses `_acquire_agentmail_service`, `ActionTag.ACTION`, `KindTag.PROACTIVE`. Returns `{sent:False}` if no base URL/secret (no dead links emitted).
- `finalize_desktop_gpu_demo(conn, *, task_id, manifest_path, agent_id)` — asserts `gpu_approval.state=="approved"`, stamps `state="built"`, `demo_finalize={ok:True, endpoint_hit:"ollama_local"}`, calls `task_hub.perform_task_action(action="complete")`.
- Wired `classify_and_gate_gpu_demo` into `queue_tutorial_builds_with_ceiling`'s per-candidate loop (before the normal path; a GPU-gated hit increments `pending_approval` and `continue`s).

Lane comment: Lane B (`cody_demo_task`) is deferred — one-line comment in code.

### 5. `.claude/commands/gpu-demo-build.md`
New desktop slash command. Frontmatter with `description` and `argument-hint [task_id]`.
Workflow:
1. Guard: stop if no TASK_ID.
2. Read task from VPS activity store via SSHFS; assert `gpu_approval.state=="approved"`.
3. Invoke existing `provision-local-gpu-ollama` skill to bring up Ollama + qwen2.5-coder:7b.
4. Scaffold demo under `~/lrepos/Cody_Code_Generations/<demo_id>/`.
5. Write `manifest.json` with `endpoint_required/endpoint_hit="ollama_local"`.
6. Call `finalize_desktop_gpu_demo` (direct Python over SSHFS — no HTTP).
7. Report path, model, manifest endpoint_hit.

---

## Signature Verification Results

All cited symbols confirmed against actual code before editing:

| Symbol | File | Verdict |
|---|---|---|
| `_is_truthy`, `os.getenv` in feature_flags.py | feature_flags.py | Confirmed — matched existing `task_hub_missions_enabled` pattern |
| `_ack_secret()` | cron_artifact_notifier.py:894 | Confirmed |
| `sign_ideation_token` / `verify_ideation_token` | cron_artifact_notifier.py:1000/1019 | Confirmed — mirrored exactly |
| `ideation_action_get` | gateway_server.py:22422 | Confirmed |
| `_activity_connect`, `_ensure_activity_schema`, `_activity_store_lock`, `_brief_chrome` | gateway_server.py | Confirmed |
| `task_hub.get_item(conn, task_id)` | task_hub.py:691 | Confirmed — `(conn, task_id)` positional |
| `task_hub.upsert_item(conn, item_dict)` | task_hub.py:1222 | Confirmed |
| `task_hub.perform_task_action(conn, *, task_id, action, agent_id, reason)` | task_hub.py:5336 | Confirmed; "complete" is in `VALID_ACTIONS` (task_hub.py:54/69) |
| `queue_tutorial_build_task(conn, *, video_id, ..., agent_ready)` | proactive_tutorial_builds.py:127 | Confirmed |
| `queue_tutorial_builds_with_ceiling` per-candidate loop | proactive_tutorial_builds.py:291 | Confirmed — wired gate at top of loop |
| `_acquire_agentmail_service()` | proactive_health_notifier.py:115 | Confirmed — `-> (service, owned: bool)` |
| `KEVIN_EMAIL` | proactive_health_notifier.py:37 | Confirmed — "kevinjdragan@gmail.com" |
| `AgentMailService.send_email(*, to, subject, text, html, force_send, action, kind, source)` | agentmail_service.py:839 | Confirmed |
| `ActionTag`, `KindTag` | email_tags import in proactive_health_notifier.py:32 | Confirmed |
| `get_activity_db_path()` | durable/db.py:69 | Confirmed |

---

## Component 6 — Expiry Cron (DEFERRED)

The fixed-time expiry cron (`_ensure_gpu_demo_approval_cron_job`) was intentionally deferred.
Reason: the cron-registration block shape in `gateway_server.py` lifespan requires careful
reading of `_register_system_cron_job` and its catch-up semantics; wiring it incorrectly
risks side-effects on the production cron registry. The core feature (approval gate + email +
finalize) is complete and default-OFF. The expiry cron can be a follow-up PR when the
operator flips the flag ON.

To implement: add a module `scripts/gpu_demo_approval_expiry.py` (marks tasks `>24h` in
`pending` state as `expired` in `gpu_approval`), register via
`gateway_server._register_system_cron_job` in the lifespan block near other approval crons,
gate with `gpu_demo_desktop_approval_enabled()`.

---

## Verification

```
py_compile: feature_flags.py           OK
py_compile: cron_artifact_notifier.py  OK
py_compile: proactive_tutorial_builds.py OK
py_compile: gateway_server.py          OK
ruff check (all 4 files):              All checks passed!
pytest tests/unit (143 tests):         143 passed in 39.60s
gpu_bound_from_candidate self-check:   all assertions passed
```

# VPS Deployment Profile Stuck at `local_workstation` (2026-03-23)

## Purpose

This incident note records the full debugging arc for the YouTube tutorial
pipeline outage that initially appeared to be a VPS deployment-profile failure
and ultimately resolved as a runtime environment-mutation bug in the gateway.

This document exists to preserve:

1. the false leads that consumed time
2. the final, code-verified root cause
3. the fix that restored production
4. the operational lessons that should influence future debugging

## Final Status

Resolved.

The production tutorial ingest path on the real VPS now succeeds with:

- `deployment_profile.profile = "vps"`
- `worker_profile = "vps"`
- `proxy_mode = "webshare"`
- successful transcript retrieval from `/api/v1/youtube/ingest`

## What Actually Happened

This incident had three distinct layers:

1. an early host-targeting mistake
2. several plausible but incomplete infrastructure hypotheses
3. a real runtime bug in the gateway process

### 1. We Initially Tested the Wrong Host

The first health and ingest checks were sent to `100.95.187.38:8002`, which is
Kevin's Tailscale desktop host (`mint-desktop`), not the production VPS.

Correct mapping:

| IP | Identity | Machine | Expected profile |
|---|---|---|---|
| `100.95.187.38` | `mint-desktop` | Kevin's desktop | `local_workstation` |
| `100.106.113.93` | `uaonvps` | Production VPS | `vps` |

That means the original observation:

- `deployment_profile.profile = "local_workstation"`

was true for the desktop, but irrelevant to the production VPS.

### 2. Some Interim Theories Were Directionally Useful but Not the Root Cause

During the investigation, several theories were reasonable enough to test:

- unmanaged systemd unit drift
- early `.env` load timing
- deployment-profile refresh timing
- proxy credential mismatch

Some of that work was still worthwhile. In particular, the repo-managed systemd
unit templates and deploy installer remain good deployment hardening. But they
did not explain the final production symptom.

### 3. The Real Production Root Cause Was Parent-Process Env Sanitization

The final root cause lived in
`src/universal_agent/execution_engine.py`.

The gateway bootstraps runtime secrets through Infisical, which populates
runtime values such as:

- `PROXY_USERNAME`
- `PROXY_PASSWORD`

The YouTube ingest path in `src/universal_agent/youtube_ingest.py` reads those
values directly from the current process environment in
`_build_webshare_proxy_config()`.

The bug was that `sanitize_env_for_subprocess()` modifies `os.environ` in place,
and that sanitization was being applied too broadly inside the long-running
gateway process.

Two code paths mattered:

1. `ProcessTurnAdapter._ensure_client()`
2. `ProcessTurnAdapter.execute()`

The child Claude SDK subprocess genuinely needed a reduced environment to avoid
`E2BIG` during spawn, but the parent gateway process needed to keep its
Infisical-loaded runtime secrets intact after that child was created.

Before the fix, the sanitization path could strip proxy credentials from the
live gateway process itself. Once that happened, the ingest endpoint could no
longer build a Webshare proxy config and returned:

- `error = "proxy_not_configured"`
- `proxy_mode = "disabled"`

even though a fresh process on the same VPS could still bootstrap secrets
successfully.

## Code-Verified Root Cause

The relevant behavior is now:

- `sanitize_env_for_subprocess()` still strips the subprocess environment down
  to a safe whitelist for Claude CLI spawn
- `_temporary_sanitized_process_env()` snapshots and restores the parent
  `os.environ`
- `ProcessTurnAdapter._ensure_client()` uses that temporary sanitization only
  around `SubprocessCLITransport.connect()`
- `ProcessTurnAdapter.execute()` no longer re-sanitizes the parent environment
  before `process_turn()`

This preserves the `E2BIG` mitigation for child-process spawn while preventing
the gateway from losing runtime secrets needed by other features.

## What Was Fixed

### Code Fix

Updated:

- `src/universal_agent/execution_engine.py`

Behavioral change:

1. child-process env sanitization is now scoped to the actual SDK subprocess
   spawn window
2. the parent gateway environment is restored immediately afterward
3. the main `process_turn()` execution path no longer strips runtime secrets out
   of the live gateway process

### Regression Coverage

Added and updated:

- `tests/gateway/test_env_sanitization.py`

The tests now verify:

1. the spawn-time child env is sanitized
2. the parent env is restored after SDK client initialization
3. `process_turn()` still sees proxy credentials such as `PROXY_USERNAME` and
   `PROXY_PASSWORD`

### Temporary Diagnostics During the Incident

A short-lived ops-authenticated debug route was added to verify proxy-env
presence inside the running gateway process and then removed after the incident
was resolved.

That temporary route is no longer present in production.

## What Was Kept Even Though It Was Not the Final Fix

The repo-managed systemd unit work remains valid and should stay:

- base service unit templates under `deployment/systemd/templates/`
- `scripts/install_vps_systemd_units.sh`
- deploy workflow updates that install canonical units from the repo

Reason:

Even though those changes were not the root fix for this incident, they still
reduce service-definition drift between deploy-time expectations and live host
state.

## Verification That Closed the Incident

The incident was considered resolved only after all of the following were true
on the real VPS:

1. the service was running on `100.106.113.93`
2. `/api/v1/health` returned `deployment_profile.profile = "vps"`
3. the temporary debug instrumentation confirmed the live gateway still had
   access to proxy env values after request handling began
4. `POST /api/v1/youtube/ingest` succeeded for the failing YouTube video
5. the response reported `proxy_mode = "webshare"`
6. the temporary debug route was removed and the ingest endpoint still worked

## Why Earlier Checks Were Misleading

### Wrong-Host Checks

A correct response from the wrong machine is still the wrong evidence.

The desktop host correctly reported `local_workstation`, but that fact said
nothing about the production VPS.

### Fresh Process vs Live Service

A fresh one-off Python process on the VPS could bootstrap proxy secrets even
while the long-running gateway process was losing them later.

That distinction was the clue that the problem was not only bootstrap, but also
post-bootstrap runtime mutation.

### `/proc/<pid>/environ` Has Limits

For this incident, `/proc/<pid>/environ` was not sufficient evidence about the
runtime state that application code observed later. The decisive evidence came
from in-process checks and end-to-end endpoint behavior.

## Lasting Lessons

1. Verify the exact target host before building a theory.
2. Differentiate bootstrap-time state from long-running runtime state.
3. If a fresh process and a live service disagree, look for state mutation after
   initialization.
4. `E2BIG` mitigation must sanitize child-process env only, not the parent
   service runtime.
5. Temporary diagnostics are acceptable in a production incident if they are
   authenticated, minimal, and removed immediately after use.

## Files Most Relevant to the Final Fix

- `src/universal_agent/execution_engine.py`
- `src/universal_agent/youtube_ingest.py`
- `tests/gateway/test_env_sanitization.py`
- `docs/03_Operations/99_Tutorial_Pipeline_Architecture_And_Operations.md`
- `docs/03_Operations/102_E2BIG_Kernel_Limits_And_Prompt_Architecture_2026-03-22.md`
- `docs/03_Operations/103_Debugging_Lessons_Living_Document.md`

## Bottom Line

The final outage was not caused by the VPS being stuck in
`local_workstation`.

The production VPS was correctly in `vps` mode. The real failure was that the
gateway's `E2BIG` protection logic mutated the parent process environment and
stripped away Infisical-loaded proxy secrets that the YouTube ingest path
needed later.

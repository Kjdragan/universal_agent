# Production Deploy Incident - March 12, 2026

## Summary

Production broke immediately after commit `ca1c9a4` (`Fix python executable ownership in prod deploy (#21)`) deployed to `main`.

Primary user-visible symptoms:

1. `https://app.clearspringcg.com/api/dashboard/gateway/api/v1/factory/capabilities` returned `502`
2. HQ-gated dashboard navigation items such as `Corporation` and `Supervisor Agents` disappeared
3. the production API service was not available behind the gateway

Production was restored by hotfix commit `28ea9ae` (`fix: rebuild stale prod venv as ua`).

## Impact

The production gateway could not reach the API successfully, so dashboard capability discovery failed.

Because the web UI hides HQ-only navigation when factory capabilities cannot be loaded, this API outage presented as both:

1. backend unavailability
2. missing dashboard tabs

## Timeline

### Last known good production state

- Good production commit: `587c8b6`
- Successful production deploy run:
  - `22988314619`
  - https://github.com/Kjdragan/universal_agent/actions/runs/22988314619

### Regression introduced

- Bad commit: `ca1c9a4`
- Failed production deploy run:
  - `22988378273`
  - https://github.com/Kjdragan/universal_agent/actions/runs/22988378273

### Recovery

- Recovery commit: `28ea9ae`
- Successful production deploy run:
  - `22988561508`
  - https://github.com/Kjdragan/universal_agent/actions/runs/22988561508
- Successful debug verification run:
  - `22988595588`
  - https://github.com/Kjdragan/universal_agent/actions/runs/22988595588

## Root Cause

The production deploy workflow had already encountered a corrupted `.venv` whose interpreter symlink pointed to a Python location that the `ua` service user could not traverse.

Commit `ca1c9a4` made a correct partial change:

1. run `uv python install 3.12` as `ua`
2. run `uv sync ...` as `ua`

But it missed the critical state transition:

1. the old `.venv` still existed
2. `.venv/bin/python3` still pointed to an inaccessible interpreter path
3. `uv sync` attempted to canonicalize that existing interpreter path
4. `uv sync` failed before it could rebuild or repair the environment

Observed failure signature from the production deploy log:

```text
error: Failed to query Python interpreter
Caused by: failed to canonicalize path `/opt/universal_agent/.venv/bin/python3`: Permission denied (os error 13)
```

This was the decisive evidence. The workflow was not failing at service restart time anymore; it was failing during dependency sync because the stale virtual environment was already unusable.

## Why The UI Tabs Disappeared

The missing UI tabs were a downstream symptom, not a separate frontend bug.

The dashboard layout checks factory capabilities through the production gateway. When that request fails, HQ-only navigation items are hidden.

Observed failure signature from the public capabilities endpoint before the fix:

```json
{"detail":"Gateway upstream unavailable.","upstream":"http://127.0.0.1:8002/api/v1/factory/capabilities","error":"fetch failed"}
```

After recovery, the same endpoint returned `200` and reported `factory_role: HEADQUARTERS`, which restored the HQ-gated navigation.

## Fix Applied

Hotfix commit `28ea9ae` changed production deployment behavior so that the workflow now:

1. transfers repository ownership to `ua`
2. checks whether `ua` can resolve `.venv/bin/python3`
3. removes `.venv` only if the interpreter path is not accessible to `ua`
4. rebuilds the environment with `uv` as `ua`

This keeps good virtual environments intact while self-healing corrupted ones.

The debug workflow was also repaired:

1. upgraded from `tailscale/github-action@v3` to `@v4`
2. added `.venv/bin/python3` symlink diagnostics

This matters because the prior debug workflow was independently broken by a `401 Unauthorized` action-download failure and could not be trusted during incident response.

## Verification

### Backend verification

Confirmed after the hotfix:

1. production deploy run completed successfully
2. debug workflow completed successfully
3. `https://app.clearspringcg.com/api/dashboard/gateway/api/v1/factory/capabilities` returned `200`

### UI verification

Confirmed via live browser inspection after recovery:

1. `Corporation` tab present
2. `Supervisor Agents` tab present
3. dashboard rendered in HQ mode

## Prevention

The following rules should be treated as the durable prevention set for this incident class:

1. Any workflow change that moves `uv` execution under a different user must account for already-existing `.venv` state.
2. Production deploy logic must validate interpreter accessibility before attempting `uv sync`.
3. Debug workflows must be kept current enough to remain usable during incidents.
4. Public capability endpoints are a valid smoke test for both backend health and HQ-gated UI availability.
5. When hotfixes land on `main`, reconcile `main` back into `develop` promptly so the release model remains coherent.

## Operational Follow-up

After the production hotfix:

1. `main` was ahead of `develop`
2. this required reconciliation back into `develop`
3. staging should be redeployed from the reconciled `develop` head before the next normal feature release

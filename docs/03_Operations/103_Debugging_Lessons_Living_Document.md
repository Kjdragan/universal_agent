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

## Seed Questions For Future Entries

When adding a new lesson, answer these:

1. What did we believe too early?
2. What evidence finally disproved it?
3. What boundary actually failed: host, process, transport, cache, credential,
   or code path?
4. What verification step should become standard next time?
5. What code or deployment pattern should we standardize to prevent recurrence?

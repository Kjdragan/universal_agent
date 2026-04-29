# Concurrency & Harness Reliability Fixes

- Bundle ID: `reliability-fixes`
- Recommended variant: `hardening_checklist`
- UA value: High - UA performs web fetches and parallel requests.
- Agent-system value: High - Prevents agent death during common tasks.

## Summary

Specific fixes for WebFetch stalls on huge pages, Proxy 204 crashes, and intermittent API 400s from cache-control races.

## Why Now

These fixes address common 'hang' issues in agent frameworks.

## For Kevin

They patched three specific bugs:
1. **WebFetch Stalls:** Large pages no longer hang.
2. **Proxy 204s:** Previously crashed session.
3. **API 400s:** Cache-control race condition on parallel requests.

If UA has custom `web_fetch` or parallel request logic, audit it for these specific edge cases.

## For UA

**Code Pattern to Avoid:**
```python
# Bad: Assumes 204 has a body
if response.status == 204:
    data = response.json() # Crash potential
```

**Correct Pattern:**
```python
if response.status == 204:
    return # No content
```

## Canonical Sources


## Variants

### Reliability Hardening Checklist

- Key: `hardening_checklist`
- Intent: Prevent the specific crashes identified by Anthropic.
- Applicability: `["Shared", "UA"]`
- Confidence: `high`

#### Safe Fetch Handling

- Kind: `prompt_pattern`
- Rationale: To handle large pages without hanging or crashing.

When implementing WebFetch, enforce:
1. **Max Size Limits:** Set a `max_content_length` (e.g., 200KB). If exceeded, truncate or fail gracefully rather than hanging.
2. **Timeout Enforcement:** Always use a strict timeout (e.g., 10s) for fetches.
3. **204 Safety:** Explicitly check `if status == 204: return None` before attempting to parse body/JSON.

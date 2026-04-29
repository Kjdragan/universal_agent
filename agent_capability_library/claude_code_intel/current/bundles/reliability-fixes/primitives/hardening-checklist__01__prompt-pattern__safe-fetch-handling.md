# Safe Fetch Handling

- Variant: `hardening_checklist`
- Kind: `prompt_pattern`
- Rationale: To handle large pages without hanging or crashing.

When implementing WebFetch, enforce:
1. **Max Size Limits:** Set a `max_content_length` (e.g., 200KB). If exceeded, truncate or fail gracefully rather than hanging.
2. **Timeout Enforcement:** Always use a strict timeout (e.g., 10s) for fetches.
3. **204 Safety:** Explicitly check `if status == 204: return None` before attempting to parse body/JSON.

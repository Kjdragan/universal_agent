# Identity + Email Guardrails (Code-Verified)

This document captures the current identity and recipient enforcement behavior.

## Identity Registry
Loaded from:
- `UA_IDENTITY_REGISTRY_PATH` (if set), otherwise `identity_registry.json`
- `UA_PRIMARY_EMAIL`
- `UA_SECONDARY_EMAILS`
- `UA_EMAIL_ALIASES`

Code: `src/universal_agent/identity/registry.py`

## Alias Resolution
Aliases like `"me"` resolve to `UA_PRIMARY_EMAIL`.
If no primary email is set, alias resolution does nothing.

## Recipient Policy
If `UA_ENFORCE_IDENTITY_RECIPIENTS=1`, recipients must be:
- In the identity registry, or
- Present in the user query text.

Code: `src/universal_agent/identity/registry.py` → `validate_recipient_policy`.

## Observability
On startup, the CLI logs the registry state:
- primary email
- alias keys

Code: `src/universal_agent/main.py` (setup_session).


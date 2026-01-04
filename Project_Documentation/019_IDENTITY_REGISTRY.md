## Identity Registry (Personal Data Resolution)

Purpose: make side-effect tools (email) deterministic by resolving human-friendly
aliases like "me" into canonical addresses before tool execution.

### Why this exists
- Letta memory is useful for reasoning, but tool calls must be deterministic.
- Email send is side-effecting; it requires explicit, validated recipients.
- This registry provides a single source of truth for personal contact data.

### Source of truth
The registry is loaded from:
1) Environment variables (preferred for local use)
2) Optional JSON file via `UA_IDENTITY_REGISTRY_PATH`
3) `identity_registry.json` in the repo root (auto-loaded if present)

### Environment variables
```
UA_PRIMARY_EMAIL=kevin.dragan@outlook.com
UA_SECONDARY_EMAILS=kevinjdragan@gmail.com
UA_EMAIL_ALIASES=me:kevin.dragan@outlook.com,my gmail:kevinjdragan@gmail.com
```

### Optional JSON registry
```
{
  "primary_email": "kevin.dragan@outlook.com",
  "secondary_emails": ["kevinjdragan@gmail.com"],
  "aliases": {
    "me": "kevin.dragan@outlook.com",
    "my gmail": "kevinjdragan@gmail.com"
  }
}
```

Sample file: `identity_registry.sample.json` (copy to `identity_registry.json` and edit).

### How resolution works
- "me" / "my email" / "myself" -> `primary_email`
- "my gmail" -> first gmail address in `secondary_emails` (or alias override)
- "my outlook" -> first outlook/hotmail address (or alias override)
- Explicit aliases in `UA_EMAIL_ALIASES` / JSON override defaults

If an alias is used but cannot be resolved, the tool call is denied with a
clear error so the model can ask for clarification.

### Tool coverage
- Direct `GMAIL_SEND_EMAIL` calls
- Nested `GMAIL_SEND_EMAIL` inside `COMPOSIO_MULTI_EXECUTE_TOOL`
- The resolver updates `to`, `recipient_email`, `cc`, and `bcc`

### Recipient policy enforcement (optional)
Enable strict policy so recipients must be known or explicitly mentioned in the user request:
```
UA_ENFORCE_IDENTITY_RECIPIENTS=1
```

Behavior:
- Allow if recipient matches `primary_email` / `secondary_emails`
- Allow if the exact email appears in the user request
- Deny otherwise (prevents accidental sends to unexpected addresses)

### Letta memory integration
Letta can store identity details for reasoning, but the registry remains the
authoritative source for side-effect tool inputs. This avoids accidental sends
to the wrong address and keeps behavior deterministic.

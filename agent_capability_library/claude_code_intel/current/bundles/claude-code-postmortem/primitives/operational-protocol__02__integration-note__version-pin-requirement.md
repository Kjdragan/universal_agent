# Version Pin Requirement

- Variant: `operational_protocol`
- Kind: `integration_note`
- Rationale: Fixes are pinned to v2.1.116+.

Ensure any dependency on `anthropic-agent-sdk` or `claude-code` CLI is pinned to `>=2.1.116`. Previous versions contain the harness race conditions.

# Tool Policies & `side_effect_class` Notes (Durable Jobs)

This note is based on your current logic in `main.py` (dedupe decision in `on_pre_tool_use_ledger`) and the uploaded `tool_policies.yaml`.

## What you have today (and why it mostly works)

- `side_effect_class` is persisted per tool call (via the ledger) and is used on resume to decide whether the tool should be **deduped** (skipped + served from prior receipt/result) versus re-run.
- Current classes: `external`, `memory`, `local`, `read_only`.
- Current rule of thumb (as you described): **dedupe anything not `read_only`** unless `replay_policy` explicitly overrides.

Your `tool_policies.yaml` already gives you a maintainable, centralized place to tune this across the tool universe.

## How `side_effect_class` is initially determined

1) **At tool-call preparation time** (ledger insert / prepare step), your classification layer assigns:
- `tool_namespace` (e.g., composio, mcp, claude_code)
- `side_effect_class`
- optional `replay_policy`

2) Classification is driven by:
- explicit policy patterns (regexes) in `tool_policies.yaml`
- keyword heuristics (`action_keywords`) for “verb-y” tools that likely mutate the world

3) If nothing matches, the system should default conservatively (treat as side-effect) to avoid duplicates.

## Can `side_effect_class` be overridden at runtime?

Yes — **best practice** is “configuration overrides, not code patches” for classification drift:

- **Primary mechanism:** `UA_TOOL_POLICIES_PATH` (or your equivalent env var) to point at a policy file.
- Optional enhancement: support `--tool-policies <path>` in CLI so tests can swap policy sets.
- If you ever need emergency overrides, add a very small “runtime overlay” file (loaded after the base YAML) so you can hot-fix one tool without editing the main policy set.

## What happens if `side_effect_class` is missing/invalid (and what it should do)

### Current risk
Your current logic reads the prior entry and does:

- if `replay_policy` exists: dedupe if `REPLAY_EXACT`
- else dedupe if `side_effect_class in ("external", "memory", "local")`
- otherwise: **do not dedupe**

If a ledger row is missing `side_effect_class` (or it’s invalid), you can accidentally treat a side-effect tool as “safe to replay,” which is the **classic double-send trap**.

### Recommended safe fallback
If `side_effect_class` is missing/invalid for a prior SUCCEEDED tool call, default to **dedupe** unless the tool is explicitly known read-only.

That is:

- missing/invalid → treat as `external` (dedupe) **or**
- missing/invalid → treat as “unknown” and consult policy mapping; if unknown → dedupe

This makes “schema gaps” fail safe.

## A small semantic nit: `REPLAY_EXACT` naming
In your current control flow, `REPLAY_EXACT` behaves like “dedupe by exact prior result,” not “re-execute.”

Two ways to clean this up (pick whichever is least disruptive):
- Rename to something like `DEDUP_EXACT` (clear intent), or
- Keep the name but ensure documentation consistently says it means “exact replay from receipt/result.”

## Suggested next improvements to `tool_policies.yaml`
Your current YAML is a good baseline. Two easy refinements:

1) Add explicit rules for high-risk tool families you know you’ll use (email, upload, write/delete).
2) Add explicit allowlist patterns for read-only tools across namespaces (MCP reads, file listings, searches).

## Suggested tests to add (fast, high confidence)
- **Policy coverage test:** list all observed `raw_tool_name`s from last N runs; fail if any are “unknown” in strict mode.
- **Missing field test:** simulate a prior entry with missing `side_effect_class` and assert it defaults to dedupe.
- **Classification snapshot test:** given a fixed list of tool names, classification output is stable (prevents accidental drift).

---

If you paste your `durable/classification.py` (or wherever `classify_tool(...)` lives), I can point out the exact lines to adjust for the “missing/invalid → fail safe” behavior.

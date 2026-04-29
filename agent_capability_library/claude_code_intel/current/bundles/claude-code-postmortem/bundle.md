# Quality Regression Post-Mortem & Fixes

- Bundle ID: `claude-code-postmortem`
- Recommended variant: `operational_protocol`
- UA value: High - Understanding root causes of 'agentic' failures helps prevent similar regressions in UA.
- Agent-system value: High - Validates that tooling/harness is the primary failure point for agent reliability, not model intelligence.

## Summary

Anthropic published a rare post-mortem regarding quality slippage in Claude Code, identifying root causes in the Agent SDK harness and system prompt isolation. Fixes deployed in v2.1.116+.

## Why Now

Active remediation phase; usage limits reset; new quality assurance protocols being adopted by Anthropic.

## For Kevin

This is a high-value learning opportunity. The team admitted that 'system prompt changes' and 'harness' issues caused the regression. 

1. **Read the Post-Mortem:** [Link](https://www.anthropic.com/engineering/april-23-postmortem)
2. **Adopt the Fix:** They mentioned creating evals specifically for *isolated* system prompt changes. If UA updates its system prompt, do we have a test suite that catches regressions in *tool usage* specifically?
3. **Monitor Version:** Ensure you are on v2.1.116+ if using Claude Code/Agent SDK directly.

## For UA

The post-mortem identifies three specific issues:
1. Isolation of system prompt changes.
2. Harness changes.
3. Config drift.

**Action:** Ensure UA's "harness" (the code that executes the model's decisions) is version-controlled separately from the model logic. When we change how UA handles tools, we should run a specific regression suite.

## Canonical Sources

- [An update on recent Claude Code quality reports](https://www.anthropic.com/engineering/april-23-postmortem) — `vendor_web` / `www.anthropic.com`

## Variants

### Ops Protocol: Regression Detection

- Key: `operational_protocol`
- Intent: To establish testing protocols that prevent the type of regression Anthropic experienced.
- Applicability: `["UA"]`
- Confidence: `high`

#### Isolation Testing for System Prompts

- Kind: `workflow_recipe`
- Rationale: Anthropic noted that system prompt changes broke tool usage in non-obvious ways.

1. **Baseline Test:** Create a set of 50 agentic tasks (web fetch, file edit, terminal command).
2. **Prompt Update:** When changing the UA system prompt, run this suite.
3. **Focus:** Don't just check 'accuracy'; check for *hangs*, *refusal to act*, and *hallucinated tool usage*.
4. **Control:** Run the test against the old prompt and the new prompt in parallel to diff 'agentic' behavior.

#### Version Pin Requirement

- Kind: `integration_note`
- Rationale: Fixes are pinned to v2.1.116+.

Ensure any dependency on `anthropic-agent-sdk` or `claude-code` CLI is pinned to `>=2.1.116`. Previous versions contain the harness race conditions.

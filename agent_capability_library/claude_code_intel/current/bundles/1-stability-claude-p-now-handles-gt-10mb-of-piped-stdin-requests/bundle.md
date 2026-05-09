# 1/ Stability

• claude -p now handles &gt;10MB of piped stdin
• Requests

- Bundle ID: `1-stability-claude-p-now-handles-gt-10mb-of-piped-stdin-requests`
- Recommended variant: `ua-adaptation`
- UA value: Likely useful for extending UA workflows and agent building blocks.
- Agent-system value: Likely transferable into standalone agent-system projects.

## Summary

1/ Stability

• claude -p now handles &gt;10MB of piped stdin
• Requests resume cleanly after waking a Mac from sleep
• Memory stays bounded when a stdio MCP server writes non-protocol data to stdout (was growing past 10GB)
• Output now appears reliably after 

## Why Now

Recent ClaudeDevs updates suggest this capability is new enough that model cutoffs may miss it, so we should materialize it now.

## For Kevin

## What changed

1/ Stability

• claude -p now handles &gt;10MB of piped stdin
• Requests resume cleanly after waking a Mac from sleep
• Memory stays bounded when a stdio MCP server writes non-protocol data to stdout (was growing past 10GB)
• Output now appears reliably after thinking completes

## Why it matters

This is most valuable if we can turn it into reusable building blocks quickly, before the underlying capability disappears into stale model knowledge.


## For UA

## UA Adoption Package

- Materialize multiple implementation primitives, not just a narrative summary.
- Keep the output reusable for UA and standalone Agent SDK projects.


## Canonical Sources


## Variants

### UA Adaptation

- Key: `ua-adaptation`
- Intent: Translate the capability into reusable UA building blocks.
- Applicability: `["UA", "Shared"]`
- Confidence: `medium`

#### UA adaptation pattern

- Kind: `ua_adaptation_pattern`
- Rationale: Turn the capability into a repeatable UA integration pattern.

## UA Adaptation Pattern

- Anchor on the canonical source(s): linked technical references.
- Distill the feature into reusable prompt/workflow/code assets before wiring it into runtime behavior.
- Prefer adding this as a reusable building block for Simone/Cody and future client-system builds.


#### Workflow recipe

- Kind: `workflow_recipe`
- Rationale: Capture the control flow needed to use the capability repeatedly.

## Workflow Recipe

1. Read the canonical linked source.
2. Extract the behavior contract.
3. Generate prompt/code/workflow primitives.
4. Validate the most promising path in UA.


### Agent SDK Transfer

- Key: `agent-sdk-transfer`
- Intent: Translate the capability into standalone agent-system primitives.
- Applicability: `["Agent SDK", "Shared"]`
- Confidence: `medium`

#### Agent SDK adaptation pattern

- Kind: `agent_sdk_adaptation_pattern`
- Rationale: Frame the capability for standalone agent systems.

## Agent SDK Adaptation Pattern

- Translate the capability into standalone agent-system patterns, not just IDE assistance.
- Capture the control flow, prompt shape, and code surface required to reproduce the feature in a new project.
- Preserve source provenance so future coding agents can rebuild from official references.


#### Prompt pattern

- Kind: `prompt_pattern`
- Rationale: Capture the reusable instruction shape behind the capability.

## Prompt Pattern

```text
Given the official feature docs and any linked demo material, synthesize:
1. the minimal agent behavior contract,
2. the reusable prompt skeleton,
3. the workflow boundaries,
4. the failure modes we should guard against.
```

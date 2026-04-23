# Building agents that reach production systems with MCP | Claude

- Bundle ID: `building-agents-that-reach-production-systems-with-mcp-claude`
- Recommended variant: `ua-adaptation`
- UA value: Likely useful for extending UA workflows and agent building blocks.
- Agent-system value: Likely transferable into standalone agent-system projects.

## Summary

New blog: Building agents that reach production systems with MCP.

When should agents use direct APIs vs CLIs vs MCP? Plus patterns for building MCP servers, context-efficient clients and pairing MCP with skills.

https://t.co/JEogw5vWly

## Why Now

Recent ClaudeDevs updates suggest this capability is new enough that model cutoffs may miss it, so we should materialize it now.

## For Kevin

## What changed

New blog: Building agents that reach production systems with MCP.

When should agents use direct APIs vs CLIs vs MCP? Plus patterns for building MCP servers, context-efficient clients and pairing MCP with skills.

https://t.co/JEogw5vWly

## Why it matters

This is most valuable if we can turn it into reusable building blocks quickly, before the underlying capability disappears into stale model knowledge.


## For UA

## UA Adoption Package

- Use the linked official source as the canonical reference.
- Materialize multiple implementation primitives, not just a narrative summary.
- Keep the output reusable for UA and standalone Agent SDK projects.


## Canonical Sources

- [Building agents that reach production systems with MCP | Claude](https://claude.com/blog/building-agents-that-reach-production-systems-with-mcp) — `generic_web` / `claude.com`
- [Building agents that reach production systems with MCP | Claude](https://claude.com/blog/building-agents-that-reach-production-systems-with-mcp) — `generic_web` / `claude.com`

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

- Anchor on the canonical source(s): Building agents that reach production systems with MCP | Claude, Building agents that reach production systems with MCP | Claude.
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

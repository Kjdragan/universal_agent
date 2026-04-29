# Isolation Testing for System Prompts

- Variant: `operational_protocol`
- Kind: `workflow_recipe`
- Rationale: Anthropic noted that system prompt changes broke tool usage in non-obvious ways.

1. **Baseline Test:** Create a set of 50 agentic tasks (web fetch, file edit, terminal command).
2. **Prompt Update:** When changing the UA system prompt, run this suite.
3. **Focus:** Don't just check 'accuracy'; check for *hangs*, *refusal to act*, and *hallucinated tool usage*.
4. **Control:** Run the test against the old prompt and the new prompt in parallel to diff 'agentic' behavior.

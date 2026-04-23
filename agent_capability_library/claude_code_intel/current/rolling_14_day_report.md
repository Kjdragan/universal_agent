# Rolling 14-Day Claude Code Builder Brief

## For Kevin

- Over the last 14 days, the most meaningful Claude Code developments clustered around code.claude.com, claude.com, platform.claude.com.
- The practical pattern is that new capability announcements quickly expand into more concrete usage guidance in linked official docs and demos.
- The current opportunity is to turn those updates into reusable agent-building primitives instead of leaving them as interesting release notes.

### Most Actionable Changes

- `strategic_follow_up` / tier `4`: New in Claude Code: /ultrareview (research preview) runs a fleet of bug-hunting agents in the cloud.

Findings land in the CLI or Desktop automatically. Run it before merging critical changes—auth, data migrations, etc. 
- `demo_task` / tier `3`: New blog: Building agents that reach production systems with MCP.

When should agents use direct APIs vs CLIs vs MCP? Plus patterns for building MCP servers, context-efficient clients and pairing MCP with skills.

https:
- `kb_update` / tier `2`: Run `claude update` to try it out. Learn more in the docs: https://t.co/NhIw6ayYXZ

## For UA

- Treat the official linked docs and repos as the canonical implementation layer.
- Prefer building reusable prompt, workflow, code, and adaptation primitives instead of one-off experiments.
- Use the rolling capability bundles below as the first retrieval target when building new UA or Agent SDK functionality.

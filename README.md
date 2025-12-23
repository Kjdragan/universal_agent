# Universal Agent

A standalone agent using Claude Agent SDK with Composio Tool Router integration.

## Features

- 🤖 Claude Agent SDK for agentic workflows
- 🔧 Composio Tool Router for 500+ tool integrations
- 📊 Logfire tracing for observability
- 📁 Automatic workspace and artifact management
- 🔍 Observer pattern for async result processing

## Quick Start

```bash
# Install dependencies
uv sync

# Set up environment variables
cp .env.example .env
# Edit .env with your API keys

# Run the agent
uv run src/universal_agent/main.py
```

## Environment Variables

Required:
- `COMPOSIO_API_KEY` - Composio authentication
- `LOGFIRE_TOKEN` - Logfire tracing (optional)

## Documentation

- [Current Context](docs/000_CURRENT_CONTEXT.md) - Project state and next steps
- [Hooks Architecture](docs/004_HOOKS_ARCHITECTURE.md) - MCP mode, Observer pattern
- [Lessons Learned](docs/010_LESSONS_LEARNED.md) - Project-specific patterns and gotchas

## Project Structure

```
universal_agent/
├── src/universal_agent/
│   └── main.py              # Main agent implementation
├── docs/                    # Documentation
├── tests/                   # Test files
├── AGENT_RUN_WORKSPACES/    # Runtime session artifacts (gitignored)
├── pyproject.toml           # Dependencies
└── .env                     # Environment variables (gitignored)
```

## License

MIT

# HANDOFF 04: Discord MCP Tool Setup

**Parent Document:** `Discord_UA_Master_Plan.md`
**Priority:** Can be done anytime (standalone, quick win)
**Complexity:** Low — configuration + existing open-source tool
**Prerequisites:** Discord bot token created

---

## Purpose

Set up a Discord MCP (Model Context Protocol) server so that AI agents (Claude Code, CODIE, ATLAS, or any MCP-compatible client) can interact with Discord as a tool — reading messages, searching channels, getting server info, and sending messages. This is a **standalone utility** that provides immediate value even before the full Intelligence Daemon is built.

### Use Cases

1. **Development tool**: While building the Discord integration, Claude Code can directly query Discord channels to understand their structure, test API interactions, and validate assumptions.
2. **Agent research tool**: VP agents (ATLAS) can query Discord channels during mission execution to find community discussions, workarounds, or expert insights relevant to their task.
3. **Simone intelligence**: Simone can commission "check what people are saying about X on Discord" as a tool call during task triage.

## Recommended MCP Server: netixc/mcp-discord (Python)

**Repository**: https://github.com/netixc/mcp-discord

**Why this one:**
- Written in Python (matches UA stack)
- Provides core tools: get_server_info, list_members, read messages, send messages
- Lightweight, minimal dependencies
- Active maintenance

### Installation

```bash
# Clone the repo
git clone https://github.com/netixc/mcp-discord.git
cd mcp-discord

# Create virtual environment
uv venv
source .venv/bin/activate

# Install (Python 3.13+ needs audioop-lts)
uv pip install audioop-lts  # if Python 3.13+
uv pip install -e .
```

### Configuration for Claude Code

Add to your Claude Code MCP configuration (`.mcp.json` in the UA repo root or `~/.claude/mcp.json`):

```json
{
  "mcpServers": {
    "discord": {
      "command": "uv",
      "args": [
        "--directory", "/path/to/mcp-discord",
        "run", "mcp-discord"
      ],
      "env": {
        "DISCORD_TOKEN": "your_discord_bot_token",
        "DEFAULT_SERVER_ID": "your_server_id"
      }
    }
  }
}
```

**For Infisical integration**: Instead of hardcoding the token, use the existing UA pattern for secret injection. The implementing agent should check how other MCP servers are configured in the UA's `.mcp.json`.

### Configuration for Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or equivalent:

```json
{
  "mcpServers": {
    "discord": {
      "command": "uv",
      "args": [
        "--directory", "/path/to/mcp-discord",
        "run", "mcp-discord"
      ],
      "env": {
        "DISCORD_TOKEN": "your_discord_bot_token",
        "DEFAULT_SERVER_ID": "your_server_id"
      }
    }
  }
}
```

### Available Tools

Once configured, the MCP server exposes these tools to any MCP-compatible client:

| Tool | Description | Example Use |
|------|------------|-------------|
| `get_server_info` | Get detailed info about a Discord server | "What channels does the Anthropic Discord have?" |
| `list_members` | List server members with roles | "Who are the admins of this server?" |
| `read_messages` | Read recent messages from a channel | "What are people discussing in #api-help?" |
| `send_message` | Send a message to a channel | "Post a status update to #mission-status" |

### Alternative: IQAIcom/mcp-discord (Node.js, More Features)

If richer functionality is needed (forum management, webhook interaction, reactions, channel creation):

**Repository**: https://github.com/IQAIcom/mcp-discord

```json
{
  "mcpServers": {
    "discord": {
      "command": "npx",
      "args": ["-y", "@iqai/mcp-discord"],
      "env": {
        "DISCORD_TOKEN": "your_discord_bot_token",
        "SAMPLING_ENABLED": "true",
        "TRANSPORT": "stdio"
      }
    }
  }
}
```

This provides additional tools for creating/deleting channels, managing categories, handling reactions, and working with webhooks. It's Node.js-based, which is fine for an MCP server (it runs as a separate process regardless).

### Alternative: Docker-based (SaseQ/discord-mcp)

If Docker is preferred for isolation:

```json
{
  "mcpServers": {
    "discord": {
      "command": "docker",
      "args": [
        "run", "--rm", "-i",
        "-e", "DISCORD_TOKEN=your_token",
        "-e", "DISCORD_GUILD_ID=your_server_id",
        "saseq/discord-mcp:latest"
      ]
    }
  }
}
```

## Integration with UA Agent Toolkit

For Phase 3 (VP agents using Discord as a research tool), the MCP server gets added to the agent's available tools. When ATLAS is executing a research mission, the agent can:

1. **Search for relevant discussions**: "Read recent messages from #api-help in the Anthropic Discord about webhook reliability"
2. **Check for announcements**: "Get the latest messages from #announcements in the LangChain Discord"
3. **Cross-reference community knowledge**: "Search for discussions about rate limiting in the OpenAI Discord"

This is complementary to the Intelligence Daemon (HANDOFF_02). The daemon provides historical depth (everything captured over time). The MCP tool provides real-time interactive queries.

---

## Implementation Notes

1. **This can be set up in 15 minutes.** It's the quickest win in the entire Discord integration project.
2. **The same bot token works for both the MCP tool and the Intelligence Daemon.** One bot application, one token, multiple uses.
3. **The MCP tool runs on-demand** (started when an agent needs it), not as a persistent daemon. It doesn't conflict with the Intelligence Daemon's persistent WebSocket connection.
4. **Test it immediately**: Once configured, ask Claude Code to "list the channels in my Discord server" or "read the last 5 messages from #general in the Anthropic Discord" to verify it works.
5. **For the owner's own development workflow**: Having Discord MCP available in Claude Desktop or the IDE means the owner can query Discord directly from their development environment while working on the UA.

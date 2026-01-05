# MCP Servers Architecture

**Document Version**: 2.0
**Last Updated**: 2026-01-05
**Status**: ACTIVE
**Related Files:**
- `src/mcp_server.py` - Local Toolkit MCP server implementation
- `src/tools/workbench_bridge.py` - WorkbenchBridge for file transfer
- `src/universal_agent/main.py:4427-4470` - MCP server configuration

---

## Overview

The Universal Agent integrates **six MCP (Model Context Protocol) servers** to provide a comprehensive toolset for local operations, external actions, web content extraction, and multimodal analysis.

| Server | Type | Purpose |
|--------|------|--------|
| `local_toolkit` | stdio | Local file ops, web extraction (10+ tools) |
| `composio` | HTTP | 500+ SaaS integrations (Gmail, Slack, etc.) |
| `edgartools` | stdio | SEC Edgar financial research |
| `video_audio` | stdio | FFmpeg video/audio editing |
| `youtube` | stdio | yt-dlp video downloads |
| `zai_vision` | stdio | GLM-4.6V image/video analysis |

---

## 1. Local Toolkit MCP Server (Custom)

### Connection Details

| Property | Value |
|----------|-------|
| **Type** | stdio |
| **Command** | `python src/mcp_server.py` |
| **Server Name** | `local_toolkit` |
| **Code Reference** | `src/mcp_server.py` |

### Tool Catalog

The local toolkit provides **10+ tools**:

| Tool | Purpose |
|------|---------|
| `crawl_parallel` | Parallel web extraction using crawl4ai |
| `write_local_file` | Write content to local files |
| `read_local_file` | Read local file contents |
| `list_directory` | List directory contents |
| `workbench_download` | Download from remote workbench |
| `workbench_upload` | Upload to remote workbench |
| `upload_to_composio` | One-step S3 upload for attachments |
| `execute_python_code` | Execute Python code locally |
| `execute_bash_command` | Execute bash commands |
| `search_local_files` | Search files by content |
| `finalize_research` | Create research corpus (report workflow) |
| `read_research_files` | Batch read research files |

---

## 2. Composio MCP Server (Remote Tool Router)

### Connection Details

| Property | Value |
|----------|-------|
| **Type** | HTTP |
| **URL** | Dynamic from `session.mcp.url` |
| **Headers** | `{"x-api-key": "<COMPOSIO_API_KEY>"}` |
| **Server Name** | `composio` |

### Tool Categories

| Category | Examples |
|----------|----------|
| **Search** | COMPOSIO_SEARCH_NEWS, COMPOSIO_SEARCH_WEB |
| **Email** | GMAIL_SEND_EMAIL, OUTLOOK_SEND_EMAIL |
| **Communication** | SLACK_SEND_MESSAGE |
| **Workbench** | COMPOSIO_REMOTE_WORKBENCH, CODEINTERPRETER_GET_FILE_CMD |

---

## 3. External MCP Servers

### edgartools (SEC Edgar Research)

| Property | Value |
|----------|-------|
| **Type** | stdio |
| **Module** | `-m edgar.ai` |
| **Purpose** | SEC filing research |

### video_audio (FFmpeg Editing)

| Property | Value |
|----------|-------|
| **Type** | stdio |
| **Path** | `external_mcps/video-audio-mcp/server.py` |
| **Purpose** | Video trimming, concatenation, effects |

### youtube (Video Downloads)

| Property | Value |
|----------|-------|
| **Type** | stdio |
| **Module** | `-m mcp_youtube` |
| **Purpose** | yt-dlp video/audio downloads |

### zai_vision (GLM-4.6V Analysis)

| Property | Value |
|----------|-------|
| **Type** | stdio |
| **Command** | `npx -y @z_ai/mcp-server` |
| **Purpose** | Image/video analysis |

---

## Configuration

**Location**: `src/universal_agent/main.py` lines 4427-4470

```python
mcp_servers={
    "composio": {
        "type": "http",
        "url": session.mcp.url,
        "headers": {"x-api-key": COMPOSIO_API_KEY}
    },
    "local_toolkit": {
        "type": "stdio",
        "command": sys.executable,
        "args": ["src/mcp_server.py"],
    },
    "edgartools": {
        "type": "stdio",
        "command": sys.executable,
        "args": ["-m", "edgar.ai"],
    },
    "video_audio": {
        "type": "stdio",
        "command": sys.executable,
        "args": ["external_mcps/video-audio-mcp/server.py"],
    },
    "youtube": {
        "type": "stdio",
        "command": sys.executable,
        "args": ["-m", "mcp_youtube"],
    },
    "zai_vision": {
        "type": "stdio",
        "command": "npx",
        "args": ["-y", "@z_ai/mcp-server"],
    },
}
```

---

## Tool Naming Convention

All MCP tools follow:
```
mcp__<server_name>__<tool_name>
```

**Examples**:
| Server | Tool Call |
|--------|-----------|
| `local_toolkit` | `mcp__local_toolkit__write_local_file` |
| `local_toolkit` | `mcp__local_toolkit__crawl_parallel` |
| `composio` | `mcp__composio__COMPOSIO_SEARCH_NEWS` |
| `youtube` | `mcp__youtube__download_video` |

---

**Document Status**: ✅ Active & Updated
**Last System Sync**: 2026-01-05

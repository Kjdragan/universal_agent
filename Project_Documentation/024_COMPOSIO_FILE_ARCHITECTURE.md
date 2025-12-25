# 024: Composio File Architecture - Complete Guide

**Date:** December 24, 2025  
**Status:** Investigation Complete - Implementation Plan Ready  
**Authors:** Kevin Dragan, Antigravity AI

---

## Executive Summary

After extensive investigation, we've established a clear understanding of the three file handling environments in our system. This document provides definitive recommendations for each use case and an implementation plan to simplify our architecture.

> [!IMPORTANT]
> **Key Decision:** Adopt a **local-first architecture**. Use Composio S3 only for tool attachments. Eliminate workbench usage entirely.

---

## Part 1: The Three Environments

### 1.1 Local Environment (Your Machine)

| Aspect | Details |
|--------|---------|
| **Location** | `AGENT_RUN_WORKSPACES/session_*/` |
| **Persistence** | ✅ Permanent until deleted |
| **Access** | Direct file system |
| **Tools** | `read_local_file`, `write_local_file`, `list_directory` |
| **Best For** | All agent work products, research, reports |

### 1.2 Composio S3 (Cloud Storage)

| Aspect | Details |
|--------|---------|
| **Location** | `s3key: "215406/gmail/GMAIL_SEND_EMAIL/..."` |
| **Persistence** | Temporary (Composio managed) |
| **Access** | Via presigned URLs |
| **Native Upload** | ✅ `FileUploadable.from_path()` (Composio SDK) |
| **Native Download** | ✅ Simple HTTP GET to `s3url` |
| **Best For** | Email attachments, Slack files, Drive uploads |

### 1.3 Composio Workbench (Remote Sandbox)

| Aspect | Details |
|--------|---------|
| **Location** | `/home/user/` in remote container |
| **Persistence** | ❌ Ephemeral (destroyed after session) |
| **Access** | Via `COMPOSIO_REMOTE_WORKBENCH` tool |
| **Native Upload** | ❌ **No native method exists** |
| **Native Download** | `CODEINTERPRETER_GET_FILE_CMD` |
| **Best For** | **Nothing - we're eliminating it** |

---

## Part 2: Use Case Decision Matrix

### 2.1 File Operations

| Use Case | Environment | Method | Notes |
|----------|-------------|--------|-------|
| Agent creates report | **Local** | `write_local_file` | Stays on disk |
| Agent reads crawled content | **Local** | `read_local_file` | From `crawl_*.md` |
| Agent processes data | **Local** | Local Python | Not workbench |
| Agent runs web scraper | **Local** | `crawl_parallel` | Crawl4AI local |

### 2.2 Email with Attachments

| Step | Environment | Method |
|------|-------------|--------|
| 1. Agent has local file | Local | `read_local_file` to verify exists |
| 2. Upload to S3 | S3 | `FileUploadable.from_path()` via new MCP tool |
| 3. Send email | Composio | `GMAIL_SEND_EMAIL` with `{s3key, mimetype, name}` |

### 2.3 Slack with File Attachments

| Step | Environment | Method |
|------|-------------|--------|
| 1. Agent has local file | Local | File in `AGENT_RUN_WORKSPACES` |
| 2. Upload to S3 | S3 | Same - `FileUploadable.from_path()` |
| 3. Send message | Composio | `SLACK_SEND_MESSAGE` with file attachment |

### 2.4 Downloading Composio Output Files

| Scenario | What Composio Returns | How to Get File |
|----------|----------------------|-----------------|
| Small data | Inline in JSON response | Parse response |
| Large data/attachment | `s3url` in response | HTTP GET to URL |
| Workbench code output | File in `/home/user/` | **N/A - we don't use workbench** |

### 2.5 Code Execution

| Use Case | Old Way (Deprecated) | New Way (Recommended) |
|----------|---------------------|----------------------|
| Process data | `COMPOSIO_REMOTE_WORKBENCH` | Run locally in agent process |
| Transform files | Workbench Python | Local Python |
| Complex analysis | Remote sandbox | Local execution |

---

## Part 3: Technology Mapping

### 3.1 Composio SDK vs MCP Tool Router

| Feature | SDK Client (`Composio()`) | MCP Tool Router |
|---------|--------------------------|-----------------|
| File auto-upload | ✅ `FileHelper` magic | ❌ Must upload manually |
| File auto-download | ✅ Auto on `file_downloadable` | ❌ Must download manually |
| Tool execution | `composio.tools.execute()` | JSON-RPC MCP calls |
| When to use | S3 uploads only | All Composio tool calls |

### 3.2 What We Use Where

| Component | Used For | Technology |
|-----------|----------|------------|
| **Claude Agent** | Core orchestration | Claude SDK |
| **Tool Router MCP** | All Composio tools (Gmail, Slack, etc.) | MCP (JSON-RPC) |
| **Local MCP Server** | File ops, crawling, S3 upload helper | FastMCP |
| **Composio SDK Client** | ONLY for `FileUploadable.from_path()` | Direct API |

---

## Part 4: Recommendations

### 4.1 Immediate Actions

| Action | Priority | Rationale |
|--------|----------|-----------|
| Replace `upload_to_composio` with native S3 method | 🔴 High | Removes hack, uses native API |
| Delete `workbench_upload` tool | 🔴 High | Redundant, no native support |
| Delete `WorkbenchBridge` class | 🟡 Medium | No longer needed |
| Keep `workbench_download` | 🟢 Low | Edge case only, might delete later |

### 4.2 New Simplified Tool

```python
@mcp.tool()
def upload_file_to_s3(local_path: str, tool_slug: str, toolkit_slug: str) -> str:
    """
    Upload a local file to Composio S3 for use as attachment.
    Returns JSON with s3key, mimetype, name for use in tool calls.
    """
    from composio import Composio
    from composio.core.models._files import FileUploadable
    
    composio = Composio()
    result = FileUploadable.from_path(
        client=composio.client,
        file=local_path,
        tool=tool_slug,
        toolkit=toolkit_slug
    )
    return json.dumps({
        "s3key": result.s3key,
        "mimetype": result.mimetype,
        "name": result.name
    })
```

### 4.3 Updated Agent Workflow for Attachments

**Before (Complex):**
```
1. Call upload_to_composio (hack with base64 + workbench)
2. Hope it works
3. Parse s3key from complex response
4. Call GMAIL_SEND_EMAIL
```

**After (Simple):**
```
1. Call upload_file_to_s3(path, "GMAIL_SEND_EMAIL", "gmail")
2. Get {s3key, mimetype, name}
3. Call GMAIL_SEND_EMAIL with attachment parameter
```

---

## Part 5: Implementation Plan

### Phase 1: Core Tool Changes

| Task | File | Change |
|------|------|--------|
| 1.1 | `src/mcp_server.py` | Replace `upload_to_composio` with `upload_file_to_s3` |
| 1.2 | `src/mcp_server.py` | Delete `workbench_upload` function |
| 1.3 | `src/tools/workbench_bridge.py` | Delete entire file OR remove `upload` method |
| 1.4 | `src/mcp_server.py` | Simplify imports (remove bridge dependency for upload) |

### Phase 2: Agent Prompt Updates

| Task | Location | Change |
|------|----------|--------|
| 2.1 | System prompt | Remove workbench references for file uploads |
| 2.2 | System prompt | Document new `upload_file_to_s3` tool |
| 2.3 | System prompt | Update email attachment workflow |
| 2.4 | Knowledge injection | Remove workbench upload guidance |

### Phase 3: Sub-Agent Updates

| Task | Sub-Agent | Change |
|------|-----------|--------|
| 3.1 | Email sub-agent (if exists) | Update attachment workflow |
| 3.2 | Slack sub-agent | Update file upload workflow |
| 3.3 | Any file-handling agents | Remove workbench references |

### Phase 4: Testing & Cleanup

| Task | Description |
|------|-------------|
| 4.1 | Test email with attachment end-to-end |
| 4.2 | Test Slack with file attachment |
| 4.3 | Delete old test files related to workbench upload |
| 4.4 | Update documentation references |

---

## Part 6: Prompt Changes Required

### 6.1 Current System Prompt References to Remove

Look for and update/remove:
- "upload_to_composio" → "upload_file_to_s3"
- "workbench" references for file uploads
- Complex attachment workflows
- Bridge/workbench upload instructions

### 6.2 New Agent Instructions for Attachments

```markdown
## Sending Email with Attachments

When you need to send an email with a file attachment:

1. Ensure the file exists locally (use `list_directory` or `read_local_file`)
2. Call `upload_file_to_s3` with:
   - `local_path`: Full path to the file
   - `tool_slug`: "GMAIL_SEND_EMAIL"
   - `toolkit_slug`: "gmail"
3. Parse the response to get `s3key`, `mimetype`, `name`
4. Call `GMAIL_SEND_EMAIL` with `attachment` parameter:
   ```json
   "attachment": {
     "s3key": "...",
     "mimetype": "...",
     "name": "..."
   }
   ```
```

### 6.3 Knowledge Base Updates

| Document | Change |
|----------|--------|
| Email workflow docs | Update attachment process |
| Architecture docs | Remove workbench upload references |
| Lessons learned | Add note about native S3 discovery |

---

## Part 7: Files to Modify/Delete

### 7.1 Files to Modify

| File | Changes |
|------|---------|
| `src/mcp_server.py` | Replace upload function, delete workbench_upload |
| `src/universal_agent/main.py` | Update any prompt references |
| `Project_Documentation/*.md` | Update architecture references |

### 7.2 Files to Delete

| File | Reason |
|------|--------|
| `src/tools/workbench_bridge.py` | No longer needed (upload removed, download rarely used) |

### 7.3 Files to Keep

| File | Reason |
|------|--------|
| `tests/test_composio_upload.py` | Documents testing approach |
| `tests/test_upload_fix.py` | Verifies new approach works |

---

## Appendix A: Native Composio Code Reference

### A.1 Upload to S3 (for attachments)

```python
from composio import Composio
from composio.core.models._files import FileUploadable

composio = Composio()
result = FileUploadable.from_path(
    client=composio.client,
    file="/path/to/file.pdf",
    tool="GMAIL_SEND_EMAIL",
    toolkit="gmail"
)
# result.s3key, result.mimetype, result.name
```

### A.2 Download from S3 (if Composio returns s3url)

```python
import requests

# s3url from Composio tool response
response = requests.get(s3url)
with open(local_path, 'wb') as f:
    f.write(response.content)
```

---

## Appendix B: Decision Log

| Date | Decision | Rationale |
|------|----------|-----------|
| 2025-12-24 | Adopt local-first architecture | Faster, simpler, files persist |
| 2025-12-24 | Use native S3 upload for attachments | No base64, no workbench, native API |
| 2025-12-24 | Eliminate workbench usage | No native upload, ephemeral, unnecessary |
| 2025-12-24 | Keep download capability as fallback | Edge case for Composio-generated files |

---

## Conclusion

The investigation revealed a much simpler architecture than what we had implemented:

1. **Local = primary** - All file operations, code execution
2. **S3 = tool attachments only** - Via native `FileUploadable.from_path()`
3. **Workbench = eliminated** - No native upload, not needed

This simplification removes ~100 lines of hack code and replaces it with ~15 lines using native APIs.


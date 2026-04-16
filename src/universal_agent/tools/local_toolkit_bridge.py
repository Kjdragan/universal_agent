from typing import Any
import base64
import json
from pathlib import Path
import sys
import os
from claude_agent_sdk import tool

# Ensure src/ is on path so we can import mcp_server directly
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

try:
    from mcp_server import (
        upload_to_composio as upload_to_composio_core,
        list_directory as list_directory_core,
        inspect_session_workspace as inspect_session_workspace_core,
        list_agent_sessions as list_agent_sessions_core,
        read_vps_file as read_vps_file_core,
        append_to_file as append_to_file_core,
        write_text_file as write_text_file_core,
        generate_image as generate_image_core,
        generate_image_with_review as generate_image_with_review_core,
        finalize_research as finalize_research_core,
        describe_image as describe_image_core,
        preview_image as preview_image_core,
        ask_user_questions as ask_user_questions_core,
        batch_tool_execute as batch_tool_execute_core,
    )
except ImportError:
    from src.mcp_server import (
        upload_to_composio as upload_to_composio_core,
        list_directory as list_directory_core,
        inspect_session_workspace as inspect_session_workspace_core,
        list_agent_sessions as list_agent_sessions_core,
        read_vps_file as read_vps_file_core,
        append_to_file as append_to_file_core,
        write_text_file as write_text_file_core,
        generate_image as generate_image_core,
        generate_image_with_review as generate_image_with_review_core,
        finalize_research as finalize_research_core,
        describe_image as describe_image_core,
        preview_image as preview_image_core,
        ask_user_questions as ask_user_questions_core,
        batch_tool_execute as batch_tool_execute_core,
    )

from universal_agent.hooks import StdoutToEventStream
from universal_agent.utils.task_guardrails import resolve_best_task_match


@tool(
    name="upload_to_composio",
    description=(
        "Upload a local file to Composio S3 for use as a tool input (e.g., Slack attachments). "
        "Note: Gmail attachments should use gws MCP tools directly (no upload needed). "
        "Returns s3key for Composio tool calls. In-process fallback for local_toolkit upload."
    ),
    input_schema={
        "path": str,
        "tool_slug": str,
        "toolkit_slug": str,
    },
)
async def upload_to_composio_wrapper(args: dict[str, Any]) -> dict[str, Any]:
    """In-process wrapper for upload_to_composio to ensure availability in gateway runs."""
    path = args.get("path")
    tool_slug = args.get("tool_slug", "GMAIL_SEND_EMAIL")
    toolkit_slug = args.get("toolkit_slug", "gmail")

    with StdoutToEventStream(prefix="[Local Toolkit]"):
        result_str = upload_to_composio_core(
            path=path,
            tool_slug=tool_slug,
            toolkit_slug=toolkit_slug,
        )

    return {
        "content": [
            {
                "type": "text",
                "text": result_str,
            }
        ]
    }


@tool(
    name="list_directory",
    description="List contents of a directory in the current workspace (in-process).",
    input_schema={"path": str},
)
async def list_directory_wrapper(args: dict[str, Any]) -> dict[str, Any]:
    path = args.get("path")
    with StdoutToEventStream(prefix="[Local Toolkit]"):
        result_str = list_directory_core(path)
    return {"content": [{"type": "text", "text": result_str}]}


@tool(
    name="inspect_session_workspace",
    description=(
        "Read-only snapshot of run workspace diagnostics: run.log/activity_journal tails, "
        "trace.json, heartbeat_state.json, transcript.md, and recent artifacts. "
        "Tool name remains legacy for compatibility."
    ),
    input_schema={
        "session_id": str,
        "include_transcript": bool,
        "tail_lines": int,
        "max_bytes_per_file": int,
        "recent_file_limit": int,
    },
)
async def inspect_session_workspace_wrapper(args: dict[str, Any]) -> dict[str, Any]:
    with StdoutToEventStream(prefix="[Local Toolkit]"):
        result_str = inspect_session_workspace_core(
            session_id=args.get("session_id", ""),
            include_transcript=args.get("include_transcript", True),
            tail_lines=args.get("tail_lines", 120),
            max_bytes_per_file=args.get("max_bytes_per_file", 65536),
            recent_file_limit=args.get("recent_file_limit", 25),
        )
    return {"content": [{"type": "text", "text": result_str}]}


@tool(
    name="list_agent_sessions",
    description=(
        "List available agent run workspaces with metadata. "
        "Tool name remains legacy for compatibility with existing callers."
    ),
    input_schema={
        "limit": int,
        "source_filter": str,
        "include_stats": bool,
    },
)
async def list_agent_sessions_wrapper(args: dict[str, Any]) -> dict[str, Any]:
    with StdoutToEventStream(prefix="[Local Toolkit]"):
        result_str = list_agent_sessions_core(
            limit=args.get("limit", 30),
            source_filter=args.get("source_filter", ""),
            include_stats=args.get("include_stats", True),
        )
    return {"content": [{"type": "text", "text": result_str}]}


@tool(
    name="read_vps_file",
    description=(
        "Read a file from the VPS filesystem (read-only). "
        "Supports project-relative or absolute paths within allowed roots: "
        "AGENT_RUN_WORKSPACES, artifacts, config, src, docs, "
        ".claude, scripts, web-ui, deployment. Directories return listing."
    ),
    input_schema={
        "path": str,
        "max_bytes": int,
        "tail_lines": int,
    },
)
async def read_vps_file_wrapper(args: dict[str, Any]) -> dict[str, Any]:
    with StdoutToEventStream(prefix="[Local Toolkit]"):
        result_str = read_vps_file_core(
            path=args.get("path", ""),
            max_bytes=args.get("max_bytes", 65536),
            tail_lines=args.get("tail_lines", 0),
        )
    return {"content": [{"type": "text", "text": result_str}]}


@tool(
    name="append_to_file",
    description="Append content to an existing file in the workspace (in-process).",
    input_schema={"path": str, "content": str},
)
async def append_to_file_wrapper(args: dict[str, Any]) -> dict[str, Any]:
    path = args.get("path")
    content = args.get("content", "")
    with StdoutToEventStream(prefix="[Local Toolkit]"):
        result_str = append_to_file_core(path, content)
    return {"content": [{"type": "text", "text": result_str}]}


@tool(
    name="write_text_file",
    description="Write a UTF-8 text file under CURRENT_RUN_WORKSPACE (CURRENT_SESSION_WORKSPACE is the legacy alias) or UA_ARTIFACTS_DIR (in-process).",
    input_schema={"path": str, "content": str, "overwrite": bool},
)
async def write_text_file_wrapper(args: dict[str, Any]) -> dict[str, Any]:
    path = args.get("path")
    content = args.get("content", "")
    overwrite = args.get("overwrite", True)
    with StdoutToEventStream(prefix="[Local Toolkit]"):
        result_str = write_text_file_core(path, content, overwrite=overwrite)
    return {"content": [{"type": "text", "text": result_str}]}


@tool(
    name="prepare_agentmail_attachment",
    description=(
        "Prepare a local file for the official AgentMail MCP send/reply tools. "
        "Reads a file from disk and returns an official AgentMail attachment object "
        "with base64 content and filename."
    ),
    input_schema={"path": str, "filename": str, "content_id": str},
)
async def prepare_agentmail_attachment_wrapper(args: dict[str, Any]) -> dict[str, Any]:
    raw_path = str(args.get("path") or "").strip()
    if not raw_path:
        return {"content": [{"type": "text", "text": "error: 'path' is required"}]}

    file_path = Path(raw_path).expanduser()
    if not file_path.is_file():
        return {"content": [{"type": "text", "text": f"error: file not found: {file_path}"}]}

    filename = str(args.get("filename") or "").strip() or file_path.name
    content_id = str(args.get("content_id") or "").strip()

    payload = {
        "filename": filename,
        "content": base64.b64encode(file_path.read_bytes()).decode("ascii"),
    }
    if content_id:
        payload["content_id"] = content_id
    return {"content": [{"type": "text", "text": json.dumps(payload)}]}


@tool(
    name="agentmail_send_with_local_attachments",
    description=(
        "Sends an email natively via the AgentMail API, automatically processing local file paths "
        "into base64 to avoid LLM context-token limits. USE EXCEPTION: You are authorized to use this Python tool "
        "INSTEAD of the official AgentMail MCP tools ONLY when sending files (like PDFs or PNGs)."
    ),
    input_schema={
        "inboxId": str,
        "to": list,
        "subject": str,
        "text": str,
        "html": str,
        "attachment_paths": list,
    }
)
async def agentmail_send_with_local_attachments_wrapper(args: dict[str, Any]) -> dict[str, Any]:
    inboxId = str(args.get("inboxId") or "").strip()
    to = args.get("to") or []
    if isinstance(to, str):
        to = [to]
    if not inboxId or not to:
        return {"content": [{"type": "text", "text": "error: inboxId and to are required"}]}

    subject = str(args.get("subject") or "").strip()
    text = str(args.get("text") or "").strip()
    html = str(args.get("html") or "").strip()
    attachment_paths = args.get("attachment_paths") or []

    attachments = []
    large_file_links = []
    large_file_text = []
    
    try:
        from src.universal_agent.artifacts import resolve_artifacts_dir
    except ImportError:
        from universal_agent.artifacts import resolve_artifacts_dir
        
    for path_str in attachment_paths:
        p = Path(path_str).expanduser()
        if p.is_file():
            size_mb = p.stat().st_size / (1024 * 1024)
            if size_mb > 4.0:
                import uuid
                import shutil
                import urllib.parse
                
                artifacts_dir = resolve_artifacts_dir()
                email_blobs_dir = artifacts_dir / "agentmail_drops"
                email_blobs_dir.mkdir(parents=True, exist_ok=True)
                
                safe_name = f"{uuid.uuid4().hex[:8]}_{p.name}"
                dest = email_blobs_dir / safe_name
                shutil.copy2(p, dest)
                
                app_url = os.getenv("FRONTEND_URL", "https://app.clearspringcg.com").rstrip("/")
                rel_path = f"agentmail_drops/{safe_name}"
                file_url = f"{app_url}/api/artifacts/files/{urllib.parse.quote(rel_path, safe='/')}"
                
                large_file_links.append(f"<li><a href='{file_url}'>{p.name}</a> ({size_mb:.1f} MB)</li>")
                large_file_text.append(f"- {p.name}: {file_url}")
            else:
                b64 = base64.b64encode(p.read_bytes()).decode("ascii")
                attachments.append({
                    "filename": p.name,
                    "content": b64
                })
        else:
            return {"content": [{"type": "text", "text": f"error: attachment not found: {path_str}"}]}

    if large_file_links:
        links_html = f"<br/><br/><p><b>Large Attachments:</b></p><ul>{''.join(large_file_links)}</ul>"
        if html:
            html += links_html
        elif text:
            # If there's text but no HTML, we must promote text to HTML to embed the links nicely
            # or just leave it as text. We will do both.
            html = f"<p>{text}</p>{links_html}"
        else:
            html = links_html
            
        links_plain = "\n\nLarge Attachments:\n" + "\n".join(large_file_text)
        text += links_plain

    payload = {
        "to": to,
        "subject": subject,
        "text": text,
        "html": html,
        "attachments": attachments
    }

    import urllib.request
    import urllib.error

    api_key = os.getenv("AGENTMAIL_API_KEY", "").strip()
    if not api_key:
        return {"content": [{"type": "text", "text": "error: AGENTMAIL_API_KEY is not set"}]}

    url = f"https://api.agentmail.to/v0/inboxes/{inboxId}/messages/send"
    req = urllib.request.Request(url, method="POST")
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Content-Type", "application/json")

    try:
        data = json.dumps(payload).encode("utf-8")
        with urllib.request.urlopen(req, data=data, timeout=60.0) as resp:
            resp_body = resp.read().decode("utf-8")
            return {"content": [{"type": "text", "text": f"Email successfully sent.\n{resp_body}"}]}
    except urllib.error.HTTPError as e:
        return {"content": [{"type": "text", "text": f"error: {e.code} API Error - {e.read().decode('utf-8')}"}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"error: {str(e)}"}]}


@tool(
    name="agentmail_reply_with_local_attachments",
    description=(
        "Replies to an email natively via the AgentMail API, automatically processing local file paths "
        "into base64 to avoid LLM context-token limits. USE EXCEPTION: You are authorized to use this Python tool "
        "INSTEAD of the official AgentMail MCP tools ONLY when replying with files (like PDFs or PNGs)."
    ),
    input_schema={
        "inboxId": str,
        "messageId": str,
        "text": str,
        "html": str,
        "attachment_paths": list,
    }
)
async def agentmail_reply_with_local_attachments_wrapper(args: dict[str, Any]) -> dict[str, Any]:
    inboxId = str(args.get("inboxId") or "").strip()
    messageId = str(args.get("messageId") or "").strip()
    
    if not inboxId or not messageId:
        return {"content": [{"type": "text", "text": "error: inboxId and messageId are required"}]}

    text = str(args.get("text") or "").strip()
    html = str(args.get("html") or "").strip()
    attachment_paths = args.get("attachment_paths") or []

    attachments = []
    large_file_links = []
    large_file_text = []

    try:
        from src.universal_agent.artifacts import resolve_artifacts_dir
    except ImportError:
        from universal_agent.artifacts import resolve_artifacts_dir
        
    for path_str in attachment_paths:
        p = Path(path_str).expanduser()
        if p.is_file():
            size_mb = p.stat().st_size / (1024 * 1024)
            if size_mb > 4.0:
                import uuid
                import shutil
                import urllib.parse
                
                artifacts_dir = resolve_artifacts_dir()
                email_blobs_dir = artifacts_dir / "agentmail_drops"
                email_blobs_dir.mkdir(parents=True, exist_ok=True)
                
                safe_name = f"{uuid.uuid4().hex[:8]}_{p.name}"
                dest = email_blobs_dir / safe_name
                shutil.copy2(p, dest)
                
                app_url = os.getenv("FRONTEND_URL", "https://app.clearspringcg.com").rstrip("/")
                rel_path = f"agentmail_drops/{safe_name}"
                file_url = f"{app_url}/api/artifacts/files/{urllib.parse.quote(rel_path, safe='/')}"
                
                large_file_links.append(f"<li><a href='{file_url}'>{p.name}</a> ({size_mb:.1f} MB)</li>")
                large_file_text.append(f"- {p.name}: {file_url}")
            else:
                b64 = base64.b64encode(p.read_bytes()).decode("ascii")
                attachments.append({
                    "filename": p.name,
                    "content": b64
                })
        else:
            return {"content": [{"type": "text", "text": f"error: attachment not found: {path_str}"}]}

    if large_file_links:
        links_html = f"<br/><br/><p><b>Large Attachments:</b></p><ul>{''.join(large_file_links)}</ul>"
        if html:
            html += links_html
        elif text:
            html = f"<p>{text}</p>{links_html}"
        else:
            html = links_html
            
        links_plain = "\n\nLarge Attachments:\n" + "\n".join(large_file_text)
        text += links_plain

    payload = {
        "text": text,
        "html": html,
        "attachments": attachments
    }

    import urllib.request
    import urllib.error

    api_key = os.getenv("AGENTMAIL_API_KEY", "").strip()
    if not api_key:
        return {"content": [{"type": "text", "text": "error: AGENTMAIL_API_KEY is not set"}]}

    url = f"https://api.agentmail.to/v0/inboxes/{inboxId}/messages/{messageId}/reply"
    req = urllib.request.Request(url, method="POST")
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Content-Type", "application/json")

    try:
        data = json.dumps(payload).encode("utf-8")
        with urllib.request.urlopen(req, data=data, timeout=60.0) as resp:
            resp_body = resp.read().decode("utf-8")
            return {"content": [{"type": "text", "text": f"Reply successfully sent.\n{resp_body}"}]}
    except urllib.error.HTTPError as e:
        return {"content": [{"type": "text", "text": f"error: {e.code} API Error - {e.read().decode('utf-8')}"}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"error: {str(e)}"}]}




@tool(
    name="finalize_research",
    description="Run the inbox research pipeline: crawl, filter, and create refined corpus (in-process).",
    input_schema={
        "session_dir": str,
        "task_name": str,
        "enable_topic_filter": bool,
        "retry_id": str,
    },
)
async def finalize_research_wrapper(args: dict[str, Any]) -> dict[str, Any]:
    session_dir = args.get("session_dir")
    task_name = resolve_best_task_match(args.get("task_name", "default"))
    enable_topic_filter = args.get("enable_topic_filter", True)
    retry_id = args.get("retry_id")
    with StdoutToEventStream(prefix="[Local Toolkit]"):
        result_str = await finalize_research_core(
            session_dir=session_dir,
            task_name=task_name,
            enable_topic_filter=enable_topic_filter,
            retry_id=retry_id,
        )
    return {"content": [{"type": "text", "text": result_str}]}


@tool(
    name="generate_image",
    description="Generate or edit an image using the configured model (in-process).",
    input_schema={
        "prompt": str,
        "input_image_path": str,
        "output_dir": str,
        "output_filename": str,
        "preview": bool,
        "model_name": str,
    },
)
async def generate_image_wrapper(args: dict[str, Any]) -> dict[str, Any]:
    with StdoutToEventStream(prefix="[Local Toolkit]"):
        result_str = generate_image_core(
            prompt=args.get("prompt"),
            input_image_path=args.get("input_image_path"),
            output_dir=args.get("output_dir"),
            output_filename=args.get("output_filename"),
            preview=args.get("preview", False),
            # Keep wrapper default aligned with the internal tool's documented valid models.
            model_name=args.get("model_name", "gemini-2.5-flash-image"),
        )
    return {"content": [{"type": "text", "text": result_str}]}


@tool(
    name="generate_image_with_review",
    description="Generate an image then have Gemini review and iteratively fix typos/missing elements (in-process).",
    input_schema={
        "prompt": str,
        "output_dir": str,
        "output_filename": str,
        "preview": bool,
        "model_name": str,
        "max_attempts": int,
    },
)
async def generate_image_with_review_wrapper(args: dict[str, Any]) -> dict[str, Any]:
    with StdoutToEventStream(prefix="[Local Toolkit]"):
        result_str = generate_image_with_review_core(
            prompt=args.get("prompt"),
            output_dir=args.get("output_dir"),
            output_filename=args.get("output_filename"),
            preview=args.get("preview", False),
            model_name=args.get("model_name", "gemini-3-pro-image-preview"),
            max_attempts=args.get("max_attempts", 3),
        )
    return {"content": [{"type": "text", "text": result_str}]}


@tool(
    name="describe_image",
    description="Describe an image for naming/metadata (in-process).",
    input_schema={"image_path": str, "max_words": int},
)
async def describe_image_wrapper(args: dict[str, Any]) -> dict[str, Any]:
    with StdoutToEventStream(prefix="[Local Toolkit]"):
        result_str = describe_image_core(
            image_path=args.get("image_path"), max_words=args.get("max_words", 10)
        )
    return {"content": [{"type": "text", "text": result_str}]}


@tool(
    name="preview_image",
    description="Preview an image via Gradio viewer (in-process).",
    input_schema={"image_path": str, "port": int},
)
async def preview_image_wrapper(args: dict[str, Any]) -> dict[str, Any]:
    with StdoutToEventStream(prefix="[Local Toolkit]"):
        result_str = preview_image_core(
            image_path=args.get("image_path"), port=args.get("port", 7860)
        )
    return {"content": [{"type": "text", "text": result_str}]}


@tool(
    name="ask_user_questions",
    description="Ask structured clarification questions (in-process).",
    input_schema={"questions": list},
)
async def ask_user_questions_wrapper(args: dict[str, Any]) -> dict[str, Any]:
    with StdoutToEventStream(prefix="[Local Toolkit]"):
        result_str = ask_user_questions_core(args.get("questions", []))
    return {"content": [{"type": "text", "text": result_str}]}


@tool(
    name="batch_tool_execute",
    description="Execute multiple tool calls in a batch (in-process).",
    input_schema={"tool_calls": list},
)
async def batch_tool_execute_wrapper(args: dict[str, Any]) -> dict[str, Any]:
    with StdoutToEventStream(prefix="[Local Toolkit]"):
        result = batch_tool_execute_core(args.get("tool_calls", []))
    return {"content": [{"type": "text", "text": str(result)}]}

LOCAL_TOOLS: dict[str, Any] = {
    "upload_to_composio": upload_to_composio_wrapper,
    "list_directory": list_directory_wrapper,
    "inspect_session_workspace": inspect_session_workspace_wrapper,
    "list_agent_sessions": list_agent_sessions_wrapper,
    "read_vps_file": read_vps_file_wrapper,
    "append_to_file": append_to_file_wrapper,
    "write_text_file": write_text_file_wrapper,
    "prepare_agentmail_attachment": prepare_agentmail_attachment_wrapper,
    "agentmail_send_with_local_attachments": agentmail_send_with_local_attachments_wrapper,
    "agentmail_reply_with_local_attachments": agentmail_reply_with_local_attachments_wrapper,
    "finalize_research": finalize_research_wrapper,
    "generate_image": generate_image_wrapper,
    "generate_image_with_review": generate_image_with_review_wrapper,
    "describe_image": describe_image_wrapper,
    "preview_image": preview_image_wrapper,
    "ask_user_questions": ask_user_questions_wrapper,
    "batch_tool_execute": batch_tool_execute_wrapper,
}

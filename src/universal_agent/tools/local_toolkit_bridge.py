from typing import Any
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
        "Upload a local file to Composio S3 for use as an email attachment or tool input. "
        "Returns s3key for GMAIL_SEND_EMAIL. In-process fallback for local_toolkit upload."
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
        "Read-only snapshot of session workspace diagnostics: run.log/activity_journal tails, "
        "trace.json, heartbeat_state.json, transcript.md, and recent artifacts."
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
        "List available agent session workspaces with metadata. "
        "Use to discover past sessions for debugging, cross-session file access, or review."
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
        "AGENT_RUN_WORKSPACES, artifacts, config, src, OFFICIAL_PROJECT_DOCUMENTATION, "
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
    description="Write a UTF-8 text file under CURRENT_SESSION_WORKSPACE or UA_ARTIFACTS_DIR (in-process).",
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

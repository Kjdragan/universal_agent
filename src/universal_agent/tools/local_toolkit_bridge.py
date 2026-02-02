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
        append_to_file as append_to_file_core,
        generate_image as generate_image_core,
        finalize_research as finalize_research_core,
        describe_image as describe_image_core,
        preview_image as preview_image_core,
        core_memory_replace as core_memory_replace_core,
        core_memory_append as core_memory_append_core,
        archival_memory_insert as archival_memory_insert_core,
        archival_memory_search as archival_memory_search_core,
        get_core_memory_blocks as get_core_memory_blocks_core,
        ask_user_questions as ask_user_questions_core,
        batch_tool_execute as batch_tool_execute_core,
    )
except ImportError:
    from src.mcp_server import (
        upload_to_composio as upload_to_composio_core,
        list_directory as list_directory_core,
        append_to_file as append_to_file_core,
        generate_image as generate_image_core,
        finalize_research as finalize_research_core,
        describe_image as describe_image_core,
        preview_image as preview_image_core,
        core_memory_replace as core_memory_replace_core,
        core_memory_append as core_memory_append_core,
        archival_memory_insert as archival_memory_insert_core,
        archival_memory_search as archival_memory_search_core,
        get_core_memory_blocks as get_core_memory_blocks_core,
        ask_user_questions as ask_user_questions_core,
        batch_tool_execute as batch_tool_execute_core,
    )

from universal_agent.hooks import StdoutToEventStream


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
    task_name = args.get("task_name", "default")
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
            model_name=args.get("model_name", "gemini-3-pro-image-preview"),
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
    name="core_memory_replace",
    description="Replace a core memory block (in-process).",
    input_schema={"label": str, "new_value": str},
)
async def core_memory_replace_wrapper(args: dict[str, Any]) -> dict[str, Any]:
    with StdoutToEventStream(prefix="[Local Toolkit]"):
        result_str = core_memory_replace_core(
            label=args.get("label"), new_value=args.get("new_value", "")
        )
    return {"content": [{"type": "text", "text": result_str}]}


@tool(
    name="core_memory_append",
    description="Append to a core memory block (in-process).",
    input_schema={"label": str, "text_to_append": str},
)
async def core_memory_append_wrapper(args: dict[str, Any]) -> dict[str, Any]:
    with StdoutToEventStream(prefix="[Local Toolkit]"):
        result_str = core_memory_append_core(
            label=args.get("label"), text_to_append=args.get("text_to_append", "")
        )
    return {"content": [{"type": "text", "text": result_str}]}


@tool(
    name="archival_memory_insert",
    description="Insert content into archival memory (in-process).",
    input_schema={"content": str, "tags": str},
)
async def archival_memory_insert_wrapper(args: dict[str, Any]) -> dict[str, Any]:
    with StdoutToEventStream(prefix="[Local Toolkit]"):
        result_str = archival_memory_insert_core(
            content=args.get("content", ""), tags=args.get("tags", "")
        )
    return {"content": [{"type": "text", "text": result_str}]}


@tool(
    name="archival_memory_search",
    description="Search archival memory (in-process).",
    input_schema={"query": str, "limit": int},
)
async def archival_memory_search_wrapper(args: dict[str, Any]) -> dict[str, Any]:
    with StdoutToEventStream(prefix="[Local Toolkit]"):
        result_str = archival_memory_search_core(
            query=args.get("query", ""), limit=args.get("limit", 5)
        )
    return {"content": [{"type": "text", "text": result_str}]}


@tool(
    name="get_core_memory_blocks",
    description="Get all core memory blocks (in-process).",
    input_schema={},
)
async def get_core_memory_blocks_wrapper(args: dict[str, Any]) -> dict[str, Any]:
    with StdoutToEventStream(prefix="[Local Toolkit]"):
        result_str = get_core_memory_blocks_core()
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

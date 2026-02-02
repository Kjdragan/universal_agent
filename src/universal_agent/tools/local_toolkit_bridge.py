from typing import Any
from claude_agent_sdk import tool

import sys
import os

# Ensure src/ is on path so we can import mcp_server directly
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

try:
    from mcp_server import upload_to_composio as upload_to_composio_core
except ImportError:
    from src.mcp_server import upload_to_composio as upload_to_composio_core

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

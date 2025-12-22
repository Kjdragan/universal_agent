"""
Composio Agent - Claude SDK with Tool Router
A standalone agent using Claude Agent SDK with Composio MCP integration.
Traces are sent to Logfire for observability.
"""

import asyncio
import os
import time
import json
from datetime import datetime
from dotenv import load_dotenv

# Load environment FIRST
load_dotenv()

import sys
import readline  # Enable better terminal input (backspace, arrow keys, history)
from prompt_toolkit import PromptSession
from prompt_toolkit.patch_stdout import patch_stdout


class DualWriter:
    """Writes to both a file and the original stream (stdout/stderr)."""

    def __init__(self, file_handle, original_stream):
        self.file = file_handle
        self.stream = original_stream

    def write(self, data):
        self.stream.write(data)
        self.stream.flush()
        self.file.write(data)
        self.file.flush()

    def flush(self):
        self.stream.flush()
        self.file.flush()

    def isatty(self):
        """Check if the stream is a TTY (needed by prompt_toolkit)."""
        return hasattr(self.stream, "isatty") and self.stream.isatty()

    def fileno(self):
        """Return file descriptor (needed by prompt_toolkit)."""
        return self.stream.fileno()


# Configure Logfire BEFORE importing Claude SDK
import logfire

# Configure Logfire for tracing
LOGFIRE_TOKEN = (
    os.getenv("LOGFIRE_TOKEN")
    or os.getenv("LOGFIRE_WRITE_TOKEN")
    or os.getenv("LOGFIRE_API_KEY")
)

if LOGFIRE_TOKEN:
    # Custom scrubbing to prevent over-redaction of previews
    def scrubbing_callback(m: logfire.ScrubMatch):
        # Whitelist specific fields often caught by scrubbers (e.g. 'session')
        if not m.path:
            return None

        last_key = m.path[-1]

        # safely check if key is a string before doing string operations
        if isinstance(last_key, str):
            if (
                last_key
                in (
                    "content_preview",
                    "input_preview",
                    "result_preview",
                    "text_preview",
                    "query",
                    "input",
                )
                or "preview" in last_key
            ):
                return m.value
        return None

    logfire.configure(
        service_name="universal-agent",
        environment="development",
        console=False,
        token=LOGFIRE_TOKEN,
        send_to_logfire="if-token-present",
        scrubbing=logfire.ScrubbingOptions(callback=scrubbing_callback),
    )

    # Instrument MCP for distributed tracing
    try:
        logfire.instrument_mcp()
        print("✅ Logfire MCP instrumentation enabled")
    except Exception as e:
        print(f"⚠️ MCP instrumentation not available: {e}")

    # Instrument HTTPX to trace all API calls
    try:
        logfire.instrument_httpx(capture_headers=True)
        print("✅ Logfire HTTPX instrumentation enabled")
    except Exception as e:
        print(f"⚠️ HTTPX instrumentation not available: {e}")

    print("✅ Logfire tracing enabled - view at https://logfire.pydantic.dev/")
else:
    print("⚠️ No LOGFIRE_TOKEN found - tracing disabled")

from claude_agent_sdk.client import ClaudeSDKClient
from claude_agent_sdk.types import (
    ClaudeAgentOptions,
    AssistantMessage,
    TextBlock,
    ToolUseBlock,
    ToolResultBlock,
    ThinkingBlock,
    UserMessage,
)
from composio import Composio

# Composio client - will be initialized in main() with file_download_dir
composio = None

# =============================================================================
# OBSERVER SETUP - For processing tool results asynchronously
# =============================================================================
import re

# Global workspace directory (set at session start)
OBSERVER_WORKSPACE_DIR = None


def parse_relative_date(relative_str: str) -> str:
    """Convert '2 hours ago' to YYYY-MM-DD format."""
    from datetime import timedelta

    now = datetime.now()
    if not relative_str:
        return now.strftime("%Y-%m-%d")

    match = re.match(
        r"(\d+)\s*(hour|minute|day|week|month)s?\s*ago", relative_str.lower()
    )
    if match:
        value = int(match.group(1))
        unit = match.group(2)
        deltas = {
            "minute": timedelta(minutes=value),
            "hour": timedelta(hours=value),
            "day": timedelta(days=value),
            "week": timedelta(weeks=value),
            "month": timedelta(days=value * 30),
        }
        return (now - deltas.get(unit, timedelta())).strftime("%Y-%m-%d")
    return now.strftime("%Y-%m-%d")


# =============================================================================
# OBSERVER PATTERN - Process tool results asynchronously (works with MCP mode)
# Note: Composio hooks (@after_execute) don't fire in MCP mode because execution
# happens on the remote server. This observer pattern processes results after
# they return to the client, saving artifacts without blocking the agent loop.
# =============================================================================

import asyncio


async def observe_and_save_search_results(
    tool_name: str, content, workspace_dir: str
) -> None:
    """
    Observer: Parse SERP tool results and save cleaned artifacts.
    Uses Claude SDK typed content (list of TextBlock objects).
    """
    is_serp_tool = any(
        kw in tool_name.upper()
        for kw in ["SEARCH_NEWS", "SEARCH_WEB", "COMPOSIO_SEARCH", "MULTI_EXECUTE"]
    )

    if not is_serp_tool:
        return

    try:
        # Extract JSON text from Claude SDK TextBlock objects
        raw_json = None

        if isinstance(content, list):
            # Claude SDK: [TextBlock(type='text', text='<json>')]
            for item in content:
                if hasattr(item, "text"):
                    raw_json = item.text
                    break
                elif isinstance(item, dict) and item.get("type") == "text":
                    raw_json = item.get("text", "")
                    break
        elif isinstance(content, str):
            raw_json = content

        if not raw_json:
            return

        # Parse JSON
        try:
            data = json.loads(raw_json)
        except json.JSONDecodeError:
            return

        if not isinstance(data, dict):
            return

        # Prepare list of payloads to process
        payloads = []

        # 1. Handle Nested "data" wrapper
        root = data
        if isinstance(root, dict) and "data" in root:
            root = root["data"]

        # 2. Check for MULTI_EXECUTE_TOOL structure
        if (
            isinstance(root, dict)
            and "results" in root
            and isinstance(root["results"], list)
        ):
            # Multi-execute result
            for item in root["results"]:
                if isinstance(item, dict) and "response" in item:
                    inner_resp = item["response"]

                    # Handle string response
                    if isinstance(inner_resp, str):
                        try:
                            inner_resp = json.loads(inner_resp)
                        except json.JSONDecodeError:
                            continue

                    # Now safe to process dict
                    if isinstance(inner_resp, dict):
                        inner_data = inner_resp.get("data") or inner_resp.get(
                            "data_preview"
                        )
                        inner_slug = item.get("tool_slug", tool_name)

                        if inner_data:
                            payloads.append((inner_slug, inner_data))

        else:
            # Single tool result
            payloads.append((tool_name, root))

        # 3. Process each payload
        saved_count = 0
        for slug, payload in payloads:
            if not isinstance(payload, dict):
                continue

            # Helper to unwrap 'results' key if it hides the actual SERP data
            search_data = payload
            if "results" in payload and isinstance(payload["results"], dict):
                search_data = payload["results"]

            # Robust extraction helper
            def safe_get_list(data, key):
                val = data.get(key, [])
                if isinstance(val, dict):
                    return list(val.values())
                if isinstance(val, list):
                    return val
                return []

            cleaned = None

            # CASE A: News Results
            if "news_results" in search_data:
                raw_list = safe_get_list(search_data, "news_results")
                cleaned = {
                    "type": "news",
                    "timestamp": datetime.now().isoformat(),
                    "tool": slug,
                    "articles": [
                        {
                            "position": a.get("position"),
                            "title": a.get("title"),
                            "url": a.get("link"),
                            "source": a.get("source", {}).get("name")
                            if isinstance(a.get("source"), dict)
                            else (
                                a.get("source")
                                if isinstance(a.get("source"), str)
                                else None
                            ),
                            "date": parse_relative_date(a.get("date", "")),
                            "snippet": a.get("snippet"),
                        }
                        for a in raw_list
                        if isinstance(a, dict)
                    ],
                }

            # CASE B: Organic Web Results
            elif "organic_results" in search_data:
                raw_list = safe_get_list(search_data, "organic_results")
                cleaned = {
                    "type": "web",
                    "timestamp": datetime.now().isoformat(),
                    "tool": slug,
                    "results": [
                        {
                            "position": r.get("position"),
                            "title": r.get("title"),
                            "url": r.get("link"),
                            "snippet": r.get("snippet"),
                        }
                        for r in search_data["organic_results"]
                    ],
                }

            # Save if we found cleanable data
            if cleaned and workspace_dir:
                try:
                    search_dir = os.path.join(workspace_dir, "search_results")
                    os.makedirs(search_dir, exist_ok=True)

                    # Make filename unique
                    timestamp_str = datetime.now().strftime("%H%M%S")
                    suffix = f"_{saved_count}" if len(payloads) > 1 else ""
                    filename = os.path.join(
                        search_dir, f"{slug}{suffix}_{timestamp_str}.json"
                    )

                    # Write file with explicit error handling
                    with open(filename, "w") as f:
                        json.dump(cleaned, f, indent=2)

                    # Verify file was actually created
                    if os.path.exists(filename):
                        file_size = os.path.getsize(filename)
                        print(f"\n📁 [OBSERVER] Saved: {filename} ({file_size} bytes)")
                        logfire.info(
                            "observer_artifact_saved",
                            path=filename,
                            type=cleaned.get("type"),
                            size=file_size,
                        )
                        saved_count += 1
                    else:
                        print(f"\n❌ [OBSERVER] File not created: {filename}")
                        logfire.error("observer_file_not_created", path=filename)

                except Exception as file_error:
                    print(f"\n❌ [OBSERVER] File I/O error: {file_error}")
                    logfire.error(
                        "observer_file_io_error",
                        error=str(file_error),
                        path=filename if "filename" in locals() else "unknown",
                    )

    except Exception as e:
        print(f"\n❌ [OBSERVER] Parse error: {e}")
        logfire.warning("observer_error", tool=tool_name, error=str(e))


async def observe_and_save_workbench_activity(
    tool_name: str, tool_input: dict, tool_result: str, workspace_dir: str
) -> None:
    """
    Observer: Capture COMPOSIO_REMOTE_WORKBENCH activity (inputs/outputs).
    Saves code execution details to workbench_activity/ directory.
    """
    if "REMOTE_WORKBENCH" not in tool_name.upper():
        return

    try:
        workbench_dir = os.path.join(workspace_dir, "workbench_activity")
        os.makedirs(workbench_dir, exist_ok=True)

        timestamp_str = datetime.now().strftime("%H%M%S")
        filename = os.path.join(workbench_dir, f"workbench_{timestamp_str}.json")

        # Parse result for metadata
        result_data = {}
        try:
            if isinstance(tool_result, str):
                import ast

                parsed_list = ast.literal_eval(tool_result)
                for item in parsed_list:
                    if isinstance(item, dict) and item.get("type") == "text":
                        result_json = json.loads(item.get("text", "{}"))
                        result_data = result_json.get("data", {})
                        break
        except:
            result_data = {"raw": tool_result[:500]}

        activity_log = {
            "timestamp": datetime.now().isoformat(),
            "tool": tool_name,
            "input": {
                "code": tool_input.get("code_to_execute", "")[
                    :1000
                ],  # Truncate for readability
                "session_id": tool_input.get("session_id"),
                "current_step": tool_input.get("current_step"),
                "thought": tool_input.get("thought"),
            },
            "output": {
                "stdout": result_data.get("stdout", ""),
                "stderr": result_data.get("stderr", ""),
                "results": result_data.get("results", ""),
                "successful": result_data.get("successful"),
            },
        }

        with open(filename, "w") as f:
            json.dump(activity_log, f, indent=2)

        print(f"\n📁 [OBSERVER] Saved workbench activity: {filename}")
        logfire.info("workbench_activity_saved", path=filename)

    except Exception as e:
        logfire.warning("workbench_observer_error", tool=tool_name, error=str(e))


# =============================================================================

# Session and options will be created in main() after Composio initialization
user_id = "user_123"
session = None


# Options will be created in main() after session is initialized
options = None


# Trace will be created in main() after session is initialized
trace = None


async def run_conversation(client, query: str, start_ts: float, iteration: int = 1):
    """Run a single conversation turn with full tracing."""
    global trace

    # Create Logfire span for this iteration
    with logfire.span(f"conversation_iteration_{iteration}", iteration=iteration):
        print(f"\n{'=' * 80}")
        print(
            f"[ITERATION {iteration}] Sending: {query[:100]}{'...' if len(query) > 100 else ''}"
        )
        print(f"{'=' * 80}")

        iter_start = time.time()
        await client.query(query)

        tool_calls_this_iter = []
        needs_user_input = False
        auth_link = None

        async for msg in client.receive_response():
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, ToolUseBlock):
                        tool_record = {
                            "iteration": iteration,
                            "name": block.name,
                            "id": block.id,
                            "time_offset_seconds": round(time.time() - start_ts, 3),
                            "input": block.input if hasattr(block, "input") else None,
                            "input_size_bytes": len(json.dumps(block.input))
                            if hasattr(block, "input") and block.input
                            else 0,
                        }
                        trace["tool_calls"].append(tool_record)
                        tool_calls_this_iter.append(tool_record)

                        # Log to Logfire with PAYLOAD preview
                        input_preview = None
                        if tool_record["input"]:
                            input_json = json.dumps(tool_record["input"])
                            # Capture up to 2KB of input for debugging code generation
                            input_preview = (
                                input_json[:2000]
                                if len(input_json) > 2000
                                else input_json
                            )

                        logfire.info(
                            "tool_call",
                            tool_name=block.name,
                            tool_id=block.id,
                            input_size=tool_record["input_size_bytes"],
                            input_preview=input_preview,
                        )

                        # Check for WORKBENCH or code execution tools
                        is_code_exec = any(
                            x in block.name.upper()
                            for x in [
                                "WORKBENCH",
                                "CODE",
                                "EXECUTE",
                                "PYTHON",
                                "SANDBOX",
                                "BASH",
                            ]
                        )
                        marker = "🏭 CODE EXECUTION" if is_code_exec else "🔧"

                        print(
                            f"\n{marker} [{block.name}] +{tool_record['time_offset_seconds']}s"
                        )
                        print(f"   Input size: {tool_record['input_size_bytes']} bytes")

                        if tool_record["input"]:
                            input_preview = json.dumps(tool_record["input"], indent=2)
                            max_len = 3000 if is_code_exec else 500
                            if len(input_preview) > max_len:
                                input_preview = input_preview[:max_len] + "..."
                            print(f"   Input: {input_preview}")

                    elif isinstance(block, TextBlock):
                        if "connect.composio.dev/link" in block.text:
                            import re

                            links = re.findall(
                                r"https://connect\.composio\.dev/link/[^\s\)]+",
                                block.text,
                            )
                            if links:
                                auth_link = links[0]
                                needs_user_input = True

                        print(
                            block.text[:3000]
                            + ("..." if len(block.text) > 3000 else "")
                        )

                        # Log reasoning to Logfire
                        logfire.info("reasoning", text_preview=block.text[:1000])

                    elif isinstance(block, ThinkingBlock):
                        # Log extended thinking
                        print(
                            f"\n🧠 Thinking (+{round(time.time() - start_ts, 1)}s)..."
                        )
                        logfire.info(
                            "thinking",
                            thinking_preview=block.thinking[:1000],
                            signature=block.signature,
                        )

            elif isinstance(msg, (UserMessage, ToolResultBlock)):
                # Handle both UserMessage (legacy) and ToolResultBlock (new SDK)
                blocks = msg.content if isinstance(msg, UserMessage) else [msg]

                for block in blocks:
                    # Check for ToolResultBlock or dict-like content in UserMessage
                    is_result = isinstance(block, ToolResultBlock) or hasattr(
                        block, "tool_use_id"
                    )

                    if is_result:
                        tool_use_id = getattr(block, "tool_use_id", None)
                        is_error = getattr(block, "is_error", False)

                        # Extract content - keep as typed object
                        block_content = getattr(block, "content", "")
                        content_str = str(block_content)

                        result_record = {
                            "tool_use_id": tool_use_id,
                            "time_offset_seconds": round(time.time() - start_ts, 3),
                            "is_error": is_error,
                            "content_size_bytes": len(content_str),
                            "content_preview": content_str[:1000]
                            if len(content_str) > 1000
                            else content_str,
                        }
                        trace["tool_results"].append(result_record)

                        # Log to Logfire with CONTENT preview
                        # Capture full content if it's reasonable size (up to 2KB)
                        full_content = content_str[:2000]

                        logfire.info(
                            "tool_result",
                            tool_use_id=result_record["tool_use_id"],
                            content_size=result_record["content_size_bytes"],
                            is_error=result_record["is_error"],
                            content_preview=full_content,
                        )

                        print(
                            f"\n📦 Tool Result ({result_record['content_size_bytes']} bytes) +{result_record['time_offset_seconds']}s"
                        )
                        if result_record["content_size_bytes"] < 5000:
                            print(
                                f"   Content: {result_record['content_preview'][:1000]}"
                            )

                        # Observer Pattern: Fire-and-forget async save for SERP results
                        # Look up tool name from tool_use_id
                        tool_name = None
                        tool_input = None
                        for tc in tool_calls_this_iter:
                            if tc.get("id") == tool_use_id:
                                tool_name = tc.get("name")
                                tool_input = tc.get("input", {})
                                break
                        if tool_name and OBSERVER_WORKSPACE_DIR:
                            # Search results observer - pass typed content
                            asyncio.create_task(
                                observe_and_save_search_results(
                                    tool_name, block_content, OBSERVER_WORKSPACE_DIR
                                )
                            )
                            # Workbench activity observer
                            asyncio.create_task(
                                observe_and_save_workbench_activity(
                                    tool_name,
                                    tool_input or {},
                                    content_str,
                                    OBSERVER_WORKSPACE_DIR,
                                )
                            )

        iter_record = {
            "iteration": iteration,
            "query": query[:200],
            "duration_seconds": round(time.time() - iter_start, 3),
            "tool_calls": len(tool_calls_this_iter),
            "needs_user_input": needs_user_input,
            "auth_link": auth_link,
        }
        trace["iterations"].append(iter_record)

        return needs_user_input, auth_link


async def classify_query(client: ClaudeSDKClient, query: str) -> str:
    """Determine if a query is SIMPLE (direct answer) or COMPLEX (needs tools)."""
    # Classification logic with definition-based prompting
    classification_prompt = (
        f"Classify the following user query as either 'SIMPLE' or 'COMPLEX'.\n"
        f"Query: {query}\n\n"
        f"Definitions:\n"
        f"- SIMPLE: Can be answered directly by your foundational knowledge (e.g., 'Capital of France', 'Explain concept') WITHOUT any context from previous turns.\n"
        f"- COMPLEX: Requires external tools, searching the web, executing code, checking real-time data, sending emails, OR confirming/continuing a previous multi-step workflow (e.g., 'yes', 'proceed', 'continue').\n\n"
        f"Respond with ONLY 'SIMPLE' or 'COMPLEX'."
    )

    # We use the client to query, but since it has tools configured, we must rely on the prompt to restrict tool usage.
    # In a production system, we might use a separate client without tools.
    await client.query(classification_prompt)

    result_text = ""
    async for msg in client.receive_response():
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, TextBlock):
                    result_text += block.text

    decision = result_text.strip().upper()

    # Safe fallback
    final_decision = "SIMPLE" if "SIMPLE" in decision else "COMPLEX"

    print(
        f"\n🤔 Query Classification: {final_decision} (Model logic: {decision[:50]}...)"
    )
    if LOGFIRE_TOKEN:
        logfire.info(
            "query_classification",
            query=query,
            decision=final_decision,
            raw_response=decision,
        )

    return final_decision


async def handle_simple_query(client: ClaudeSDKClient, query: str) -> bool:
    """
    Handle simple queries directly without complex tool loops.
    Returns True if handled successfully, False if tool use was attempted (fallback needed).
    """
    print(f"\n⚡ Direct Answer (Fast Path):")
    print("-" * 40)

    await client.query(query)

    full_response = ""
    tool_use_detected = False

    async for msg in client.receive_response():
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, TextBlock):
                    print(block.text, end="", flush=True)
                    full_response += block.text
                elif isinstance(block, ToolUseBlock):
                    # ABORT! The model wants to use a tool.
                    tool_use_detected = True
                    break

            if tool_use_detected:
                break

    if tool_use_detected:
        print("\n" + "=" * 40)
        print(
            "⚠️  Model attempted tool use in Fast Path. Redirecting to Complex Path..."
        )
        if LOGFIRE_TOKEN:
            logfire.warn("fast_path_fallback", reason="tool_use_detected")
        return False

    print("\n" + "-" * 40)
    if LOGFIRE_TOKEN:
        logfire.info("direct_answer", length=len(full_response))
    return True


async def main():
    global trace

    # Create main span for entire execution
    with logfire.span("standalone_composio_test"):
        # Setup Session Workspace
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        workspace_dir = os.path.join("AGENT_RUN_WORKSPACES", f"session_{timestamp}")
        os.makedirs(workspace_dir, exist_ok=True)

        # Initialize Composio with automatic file downloads to this workspace
        global composio
        downloads_dir = os.path.join(workspace_dir, "downloads")
        os.makedirs(downloads_dir, exist_ok=True)
        composio = Composio(
            api_key=os.environ["COMPOSIO_API_KEY"], file_download_dir=downloads_dir
        )

        # Create Composio session now that client is initialized
        global session
        session = composio.create(user_id=user_id)

        # Create ClaudeAgentOptions now that session is available
        global options
        options = ClaudeAgentOptions(
            system_prompt=(
                f"Result Date: {datetime.now().strftime('%A, %B %d, %Y')}\n"
                "TEMPORAL CONSISTENCY WARNING: You are operating in a timeline where it is December 2025. "
                "If 'real-world' search tools return results dated 2024, explicitly note the date discrepancy. "
                "Do NOT present 2024 news as 2025 news without qualification.\n\n"
                "You are a helpful assistant with access to external tools. "
                "You can execute code when needed using COMPOSIO_REMOTE_WORKBENCH or any available code execution tool.\n\n"
                "IMPORTANT EXECUTION GUIDELINES:\n"
                "- When the user requests an action (send email, upload file, execute code), proceed immediately without asking for confirmation.\n"
                "- The user has already authorized these actions by making the request.\n"
                "- Do not ask 'Should I proceed?' or 'Do you want me to send this?'\n"
                "- Complete the full task end-to-end in a single workflow.\n"
                "- If authentication is required, guide the user through it, then continue automatically.\n\n"
                "REMOTE vs LOCAL WORKFLOW:\n"
                "- The 'COMPOSIO' tools act as your Hands (Search, Email, Remote Execution).\n"
                "- The 'LOCAL_TOOLKIT' and your own capabilities act as your Brain (Analysis, Writing, Reasoning).\n"
                "GUIDELINES:\n"
                "1. DATA FLOW POLICY (LOCAL-FIRST): Prefer receiving data DIRECTLY into your context.\n"
                "   - Do NOT set `sync_response_to_workbench=True` unless you expect massive data (>5MB).\n"
                "   - Default behavior (`sync=False`) is faster and avoids unnecessary download steps.\n"
                "2. DATA COMPLETENESS: If a tool returns 'data_preview' or says 'Saved large response to <FILE>', it means the data was TRUNCATED.\n"
                "   - In these cases (and ONLY these cases), use 'workbench_download' to fetch the full file.\n"
                "3. WORKBENCH USAGE: Use the Remote Workbench ONLY for:\n"
                "   - External Action execution (APIs, Browsing).\n"
                "   - 'Heavy' operations that require specific Linux binaries (ffmpeg, etc).\n"
                "   - Untrusted code execution.\n"
                "   - DO NOT use it as a text editor or file buffer for small data. Do that LOCALLY.\n"
                "4. DELEGATION: For complex report generation, use the local 'report-creation-expert' sub-agent.\n"
                "   - CRITICAL: You MUST inject the 'Result Date' context into the sub-agent's instructions.\n"
                "5. UPLOADS: Only upload files to workbench if an external tool REQUIRES a remote file path (e.g. email attachment).\n\n"
                "ONE-SHOT EXAMPLE (Report Generation):\n"
                "User: 'Research X and email me a report.'\n"
                "You: \n"
                "  1. [Local] Search and gather data.\n"
                "  2. [Local] Generate 'report.html' using `write_local_file`.\n"
                "  3. [Bridge] Upload 'report.html' to remote using `workbench_upload`.\n"
                "  4. [Remote] Send email using `GMAIL_SEND_EMAIL` attached with the remote file path."
            ),
            mcp_servers={
                "composio": {
                    "type": "http",
                    "url": session.mcp.url,
                    "headers": {"x-api-key": os.environ["COMPOSIO_API_KEY"]},
                },
                "local_toolkit": {
                    "type": "stdio",
                    "command": sys.executable,
                    "args": ["src/mcp_server.py"],
                },
            },
            permission_mode="bypassPermissions",
        )

        # Initialize trace dict now that session is available
        global trace
        trace = {
            "session_info": {
                "url": session.mcp.url,
                "user_id": user_id,
                "timestamp": datetime.now().isoformat(),
            },
            "query": None,
            "start_time": None,
            "end_time": None,
            "total_duration_seconds": None,
            "tool_calls": [],
            "tool_results": [],
            "iterations": [],
            "logfire_enabled": bool(LOGFIRE_TOKEN),
        }

        # Configure observer to save artifacts to this workspace
        global OBSERVER_WORKSPACE_DIR
        OBSERVER_WORKSPACE_DIR = workspace_dir

        # Setup Output Logging
        run_log_path = os.path.join(workspace_dir, "run.log")
        log_file = open(run_log_path, "a", encoding="utf-8")
        sys.stdout = DualWriter(log_file, sys.stdout)
        sys.stderr = DualWriter(log_file, sys.stderr)

        # Log session info now that logging is set up

        # Inject Workspace Path into System Prompt for Sub-Agents
        abs_workspace_path = os.path.abspath(workspace_dir)
        options.system_prompt += (
            f"\n\nContext:\nCURRENT_SESSION_WORKSPACE: {abs_workspace_path}\n"
        )
        print(f"✅ Injected Session Workspace: {abs_workspace_path}")

        # Extract Trace ID (Lazy import to ensure OTel is ready)
        # Extract Trace ID (Lazy import to ensure OTel is ready)
        import opentelemetry.trace

        span = opentelemetry.trace.get_current_span()
        trace_id = span.get_span_context().trace_id
        trace_id_hex = format(trace_id, "032x")
        trace["trace_id"] = trace_id_hex

        print(f"\n=== Composio Session Info ===")
        print(f"Session URL: {session.mcp.url}")
        print(f"User ID: {user_id}")
        print(f"Timestamp: {timestamp}")
        print(f"Trace ID: {trace_id_hex}")
        print(f"============================\n")

        print("=" * 80)
        print("Composio Agent Ready")
        print("=" * 80)
        print()

        trace["start_time"] = datetime.now().isoformat()
        start_ts = time.time()

        prompt_session = PromptSession()

        async with ClaudeSDKClient(options) as client:
            while True:
                # 1. Get User Input
                print("\n" + "=" * 80)
                try:
                    with patch_stdout():
                        user_input = await prompt_session.prompt_async(
                            "🤖 Enter your request (or 'quit'): ",
                        )
                    user_input = user_input.strip()
                except (EOFError, KeyboardInterrupt):
                    break
                if not user_input or user_input.lower() in ("quit", "exit"):
                    break

                trace["query"] = user_input
                if LOGFIRE_TOKEN:
                    logfire.info("query_started", query=user_input)

                # 2. Determine Complexity
                complexity = await classify_query(client, user_input)

                # 3. Route Query
                is_simple = complexity == "SIMPLE"

                if is_simple:
                    # Try Fast Path
                    success = await handle_simple_query(client, user_input)
                    if not success:
                        is_simple = False  # Fallback to Complex Path

                if not is_simple:
                    # Complex Path (Tool Loop)
                    iteration = 1
                    current_query = user_input

                    while True:
                        needs_input, auth_link = await run_conversation(
                            client, current_query, start_ts, iteration
                        )

                        if needs_input and auth_link:
                            print(f"\n{'=' * 80}")
                            print("🔐 AUTHENTICATION REQUIRED")
                            print(f"{'=' * 80}")
                            print(f"\nPlease open this link in your browser:\n")
                            print(f"  {auth_link}\n")
                            print(
                                "After completing authentication, press Enter to continue..."
                            )
                            input()

                            current_query = "I have completed the authentication. Please continue with the task."
                            iteration += 1
                            continue
                        else:
                            break

            # End of Session Summary
            end_ts = time.time()
            trace["end_time"] = datetime.now().isoformat()
            trace["total_duration_seconds"] = round(end_ts - start_ts, 3)

            # Log final metrics to Logfire
            if LOGFIRE_TOKEN:
                logfire.info(
                    "session_complete",
                    total_time_seconds=trace["total_duration_seconds"],
                    total_tool_calls=len(trace["tool_calls"]),
                    total_iterations=len(trace["iterations"]),
                )

            print(f"\n{'=' * 80}")
            print("=== FINAL EXECUTION SUMMARY ===")
            print(f"{'=' * 80}")
            print(f"Total Time: {trace['total_duration_seconds']} seconds")
            print(f"Total Iterations: {len(trace['iterations'])}")
            print(f"Total Tool Calls: {len(trace['tool_calls'])}")
            print(f"Total Tool Results: {len(trace['tool_results'])}")

            # Check for code execution tools
            code_exec_used = False
            for tc in trace["tool_calls"]:
                if any(
                    x in tc["name"].upper()
                    for x in [
                        "WORKBENCH",
                        "CODE",
                        "EXECUTE",
                        "PYTHON",
                        "SANDBOX",
                        "BASH",
                    ]
                ):
                    code_exec_used = True
                    break

            if code_exec_used:
                print("\n🏭 CODE EXECUTION WAS USED!")
            else:
                print("\n⚠️ NO CODE EXECUTION - Agent may have simulated results")

            print("\n=== TOOL CALL BREAKDOWN ===")
            for tc in trace["tool_calls"]:
                marker = (
                    "🏭"
                    if any(
                        x in tc["name"].upper()
                        for x in ["WORKBENCH", "CODE", "EXECUTE", "BASH"]
                    )
                    else "  "
                )
                print(
                    f"  {marker} Iter {tc['iteration']} | +{tc['time_offset_seconds']:>6.1f}s | {tc['name']}"
                )

            print(f"{'=' * 80}")

            # Save trace
            # Save trace to workspace
            trace_path = os.path.join(workspace_dir, "trace.json")
            with open(trace_path, "w") as f:
                json.dump(trace, f, indent=2, default=str)
            print(f"\n📊 Full trace saved to {trace_path}")

            # Save summary text
            with open(os.path.join(workspace_dir, "summary.txt"), "w") as f:
                f.write(f"Total Time: {trace['total_duration_seconds']}s\n")
                f.write(f"Iterations: {len(trace['iterations'])}\n")
                f.write(f"Status: {trace.get('status', 'complete')}\n")

            if LOGFIRE_TOKEN and "trace_id" in trace:
                project_slug = os.getenv(
                    "LOGFIRE_PROJECT_SLUG", "Kjdragan/composio-claudemultiagent"
                )
                logfire_url = f"https://logfire.pydantic.dev/{project_slug}?q=trace_id%3D%27{trace['trace_id']}%27"
                print(f"📈 Logfire Trace: {logfire_url}")
            elif LOGFIRE_TOKEN:
                print(f"📈 Logfire traces available at: https://logfire.pydantic.dev/")

            print("\n" + "=" * 80)
            print("\n" + "=" * 80)
            print("Session ended. Thank you!")


if __name__ == "__main__":
    print("\n" + "=" * 80)
    print("Composio Agent - Claude SDK with Tool Router")
    print("Logfire tracing enabled for observability.")
    print("=" * 80 + "\n")
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("\n\n⚠️ Execution cancelled by user.")
        # logfire might not be configured if it failed early, but we try
        if "logfire" in globals() and LOGFIRE_TOKEN:
            logfire.warn("execution_cancelled")
    except Exception as e:
        print(f"\n\n❌ Execution error: {e}")
        if "logfire" in globals() and LOGFIRE_TOKEN:
            logfire.error("execution_error", error=str(e))

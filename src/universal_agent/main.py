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

# Add 'src' to sys.path to allow imports from universal_agent package
# This ensures functional imports regardless of invocation directory
current_dir = os.path.dirname(os.path.abspath(__file__))
src_dir = os.path.dirname(os.path.dirname(current_dir))  # Go up two levels to 'src'
if os.path.join(src_dir, "src") in sys.path:
     pass
else:
     sys.path.append(os.path.join(src_dir, "src"))

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
    AgentDefinition,
    AssistantMessage,
    TextBlock,
    ToolUseBlock,
    ToolResultBlock,
    ThinkingBlock,
    UserMessage,
    # Hook types for SubagentStop pattern
    HookMatcher,
    HookContext,
    HookJSONOutput,
)
from composio import Composio
# Local MCP server provides: crawl_parallel, read_local_file, write_local_file

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

# =============================================================================





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
    # Match search tools but exclude tool discovery (SEARCH_TOOLS)
    tool_upper = tool_name.upper()
    
    # Exclude tool discovery - COMPOSIO_SEARCH_TOOLS searches for tools, not web
    if "SEARCH_TOOLS" in tool_upper:
        return
    
    # Comprehensive allowlist for Composio search providers only
    # Note: Native WebSearch excluded - we only want intentional Composio research
    # MULTI_EXECUTE included because it may wrap search calls - inner parsing filters by tool_slug
    search_keywords = [
        # Composio native search (SEARCH_TOOLS excluded above)
        "COMPOSIO_SEARCH",
        # SerpAPI variants
        "SERPAPI", "SERP_API",
        # Future providers
        "EXA_SEARCH", "EXA_",
        "TAVILY", "TAVILI",  # Common misspelling
        # Generic Composio patterns
        "SEARCH_NEWS", "SEARCH_WEB", "SEARCH_GOOGLE", "SEARCH_BING",
        # Wrapper that may contain search results - inner parsing filters by tool_slug
        "MULTI_EXECUTE",
    ]
    is_serp_tool = any(kw in tool_upper for kw in search_keywords)

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

        # Special handling for Claude's native WebSearch format:
        # "Web search results for query: ...\n\nLinks: [{...}, {...}]"
        if "WebSearch" in tool_name or raw_json.startswith("Web search results"):
            import re
            links_match = re.search(r'Links:\s*(\[.*\])', raw_json, re.DOTALL)
            if links_match:
                try:
                    links_list = json.loads(links_match.group(1))
                    # Convert to our standard format
                    data = {
                        "organic_results": [
                            {
                                "title": item.get("title"),
                                "link": item.get("url"),
                                "snippet": item.get("snippet", ""),
                            }
                            for item in links_list
                            if isinstance(item, dict)
                        ]
                    }
                except json.JSONDecodeError:
                    return
            else:
                return
        else:
            # Parse JSON normally
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
                            "position": a.get("position")
                            or (idx + 1),  # Use API position or 1-indexed array order
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
                        for idx, a in enumerate(raw_list)
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
                            "position": r.get("position")
                            or (idx + 1),  # Use API position or 1-indexed array order
                            "title": r.get("title"),
                            "url": r.get("link"),
                            "snippet": r.get("snippet"),
                        }
                        for idx, r in enumerate(raw_list)
                    ],
                }

            # CASE C: SEARCH_WEB with 'answer' + 'citations' format
            # COMPOSIO_SEARCH_WEB returns: {"answer": "...", "citations": [{id, source, snippet}, ...]}
            elif "citations" in search_data or "answer" in search_data:
                citations = safe_get_list(search_data, "citations")
                cleaned = {
                    "type": "web_answer",
                    "timestamp": datetime.now().isoformat(),
                    "tool": slug,
                    "answer": search_data.get("answer", ""),
                    "results": [
                        {
                            "position": idx + 1,
                            "title": c.get("source", c.get("id", "")),
                            "url": c.get("id", c.get("source", "")),  # 'id' often contains URL
                            "snippet": c.get("snippet", ""),
                        }
                        for idx, c in enumerate(citations)
                        if isinstance(c, dict)
                    ],
                }


            # Save if we found cleanable data
            if cleaned and workspace_dir:
                filename = "unknown"  # Initialize before try block
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
                        # Return feedback that can be injected into agent context
                        # This tells the agent NOT to save again
                        print(f"   ⚠️  Agent: DO NOT save search results again - already persisted locally.")
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
        except (json.JSONDecodeError, ValueError, SyntaxError):
            result_data = {
                "raw": tool_result[:500] if isinstance(tool_result, str) else ""
            }

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


# Persistent reports directory (outside session workspaces)
SAVED_REPORTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "SAVED_REPORTS"
)


async def observe_and_save_work_products(
    tool_name: str, tool_input: dict, tool_result: str, workspace_dir: str
) -> None:
    """
    Observer: Copy work product reports to persistent SAVED_REPORTS directory.
    This supplements the session workspace save - reports are saved to BOTH locations.
    """
    if "write_local_file" not in tool_name.lower():
        return
    
    # Only process if this is a work_products file
    file_path = tool_input.get("path", "")
    if "work_products" not in file_path:
        return
    
    try:
        # Ensure persistent directory exists
        os.makedirs(SAVED_REPORTS_DIR, exist_ok=True)
        
        # Extract original filename
        original_filename = os.path.basename(file_path)
        name_part, ext = os.path.splitext(original_filename)
        
        # Add timestamp for uniqueness
        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        persistent_filename = f"{name_part}_{timestamp_str}{ext}"
        persistent_path = os.path.join(SAVED_REPORTS_DIR, persistent_filename)
        
        # Get the full path of the source file
        abs_workspace_dir = os.path.abspath(workspace_dir) if not os.path.isabs(workspace_dir) else workspace_dir
        # Normalize the path - handle both relative and absolute paths
        if os.path.isabs(file_path):
            source_path = file_path
        else:
            # Find the session directory from the path
            base_dir = os.path.dirname(os.path.dirname(abs_workspace_dir))  # Up to AGENT_RUN_WORKSPACES
            source_path = os.path.join(base_dir, file_path)
        
        # Wait a moment for the file to be written
        await asyncio.sleep(0.5)
        
        # Copy the file
        if os.path.exists(source_path):
            import shutil
            shutil.copy2(source_path, persistent_path)
            print(f"\n📁 [OBSERVER] Saved to persistent: {persistent_path}")
            logfire.info("work_product_saved_persistent", source=file_path, dest=persistent_path)
        else:
            logfire.warning("work_product_source_not_found", path=source_path)
            
    except Exception as e:
        logfire.warning("work_product_observer_error", tool=tool_name, error=str(e))





def verify_subagent_compliance(
    tool_name: str, tool_content: str, workspace_dir: str
) -> str | None:
    """
    Verify that report-creation-expert sub-agent saved required artifacts.
    Returns an error message to inject if compliance failed, None if OK.
    """
    # Only check for Task (sub-agent) tool results
    if "task" not in tool_name.lower():
        return None

    # Check if this looks like a report sub-agent completion
    content_lower = tool_content.lower() if isinstance(tool_content, str) else ""
    is_report_task = any(
        keyword in content_lower
        for keyword in ["report", "comprehensive", "html", "work_products"]
    )

    if not is_report_task:
        return None

    # Check for Evidence of Research Data (search_results/*.md)
    search_results_dir = os.path.join(workspace_dir, "search_results")
    
    has_search_results = False
    if os.path.exists(search_results_dir):
        # Check if directory is not empty
        if any(os.scandir(search_results_dir)):
            has_search_results = True

    if has_search_results:
        return None  # Compliant: Data preserved

    # Conditional Check: Did we promise a "Comprehensive" report?
    # If the output claims "Comprehensive" or "Deep Dive", we EXPECT data to allow audit.
    # If it's just a summary, allow skipping extraction.
    is_claimed_comprehensive = any(
        keyword in content_lower
        for keyword in ["comprehensive", "deep dive", "full analysis", "detailed report"]
    )

    if is_claimed_comprehensive:
        logfire.warning(
            "subagent_compliance_failed",
            reason="comprehensive_report_without_data",
            workspace=workspace_dir,
        )
        return (
            "\n\n❌ **COMPLIANCE ERROR**: The report claimed to be 'Comprehensive' but no "
            "raw research data (search_results) was saved. \n"
            "**Rule**: For comprehensive reports, you MUST Use `crawl_parallel` to extract and preserve source data.\n"
            "If this was a simple summary, do not label it as 'Comprehensive'."
        )
    
    return None


# =============================================================================
# HOOK CALLBACKS - For sub-agent lifecycle events
# =============================================================================

async def on_subagent_stop(
    input_data: dict, tool_use_id: str | None, context: HookContext
) -> HookJSONOutput:
    """
    Hook: Fires when a sub-agent completes its work.
    Verifies artifacts were created and injects guidance for next steps.
    """
    logfire.info("subagent_stop_hook_fired", input_preview=str(input_data)[:500])
    
    # Check if report was created in work_products/
    global OBSERVER_WORKSPACE_DIR
    if OBSERVER_WORKSPACE_DIR:
        work_products = os.path.join(OBSERVER_WORKSPACE_DIR, "work_products")
        search_results = os.path.join(OBSERVER_WORKSPACE_DIR, "search_results")
        
        # Check for HTML report
        has_report = False
        report_file = None
        if os.path.exists(work_products):
            html_files = [f for f in os.listdir(work_products) if f.endswith(".html")]
            if html_files:
                has_report = True
                report_file = html_files[0]
        
        # Check for extracted content
        has_extracted = False
        if os.path.exists(search_results):
            md_files = [f for f in os.listdir(search_results) if f.endswith(".md")]
            has_extracted = len(md_files) > 0
        
        if has_report:
            logfire.info(
                "subagent_report_created",
                report_file=report_file,
                work_products_dir=work_products,
            )
            return {
                "systemMessage": (
                    f"✅ Sub-agent completed successfully! Report saved: {report_file}\n\n"
                    "NEXT STEPS (REQUIRED):\n"
                    "1. Update TodoWrite to mark 'Delegate report creation' as completed\n"
                    f"2. Upload report using workbench_upload('{work_products}/{report_file}', '/home/user/{report_file}')\n"
                    "3. Send email using GMAIL_SEND_EMAIL with the remote file path\n"
                    "4. Mark all tasks complete in TodoWrite"
                )
            }
        elif has_extracted:
            logfire.warning(
                "subagent_no_report_but_has_data",
                extracted_count=len(md_files),
                search_results_dir=search_results,
            )
            return {
                "systemMessage": (
                    f"⚠️ Sub-agent finished but no report found in work_products/. "
                    f"However, {len(md_files)} markdown files were extracted to search_results/.\n"
                    "The sub-agent may have failed during synthesis. Check the extracted content and retry."
                )
            }
        else:
            logfire.warning("subagent_no_artifacts", workspace=OBSERVER_WORKSPACE_DIR)
            return {
                "systemMessage": (
                    "⚠️ Sub-agent finished but no artifacts found. "
                    "Check if URLs were passed correctly and retry the task."
                )
            }
    
    return {}


# =============================================================================

# Session and options will be created in main() after Composio initialization
user_id = "user_123"
session = None


# Options will be created in main() after session is initialized
options = None


# Trace will be created in main() after session is initialized
# Type hint: Dict[str, Any] - initialized in main()
trace: dict = {}


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
        final_text = ""  # Buffer to capture final agent response for printing after execution summary

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

                        # Check for sub-agent context (parent_tool_use_id indicates this call is from within a sub-agent)
                        parent_tool_id = getattr(msg, "parent_tool_use_id", None)

                        logfire.info(
                            "tool_call",
                            tool_name=block.name,
                            tool_id=block.id,
                            input_size=tool_record["input_size_bytes"],
                            input_preview=input_preview,
                            parent_tool_use_id=parent_tool_id,  # Sub-agent context
                            is_subagent_call=bool(parent_tool_id),
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

                        # Buffer final text to print AFTER execution summary (not printed here)
                        final_text = block.text[:3000] + ("..." if len(block.text) > 3000 else "")

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
                        # Always show a preview of the result content
                        preview = result_record.get("content_preview", "")[:500]
                        if preview:
                            print(
                                f"   Preview: {preview}{'...' if len(result_record.get('content_preview', '')) > 500 else ''}"
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
                            # Work products observer - copy reports to persistent directory
                            asyncio.create_task(
                                observe_and_save_work_products(
                                    tool_name,
                                    tool_input or {},
                                    content_str,
                                    OBSERVER_WORKSPACE_DIR,
                                )
                            )


                            # Post-subagent compliance verification (for Task results)
                            compliance_error = verify_subagent_compliance(
                                tool_name, content_str, OBSERVER_WORKSPACE_DIR
                            )
                            if compliance_error:
                                # Log the compliance failure prominently
                                print(compliance_error)
                                logfire.warning(
                                    "subagent_compliance_message_injected",
                                    error=compliance_error[:200],
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

        return needs_user_input, auth_link, final_text


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
    with logfire.span("standalone_composio_test") as span:
        # Setup Session Workspace
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        workspace_dir = os.path.join("AGENT_RUN_WORKSPACES", f"session_{timestamp}")
        os.makedirs(workspace_dir, exist_ok=True)

        # Initialize Composio with automatic file downloads to this workspace
        global composio
        downloads_dir = os.path.join(workspace_dir, "downloads")
        os.makedirs(downloads_dir, exist_ok=True)
      # =========================================================================
    # 2. NON-BLOCKING AUTH FIX
    # Monkeypatch builtins.input to prevent EOFError during headless auth prompts
    # =========================================================================
    import builtins
    
    original_input = builtins.input
    
    def non_blocking_input(prompt=None):
        if prompt:
            print(prompt, end="")  # Print prompt to stdout/logs
        # Return empty string instead of blocking/crashing
        print("\n\n [System] Non-blocking input called. Skipping pause.", flush=True)
        return ""
        
    builtins.input = non_blocking_input
    print("✅ Non-blocking input handler installed (Fix 1)")

    # =========================================================================
    # 3. Initialize Composio    # User Identity
    # Using specific entity ID that holds the active integrations (GitHub, Linear, Notion, etc.)
    # user_id = "user_123"  # Consolidated to the primary admin identity
    user_id = "pg-test-86524ebc-9b1e-4f08-bd20-b77dd71c2df9"
    
    from universal_agent.utils.composio_discovery import discover_connected_toolkits, get_local_tools

    # Initialize Client
    composio = Composio(
        api_key=os.getenv("COMPOSIO_API_KEY"),
        file_download_dir=downloads_dir
    )

    # Register custom tools BEFORE session creation
    # Custom tools should be auto-discovered when registered before composio.create()
    # Local MCP tools are exposed via stdio server defined in mcp_servers config

    # Create Composio session
    # IMPORTANT: Per 000_CURRENT_CONTEXT.md, we disable external crawlers to force use of
    # local crawl_parallel tool. This prevents COMPOSIO_SEARCH_TOOLS from recommending
    # firecrawl or exa web scrapers.
    # Create Composio session
    # IMPORTANT: Per 000_CURRENT_CONTEXT.md, we disable external crawlers to force use of
    # local crawl_parallel tool. This prevents COMPOSIO_SEARCH_TOOLS from recommending
    # firecrawl or exa web scrapers.
    session = composio.create(
        user_id=user_id,
        toolkits={"disable": ["firecrawl", "exa"]}
    )

    # 1. Dynamic Remote Discovery
    # Using client.connected_accounts.list(user_ids=[user_id]) for reliable persistent connection check
    ALLOWED_APPS = discover_connected_toolkits(composio, user_id)
    if not ALLOWED_APPS:
        # Fallback if discovery completely fails or no apps connected
        ALLOWED_APPS = ["gmail", "github", "codeinterpreter", "slack", "composio_search"]
        print(f"⚠️ Discovery returned 0 apps (or only codeinterpreter). Using defaults: {ALLOWED_APPS}")
    else:
        print(f"✅ Discovered Active Composio Apps: {ALLOWED_APPS}")

    # 2. Local MCP Discovery
    local_tools = get_local_tools()
    print(f"✅ Active Local MCP Tools: {local_tools}")

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
            "🔍 SEARCH TOOL PREFERENCE:\n"
            "- For web/news research, ALWAYS use Composio search tools (SERPAPI_SEARCH, COMPOSIO_SEARCH_NEWS, etc.).\n"
            "- Do NOT use native 'WebSearch' - it bypasses our artifact saving system.\n"
            "- Composio search results are auto-saved by the Observer for sub-agent access.\n\n"
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
            "   - 🚫 NEVER use REMOTE_WORKBENCH to save search results. The Observer already saves them automatically.\n"
            "4. 🚨 MANDATORY DELEGATION FOR REPORTS (HAND-OFF PROTOCOL):\n"
            "   - Role: You are the SCOUT. You find the information sources.\n"
            "   - Sub-Agent Role: The EXPERT. They process and synthesize the sources.\n"
            "   - PROCEDURE:\n"
            "     1. COMPOSIO Search -> Results are **AUTO-SAVED** by Observer to `search_results/`. DO NOT save again.\n"
            "     2. DO NOT read these files or extract URLs yourself. You are not the Expert.\n"
            "     3. DELEGATE immediately to 'report-creation-expert' using `Task`.\n"
            "     4. HAND-OFF PROMPT (Use EXACTLY this string, do not add URLs):\n"
            "        'Scan the search_results/ directory for JSON files and generate the report.'\n"
            "   - ✅ SubagentStop HOOK: When the sub-agent finishes, a hook will inject a system message with next steps.\n"
            "     Wait for this message before proceeding with upload/email.\n"
            "5. 📤 EMAIL ATTACHMENTS - USE `upload_to_composio` (ONE-STEP SOLUTION):\n"
            "   - For email attachments, call `mcp__local_toolkit__upload_to_composio(path='/local/path/to/file', session_id='xxx')`\n"
            "   - This tool handles EVERYTHING: local→remote→S3 in ONE call.\n"
            "   - It returns `s3_key` which you pass to GMAIL_SEND_EMAIL's `attachment.s3key` field.\n"
            "   - DO NOT manually call workbench_upload + REMOTE_WORKBENCH. That's the old, broken way.\n"
            "6. ⚠️ LOCAL vs REMOTE FILESYSTEM:\n"
            "   - LOCAL paths: `/home/kjdragan/...` or relative paths - accessible by local_toolkit tools.\n"
            "   - REMOTE paths: `/home/user/...` - only accessible inside COMPOSIO_REMOTE_WORKBENCH sandbox.\n"
            "7. 📁 WORK PRODUCTS - MANDATORY AUTO-SAVE:\n"
            "   🚨 BEFORE responding with ANY significant output, you MUST save it first.\n"
            "   - TRIGGERS: Tables, summaries, analyses, code generated for user, extracted data.\n"
            "   - EXCEPTION: Do NOT use this for 'Reports'. Delegate Reports to the 'Report Creation Expert' (Rule 4).\n"
            "   - HOW: Call `write_local_file` with:\n"
            "     - `file_path`: CURRENT_SESSION_WORKSPACE + '/work_products/' + descriptive_name\n"
            "     - `content`: The full output you're about to show the user\n"
            "   - NAMING: `dependency_summary.md`, `calendar_events.txt`, `generated_script.py`\n"
            "   - `work_products` dir is auto-created. Just save there.\n"
            "     Wait for 'File saved...' confirmation before proceeding.\n\n"
            "8. ⚡ COMPOSIO_MULTI_EXECUTE_TOOL USAGE:\n"
            "   - When using `COMPOSIO_MULTI_EXECUTE_TOOL`, you MUST provide `tool_slug` for EACH item in the `tools` list.\n"
            "   - INCORRECT: `{'tools': [{'arguments': {...}}]}`\n"
            "   - CORRECT: `{'tools': [{'tool_slug': 'googlecalendar', 'arguments': {...}}]}`\n"
            "   - omitting `tool_slug` will cause the action to fail.\n\n"
            "9. 🔗 MANDATORY REPORT DELEGATION (YOU MUST DELEGATE):\n"
            "   🚨 TRIGGER KEYWORDS REQUIRING DELEGATION: 'report', 'comprehensive', 'detailed', 'in-depth', 'analysis', 'research'.\n"
            "   IF the user query contains ANY of these keywords, you MUST delegate to 'report-creation-expert'.\n"
            "   - After a Composio search, the Observer AUTO-SAVES results to `search_results/` directory.\n"
            "   - You will see: '📁 [OBSERVER] Saved: search_results/xxx.json'.\n"
            "   - DO NOT write the report yourself. DO NOT call `crawl_parallel` yourself.\n"
            "   - IMMEDIATELY delegate to 'report-creation-expert' with: 'Scan search_results/ directory for JSON files and generate the report.'\n"
            "   - WHY: The sub-agent will scrape ALL URLs for full article content. Your search only has snippets.\n"
            "   - WITHOUT DELEGATION: Your report will be shallow (snippets only). WITH DELEGATION: Deep research (full articles).\n"
            "   - Trust the Observer. Trust the sub-agent. Your job is to search and delegate.\n\n"
            "10. 💡 PROACTIVE FOLLOW-UP SUGGESTIONS:\n"
            "   - After completing a task, suggest 2-3 helpful follow-up actions based on what was just accomplished.\n"
            "   - Examples: 'Would you like me to email this report?', 'Should I save this to a different format?',\n"
            "     'I can schedule a calendar event for the mentioned deadline if you'd like.'\n"
            "   - Keep suggestions relevant to the completed task and the user's apparent goals."
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
                    "args": [os.path.join(os.path.dirname(os.path.dirname(__file__)), "mcp_server.py")],
                },
            },
            # NOTE: Do NOT set allowed_tools here - that would restrict to ONLY those tools.
            # The agent needs access to BOTH Composio tools (COMPOSIO_SEARCH_NEWS, etc.)
            # AND local_toolkit tools (crawl_parallel, read_local_file, etc.)
            # Sub-agents inherit all tools via model="inherit".
            agents={
                "report-creation-expert": AgentDefinition(
                    description=(
                        "🚨 MANDATORY DELEGATION TARGET for ALL report generation tasks. "
                        "WHEN TO DELEGATE (REQUIRED): User asks for 'report', 'comprehensive', 'detailed', "
                        "'in-depth', 'analysis', or 'summary'.\n"
                        "🛑 STOP! DO NOT WRITE REPORTS YOURSELF. YOU WILL FAIL.\n"
                        "You lack the ability to process full context. You MUST delegate to the 'Report Creation Expert'.\n"
                        "REQUIRED DELEGATION PROMPT: 'Scan all JSON files in search_results/ directory using list_directory and generate report.'\n"
                        "DO NOT pass URLs manually. Pass the directory instruction only."
                        "PRIMARY AGENT MUST NOT: Generate reports directly. "
                        "REQUIRED INPUT: Tell the sub-agent to 'Scan all JSON files in search_results/ directory'. DO NOT list individual URLs. "
                        "THIS SUB-AGENT PROVIDES: Full article content extraction via crawl_parallel, professional report synthesis."
                    ),
                    prompt=(
                        f"Result Date: {datetime.now().strftime('%A, %B %d, %Y')}\n"
                        f"CURRENT_SESSION_WORKSPACE: {workspace_dir}\n\n"
                        "You are a **Report Creation Expert**.\n\n"
                        "🚨 CRITICAL TOOL INSTRUCTIONS:\n"
                        "1. DO NOT use COMPOSIO_SEARCH_TOOLS - you already have the tools you need.\n"
                        "2. DO NOT use Firecrawl or any Composio crawling tools.\n"
                        "3. USE ONLY these specific tools:\n"
                        "   - `mcp__local_toolkit__list_directory` - to find search result files\n"
                        "   - `mcp__local_toolkit__read_local_file` - to get URLs from JSONs\n"
                        "   - `mcp__local_toolkit__crawl_parallel` - to extract content\n"
                        "   - `mcp__local_toolkit__write_local_file` - to save report\n\n"
                        "---\n\n"
                        "## WORKFLOW\n\n"
                        "### Step 1: Discover & Scrape (MANDATORY)\n"
                        "1. Call `mcp__local_toolkit__list_directory` on filenames in `{workspace_dir}/search_results/`.\n"
                        "2. Read EVERY `.json` file found using `read_local_file`.\n"
                        "3. Extract EVERY `url` from ALL files (consolidate into one list).\n"
                        "4. Call `mcp__local_toolkit__crawl_parallel` with the COMPLETE list (no batch limit).\n"
                        "   *Our scraper is instant. Do not cherry-pick. If you find 40 URLs, scrape 40 URLs.*\n\n"
                        "### Step 2: Read ALL Scraped Content (MANDATORY - DO NOT SKIP FILES)\n"
                        "1. After crawl_parallel completes, call `list_directory` again to see all `crawl_*.md` files.\n"
                        "2. You MUST read EVERY crawl_*.md file. There is no limit.\n"
                        "3. If there are more than ~6 files, read them in batches (6 files per read operation).\n"
                        "4. DO NOT generate the report until you have read ALL crawl_*.md files.\n"
                        "5. Keep track: 'Read X of Y files' - if X < Y, continue reading.\n\n"
                        "### Step 3: Synthesize Report\n"
                        "Evaluate your source material and design an appropriate structure for the content.\n\n"
                        "**QUALITY PRINCIPLES:**\n"
                        "- Use specific quotes, numbers, and named examples to support points\n"
                        "- Avoid generic statements like 'experts say' or 'it's widely believed'\n"
                        "- Include interesting findings even if they counter main themes - present what you found\n"
                        "- Credit sources naturally inline (e.g., 'according to ISW...' or 'the BBC reports...')\n"
                        "- List all sources at the end of the report\n"
                        "- Professional HTML presentation appropriate to the content\n\n"
                        "**DEPTH CALIBRATION:**\n"
                        "- Match report depth to source richness\n"
                        "- Brief sources (3-5 articles) → Focused, concise summary\n"
                        "- Rich sources (10+ articles) → Comprehensive, multi-section analysis\n"
                        "- Let the structure emerge from the material, not from a template\n\n"
                        "**SYNTHESIS & COHERENCE:**\n"
                        "- Where sources discuss related topics, group and synthesize them into cohesive sections\n"
                        "- BUT: News often covers genuinely disjointed events - don't force artificial connections\n"
                        "- Prioritize completeness over flow - include all interesting facts even if standalone\n"
                        "- It's okay to have distinct sections for unrelated developments\n"
                        "- Aim for thematic grouping where natural, standalone items where not\n\n"
                        "### Step 4: Save Report\n"
                        f"Save as `.html` to `{workspace_dir}/work_products/` using `mcp__local_toolkit__write_local_file`.\n\n"
                        "🚨 START IMMEDIATELY: List the directory to find your targets."
                    ),
                    # Omit 'tools' so sub-agent inherits ALL tools including MCP tools
                    model="inherit",
                ),
                "slack-expert": AgentDefinition(
                    description=(
                        "Expert for Slack workspace interactions. "
                        "DELEGATE when user mentions: 'slack', 'channel', '#channel-name', "
                        "'post to slack', 'summarize messages', 'what was discussed in'."
                    ),
                    prompt=(
                        f"Result Date: {datetime.now().strftime('%A, %B %d, %Y')}\n"
                        f"CURRENT_SESSION_WORKSPACE: {workspace_dir}\n\n"
                        "You are a **Slack Expert**.\n\n"
                        "## AVAILABLE TOOLS\n"
                        "- `SLACK_LIST_CHANNELS` - List available channels\n"
                        "- `SLACK_FETCH_CONVERSATION_HISTORY` - Get messages from a channel\n"
                        "- `SLACK_SEND_MESSAGE` - Post a message to a channel\n\n"
                        "## WORKFLOW FOR SUMMARIZATION\n"
                        "1. Use `SLACK_LIST_CHANNELS` to find the channel ID by name\n"
                        "2. Use `SLACK_FETCH_CONVERSATION_HISTORY` with the channel ID and `limit` parameter\n"
                        "3. Extract key information: topics discussed, decisions made, action items\n"
                        "4. Write a brief summary to the workspace using `mcp__local_toolkit__write_local_file`\n\n"
                        "## WORKFLOW FOR POSTING\n"
                        "1. Use `SLACK_LIST_CHANNELS` to find the target channel ID\n"
                        "2. Format your message clearly with sections if needed\n"
                        "3. Use `SLACK_SEND_MESSAGE` with the channel ID and formatted message\n\n"
                        "🚨 IMPORTANT: Always use channel IDs (not names) for API calls."
                    ),
                    model="inherit",
                ),
            },
            hooks={
                "SubagentStop": [
                    HookMatcher(matcher=None, hooks=[on_subagent_stop]),
                ],
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
    # Safely append to system_prompt (ensure it's a string)
    if options.system_prompt and isinstance(options.system_prompt, str):
        options.system_prompt += (
            f"\n\nContext:\nCURRENT_SESSION_WORKSPACE: {abs_workspace_path}\n"
        )
    else:
        # Create new context if no system prompt set
        options.system_prompt = (
            f"Context:\nCURRENT_SESSION_WORKSPACE: {abs_workspace_path}\n"
        )
    print(f"✅ Injected Session Workspace: {abs_workspace_path}")

    # Extract Trace ID (Lazy import to ensure OTel is ready)
    # Extract Trace ID (Lazy import to ensure OTel is ready)
    if LOGFIRE_TOKEN:
        try:
            # Span was created above with 'as span'
            trace_id = span.get_span_context().trace_id
            trace_id_hex = format(trace_id, "032x")
            trace["trace_id"] = trace_id_hex
        except Exception as e:
            print(f"⚠️ Failed to extract trace ID: {e}")
            trace_id_hex = "0" * 32
    else:
        trace_id_hex = "N/A"

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
                    # Complex Path (Tool Loop) - track per-request timing
                    request_start_ts = time.time()
                    iteration = 1
                    current_query = user_input

                    final_response_text = ""
                    while True:
                        needs_input, auth_link, final_text = await run_conversation(
                            client, current_query, start_ts, iteration
                        )
                        final_response_text = final_text  # Capture for printing after summary

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

                    # Per-request Execution Summary (shown before agent response)
                    request_end_ts = time.time()
                    request_duration = round(request_end_ts - request_start_ts, 3)
                    
                    # Collect tool calls for this request
                    request_tool_calls = [tc for tc in trace["tool_calls"] if tc.get("time_offset_seconds", 0) >= (request_start_ts - start_ts)]
                    
                    print(f"\n{'=' * 80}")
                    print("=== EXECUTION SUMMARY ===")
                    print(f"{'=' * 80}")
                    print(f"Execution Time: {request_duration} seconds")
                    print(f"Tool Calls: {len(request_tool_calls)}")
                    
                    # Check for code execution
                    code_exec_used = any(
                        any(x in tc["name"].upper() for x in ["WORKBENCH", "CODE", "EXECUTE", "PYTHON", "SANDBOX", "BASH"])
                        for tc in request_tool_calls
                    )
                    if code_exec_used:
                        print("🏭 Code execution was used")
                    
                    # Tool breakdown
                    if request_tool_calls:
                        print("\n=== TOOL CALL BREAKDOWN ===")
                        for tc in request_tool_calls:
                            marker = "🏭" if any(x in tc["name"].upper() for x in ["WORKBENCH", "CODE", "EXECUTE", "BASH"]) else "  "
                            print(f"  {marker} Iter {tc['iteration']} | +{tc['time_offset_seconds']:>6.1f}s | {tc['name']}")
                    
                    print(f"{'=' * 80}")
                    
                    # Print agent's final response (with follow-up suggestions) AFTER execution summary
                    if final_response_text:
                        print(f"\n{final_response_text}")

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
            print("=== SESSION COMPLETE ===")
            print(f"{'=' * 80}")
            print(f"Total Session Time: {trace['total_duration_seconds']} seconds")
            print(f"Total Tool Calls: {len(trace['tool_calls'])}")
            print(f"{'=' * 80}")

            # Save trace
            # Save trace to workspace
            trace_path = os.path.join(workspace_dir, "trace.json")
            with open(trace_path, "w") as f:
                json.dump(trace, f, indent=2, default=str)
            print(f"\n📊 Full trace saved to {trace_path}")

            # Save comprehensive session summary
            summary_path = os.path.join(workspace_dir, "session_summary.txt")
            with open(summary_path, "w") as f:
                f.write("=" * 60 + "\n")
                f.write("SESSION SUMMARY\n")
                f.write("=" * 60 + "\n\n")
                f.write(f"Session Start: {trace.get('start_time', 'N/A')}\n")
                f.write(f"Session End: {trace.get('end_time', 'N/A')}\n")
                f.write(f"Execution Time: {trace['total_duration_seconds']}s\n")
                f.write(f"Total Iterations: {len(trace['iterations'])}\n")
                f.write(f"Total Tool Calls: {len(trace['tool_calls'])}\n")
                f.write(f"Total Tool Results: {len(trace['tool_results'])}\n")
                f.write(f"Status: {trace.get('status', 'complete')}\n\n")
                
                # Code execution check
                code_exec_tools = ["WORKBENCH", "CODE", "EXECUTE", "PYTHON", "SANDBOX", "BASH"]
                code_exec_used = any(
                    any(x in tc["name"].upper() for x in code_exec_tools)
                    for tc in trace["tool_calls"]
                )
                f.write(f"Code Execution Used: {'Yes' if code_exec_used else 'No'}\n\n")
                
                # Tool call breakdown
                f.write("=" * 60 + "\n")
                f.write("TOOL CALL BREAKDOWN\n")
                f.write("=" * 60 + "\n")
                for tc in trace["tool_calls"]:
                    marker = "🏭 " if any(x in tc["name"].upper() for x in ["WORKBENCH", "CODE", "EXECUTE", "BASH"]) else "   "
                    f.write(f"{marker}Iter {tc['iteration']} | +{tc['time_offset_seconds']:>6.1f}s | {tc['name']}\n")
                
                # Logfire trace link
                if LOGFIRE_TOKEN and "trace_id" in trace:
                    project_slug = os.getenv("LOGFIRE_PROJECT_SLUG", "Kjdragan/composio-claudemultiagent")
                    logfire_url = f"https://logfire.pydantic.dev/{project_slug}?q=trace_id%3D%27{trace['trace_id']}%27"
                    f.write(f"\nLogfire Trace: {logfire_url}\n")
            
            print(f"📋 Session summary saved to {summary_path}")

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

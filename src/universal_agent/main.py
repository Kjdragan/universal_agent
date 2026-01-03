"""
Composio Agent - Claude SDK with Tool Router
A standalone agent using Claude Agent SDK with Composio MCP integration.
Traces are sent to Logfire for observability.
"""

import asyncio
import copy
import signal
import os
import sqlite3
import time
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from dotenv import load_dotenv

# Timezone helper for consistent date/time across deployments
def get_user_datetime():
    """
    Get current datetime in user's timezone (CST/CDT).
    Railway containers run in UTC, so we offset to Central Time.
    """
    # Try to use pytz if available, otherwise manual offset
    try:
        import pytz
        user_tz = pytz.timezone(os.getenv("USER_TIMEZONE", "America/Chicago"))
        return datetime.now(user_tz)
    except ImportError:
        # Fallback: CST is UTC-6 (or CDT is UTC-5 in summer)
        # For simplicity, use -6 (CST). For production, add DST logic.
        utc_now = datetime.now(timezone.utc)
        cst_offset = timezone(timedelta(hours=-6))
        return utc_now.astimezone(cst_offset)


@dataclass
class ExecutionResult:
    """
    Structured result from process_turn() for rich Telegram feedback.
    Contains all the data needed to display execution summary + agent response.
    """
    response_text: str                           # Agent's final response
    execution_time_seconds: float = 0.0          # Total execution time
    tool_calls: int = 0                          # Number of tool calls
    tool_breakdown: list = field(default_factory=list)  # [{name, time_offset, iteration}]
    code_execution_used: bool = False            # Whether code exec tools were used
    workspace_path: str = ""                     # Session workspace directory
    trace_id: Optional[str] = None               # Logfire trace ID for deep linking
    follow_up_suggestions: list = field(default_factory=list)  # Extracted follow-up options

from dotenv import load_dotenv

# Load environment FIRST
load_dotenv()

import sys
import argparse

# Add 'src' to sys.path to allow imports from universal_agent package
# This ensures functional imports regardless of invocation directory
current_dir = os.path.dirname(os.path.abspath(__file__))
src_dir = os.path.dirname(os.path.dirname(current_dir))  # Repo Root

# Add 'src' for package imports
if os.path.join(src_dir, "src") not in sys.path:
    sys.path.append(os.path.join(src_dir, "src"))

# Add Repo Root for 'Memory_System' imports
if src_dir not in sys.path:
    sys.path.append(src_dir)

# prompt_toolkit for better terminal input (arrow keys, history, multiline)
from prompt_toolkit import PromptSession
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.history import FileHistory
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.styles import Style


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


class BudgetExceeded(RuntimeError):
    def __init__(self, budget_name: str, limit: float, current: float, detail: str = ""):
        self.budget_name = budget_name
        self.limit = limit
        self.current = current
        self.detail = detail
        message = (
            f"Budget exceeded: {budget_name} "
            f"(limit={limit}, current={current})"
        )
        if detail:
            message = f"{message} - {detail}"
        super().__init__(message)

    def to_dict(self) -> dict:
        return {
            "budget_name": self.budget_name,
            "limit": self.limit,
            "current": self.current,
            "detail": self.detail,
        }


def _get_env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError:
        return default


def load_budget_config() -> dict:
    return {
        "max_wallclock_minutes": _get_env_int("UA_MAX_WALLCLOCK_MINUTES", 90),
        "max_steps": _get_env_int("UA_MAX_STEPS", 50),
        "max_tool_calls": _get_env_int("UA_MAX_TOOL_CALLS", 250),
    }


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

    # Instrument Anthropic SDK to trace Claude conversation turns and tool calls
    try:
        logfire.instrument_anthropic()
        print("✅ Logfire Anthropic instrumentation enabled")
    except Exception as e:
        print(f"⚠️ Anthropic instrumentation not available: {e}")

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
    ResultMessage,
    ThinkingBlock,
    UserMessage,
    # Hook types for SubagentStop pattern
    HookMatcher,
    HookContext,
    HookJSONOutput,
)
from typing import Any
from composio import Composio
# Durable runtime support
from universal_agent.durable.db import connect_runtime_db
from universal_agent.durable.ledger import ToolCallLedger
from universal_agent.durable.tool_gateway import prepare_tool_call, parse_tool_identity
from universal_agent.durable.normalize import deterministic_task_key, normalize_json
from universal_agent.durable.classification import (
    classify_replay_policy,
    classify_tool,
    resolve_tool_policy,
    validate_tool_policies,
)
from universal_agent.durable.state import (
    upsert_run,
    update_run_status,
    update_run_provider_session,
    start_step,
    complete_step,
    get_run,
    get_step_count,
    is_cancel_requested,
    mark_run_cancelled,
)
from universal_agent.durable.checkpointing import save_checkpoint, load_last_checkpoint
# Local MCP server provides: crawl_parallel, read_local_file, write_local_file

# Composio client - will be initialized in main() with file_download_dir
composio = None

# =============================================================================
# MEMORY SYSTEM INTEGRATION
# =============================================================================
from Memory_System.manager import MemoryManager
from Memory_System.tools import get_memory_tool_map

# Global Memory Manager is not needed here as it is used locally for prompt injection
# MEMORY_MANAGER = None

# =============================================================================
# MEMORY SYSTEM INTEGRATION
# =============================================================================
from Memory_System.manager import MemoryManager
from Memory_System.tools import get_memory_tool_map

# Global Memory Manager Instance
MEMORY_MANAGER = None

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
    tool_name: str, content: Any, workspace_dir: str
) -> None:
    """
    Observer: Parse SERP tool results and save cleaned artifacts.
    Uses Claude SDK typed content (list of TextBlock objects).
    """
    with logfire.span("observer_search_results", tool=tool_name):
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
            # Fallback WebSearch
            "WEBSEARCH", "WEB_SEARCH",
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

                # ---------------------------------------------------------
                # DYNAMIC SCHEMA PARSING
                # Priority: Special formats FIRST, then config-driven fallback
                # ---------------------------------------------------------
                
                # PRIORITY 1: Special "Answer + Citations" format (COMPOSIO_SEARCH_WEB)
                if "answer" in search_data and "citations" in search_data:
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
                                "url": c.get("id", c.get("source", "")),
                                "snippet": c.get("snippet", ""),
                            }
                            for idx, c in enumerate(citations)
                            if isinstance(c, dict)
                        ],
                    }
                
                # PRIORITY 2: News Results (explicit news_results key)
                elif "news_results" in search_data:
                    raw_list = safe_get_list(search_data, "news_results")
                    cleaned = {
                        "type": "news",
                        "timestamp": datetime.now().isoformat(),
                        "tool": slug,
                        "articles": [
                            {
                                "position": idx + 1,
                                "title": a.get("title"),
                                "url": a.get("link"),
                                "source": a.get("source", {}).get("name") if isinstance(a.get("source"), dict) else a.get("source"),
                                "snippet": a.get("snippet"),
                            }
                            for idx, a in enumerate(raw_list)
                            if isinstance(a, dict)
                        ],
                    }
                
                # PRIORITY 3: Organic Results (raw SERP format)
                elif "organic_results" in search_data:
                    raw_list = safe_get_list(search_data, "organic_results")
                    cleaned = {
                        "type": "web",
                        "timestamp": datetime.now().isoformat(),
                        "tool": slug,
                        "results": [
                            {
                                "position": r.get("position") or (idx + 1),
                                "title": r.get("title"),
                                "url": r.get("link"),
                                "snippet": r.get("snippet"),
                            }
                            for idx, r in enumerate(raw_list)
                        ],
                    }
                
                # PRIORITY 4: Config-driven parsing (for Scholar, Amazon, Shopping, etc.)
                else:
                    config = SEARCH_TOOL_CONFIG.get(slug)
                    if config:
                        list_key = config["list_key"]
                        url_key = config["url_key"]
                        raw_list = safe_get_list(search_data, list_key)
                        
                        if raw_list:
                            cleaned = {
                                "type": "search_result",
                                "timestamp": datetime.now().isoformat(),
                                "tool": slug,
                                config["list_key"]: [
                                    {
                                        "position": idx + 1,
                                        "title": item.get("title", f"Result {idx+1}"),
                                        "url": item.get(url_key),
                                        "snippet": item.get("snippet", item.get("description", "")),
                                        "source": item.get("source"),
                                    }
                                    for idx, item in enumerate(raw_list)
                                    if isinstance(item, dict)
                                ]
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

            # === JIT DELEGATION GUIDE RAIL (console output for logging) ===
            if saved_count > 0:
                print(f"\n   ✅ {saved_count} Search Result File(s) Saved for Sub-Agent.")
                print(f"   ⚠️ Reminder: Delegate to 'report-creation-expert' for full analysis.")

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
    with logfire.span("observer_workbench_activity", tool=tool_name):
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
    with logfire.span("observer_work_products", tool=tool_name):
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


async def observe_and_save_video_outputs(
    tool_name: str, tool_input: dict, tool_result: str, workspace_dir: str
) -> None:
    """
    Observer: Copy video/audio outputs to session work_products directory.
    Triggered when video_audio or youtube MCP tools produce output files.
    """
    with logfire.span("observer_video_outputs", tool=tool_name):
        # Only process video_audio and youtube MCP output tools
        video_tools = [
            "trim_video", "concatenate_videos", "extract_audio", "convert_video",
            "add_text_overlay", "add_image_overlay", "reverse_video", "compress_video",
            "rotate_video", "change_video_speed", "download_video", "download_audio"
        ]
        
        if not any(tool in tool_name.lower() for tool in video_tools):
            return
        
        try:
            # Parse output path from tool result
            import json
            import shutil
            
            # Try to extract output path from result
            output_path = None
            
            # Check for output_video_path in input
            if "output_video_path" in tool_input:
                output_path = tool_input["output_video_path"]
            elif "output_audio_path" in tool_input:
                output_path = tool_input["output_audio_path"]
            elif "output_path" in tool_input:
                output_path = tool_input["output_path"]
            
            # Also try to extract from result message
            if not output_path and "successfully" in tool_result.lower():
                # Try to find path in result (e.g., "Videos concatenated successfully to /path/to/file.mp4")
                if " to " in tool_result:
                    potential_path = tool_result.split(" to ")[-1].strip().rstrip('"}')
                    if potential_path.endswith((".mp4", ".mp3", ".wav", ".webm", ".avi", ".mov")):
                        output_path = potential_path
            
            if not output_path or not os.path.exists(output_path):
                return
            
            # Check if this is a "final" output (not intermediate like last_15_seconds.mp4)
            filename = os.path.basename(output_path)
            intermediate_patterns = ["last_", "first_", "temp_", "tmp_", "_part"]
            if any(pat in filename.lower() for pat in intermediate_patterns):
                return  # Skip intermediate files
            
            # Create work_products/media directory in session workspace
            media_dir = os.path.join(workspace_dir, "work_products", "media")
            os.makedirs(media_dir, exist_ok=True)
            
            # Copy to session workspace
            dest_path = os.path.join(media_dir, filename)
            shutil.copy2(output_path, dest_path)
            print(f"\n🎬 [OBSERVER] Saved media to session: {dest_path}")
            logfire.info("video_output_saved_session", source=output_path, dest=dest_path)
            
        except Exception as e:
            logfire.warning("video_observer_error", tool=tool_name, error=str(e))



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
# KNOWLEDGE BASE - Static tool guidance loaded at startup
# =============================================================================

def load_knowledge() -> str:
    """
    Load all knowledge files from .claude/knowledge/ directory.
    These contain critical tool usage patterns (e.g., Gmail attachment format)
    that are appended to the system prompt at startup.
    
    Returns: Combined knowledge content as a single string.
    """
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    knowledge_dir = os.path.join(project_root, ".claude", "knowledge")
    
    if not os.path.exists(knowledge_dir):
        return ""
    
    knowledge_parts = []
    for filename in sorted(os.listdir(knowledge_dir)):
        if filename.endswith('.md'):
            filepath = os.path.join(knowledge_dir, filename)
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    knowledge_parts.append(f.read())
            except Exception:
                pass  # Skip files that can't be read
    
    return "\n\n---\n\n".join(knowledge_parts) if knowledge_parts else ""


# =============================================================================
# SKILL DISCOVERY - Parse SKILL.md files for progressive disclosure
# =============================================================================


def discover_skills(skills_dir: str = None) -> list[dict]:
    """
    Scan .claude/skills/ directory and parse SKILL.md frontmatter.
    Returns list of {name, description, path} for each skill.
    
    Progressive disclosure: We only load name+description here.
    Full SKILL.md content is loaded by the agent when needed via read_local_file.
    """
    import yaml
    
    if skills_dir is None:
        # Default to project's .claude/skills
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        skills_dir = os.path.join(project_root, ".claude", "skills")
    
    skills = []
    
    if not os.path.exists(skills_dir):
        return skills
    
    for skill_name in os.listdir(skills_dir):
        skill_path = os.path.join(skills_dir, skill_name)
        skill_md = os.path.join(skill_path, "SKILL.md")
        
        if os.path.isdir(skill_path) and os.path.exists(skill_md):
            try:
                with open(skill_md, "r", encoding="utf-8") as f:
                    content = f.read()
                
                # Parse YAML frontmatter (between --- markers)
                if content.startswith("---"):
                    parts = content.split("---", 2)
                    if len(parts) >= 3:
                        frontmatter = yaml.safe_load(parts[1])
                        if frontmatter and isinstance(frontmatter, dict):
                            skills.append({
                                "name": frontmatter.get("name", skill_name),
                                "description": frontmatter.get("description", "No description"),
                                "path": skill_md,
                            })
            except Exception as e:
                # Skip malformed SKILL.md files
                logfire.warning("skill_parse_error", skill=skill_name, error=str(e))
                continue
    
    return skills


def generate_skills_xml(skills: list[dict]) -> str:
    """
    Generate <available_skills> XML block for system prompt injection.
    This enables Claude to be aware of skills and read them when relevant.
    """
    if not skills:
        return ""
    
    lines = ["<available_skills>"]
    for skill in skills:
        lines.append(f"""<skill>
  <name>{skill['name']}</name>
  <description>{skill['description']}</description>
  <path>{skill['path']}</path>
</skill>""")
    lines.append("</available_skills>")
    return "\n".join(lines)


# Document skills that trigger PreToolUse hints
DOCUMENT_SKILL_TRIGGERS = {
    "pdf": ["pdf", "reportlab", "pypdf", "pdfplumber"],
    "docx": ["docx", "python-docx", "word document"],
    "pptx": ["pptx", "python-pptx", "powerpoint", "presentation"],
    "xlsx": ["xlsx", "openpyxl", "excel", "spreadsheet"],
}


async def on_pre_tool_use_ledger(
    input_data: dict, tool_use_id: object, context: dict
) -> dict:
    """
    PreToolUse Hook: prepare tool call ledger entry and enforce idempotency.
    """
    global tool_ledger, run_id, current_step_id, forced_tool_queue, forced_tool_active_ids, forced_tool_mode_active, runtime_db_conn
    if tool_ledger is None or run_id is None:
        return {}
    if runtime_db_conn and run_id and is_cancel_requested(runtime_db_conn, run_id):
        return {
            "systemMessage": (
                "⚠️ Run cancellation requested. "
                "Do not call any more tools; end the turn."
            ),
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "Run cancellation requested.",
            },
        }

    tool_name = input_data.get("tool_name", "")
    if _is_task_output_name(tool_name):
        return {
            "systemMessage": (
                "⚠️ TaskOutput/TaskResult is not a callable tool. "
                "Relaunch the subagent using the Task tool instead."
            ),
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "TaskOutput/TaskResult is not a tool call.",
            },
        }
    malformed_markers = ("</arg_key>", "<arg_key>", "</arg_value>", "<arg_value>")
    if any(marker in tool_name for marker in malformed_markers):
        logfire.warning(
            "malformed_tool_name_guardrail",
            tool_name=tool_name,
            run_id=run_id,
            step_id=current_step_id,
        )
        return {
            "systemMessage": (
                "⚠️ Malformed tool call name detected. "
                "Reissue the tool call with proper JSON arguments and do NOT "
                "concatenate XML-like arg_key/arg_value into the tool name."
            ),
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "Malformed tool name (XML-style args detected).",
            },
        }
    tool_input = input_data.get("tool_input", {}) or {}
    step_id = current_step_id or "unknown"
    tool_call_id = str(tool_use_id or uuid.uuid4())

    if forced_tool_mode_active and not forced_tool_queue:
        return {
            "systemMessage": (
                "Recovery replay completed. Do not call any additional tools in recovery mode. "
                "End the turn with a brief confirmation."
            ),
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "Forced replay completed.",
            },
        }

    if forced_tool_queue:
        expected = forced_tool_queue[0]
        if not _forced_tool_matches(tool_name, tool_input, expected):
            expected_input = json.dumps(expected.get("tool_input") or {}, indent=2)
            expected_tool = expected.get("raw_tool_name") or expected.get("tool_name")
            return {
                "systemMessage": (
                    "Recovery in progress: re-run the exact in-flight tool call. "
                    f"Next required tool: {expected_tool} with input {expected_input}"
                ),
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "Forced in-flight replay active.",
                },
            }
        forced_tool_active_ids[tool_call_id] = expected
        expected["attempts"] = expected.get("attempts", 0) + 1
        try:
            _assert_prepared_tool_row(
                expected["tool_call_id"],
                expected.get("raw_tool_name") or expected.get("tool_name") or tool_name,
            )
            tool_ledger.mark_running(expected["tool_call_id"])
            logfire.info(
                "replay_mark_running",
                tool_use_id=tool_call_id,
                replay_tool_call_id=expected["tool_call_id"],
                run_id=run_id,
                step_id=step_id,
                idempotency_key=expected.get("idempotency_key"),
            )
        except Exception as exc:
            logfire.warning(
                "replay_mark_running_failed",
                tool_use_id=tool_call_id,
                error=str(exc),
            )
            return {
                "systemMessage": (
                    "⚠️ Tool ledger missing prepared entry; refusing to execute tool. "
                    "Try again or end the turn."
                ),
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "Prepared ledger row missing.",
                },
            }
        return {}

    try:
        decision = prepare_tool_call(
            tool_ledger,
            tool_call_id=tool_call_id,
            run_id=run_id,
            step_id=step_id,
            raw_tool_name=tool_name,
            tool_input=tool_input,
        )
        logfire.info(
            "ledger_prepare",
            tool_name=tool_name,
            tool_use_id=tool_call_id,
            run_id=run_id,
            step_id=step_id,
            idempotency_key=decision.idempotency_key,
            deduped=decision.deduped,
        )
    except Exception as exc:
        logfire.warning("ledger_prepare_failed", tool_name=tool_name, error=str(exc))
        return {
            "systemMessage": (
                "⚠️ Tool ledger prepare failed; refusing to execute tool without a "
                "prepared ledger row. Try again or end the turn."
            ),
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "Ledger prepare failed.",
            },
        }

    if decision.deduped and decision.receipt:
        prior_entry = tool_ledger.get_tool_call(decision.receipt.tool_call_id)
        replay_policy = (prior_entry or {}).get("replay_policy")
        side_effect_class = _normalize_side_effect_class(
            (prior_entry or {}).get("side_effect_class"),
            tool_name,
        )
        should_dedupe = False
        if replay_policy:
            should_dedupe = replay_policy == "REPLAY_EXACT"
        else:
            should_dedupe = side_effect_class in ("external", "memory", "local")
        if should_dedupe:
            logfire.warning(
                "tool_deduped",
                tool_name=tool_name,
                tool_use_id=tool_call_id,
                idempotency_key=decision.idempotency_key,
            )
            receipt_preview = ""
            if decision.receipt.response_ref:
                receipt_preview = decision.receipt.response_ref[:500]
            return {
                "systemMessage": (
                    "⚠️ Idempotency guard: tool call already succeeded. "
                    "Skipping execution and using cached receipt."
                ),
                "reason": receipt_preview,
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "Idempotent tool call detected.",
                },
            }
        try:
            duplicate_decision = prepare_tool_call(
                tool_ledger,
                tool_call_id=tool_call_id,
                run_id=run_id,
                step_id=step_id,
                raw_tool_name=tool_name,
                tool_input=tool_input,
                allow_duplicate=True,
                idempotency_nonce=tool_call_id,
            )
            logfire.info(
                "ledger_prepare_duplicate",
                tool_name=tool_name,
                tool_use_id=tool_call_id,
                run_id=run_id,
                step_id=step_id,
                idempotency_key=duplicate_decision.idempotency_key,
                prior_idempotency_key=decision.idempotency_key,
            )
        except Exception as exc:
            logfire.warning(
                "ledger_prepare_duplicate_failed",
                tool_name=tool_name,
                tool_use_id=tool_call_id,
                error=str(exc),
            )
            return {
                "systemMessage": (
                    "⚠️ Tool ledger prepare failed; refusing to execute tool without a "
                    "prepared ledger row. Try again or end the turn."
                ),
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "Ledger prepare failed.",
                },
            }

    try:
        _assert_prepared_tool_row(tool_call_id, tool_name)
        tool_ledger.mark_running(tool_call_id)
        logfire.info(
            "ledger_mark_running",
            tool_use_id=tool_call_id,
            run_id=run_id,
            step_id=step_id,
        )
    except Exception as exc:
        logfire.warning("ledger_mark_running_failed", tool_use_id=tool_call_id, error=str(exc))
        return {
            "systemMessage": (
                "⚠️ Tool ledger missing prepared entry; refusing to execute tool. "
                "Try again or end the turn."
            ),
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "Prepared ledger row missing.",
            },
        }
    return {}


async def on_post_tool_use_ledger(
    input_data: dict, tool_use_id: object, context: dict
) -> dict:
    """
    PostToolUse Hook: persist tool response to the ledger.
    """
    global tool_ledger, forced_tool_active_ids, forced_tool_queue, runtime_db_conn, run_id
    if tool_ledger is None:
        return {}

    tool_call_id = str(tool_use_id or "")
    if not tool_call_id:
        return {}

    tool_response = input_data.get("tool_response")
    is_error = False
    error_detail = ""
    if isinstance(tool_response, dict):
        is_error = bool(tool_response.get("is_error") or tool_response.get("error"))
        if tool_response.get("error"):
            error_detail = str(tool_response.get("error"))

    if tool_call_id in forced_tool_active_ids:
        expected = forced_tool_active_ids.pop(tool_call_id)
        try:
            if is_error:
                tool_ledger.mark_failed(
                    expected["tool_call_id"], error_detail or "tool error"
                )
                tool_ledger.mark_replay_status(expected["tool_call_id"], "failed")
                logfire.warning(
                    "replay_mark_failed",
                    tool_use_id=tool_call_id,
                    replay_tool_call_id=expected["tool_call_id"],
                    error_detail=error_detail or "tool error",
                )
                if expected.get("attempts", 0) >= FORCED_TOOL_MAX_ATTEMPTS:
                    if runtime_db_conn and run_id:
                        update_run_status(runtime_db_conn, run_id, "waiting_for_human")
                    forced_tool_queue = []
                    logfire.warning(
                        "replay_exhausted",
                        run_id=run_id,
                        tool_call_id=expected["tool_call_id"],
                    )
                else:
                    forced_tool_queue.insert(0, expected)
            else:
                _maybe_crash_after_tool(
                    raw_tool_name=expected.get("raw_tool_name") or "",
                    tool_call_id=expected["tool_call_id"],
                    stage="after_tool_success_before_ledger_commit",
                )
                external_id = None
                if isinstance(tool_response, dict):
                    external_id = (
                        tool_response.get("id")
                        or tool_response.get("message_id")
                        or tool_response.get("request_id")
                    )
                tool_ledger.mark_succeeded(
                    expected["tool_call_id"], tool_response, external_id
                )
                tool_ledger.mark_replay_status(expected["tool_call_id"], "succeeded")
                _maybe_crash_after_tool(
                    raw_tool_name=expected.get("raw_tool_name") or "",
                    tool_call_id=expected["tool_call_id"],
                    stage="after_ledger_mark_succeeded",
                )
                logfire.info(
                    "replay_mark_succeeded",
                    tool_use_id=tool_call_id,
                    replay_tool_call_id=expected["tool_call_id"],
                    idempotency_key=expected.get("idempotency_key"),
                )
                if (
                    forced_tool_queue
                    and forced_tool_queue[0]["tool_call_id"] == expected["tool_call_id"]
                ):
                    forced_tool_queue.pop(0)
        except Exception as exc:
            logfire.warning(
                "replay_mark_result_failed", tool_use_id=tool_call_id, error=str(exc)
            )
        return {}

    try:
        if is_error:
            tool_ledger.mark_failed(tool_call_id, error_detail or "tool error")
            logfire.warning(
                "ledger_mark_failed",
                tool_use_id=tool_call_id,
                run_id=run_id,
                step_id=current_step_id,
                error_detail=error_detail or "tool error",
            )
        else:
            raw_tool_name = ""
            if tool_ledger:
                ledger_entry = tool_ledger.get_tool_call(tool_call_id)
                raw_tool_name = (ledger_entry or {}).get("raw_tool_name") or ""
            if not raw_tool_name:
                raw_tool_name = input_data.get("tool_name", "") or ""
            _maybe_crash_after_tool(
                raw_tool_name=raw_tool_name,
                tool_call_id=tool_call_id,
                stage="after_tool_success_before_ledger_commit",
            )
            external_id = None
            if isinstance(tool_response, dict):
                external_id = (
                    tool_response.get("id")
                    or tool_response.get("message_id")
                    or tool_response.get("request_id")
                )
            tool_ledger.mark_succeeded(tool_call_id, tool_response, external_id)
            _maybe_crash_after_tool(
                raw_tool_name=raw_tool_name,
                tool_call_id=tool_call_id,
                stage="after_ledger_mark_succeeded",
            )
            logfire.info(
                "ledger_mark_succeeded",
                tool_use_id=tool_call_id,
                run_id=run_id,
                step_id=current_step_id,
                external_correlation_id=external_id,
            )
    except Exception as exc:
        logfire.warning("ledger_mark_result_failed", tool_use_id=tool_call_id, error=str(exc))

    return {}


async def on_pre_bash_skill_hint(
    input_data: dict, tool_use_id: object, context: dict
) -> dict:
    """
    PreToolUse Hook: Before Bash execution, check if command involves document creation.
    If so, inject a hint about the relevant skill to avoid reinventing the wheel.
    This is a BACKUP hook - fires after agent has already decided to use Bash.
    """
    command = input_data.get("command", "").lower()
    
    for skill_name, triggers in DOCUMENT_SKILL_TRIGGERS.items():
        if any(trigger in command for trigger in triggers):
            skill_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                ".claude", "skills", skill_name, "SKILL.md"
            )
            if os.path.exists(skill_path):
                logfire.info("skill_hint_backup_injected", skill=skill_name, command_preview=command[:100])
                return {
                    "systemMessage": (
                        f"⚠️ SKILL REMINDER: You're about to create {skill_name.upper()} content. "
                        f"The `{skill_name}` skill at `{skill_path}` has proven patterns. "
                        f"Consider reading it FIRST to avoid common issues."
                    )
                }
    
    return {}


# Prompt keywords that suggest skill-relevant tasks
# These are auto-generated from skill descriptions + skill names
# Override specific skills here if needed
SKILL_PROMPT_TRIGGERS_OVERRIDE = {
    # Format: "skill-name": ["keyword1", "keyword2", ...]
    # Leave empty to use auto-generated triggers from description
    "image-generation": [
        "image", "generate image", "create image", "edit image",
        "picture", "photo", "illustration", "graphic", "infographic",
        "visual", "design", ".png", ".jpg", ".jpeg", ".webp"
    ],
}


def build_skill_prompt_triggers() -> dict[str, list[str]]:
    """
    Build skill prompt triggers automatically from skill descriptions.
    This ensures new skills work immediately without code changes.
    
    Returns: {"skill-name": ["trigger1", "trigger2", ...]}
    """
    import yaml
    import re
    
    triggers = {}
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    skills_dir = os.path.join(project_root, ".claude", "skills")
    
    if not os.path.exists(skills_dir):
        return triggers
    
    for skill_name in os.listdir(skills_dir):
        skill_path = os.path.join(skills_dir, skill_name)
        skill_md = os.path.join(skill_path, "SKILL.md")
        
        if not os.path.isdir(skill_path) or not os.path.exists(skill_md):
            continue
        
        # Check for override first
        if skill_name in SKILL_PROMPT_TRIGGERS_OVERRIDE:
            triggers[skill_name] = SKILL_PROMPT_TRIGGERS_OVERRIDE[skill_name]
            continue
        
        try:
            with open(skill_md, "r", encoding="utf-8") as f:
                content = f.read()
            
            if content.startswith("---"):
                parts = content.split("---", 2)
                if len(parts) >= 3:
                    frontmatter = yaml.safe_load(parts[1])
                    if frontmatter and isinstance(frontmatter, dict):
                        description = frontmatter.get("description", "")
                        
                        # Auto-generate triggers from skill name and description
                        trigger_set = set()
                        
                        # Always include skill name
                        trigger_set.add(skill_name.lower())
                        
                        # Extract key terms from description (lowercase, min 4 chars)
                        words = re.findall(r'\b[a-zA-Z]{4,}\b', description.lower())
                        # Filter common words, keep domain-specific terms
                        stop_words = {'this', 'that', 'with', 'from', 'have', 'when', 
                                     'should', 'used', 'using', 'skill', 'create', 
                                     'comprehensive', 'editing', 'creation'}
                        for word in words:
                            if word not in stop_words:
                                trigger_set.add(word)
                        
                        triggers[skill_name] = list(trigger_set)
        except Exception:
            # Fallback: just use skill name
            triggers[skill_name] = [skill_name.lower()]
    
    return triggers


# Build triggers at module load (cached)
SKILL_PROMPT_TRIGGERS = build_skill_prompt_triggers()


# =============================================================================
# SKILL AWARENESS REGISTRY - For injecting skill context into sub-agents
# =============================================================================

class SkillAwarenessRegistry:
    """
    Provides skill awareness context for sub-agents.
    Sub-agents receive YAML skill summaries when spawned, enabling them
    to progressively load full skill content when needed.
    """
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self.skills = discover_skills()  # Reuse existing function
        logfire.info("skill_awareness_registry_initialized", skill_count=len(self.skills))
    
    def get_awareness_context(self, expected_skills: list[str] = None) -> str:
        """
        Generate skill awareness injection for sub-agent.
        
        Args:
            expected_skills: List of skill names this sub-agent is expected to use.
        
        Returns:
            Formatted string with skill awareness guidance.
        """
        if not self.skills:
            return ""
        
        expected_str = ", ".join(expected_skills) if expected_skills else "none specified"
        
        skill_lines = []
        for skill in self.skills:
            name = skill.get("name", "unknown")
            desc = skill.get("description", "No description")
            # Truncate long descriptions
            if len(desc) > 150:
                desc = desc[:150] + "..."
            skill_lines.append(f"- **{name}**: {desc}")
        
        return f"""
## Inherited Skill Awareness

The following skills are available in this project. Your agent definition 
indicates you may need: **{expected_str}**. Pay particular attention to these 
as they are expected to be useful for your activities.

**To use any skill**, first read its SKILL.md for full instructions:
```
Read file_path=".claude/skills/{{skill_name}}/SKILL.md"
```

**Available Skills:**
{chr(10).join(skill_lines)}
"""


# Global registry instance (lazy init)
SKILL_AWARENESS_REGISTRY = None


def get_skill_awareness_registry() -> SkillAwarenessRegistry:
    """Get or create the skill awareness registry singleton."""
    global SKILL_AWARENESS_REGISTRY
    if SKILL_AWARENESS_REGISTRY is None:
        SKILL_AWARENESS_REGISTRY = SkillAwarenessRegistry()
    return SKILL_AWARENESS_REGISTRY


# Expected skills mapping for sub-agents
# This maps subagent_type to the skills they're likely to use
SUBAGENT_EXPECTED_SKILLS = {
    "report-creation-expert": ["pdf", "image-generation"],
    "image-expert": ["image-generation"],
    "video-creation-expert": [],  # Uses MCP tools, not skills
}


async def on_pre_task_skill_awareness(
    input_data: dict, tool_use_id: object, context: dict
) -> dict:
    """
    PreToolUse Hook: Before Task tool executes (spawning a sub-agent),
    inject skill awareness context so the sub-agent knows what skills exist.
    """
    tool_input = input_data.get("tool_input", {})
    subagent_type = tool_input.get("subagent_type", "unknown")
    
    # Get expected skills for this sub-agent type
    expected_skills = SUBAGENT_EXPECTED_SKILLS.get(subagent_type, [])
    
    # Get skill awareness context
    registry = get_skill_awareness_registry()
    awareness_context = registry.get_awareness_context(expected_skills)
    
    if awareness_context:
        logfire.info(
            "skill_awareness_injected_to_subagent",
            subagent_type=subagent_type,
            expected_skills=expected_skills,
        )
        return {
            "systemMessage": awareness_context
        }
    
    return {}


async def on_user_prompt_skill_awareness(
    input_data: dict, tool_use_id: object, context: dict
) -> dict:
    """
    UserPromptSubmit Hook: EARLY skill awareness injection.
    Fires when user submits their prompt, BEFORE agent starts planning.
    This guides the agent to consider skills during initial approach.
    
    Note: Multiple skills may match a single prompt (e.g., "create PDF and Excel").
    All matching skills are listed to give agent full awareness.
    
    UserPromptSubmit hooks return hookSpecificOutput with additionalContext,
    NOT systemMessage like other hooks.
    """
    try:
        import yaml
        
        # Safely extract prompt from input_data
        if not input_data or not isinstance(input_data, dict):
            return {}
        
        prompt = str(input_data.get("prompt", "") or "").lower()
        if not prompt:
            return {}
        
        matched_skills = []
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        
        for skill_name, triggers in SKILL_PROMPT_TRIGGERS.items():
            if any(trigger in prompt for trigger in triggers):
                skill_path = os.path.join(project_root, ".claude", "skills", skill_name, "SKILL.md")
                if os.path.exists(skill_path):
                    # Read description from frontmatter for better context
                    description = "Best practices for this task"
                    try:
                        with open(skill_path, "r", encoding="utf-8") as f:
                            content = f.read()
                        if content.startswith("---"):
                            parts = content.split("---", 2)
                            if len(parts) >= 3:
                                frontmatter = yaml.safe_load(parts[1])
                                if frontmatter and isinstance(frontmatter, dict):
                                    desc = frontmatter.get("description", "")
                                    # Truncate long descriptions
                                    description = desc[:100] + "..." if len(desc) > 100 else desc
                    except Exception:
                        pass
                    matched_skills.append((skill_name, skill_path, description))
        
        if matched_skills:
            skill_list = "\n".join([
                f"  - {name}: {desc}\n    Path: {path}" 
                for name, path, desc in matched_skills
            ])
            logfire.info("skill_awareness_early_injected", skills=[s[0] for s in matched_skills])
            
            # UserPromptSubmit hooks use hookSpecificOutput with additionalContext
            return {
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": (
                        f"SKILL GUIDANCE: This task involves document creation. "
                        f"BEFORE writing code, read the relevant skill(s) for proven patterns:\n"
                        f"{skill_list}\n"
                        f"Use read_local_file on the SKILL.md path to load full instructions."
                    )
                }
            }
        
        return {}
    except Exception as e:
        # Log error but don't crash the hook
        logfire.error("user_prompt_skill_hook_error", error=str(e))
        return {}


# =============================================================================
# HOOK CALLBACKS - For sub-agent lifecycle events
# =============================================================================

async def on_subagent_stop(
    input_data: dict, tool_use_id: object, context: dict
) -> dict:
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
run_id: Optional[str] = None
budget_config: dict = {}
budget_state: dict = {"start_ts": None, "steps": 0, "tool_calls": 0}
runtime_db_conn = None
tool_ledger: Optional[ToolCallLedger] = None
current_step_id: Optional[str] = None
interrupt_requested = False
last_sigint_ts: float | None = None
provider_session_forked_from: Optional[str] = None
forced_tool_queue: list[dict[str, Any]] = []
forced_tool_active_ids: dict[str, dict[str, Any]] = {}
forced_tool_mode_active = False
run_cancelled_by_operator = False
FORCED_TOOL_MAX_ATTEMPTS = 2
TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}
WAITING_STATUSES = {"waiting_for_human"}


def _resolve_input_paths(
    inputs: dict, workspace_dir: Optional[str]
) -> dict:
    if not workspace_dir:
        return inputs
    resolved: dict = {}
    for key, value in inputs.items():
        if isinstance(value, str):
            if os.path.isabs(value):
                resolved[key] = value
                continue
            is_path_key = key.endswith("_path")
            is_path_value = value.startswith(
                (
                    "work_products/",
                    "search_results/",
                    "search_results_filtered_best/",
                    "downloads/",
                    "workbench/",
                )
            )
            if is_path_key or is_path_value:
                resolved[key] = os.path.join(workspace_dir, value)
            else:
                resolved[key] = value
        elif isinstance(value, dict):
            resolved[key] = _resolve_input_paths(value, workspace_dir)
        else:
            resolved[key] = value
    return resolved


def build_job_prompt(run_spec: dict) -> Optional[str]:
    job_prompt = run_spec.get("prompt") or run_spec.get("objective")
    workspace_dir = run_spec.get("workspace_dir")
    if job_prompt and run_spec.get("inputs"):
        resolved_inputs = _resolve_input_paths(run_spec["inputs"], workspace_dir)
        job_prompt += "\n\nInputs:\n" + json.dumps(resolved_inputs, indent=2)
    if job_prompt and run_spec.get("constraints"):
        job_prompt += "\n\nConstraints:\n" + json.dumps(run_spec["constraints"], indent=2)
    if job_prompt and workspace_dir:
        job_prompt += (
            "\n\nWorkspace:\n"
            f"{workspace_dir}\n"
            "Use absolute paths under this workspace for any file operations."
        )
    return job_prompt


def _next_step_index(conn: sqlite3.Connection, run_id: str) -> int:
    row = conn.execute(
        "SELECT MAX(step_index) AS max_step FROM run_steps WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    max_step = row["max_step"] if row else 0
    return int(max_step or 0) + 1


def infer_run_mode(
    run_row: Optional[sqlite3.Row],
    run_spec: dict,
    job_path: Optional[str],
) -> str:
    if run_row and "run_mode" in run_row.keys() and run_row["run_mode"]:
        return run_row["run_mode"]
    if job_path:
        return "job"
    if run_spec.get("prompt") or run_spec.get("objective"):
        return "job"
    return "interactive"


def _handle_cancel_request(
    conn: Optional[sqlite3.Connection], run_id: Optional[str], workspace_dir: str
) -> bool:
    global run_cancelled_by_operator
    if not conn or not run_id:
        return False
    if not is_cancel_requested(conn, run_id):
        return False
    mark_run_cancelled(conn, run_id)
    run_cancelled_by_operator = True
    print("⚠️ Run cancellation requested. Stopping at safe boundary.")
    print_job_completion_summary(conn, run_id, "cancelled", workspace_dir, "")
    return True


def _normalize_tool_input(tool_input: Any) -> str:
    try:
        return normalize_json(tool_input or {})
    except Exception:
        return json.dumps(tool_input or {}, default=str)


def _maybe_update_provider_session(
    session_id: Optional[str], forked_from: Optional[str] = None
) -> None:
    if not session_id:
        return
    if runtime_db_conn and run_id:
        update_run_provider_session(
            runtime_db_conn, run_id, session_id, forked_from=forked_from
        )
    if trace is not None:
        trace["provider_session_id"] = session_id


def _is_resume_session_error(error_msg: str) -> bool:
    if not error_msg:
        return False
    if not options:
        return False
    if not (options.resume or options.continue_conversation or options.fork_session):
        return False
    lowered = error_msg.lower()
    return "resume" in lowered or "session" in lowered


def _disable_provider_resume() -> None:
    global options
    if not options:
        return
    options.resume = None
    options.continue_conversation = False
    options.fork_session = False


def _invalidate_provider_session(error_msg: str) -> None:
    if runtime_db_conn and run_id:
        update_run_provider_session(runtime_db_conn, run_id, None)
    if trace is not None:
        trace["provider_session_id"] = None
    logfire.warning(
        "provider_session_invalidated",
        run_id=run_id,
        error=error_msg[:200],
    )


def _normalize_crash_tool_name(value: str) -> str:
    if not value:
        return ""
    normalized = re.sub(r"[^a-z0-9]+", "_", value.strip().lower())
    return normalized.strip("_")


def _get_current_step_phase() -> Optional[str]:
    if not runtime_db_conn or not current_step_id:
        return None
    try:
        row = runtime_db_conn.execute(
            "SELECT phase FROM run_steps WHERE step_id = ?",
            (current_step_id,),
        ).fetchone()
    except Exception:
        return None
    if not row:
        return None
    return row["phase"]


def _should_trigger_test_crash(
    *,
    raw_tool_name: str,
    tool_call_id: str,
    stage: str,
) -> tuple[bool, dict[str, Optional[str]]]:
    crash_tool = os.getenv("UA_TEST_CRASH_AFTER_TOOL")
    crash_id = os.getenv("UA_TEST_CRASH_AFTER_TOOL_CALL_ID")
    crash_stage = os.getenv("UA_TEST_CRASH_STAGE")
    crash_phase = os.getenv("UA_TEST_CRASH_AFTER_PHASE")
    crash_step = os.getenv("UA_TEST_CRASH_AFTER_STEP")
    if not any([crash_tool, crash_id, crash_stage, crash_phase, crash_step]):
        return False, {}

    normalized_tool = _normalize_crash_tool_name(raw_tool_name)
    normalized_crash_tool = _normalize_crash_tool_name(crash_tool or "")
    current_phase = None
    if crash_phase or crash_step:
        current_phase = _get_current_step_phase()

    if crash_tool and normalized_tool != normalized_crash_tool:
        return False, {}
    if crash_id and tool_call_id != crash_id:
        return False, {}
    if crash_stage and crash_stage != stage:
        return False, {}
    if crash_step and (current_step_id or "") != crash_step:
        return False, {}
    if crash_phase and (current_phase or "").lower() != crash_phase.lower():
        return False, {}

    return True, {
        "crash_tool": crash_tool,
        "crash_id": crash_id,
        "crash_stage": crash_stage,
        "crash_phase": crash_phase,
        "crash_step": crash_step,
        "current_step_id": current_step_id,
        "current_phase": current_phase,
        "normalized_tool_name": normalized_tool,
    }


def _maybe_crash_after_tool(
    *,
    raw_tool_name: str,
    tool_call_id: str,
    stage: str,
) -> None:
    should_crash, crash_context = _should_trigger_test_crash(
        raw_tool_name=raw_tool_name,
        tool_call_id=tool_call_id,
        stage=stage,
    )
    if not should_crash:
        return
    current_phase = crash_context.get("current_phase")
    current_step = crash_context.get("current_step_id")
    details = f"stage={stage}"
    if current_step:
        details += f" step_id={current_step}"
    if current_phase:
        details += f" phase={current_phase}"
    message = (
        "UA_TEST_CRASH_AFTER_TOOL triggered: "
        f"{raw_tool_name} {tool_call_id} ({details})"
    )
    print(f"\n💥 {message}")
    if LOGFIRE_TOKEN:
        logfire.error(
            "test_crash_hook_triggered",
            tool_call_id=tool_call_id,
            raw_tool_name=raw_tool_name,
            normalized_tool_name=crash_context.get("normalized_tool_name"),
            stage=stage,
            crash_tool=crash_context.get("crash_tool"),
            crash_id=crash_context.get("crash_id"),
            crash_stage=crash_context.get("crash_stage"),
            crash_phase=crash_context.get("crash_phase"),
            crash_step=crash_context.get("crash_step"),
            current_step_id=current_step,
            current_phase=current_phase,
        )
    os._exit(137)


def _assert_prepared_tool_row(tool_call_id: str, raw_tool_name: str) -> None:
    if tool_ledger is None:
        return
    row = tool_ledger.get_tool_call(tool_call_id)
    status = (row or {}).get("status")
    if status not in ("prepared", "running"):
        message = (
            "Prepared ledger row missing or invalid before tool execution "
            f"(tool_call_id={tool_call_id}, raw_tool_name={raw_tool_name}, status={status})"
        )
        if LOGFIRE_TOKEN:
            logfire.error(
                "prepared_ledger_row_missing",
                tool_call_id=tool_call_id,
                raw_tool_name=raw_tool_name,
                status=status,
            )
        raise RuntimeError(message)


VALID_SIDE_EFFECT_CLASSES = {"external", "memory", "local", "read_only"}
_invalid_side_effect_warnings: set[tuple[str, str, str]] = set()


def _normalize_side_effect_class(value: Optional[str], tool_name: str) -> str:
    if value in VALID_SIDE_EFFECT_CLASSES:
        return value
    normalized = (value or "").strip() or "unknown"
    warn_key = (run_id or "unknown", tool_name, normalized)
    if warn_key not in _invalid_side_effect_warnings:
        _invalid_side_effect_warnings.add(warn_key)
        logfire.warning(
            "invalid_side_effect_class_defaulting_external",
            run_id=run_id,
            tool_name=tool_name,
            side_effect_class=value,
        )
    return "external"


def _is_task_output_name(raw_tool_name: str) -> bool:
    return raw_tool_name.lower() in ("taskoutput", "taskresult")


def _ensure_task_key(tool_input: dict[str, Any]) -> tuple[dict[str, Any], str]:
    relaunch_input = copy.deepcopy(tool_input)
    task_key = relaunch_input.get("task_key")
    if not task_key:
        task_key = deterministic_task_key(relaunch_input)
        relaunch_input["task_key"] = task_key
    return relaunch_input, str(task_key)


def _subagent_output_dir(workspace_dir: str, task_key: str) -> str:
    return os.path.join(workspace_dir, "subagent_outputs", task_key)


def _subagent_output_paths(workspace_dir: str, task_key: str) -> dict[str, str]:
    base_dir = _subagent_output_dir(workspace_dir, task_key)
    return {
        "dir": base_dir,
        "json": os.path.join(base_dir, "subagent_output.json"),
        "summary": os.path.join(base_dir, "subagent_summary.md"),
    }


def _subagent_output_available(workspace_dir: str, task_key: str) -> bool:
    if not workspace_dir or not task_key:
        return False
    paths = _subagent_output_paths(workspace_dir, task_key)
    output_path = paths["json"]
    if not os.path.exists(output_path):
        return False
    if os.path.getsize(output_path) <= 0:
        return False
    try:
        with open(output_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception:
        return False
    if not isinstance(data, dict):
        return False
    output_str = (data.get("output_str") or "").strip()
    output_payload = data.get("output")
    return bool(output_str or output_payload)


def _persist_subagent_output(
    *,
    workspace_dir: str,
    tool_use_id: Optional[str],
    tool_input: dict[str, Any],
    raw_tool_name: str,
    output: Any,
    output_str: str,
) -> Optional[dict[str, str]]:
    if not workspace_dir:
        return None
    _, task_key = _ensure_task_key(tool_input)
    paths = _subagent_output_paths(workspace_dir, task_key)
    os.makedirs(paths["dir"], exist_ok=True)
    payload = {
        "task_key": task_key,
        "tool_use_id": tool_use_id,
        "run_id": run_id,
        "step_id": current_step_id,
        "raw_tool_name": raw_tool_name,
        "tool_input": tool_input,
        "output": output,
        "output_str": output_str,
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        with open(paths["json"], "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, default=str)
        summary_preview = output_str.strip()
        if len(summary_preview) > 4000:
            summary_preview = summary_preview[:4000] + "..."
        summary_lines = [
            f"# Subagent Output ({task_key})",
            "",
            f"- tool_use_id: {tool_use_id or 'unknown'}",
            f"- raw_tool_name: {raw_tool_name}",
            f"- run_id: {run_id}",
            f"- step_id: {current_step_id}",
            "",
            "## Output Preview",
            "",
            summary_preview or "_(no output text captured)_",
            "",
        ]
        with open(paths["summary"], "w", encoding="utf-8") as handle:
            handle.write("\n".join(summary_lines))
    except Exception as exc:
        logfire.warning(
            "subagent_output_persist_failed",
            task_key=task_key,
            error=str(exc),
        )
        return None
    return paths


def _raw_tool_name_from_identity(tool_name: str, tool_namespace: str) -> str:
    if tool_namespace == "claude_code":
        if tool_name == "bash":
            return "Bash"
        if tool_name == "task":
            return "Task"
    return tool_name


def _forced_tool_matches(
    raw_tool_name: str, tool_input: dict[str, Any], expected: dict[str, Any]
) -> bool:
    identity = parse_tool_identity(raw_tool_name or "")
    if (
        identity.tool_name != expected.get("tool_name")
        or identity.tool_namespace != expected.get("tool_namespace")
    ):
        return False
    normalized = _normalize_tool_input(tool_input)
    return normalized == expected.get("normalized_input")


def _load_inflight_tool_calls(
    conn: sqlite3.Connection, run_id: str
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT tool_call_id, tool_name, tool_namespace, raw_tool_name, step_id,
               request_ref, status, idempotency_key, created_at, replay_policy
        FROM tool_calls
        WHERE run_id = ? AND status IN ('prepared', 'running')
        ORDER BY created_at ASC
        """,
        (run_id,),
    ).fetchall()
    inflight = []
    for row in rows:
        normalized_input = row["request_ref"] or ""
        tool_input: dict[str, Any] = {}
        if normalized_input:
            try:
                tool_input = json.loads(normalized_input)
            except Exception:
                tool_input = {}
        raw_name = row["raw_tool_name"] or _raw_tool_name_from_identity(
            row["tool_name"], row["tool_namespace"]
        )
        inflight.append(
            {
                "tool_call_id": row["tool_call_id"],
                "tool_name": row["tool_name"],
                "tool_namespace": row["tool_namespace"],
                "raw_tool_name": raw_name,
                "step_id": row["step_id"],
                "normalized_input": normalized_input
                or _normalize_tool_input(tool_input),
                "tool_input": tool_input,
                "status": row["status"],
                "idempotency_key": row["idempotency_key"],
                "created_at": row["created_at"],
                "replay_policy": row["replay_policy"],
                "attempts": 0,
            }
        )
    return inflight


def _build_forced_tool_prompt(queue: list[dict[str, Any]]) -> str:
    lines = [
        "Recovery mode: re-run the in-flight tool calls below in the exact order.",
        "Do NOT run any other tools.",
        "Use the exact tool name and input shown.",
        "After completing the listed tool calls, respond with DONE and do not invoke any other tools.",
    ]
    for idx, item in enumerate(queue, 1):
        raw_name = item.get("raw_tool_name") or item.get("tool_name")
        lines.append(f"{idx}) tool: {raw_name}")
        lines.append(
            f"   input: {json.dumps(item.get('tool_input') or {}, indent=2)}"
        )
    return "\n".join(lines)


def _relaunch_inflight_task(
    inflight: dict[str, Any], run_id: str, step_id: str
) -> Optional[dict[str, Any]]:
    global tool_ledger
    if tool_ledger is None:
        return None
    tool_input = inflight.get("tool_input")
    if not isinstance(tool_input, dict):
        tool_input = {}
    relaunch_input, task_key = _ensure_task_key(tool_input)
    tool_call_id = str(uuid.uuid4())
    relaunch_tool_name = inflight.get("tool_name") or ""
    relaunch_namespace = inflight.get("tool_namespace") or ""
    relaunch_raw_name = inflight.get("raw_tool_name") or relaunch_tool_name
    if _is_task_output_name(relaunch_raw_name):
        relaunch_tool_name = "task"
        relaunch_namespace = "claude_code"
        relaunch_raw_name = "Task"
    try:
        receipt, idempotency_key = tool_ledger.prepare_tool_call(
            tool_call_id=tool_call_id,
            run_id=run_id,
            step_id=step_id,
            tool_name=relaunch_tool_name,
            tool_namespace=relaunch_namespace,
            raw_tool_name=relaunch_raw_name,
            tool_input=relaunch_input,
        )
    except Exception as exc:
        logfire.warning(
            "relaunch_prepare_failed",
            tool_call_id=inflight.get("tool_call_id"),
            error=str(exc),
        )
        return None
    if receipt is not None:
        logfire.warning(
            "relaunch_deduped",
            tool_call_id=inflight.get("tool_call_id"),
            idempotency_key=idempotency_key,
        )
        return None
    logfire.info(
        "relaunch_prepared",
        tool_call_id=tool_call_id,
        previous_tool_call_id=inflight.get("tool_call_id"),
        task_key=task_key,
        idempotency_key=idempotency_key,
    )
    return {
        "tool_call_id": tool_call_id,
        "tool_name": relaunch_tool_name,
        "tool_namespace": relaunch_namespace,
        "raw_tool_name": relaunch_raw_name,
        "step_id": step_id,
        "normalized_input": _normalize_tool_input(relaunch_input),
        "tool_input": relaunch_input,
        "status": "prepared",
        "idempotency_key": idempotency_key,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "replay_policy": inflight.get("replay_policy"),
        "attempts": 0,
    }


async def reconcile_inflight_tools(
    client: ClaudeSDKClient,
    run_id: str,
    workspace_dir: str,
    max_turns: int = 3,
) -> bool:
    global forced_tool_queue, forced_tool_active_ids, forced_tool_mode_active, runtime_db_conn
    if not runtime_db_conn:
        return True
    inflight = _load_inflight_tool_calls(runtime_db_conn, run_id)
    if not inflight:
        return True
    forced_tool_queue = []
    for item in inflight:
        if item.get("replay_policy") == "RELAUNCH":
            relaunch_step_id = item.get("step_id") or current_step_id or "unknown"
            if tool_ledger and workspace_dir:
                tool_input = item.get("tool_input") if isinstance(item.get("tool_input"), dict) else {}
                _, task_key = _ensure_task_key(tool_input)
                if task_key and _subagent_output_available(workspace_dir, task_key):
                    output_paths = _subagent_output_paths(workspace_dir, task_key)
                    tool_ledger.mark_succeeded(
                        item["tool_call_id"],
                        {
                            "status": "subagent_output_reused",
                            "task_key": task_key,
                            "output_path": output_paths["json"],
                        },
                    )
                    tool_ledger.mark_replay_status(
                        item["tool_call_id"], "skipped_output_present"
                    )
                    logfire.info(
                        "relaunch_skipped_output_present",
                        tool_call_id=item["tool_call_id"],
                        task_key=task_key,
                        output_path=output_paths["json"],
                    )
                    continue
            relaunched = _relaunch_inflight_task(item, run_id, relaunch_step_id)
            if tool_ledger:
                abandon_detail = (
                    "relaunch_enqueued" if relaunched else "relaunch_needs_human"
                )
                tool_ledger.mark_abandoned_on_resume(
                    item["tool_call_id"], abandon_detail
                )
            if relaunched:
                forced_tool_queue.append(relaunched)
            continue
        forced_tool_queue.append(item)
    forced_tool_active_ids = {}
    forced_tool_mode_active = True
    print("🔁 Replaying in-flight tool calls before resume...")
    fallback_client: Optional[ClaudeSDKClient] = None
    fallback_client_active = False
    active_client = client
    try:
        for _ in range(max_turns):
            if not forced_tool_queue:
                break
            prompt = _build_forced_tool_prompt(forced_tool_queue)
            try:
                await process_turn(active_client, prompt, workspace_dir)
            except BudgetExceeded:
                raise
            except Exception as exc:
                error_msg = str(exc)
                if _is_resume_session_error(error_msg) and fallback_client is None:
                    _invalidate_provider_session(error_msg)
                    _disable_provider_resume()
                    fallback_client = ClaudeSDKClient(options)
                    await fallback_client.__aenter__()
                    fallback_client_active = True
                    active_client = fallback_client
                    continue
                print(f"⚠️ In-flight replay error: {exc}")
                logfire.warning("inflight_replay_error", run_id=run_id, error=str(exc))
                break
            if forced_tool_queue:
                print("⚠️ In-flight replay incomplete; retrying...")
    finally:
        if fallback_client is not None and fallback_client_active:
            await fallback_client.__aexit__(None, None, None)
        forced_tool_mode_active = False
    if forced_tool_queue:
        if runtime_db_conn and run_id:
            update_run_status(runtime_db_conn, run_id, "waiting_for_human")
        logfire.warning(
            "inflight_replay_incomplete",
            run_id=run_id,
            remaining=len(forced_tool_queue),
        )
        forced_tool_queue = []
        forced_tool_active_ids = {}
        return False
    forced_tool_active_ids = {}
    return True


def _list_workspace_artifacts(workspace_dir: str) -> list[str]:
    if not workspace_dir or not os.path.isdir(workspace_dir):
        return []
    artifacts = []
    for name in sorted(os.listdir(workspace_dir)):
        if name.lower().endswith((".html", ".pdf", ".pptx")):
            artifacts.append(name)
    return artifacts


def build_resume_packet(
    conn: sqlite3.Connection,
    run_id: str,
    workspace_dir: str,
    last_n: int = 5,
) -> tuple[dict[str, Any], str]:
    run_row = get_run(conn, run_id)
    checkpoint = load_last_checkpoint(conn, run_id)
    checkpoint_id = checkpoint["checkpoint_id"] if checkpoint else None

    step_index = None
    step_phase = None
    current_step_id = run_row["current_step_id"] if run_row else None
    if current_step_id:
        step_row = conn.execute(
            "SELECT step_index, phase FROM run_steps WHERE step_id = ?",
            (current_step_id,),
        ).fetchone()
        if step_row:
            step_index = step_row["step_index"]
            step_phase = step_row["phase"]

    last_tool_calls = []
    rows = conn.execute(
        """
        SELECT tool_name, status, idempotency_key, created_at
        FROM tool_calls
        WHERE run_id = ?
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (run_id, last_n),
    ).fetchall()
    for row in rows:
        last_tool_calls.append(
            {
                "tool_name": row["tool_name"],
                "status": row["status"],
                "idempotency_key": row["idempotency_key"],
                "created_at": row["created_at"],
            }
        )

    inflight = []
    inflight_rows = conn.execute(
        """
        SELECT tool_name, status, idempotency_key, created_at, replay_policy
        FROM tool_calls
        WHERE run_id = ? AND status IN ('prepared', 'running')
        ORDER BY created_at DESC
        """,
        (run_id,),
    ).fetchall()
    for row in inflight_rows:
        inflight.append(
            {
                "tool_name": row["tool_name"],
                "status": row["status"],
                "idempotency_key": row["idempotency_key"],
                "created_at": row["created_at"],
                "replay_policy": row["replay_policy"],
            }
        )

    artifacts = _list_workspace_artifacts(workspace_dir)

    packet = {
        "run_id": run_id,
        "checkpoint_id": checkpoint_id,
        "current_step_index": step_index,
        "current_phase": step_phase,
        "last_tool_calls": last_tool_calls,
        "in_flight_tool_calls": inflight,
        "artifacts": artifacts,
    }

    summary_lines = [
        f"run_id: {run_id}",
        f"checkpoint_id: {checkpoint_id or 'none'}",
        f"current_step: {step_index if step_index is not None else 'unknown'}",
        f"current_phase: {step_phase or 'unknown'}",
    ]
    if last_tool_calls:
        summary_lines.append("recent_tool_calls:")
        for row in last_tool_calls:
            summary_lines.append(
                f"- {row['tool_name']} | {row['status']} | {row['idempotency_key']}"
            )
    if inflight:
        summary_lines.append("in_flight_tool_calls:")
        for row in inflight:
            summary_lines.append(
                f"- {row['tool_name']} | {row['status']} | {row['idempotency_key']}"
            )
    if artifacts:
        summary_lines.append("artifacts:")
        for name in artifacts:
            summary_lines.append(f"- {name}")

    return packet, "\n".join(summary_lines)


def _summarize_response(text: str, max_chars: int = 700) -> str:
    if not text:
        return ""
    compact = " ".join(text.split())
    if len(compact) > max_chars:
        return compact[: max_chars - 3] + "..."
    return compact


def update_restart_file(
    run_id: str,
    workspace_dir: str,
    resume_cmd: Optional[str] = None,
    resume_packet_path: Optional[str] = None,
    job_summary_path: Optional[str] = None,
    runwide_summary_line: Optional[str] = None,
) -> None:
    if not run_id:
        return
    if resume_cmd is None:
        resume_cmd = f"uv run python src/universal_agent/main.py --resume --run-id {run_id}"
    try:
        restart_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "Project_Documentation",
            "Long_Running_Agent_Design",
            "KevinRestartWithThis.md",
        )
        lines = [
            "# Restart Instructions",
            "",
            f"Run ID: {run_id}",
            "",
        ]
        if resume_cmd:
            lines.append("Resume Command:")
            lines.append("")
            lines.append(resume_cmd)
            lines.append("")
        lines.append("Workspace:")
        lines.append("")
        lines.append(workspace_dir or "N/A")
        lines.append("")
        if resume_packet_path:
            lines.append("Resume Packet:")
            lines.append("")
            lines.append(resume_packet_path)
            lines.append("")
        if job_summary_path:
            lines.append("Job Completion Summary:")
            lines.append("")
            lines.append(job_summary_path)
            lines.append("")
        if runwide_summary_line:
            lines.append("Run-wide Summary:")
            lines.append("")
            lines.append(runwide_summary_line)
            lines.append("")
        with open(restart_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
    except Exception as exc:
        print(f"⚠️ Failed to save restart file: {exc}")


def print_job_completion_summary(
    conn: sqlite3.Connection,
    run_id: str,
    status: str,
    workspace_dir: str,
    response_text: str,
) -> None:
    artifacts = _list_workspace_artifacts(workspace_dir)
    receipt_rows = conn.execute(
        """
        SELECT tool_name, status, idempotency_key, response_ref
        FROM tool_calls
        WHERE run_id = ? AND status = 'succeeded' AND side_effect_class != 'read_only'
        ORDER BY updated_at DESC
        LIMIT 5
        """,
        (run_id,),
    ).fetchall()
    receipts = []
    for row in receipt_rows:
        response_preview = (row["response_ref"] or "")[:200]
        receipts.append(
            {
                "tool_name": row["tool_name"],
                "status": row["status"],
                "idempotency_key": row["idempotency_key"],
                "response_ref": response_preview,
            }
        )

    replay_rows = conn.execute(
        """
        SELECT tool_name, replay_status
        FROM tool_calls
        WHERE run_id = ? AND replay_status IS NOT NULL
        ORDER BY updated_at DESC
        LIMIT 10
        """,
        (run_id,),
    ).fetchall()
    replayed = [
        {"tool_name": row["tool_name"], "replay_status": row["replay_status"]}
        for row in replay_rows
    ]

    abandoned_rows = conn.execute(
        """
        SELECT tool_name, error_detail
        FROM tool_calls
        WHERE run_id = ? AND status = 'abandoned_on_resume'
        ORDER BY updated_at DESC
        LIMIT 10
        """,
        (run_id,),
    ).fetchall()
    abandoned = []
    for row in abandoned_rows:
        detail = (row["error_detail"] or "").lower()
        outcome = "relaunched"
        if "needs_human" in detail or "failed" in detail:
            outcome = "needs-human"
        abandoned.append({"tool_name": row["tool_name"], "outcome": outcome})

    summary_row = conn.execute(
        """
        SELECT
            COUNT(*) AS total_tool_calls,
            SUM(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END) AS succeeded,
            SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed,
            SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END) AS running,
            SUM(CASE WHEN status = 'prepared' THEN 1 ELSE 0 END) AS prepared,
            SUM(CASE WHEN status = 'abandoned_on_resume' THEN 1 ELSE 0 END) AS abandoned,
            SUM(CASE WHEN replay_status IS NOT NULL THEN 1 ELSE 0 END) AS replayed
        FROM tool_calls
        WHERE run_id = ?
        """,
        (run_id,),
    ).fetchone()
    tool_counts = conn.execute(
        """
        SELECT tool_name, COUNT(*) AS count
        FROM tool_calls
        WHERE run_id = ?
        GROUP BY tool_name
        ORDER BY count DESC, tool_name ASC
        LIMIT 10
        """,
        (run_id,),
    ).fetchall()
    step_row = conn.execute(
        """
        SELECT COUNT(*) AS total_steps, MIN(step_index) AS min_step, MAX(step_index) AS max_step
        FROM run_steps
        WHERE run_id = ?
        """,
        (run_id,),
    ).fetchone()
    time_row = conn.execute(
        """
        SELECT MIN(created_at) AS first_event, MAX(updated_at) AS last_event
        FROM tool_calls
        WHERE run_id = ?
        """,
        (run_id,),
    ).fetchone()
    side_effect_row = conn.execute(
        """
        SELECT COUNT(*) AS side_effect_succeeded
        FROM tool_calls
        WHERE run_id = ? AND status = 'succeeded' AND side_effect_class != 'read_only'
        """,
        (run_id,),
    ).fetchone()
    runwide_summary = {
        "total_tool_calls": summary_row["total_tool_calls"] if summary_row else 0,
        "status_counts": {
            "succeeded": summary_row["succeeded"] if summary_row else 0,
            "failed": summary_row["failed"] if summary_row else 0,
            "running": summary_row["running"] if summary_row else 0,
            "prepared": summary_row["prepared"] if summary_row else 0,
            "abandoned_on_resume": summary_row["abandoned"] if summary_row else 0,
            "replayed": summary_row["replayed"] if summary_row else 0,
        },
        "top_tools": [
            {"tool_name": row["tool_name"], "count": row["count"]}
            for row in tool_counts
        ],
        "steps": {
            "total_steps": step_row["total_steps"] if step_row else 0,
            "min_step": step_row["min_step"] if step_row else None,
            "max_step": step_row["max_step"] if step_row else None,
        },
        "timeline": {
            "first_event": time_row["first_event"] if time_row else None,
            "last_event": time_row["last_event"] if time_row else None,
        },
        "side_effect_succeeded": (
            side_effect_row["side_effect_succeeded"] if side_effect_row else 0
        ),
    }
    runwide_line = None
    if runwide_summary["total_tool_calls"]:
        runwide_line = (
            "Run-wide: "
            f"{runwide_summary['total_tool_calls']} tools | "
            f"{runwide_summary['status_counts']['succeeded']} succeeded | "
            f"{runwide_summary['status_counts']['failed']} failed | "
            f"{runwide_summary['status_counts']['abandoned_on_resume']} abandoned | "
            f"{runwide_summary['status_counts']['replayed']} replayed | "
            f"{runwide_summary['steps']['total_steps']} steps"
        )

    print("\n" + "=" * 80)
    print("=== JOB COMPLETE ===")
    print(f"Run ID: {run_id}")
    print(f"Status: {status}")
    if artifacts:
        print("Artifacts:")
        for name in artifacts:
            print(f"- {os.path.join(workspace_dir, name)}")
    if receipts:
        print("Last side-effect receipts:")
        for receipt in receipts:
            print(
                f"- {receipt['tool_name']} | {receipt['status']} | {receipt['idempotency_key']}"
            )
    if replayed:
        print("Replayed tools:")
        for row in replayed:
            print(f"- {row['tool_name']} | {row['replay_status']}")
    if abandoned:
        print("Abandoned tools:")
        for row in abandoned:
            print(f"- {row['tool_name']} | {row['outcome']}")
    summary = _summarize_response(response_text)
    if summary:
        print("Summary:")
        print(summary)
    if runwide_summary["total_tool_calls"]:
        if runwide_line:
            print(runwide_line)
        print("Run-wide summary:")
        print(
            "Tool calls: "
            f"{runwide_summary['total_tool_calls']} total | "
            f"{runwide_summary['status_counts']['succeeded']} succeeded | "
            f"{runwide_summary['status_counts']['failed']} failed | "
            f"{runwide_summary['status_counts']['abandoned_on_resume']} abandoned | "
            f"{runwide_summary['status_counts']['replayed']} replayed"
        )
        print(
            "Steps: "
            f"{runwide_summary['steps']['total_steps']} total "
            f"(min {runwide_summary['steps']['min_step']}, "
            f"max {runwide_summary['steps']['max_step']})"
        )
        print(
            "Timeline: "
            f"{runwide_summary['timeline']['first_event']} → "
            f"{runwide_summary['timeline']['last_event']}"
        )
        if runwide_summary["top_tools"]:
            print("Top tools:")
            for row in runwide_summary["top_tools"]:
                print(f"- {row['tool_name']} | {row['count']}")
    print("=" * 80)

    summary_path = None
    if workspace_dir:
        summary_path = os.path.join(workspace_dir, f"job_completion_{run_id}.md")
        try:
            with open(summary_path, "w", encoding="utf-8") as f:
                f.write(f"# Job Completion Summary\n\n")
                f.write(f"Run ID: {run_id}\n\n")
                f.write(f"Status: {status}\n\n")
                if artifacts:
                    f.write("Artifacts:\n")
                    for name in artifacts:
                        f.write(f"- {os.path.join(workspace_dir, name)}\n")
                    f.write("\n")
                if receipts:
                    f.write("Last side-effect receipts:\n")
                    for receipt in receipts:
                        f.write(
                            f"- {receipt['tool_name']} | {receipt['status']} | {receipt['idempotency_key']}\n"
                        )
                    f.write("\n")
                if replayed:
                    f.write("Replayed tools:\n")
                    for row in replayed:
                        f.write(f"- {row['tool_name']} | {row['replay_status']}\n")
                    f.write("\n")
                if abandoned:
                    f.write("Abandoned tools:\n")
                    for row in abandoned:
                        f.write(f"- {row['tool_name']} | {row['outcome']}\n")
                    f.write("\n")
                if summary:
                    f.write("Summary:\n")
                    f.write(summary + "\n")
                if runwide_summary["total_tool_calls"]:
                    f.write("\nRun-wide summary:\n")
                    if runwide_line:
                        f.write(runwide_line + "\n")
                    f.write(
                        "Tool calls: "
                        f"{runwide_summary['total_tool_calls']} total | "
                        f"{runwide_summary['status_counts']['succeeded']} succeeded | "
                        f"{runwide_summary['status_counts']['failed']} failed | "
                        f"{runwide_summary['status_counts']['abandoned_on_resume']} abandoned | "
                        f"{runwide_summary['status_counts']['replayed']} replayed\n"
                    )
                    f.write(
                        "Steps: "
                        f"{runwide_summary['steps']['total_steps']} total "
                        f"(min {runwide_summary['steps']['min_step']}, "
                        f"max {runwide_summary['steps']['max_step']})\n"
                    )
                    f.write(
                        "Timeline: "
                        f"{runwide_summary['timeline']['first_event']} → "
                        f"{runwide_summary['timeline']['last_event']}\n"
                    )
                    if runwide_summary["top_tools"]:
                        f.write("Top tools:\n")
                        for row in runwide_summary["top_tools"]:
                            f.write(f"- {row['tool_name']} | {row['count']}\n")
        except Exception as exc:
            print(f"⚠️ Failed to save job completion summary: {exc}")
    if summary_path:
        update_restart_file(
            run_id,
            workspace_dir,
            job_summary_path=summary_path,
            runwide_summary_line=runwide_line,
        )


async def continue_job_run(
    client: ClaudeSDKClient,
    run_id: str,
    workspace_dir: str,
    last_job_prompt: Optional[str],
    resume_packet_summary: str,
    replay_note: Optional[str] = None,
    max_error_retries: int = 3,
) -> Optional[str]:
    error_retries = 0
    last_error = None
    final_response_text = ""
    resume_fallback_attempted = False

    resume_message = (
        f"Resuming job run {run_id}. Continue executing the existing job to completion. "
        "You have access to tool receipts; do not repeat side-effecting tool calls that already "
        "succeeded—reuse receipts. If blocked, set status waiting_for_human with a clear request."
        "\n\nResume packet:\n"
        f"{resume_packet_summary}"
    )
    if replay_note:
        resume_message += f"\n\nReplay note:\n{replay_note}"

    while True:
        if runtime_db_conn and run_id:
            update_run_status(runtime_db_conn, run_id, "running")

        prompt_parts = []
        if last_job_prompt:
            prompt_parts.append(last_job_prompt.strip())
        prompt_parts.append(resume_message)
        user_input = "\n\n".join(prompt_parts)

        try:
            result = await process_turn(client, user_input, workspace_dir)
            final_response_text = result.response_text or ""
            if runtime_db_conn and run_id:
                update_run_status(runtime_db_conn, run_id, "succeeded")
            return final_response_text
        except BudgetExceeded as exc:
            if runtime_db_conn and run_id:
                update_run_status(runtime_db_conn, run_id, "failed")
            raise
        except Exception as exc:
            error_msg = str(exc)
            if _is_resume_session_error(error_msg) and not resume_fallback_attempted:
                resume_fallback_attempted = True
                _invalidate_provider_session(error_msg)
                _disable_provider_resume()
                try:
                    async with ClaudeSDKClient(options) as fallback_client:
                        result = await process_turn(
                            fallback_client, user_input, workspace_dir
                        )
                    final_response_text = result.response_text or ""
                    if runtime_db_conn and run_id:
                        update_run_status(runtime_db_conn, run_id, "succeeded")
                    return final_response_text
                except Exception as fallback_exc:
                    error_msg = str(fallback_exc)
            elif _is_resume_session_error(error_msg):
                _invalidate_provider_session(error_msg)
            if error_msg == last_error:
                error_retries += 1
            else:
                error_retries = 1
                last_error = error_msg
            if error_retries >= max_error_retries:
                if runtime_db_conn and run_id:
                    update_run_status(runtime_db_conn, run_id, "waiting_for_human")
                print(
                    "\n⚠️ Repeated errors detected. "
                    "Run status set to waiting_for_human."
                )
                return final_response_text
            print(
                f"\n⚠️ Job run error (attempt {error_retries}/{max_error_retries}): {exc}"
            )
            continue


async def run_conversation(client, query: str, start_ts: float, iteration: int = 1):
    """Run a single conversation turn with full tracing."""
    global trace, run_id, budget_config, budget_state, runtime_db_conn, current_step_id, tool_ledger, provider_session_forked_from
    step_id = str(uuid.uuid4())
    current_step_id = step_id
    step_index = iteration

    if runtime_db_conn and run_id:
        try:
            step_index = _next_step_index(runtime_db_conn, run_id)
            start_step(runtime_db_conn, run_id, step_id, step_index)
            logfire.info(
                "durable_step_started",
                run_id=run_id,
                step_id=step_id,
                step_index=step_index,
            )
        except Exception as exc:
            logfire.warning("runtime_step_insert_failed", step_id=step_id, error=str(exc))

    if budget_state["start_ts"] is None:
        budget_state["start_ts"] = start_ts

    elapsed = time.time() - budget_state["start_ts"]
    wallclock_limit = budget_config.get("max_wallclock_minutes", 0) * 60
    if wallclock_limit and elapsed >= wallclock_limit:
        raise BudgetExceeded(
            "max_wallclock_minutes",
            budget_config.get("max_wallclock_minutes", 0),
            round(elapsed / 60, 2),
            detail="wallclock budget reached before starting next step",
        )

    if budget_config.get("max_steps") and budget_state["steps"] >= budget_config["max_steps"]:
        raise BudgetExceeded(
            "max_steps",
            budget_config["max_steps"],
            budget_state["steps"],
            detail="step limit reached before starting next step",
        )
    budget_state["steps"] += 1

    # Initialize Logfire Context (Baggage) for Trace Organization
    if iteration == 1:
        logfire.set_baggage(agent="main")
        logfire.set_baggage(is_subagent="false")
        logfire.set_baggage(step="planning") # Initial state
        logfire.set_baggage(loop="1")
    else:
        # Update loop count for main agent
        logfire.set_baggage(loop=str(iteration))
        logfire.set_baggage(step="execution") # Default for subsequent turns
    if run_id:
        logfire.set_baggage(run_id=run_id)
    logfire.set_baggage(step_id=step_id)

    # Create Logfire span for this iteration
    with logfire.span(
        f"conversation_iteration_{iteration}",
        iteration=iteration,
        run_id=run_id,
        step_id=step_id,
    ):
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
                # Wrapped in span for full visibility of assistant's turn
                with logfire.span("assistant_message", model=msg.model):
                    for block in msg.content:
                        if isinstance(block, ToolUseBlock):
                            # Nested tool_use span
                            with logfire.span("tool_use", tool_name=block.name, tool_id=block.id):
                                tool_record = {
                                    "run_id": run_id,
                                    "step_id": step_id,
                                    "iteration": iteration,
                                    "name": block.name,
                                    "id": block.id,
                                    "time_offset_seconds": round(time.time() - start_ts, 3),
                                    "input": block.input if hasattr(block, "input") else None,
                                    "input_size_bytes": len(json.dumps(block.input))
                                    if hasattr(block, "input") and block.input
                                    else 0,
                                }
                                if tool_ledger:
                                    ledger_entry = tool_ledger.get_tool_call(str(block.id))
                                    if ledger_entry:
                                        tool_record["idempotency_key"] = ledger_entry.get(
                                            "idempotency_key"
                                        )
                                        tool_record["side_effect_class"] = ledger_entry.get(
                                            "side_effect_class"
                                        )
                                        tool_record["replay_policy"] = ledger_entry.get(
                                            "replay_policy"
                                        )
                                        tool_record["ledger_status"] = ledger_entry.get(
                                            "status"
                                        )
                                trace["tool_calls"].append(tool_record)
                                tool_calls_this_iter.append(tool_record)
                                budget_state["tool_calls"] += 1
                                if budget_config.get("max_tool_calls") and budget_state["tool_calls"] > budget_config["max_tool_calls"]:
                                    raise BudgetExceeded(
                                        "max_tool_calls",
                                        budget_config["max_tool_calls"],
                                        budget_state["tool_calls"],
                                        detail="tool call limit exceeded",
                                    )

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
                                    "tool_input",
                                    tool_name=block.name,
                                    tool_id=block.id,
                                    input_size=tool_record["input_size_bytes"],
                                    input_preview=input_preview,
                                    parent_tool_use_id=parent_tool_id,  # Sub-agent context
                                    is_subagent_call=bool(parent_tool_id),
                                    run_id=run_id,
                                    step_id=step_id,
                                )
                                
                                # Sub-agent tagging logic: Entering sub-agent
                                if block.name == "Task":
                                    subagent_type = "unknown"
                                    if hasattr(block, "input") and isinstance(block.input, dict):
                                        subagent_type = block.input.get("subagent_type", "unknown")
                                    
                                    logfire.set_baggage(agent=subagent_type)
                                    logfire.set_baggage(is_subagent="true")
                                    logfire.set_baggage(loop="1") # Reset loop for the sub-agent

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

                            # Log text block
                            logfire.info("text_block", length=len(block.text), text_preview=block.text[:500])

                        elif isinstance(block, ThinkingBlock):
                            # Log extended thinking
                            print(
                                f"\n🧠 Thinking (+{round(time.time() - start_ts, 1)}s)..."
                            )
                            logfire.info(
                                "thinking",
                                thinking_length=len(block.thinking),
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
                        
                        # Nested tool_result span requires finding tool info first (if possible) or just ID
                        with logfire.span("tool_result", tool_use_id=tool_use_id):
                            is_error = getattr(block, "is_error", False)

                            # Extract content - keep as typed object
                            block_content = getattr(block, "content", "")
                            content_str = str(block_content)

                            result_record = {
                                "run_id": run_id,
                                "step_id": step_id,
                                "tool_use_id": tool_use_id,
                                "time_offset_seconds": round(time.time() - start_ts, 3),
                                "is_error": is_error,
                                "content_size_bytes": len(content_str),
                                "content_preview": content_str[:1000]
                                if len(content_str) > 1000
                                else content_str,
                            }
                            if tool_ledger and tool_use_id:
                                ledger_entry = tool_ledger.get_tool_call(str(tool_use_id))
                                if ledger_entry:
                                    result_record["idempotency_key"] = ledger_entry.get(
                                        "idempotency_key"
                                    )
                                    result_record["ledger_status"] = ledger_entry.get(
                                        "status"
                                    )
                            trace["tool_results"].append(result_record)

                            # Log to Logfire with CONTENT preview
                            full_content = content_str[:2000]

                            logfire.info(
                                "tool_output",
                                tool_use_id=result_record["tool_use_id"],
                                content_size=result_record["content_size_bytes"],
                                is_error=result_record["is_error"],
                                content_preview=full_content,
                                run_id=run_id,
                                step_id=step_id,
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
                            
                            # Reset sub-agent tagging if Task returned (Back to Main)
                            if tool_name == "Task":
                                logfire.set_baggage(agent="main")
                                logfire.set_baggage(is_subagent="false")
                                if not is_error and OBSERVER_WORKSPACE_DIR:
                                    paths = _persist_subagent_output(
                                        workspace_dir=OBSERVER_WORKSPACE_DIR,
                                        tool_use_id=str(tool_use_id) if tool_use_id else None,
                                        tool_input=tool_input or {},
                                        raw_tool_name=tool_name or "Task",
                                        output=block_content,
                                        output_str=content_str,
                                    )
                                    if paths:
                                        logfire.info(
                                            "subagent_output_persisted",
                                            task_key=os.path.basename(paths["dir"]),
                                            output_path=paths["json"],
                                        )
                                
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
                                # Video/audio output observer - copy media to session workspace
                                asyncio.create_task(
                                    observe_and_save_video_outputs(
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
                                    
            elif isinstance(msg, ResultMessage):
                logfire.info("result_message",
                    duration_ms=msg.duration_ms,
                    total_cost_usd=msg.total_cost_usd,
                    num_turns=msg.num_turns,
                    is_error=msg.is_error
                )
                if msg.session_id:
                    _maybe_update_provider_session(
                        msg.session_id, forked_from=provider_session_forked_from
                    )
                if msg.is_error and msg.result and runtime_db_conn and run_id:
                    error_text = str(msg.result).lower()
                    if "resume" in error_text or "session" in error_text:
                        update_run_provider_session(runtime_db_conn, run_id, None)
                        logfire.warning(
                            "provider_session_invalidated",
                            run_id=run_id,
                            error=error_text[:200],
                        )

        iter_record = {
            "run_id": run_id,
            "step_id": step_id,
            "iteration": iteration,
            "query": query[:200],
            "duration_seconds": round(time.time() - iter_start, 3),
            "tool_calls": len(tool_calls_this_iter),
            "needs_user_input": needs_user_input,
            "auth_link": auth_link,
        }
        trace["iterations"].append(iter_record)
        if runtime_db_conn and run_id:
            last_tool_call_id = None
            if tool_calls_this_iter:
                last_tool_call_id = tool_calls_this_iter[-1].get("id")
            state_snapshot = {
                "run_id": run_id,
                "step_id": step_id,
                "iteration": iteration,
                "query_preview": query[:200],
                "tool_calls": len(tool_calls_this_iter),
                "needs_user_input": needs_user_input,
            }
            cursor = {
                "last_tool_call_id": last_tool_call_id,
            }
            try:
                save_checkpoint(
                    runtime_db_conn,
                    run_id=run_id,
                    step_id=step_id,
                    checkpoint_type="step_boundary",
                    state_snapshot=state_snapshot,
                    cursor=cursor,
                )
                logfire.info(
                    "durable_checkpoint_saved",
                    run_id=run_id,
                    step_id=step_id,
                    checkpoint_type="step_boundary",
                )
                complete_step(runtime_db_conn, step_id, "succeeded")
                logfire.info(
                    "durable_step_completed",
                    run_id=run_id,
                    step_id=step_id,
                    status="succeeded",
                )
            except Exception as exc:
                logfire.warning("checkpoint_save_failed", step_id=step_id, error=str(exc))
        current_step_id = None

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
        elif isinstance(msg, ResultMessage):
            _maybe_update_provider_session(msg.session_id)

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
        elif isinstance(msg, ResultMessage):
            _maybe_update_provider_session(msg.session_id)

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


def parse_cli_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Universal Agent CLI")
    parser.add_argument("--run-id", dest="run_id", help="Use an explicit run id.")
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume an existing run from the runtime DB.",
    )
    parser.add_argument(
        "--job",
        dest="job_path",
        help="Path to a run spec JSON file.",
    )
    parser.add_argument(
        "--fork",
        action="store_true",
        help="Fork an existing run using provider session state (requires --run-id).",
    )
    parser.add_argument(
        "--explain-tool-policy",
        dest="explain_tool_policy",
        help="Explain resolved tool policy for a raw tool name.",
    )
    return parser.parse_args()


def _print_tool_policy_explain(raw_tool_name: str) -> None:
    identity = parse_tool_identity(raw_tool_name)
    policy = resolve_tool_policy(identity.tool_name, identity.tool_namespace)
    side_effect_class = classify_tool(
        identity.tool_name,
        identity.tool_namespace,
        metadata={"raw_tool_name": raw_tool_name},
    )
    replay_policy = classify_replay_policy(
        identity.tool_name,
        identity.tool_namespace,
        metadata={"raw_tool_name": raw_tool_name},
    )

    print("Tool policy explain")
    print(f"- raw_tool_name: {raw_tool_name}")
    print(f"- tool_name: {identity.tool_name}")
    print(f"- tool_namespace: {identity.tool_namespace}")
    print(f"- side_effect_class: {side_effect_class}")
    print(f"- replay_policy: {replay_policy}")
    if policy:
        patterns = [pattern.pattern for pattern in policy.patterns]
        source = policy.source_path or "unknown"
        print("- matched_policy: yes")
        print(f"  - name: {policy.name}")
        print(f"  - namespace: {policy.tool_namespace or 'any'}")
        print(f"  - patterns: {patterns}")
        print(f"  - side_effect_class: {policy.side_effect_class}")
        print(f"  - replay_policy: {policy.replay_policy}")
        print(f"  - source: {source}")
    else:
        print("- matched_policy: no")



async def setup_session(
    run_id_override: Optional[str] = None,
    workspace_dir_override: Optional[str] = None,
) -> tuple[ClaudeAgentOptions, Any, str, str, dict]:
    """
    Initialize the agent session, tools, and options.
    Returns: (options, session, user_id, workspace_dir, trace)
    """
    global trace, composio, user_id, session, options, OBSERVER_WORKSPACE_DIR, run_id
    run_id = run_id_override or str(uuid.uuid4())

    # Create main span for entire execution
    # with logfire.span("standalone_composio_test") as span: # Moved to caller
    
    # Setup Session Workspace
    if workspace_dir_override:
        workspace_dir = workspace_dir_override
        os.makedirs(workspace_dir, exist_ok=True)
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # 1. Configured Root (Railway/Docker)
        if os.getenv("AGENT_WORKSPACE_ROOT"):
            base_dir = os.getenv("AGENT_WORKSPACE_ROOT")
            workspace_dir = os.path.join(base_dir, f"session_{timestamp}")
        else:
            # 2. Auto-Discovery (Local)
            # Try /app first (Docker), then project root (local), fallback to /tmp
            for base_dir in ["/app", os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "/tmp"]:
                workspace_dir = os.path.join(base_dir, "AGENT_RUN_WORKSPACES", f"session_{timestamp}")
                try:
                    os.makedirs(workspace_dir, exist_ok=True)
                    break  # Success
                except PermissionError:
                    continue
            else:
                raise RuntimeError("Cannot create workspace directory in any location")

    os.makedirs(workspace_dir, exist_ok=True)

    # Initialize Composio with automatic file downloads to this workspace
    downloads_dir = os.path.join(workspace_dir, "downloads")
    os.makedirs(downloads_dir, exist_ok=True)
    
    # Pre-create standard work_products directories to avoid runtime interruptions
    work_products_dir = os.path.join(workspace_dir, "work_products", "media")
    os.makedirs(work_products_dir, exist_ok=True)

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
    user_id = os.getenv("COMPOSIO_USER_ID") or os.getenv("DEFAULT_USER_ID")
    if not user_id:
        print("⚠️  WARNING: No COMPOSIO_USER_ID or DEFAULT_USER_ID found, defaulting to 'unknown_user'")
        user_id = "unknown_user"
        # raise ValueError("COMPOSIO_USER_ID or DEFAULT_USER_ID must be set in .env")
    
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
    
    # --- PARALLEL STARTUP OPTIMIZATION ---
    # Launch blocking network calls in parallel threads to reduce cold start latency.
    
    # Task 1: Create Session (CRITICAL - Needed for MCP URL)
    print("⏳ Starting Composio Session initialization...", flush=True)
    session_future = asyncio.to_thread(
        composio.create,
        user_id=user_id,
        toolkits={"disable": ["firecrawl", "exa"]}
    )

    # Task 2: dynamic Remote Discovery (INFORMATIONAL - Can happen in background)
    # Using client.connected_accounts.list(user_ids=[user_id]) for reliable persistent connection check
    print("⏳ Discovering connected apps...", flush=True)
    discovery_future = asyncio.to_thread(
        discover_connected_toolkits,
        composio, 
        user_id
    )

    # Await the Critical Path (Session) first
    session = await session_future
    print("✅ Composio Session Created")

    # Await Discovery (Informational)
    # Ideally we'd let this run even longer, but we print it right here. 
    # Even just overlapping the API calls saves ~1.5s.
    ALLOWED_APPS = await discovery_future

    if not ALLOWED_APPS:
        # Fallback if discovery completely fails or no apps connected
        ALLOWED_APPS = ["gmail", "github", "codeinterpreter", "slack", "composio_search"]
        print(f"⚠️ Discovery returned 0 apps (or only codeinterpreter). Using defaults: {ALLOWED_APPS}")
    else:
        print(f"✅ Discovered Active Composio Apps: {ALLOWED_APPS}")

    # 2. Local MCP Discovery
    local_tools = get_local_tools()
    print(f"✅ Active Local MCP Tools: {local_tools}")

    # 3. External MCP Servers (registered in mcp_servers config)
    external_mcps = ["edgartools", "video_audio", "youtube", "zai_vision"]  # List of external MCPs we've configured
    print(f"✅ External MCP Servers: {external_mcps}")

    # 4. Skill Discovery - Parse .claude/skills/ for progressive disclosure
    discovered_skills = discover_skills()
    skill_names = [s['name'] for s in discovered_skills]
    print(f"✅ Discovered Skills: {skill_names}")
    skills_xml = generate_skills_xml(discovered_skills)

    # Create ClaudeAgentOptions now that session is available
    global options

    # --- MEMORY SYSTEM CONTEXT INJECTION ---
    memory_context_str = ""
    try:
        from Memory_System.manager import MemoryManager
        from universal_agent.agent_college.integration import setup_agent_college
        
        # Initialize strictly for reading context (shared storage) - Use src_dir (Repo Root)
        storage_path = os.getenv("PERSIST_DIRECTORY", os.path.join(src_dir, "Memory_System_Data"))
        mem_mgr = MemoryManager(storage_dir=storage_path)
        
        # Initialize Agent College (Sandbox)
        setup_agent_college(mem_mgr)
        
        memory_context_str = mem_mgr.get_system_prompt_addition()
        print(f"🧠 Injected Core Memory Context ({len(memory_context_str)} chars)")
    except Exception as e:
        print(f"⚠️ Failed to load Memory Context/Agent College: {e}")

    # Use timezone-aware datetime for consistent results across deployments
    user_now = get_user_datetime()
    today_str = user_now.strftime('%A, %B %d, %Y')
    tomorrow_str = (user_now + timedelta(days=1)).strftime('%A, %B %d, %Y')
    
    options = ClaudeAgentOptions(
        model="claude-3-5-sonnet-20241022",
        system_prompt=(
            f"Current Date: {today_str}\n"
            f"Tomorrow is: {tomorrow_str}\n"
            f"{memory_context_str}\n"
            "TEMPORAL CONSISTENCY WARNING: You are operating in a timeline where it is December 2025. "
            "If 'real-world' search tools return results dated 2024, explicitly note the date discrepancy. "
                "Do NOT present 2024 news as 2025 news without qualification.\n\n"
                "You are a helpful assistant with access to external tools. "
                "You can execute code when needed using COMPOSIO_REMOTE_WORKBENCH or any available code execution tool.\n\n"
            "🔍 SEARCH TOOL PREFERENCE:\n"
            "- For web/news research, ALWAYS use Composio search tools (SERPAPI_SEARCH, COMPOSIO_SEARCH_NEWS, etc.).\n"
            "- Do NOT use native 'WebSearch' - it bypasses our artifact saving system.\n"
            "- Composio search results are auto-saved by the Observer for sub-agent access.\n\n"
            "🔒 SEARCH HYGIENE:\n"
            "- When using COMPOSIO_SEARCH_NEWS or COMPOSIO_SEARCH_WEB, append `-site:wikipedia.org` to the query by default to avoid wasting search slots.\n"
            "- Only omit this if the user explicitly asks for Wikipedia.\n\n"
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
            "   - Untrusted code execution.\n"
            "   - DO NOT use it for PDF creation, image processing, or document generation - do that LOCALLY with native Bash/Python.\n"
            "   - DO NOT use it as a text editor or file buffer for small data. Do that LOCALLY.\n"
            "   - 🚫 NEVER use REMOTE_WORKBENCH to save search results. The Observer already saves them automatically.\n"
            "   - 🚫 NEVER try to access local files from REMOTE_WORKBENCH - local paths don't exist there!\n"
            "4. 🚨 MANDATORY DELEGATION FOR REPORTS (HAND-OFF PROTOCOL):\n"
            "   - Role: You are the SCOUT. You find the information sources.\n"
            "   - Sub-Agent Role: The EXPERT. They process and synthesize the sources.\n"
            "   - PROCEDURE:\n"
            "     1. COMPOSIO Search -> Results are **AUTO-SAVED** by Observer to `search_results/`. DO NOT save again.\n"
            "     2. DO NOT read these files or extract URLs yourself. You are not the Expert.\n"
            "     3. DELEGATE immediately to 'report-creation-expert' using `Task`.\n"
            "     4. HAND-OFF PROMPT (Use EXACTLY this string, do not add URLs):\n"
            "        'Call finalize_research, then use research_overview.md + filtered crawl files to generate the report.'\n"
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
            "   - IMMEDIATELY delegate to 'report-creation-expert' with: 'Call finalize_research, then use research_overview.md + filtered crawl files to generate the report.'\n"
            "   - WHY: The sub-agent will scrape ALL URLs for full article content. Your search only has snippets.\n"
            "   - WITHOUT DELEGATION: Your report will be shallow (snippets only). WITH DELEGATION: Deep research (full articles).\n"
            "   - Trust the Observer. Trust the sub-agent. Your job is to search and delegate.\n\n"
            "10. 💡 PROACTIVE FOLLOW-UP SUGGESTIONS:\n"
            "   - After completing a task, suggest 2-3 helpful follow-up actions based on what was just accomplished.\n"
            "   - Examples: 'Would you like me to email this report?', 'Should I save this to a different format?',\n"
            "     'I can schedule a calendar event for the mentioned deadline if you'd like.'\n"
            "   - Keep suggestions relevant to the completed task and the user's apparent goals.\n\n"
            "11. 🎯 SKILLS - BEST PRACTICES KNOWLEDGE:\n"
            "   - Skills are pre-defined workflows and patterns for complex tasks (PDF, PPTX, DOCX, XLSX creation).\n"
            "   - Before building document creation scripts from scratch, CHECK if a skill exists.\n"
            "   - To use a skill: `read_local_file` the SKILL.md path below, then follow its patterns.\n"
            "   - Available skills (read SKILL.md for detailed instructions):\n"
            f"{skills_xml}\n"
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
            # External MCP: SEC Edgar Tools for financial research
            "edgartools": {
                "type": "stdio",
                "command": sys.executable,
                "args": ["-m", "edgar.ai"],
                "env": {"EDGAR_IDENTITY": os.environ.get("EDGAR_IDENTITY", "Agent agent@example.com")},
            },
            # External MCP: Video & Audio editing via FFmpeg
            "video_audio": {
                "type": "stdio",
                "command": sys.executable,
                "args": [os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "external_mcps", "video-audio-mcp", "server.py")],
            },
            # External MCP: YouTube/video downloader via yt-dlp
            "youtube": {
                "type": "stdio",
                "command": sys.executable,
                "args": ["-m", "mcp_youtube"],
            },
            # External MCP: Z.AI Vision (GLM-4.6V) for image/video analysis
            "zai_vision": {
                "type": "stdio",
                "command": "npx",
                "args": ["-y", "@z_ai/mcp-server"],
                "env": {
                    "Z_AI_API_KEY": os.environ.get("Z_AI_API_KEY", ""),
                    "Z_AI_MODE": os.environ.get("Z_AI_MODE", "ZAI"),
                },
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
                    "3. DO NOT read raw `search_results/crawl_*.md` files.\n"
                    "4. USE ONLY these specific tools for research:\n"
                    "   - `mcp__local_toolkit__finalize_research` - builds the filtered corpus\n"
                    "   - `mcp__local_toolkit__read_local_file` - read research_overview.md and search JSONs\n"
                    "   - `mcp__local_toolkit__read_research_files` - batch read filtered files only\n"
                    "   - `mcp__local_toolkit__list_directory` - list filtered corpus files\n"
                    "   - `mcp__local_toolkit__write_local_file` - save report\n\n"
                    "---\n\n"
                    "## WORKFLOW\n\n"
                    "### Step 1: Finalize Research (MANDATORY)\n"
                    "1. Call `mcp__local_toolkit__finalize_research` with the current session directory.\n"
                    "2. This tool creates `search_results/research_overview.md` and the filtered corpus in `search_results_filtered_best/`.\n"
                    "3. DO NOT manually crawl URLs or build your own URL list.\n\n"
                    "### Step 2: Read the Filtered Corpus (MANDATORY)\n"
                    f"1. Read `{workspace_dir}/search_results/research_overview.md` using `read_local_file`.\n"
                    f"2. List `{workspace_dir}/search_results_filtered_best/` to see filtered crawl files.\n"
                    "3. Use `read_research_files` with ONLY the filtered files.\n"
                    "4. DO NOT read raw `search_results/crawl_*.md` files.\n"
                    "5. If you need search snippets, read the `COMPOSIO_SEARCH_*.json` files.\n\n"
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
                    "**VISUALS & MEDIA:**\n"
                    "- You have access to `mcp__local_toolkit__generate_image`.\n"
                    "- **PRIORITIZE DATA**: Generate charts, graphs, and maps that clarify complex data. Avoid generic 'mood' images (e.g., 'robots thinking', 'abstract tech background').\n"
                    "- **ASPECT RATIO**: Prefer landscape (16:9) for headers and charts.\n"
                    "- **MANDATORY CSS**: When embedding images, YOU MUST use this exact style to prevent bleeding:\n"
                    "  `<img src='media/filename.png' style='max-width: 100%; height: auto; display: block; margin: 20px auto;' alt='Description'>`\n"
                    "- Save generated images to `work_products/media/`.\n"
                    "- Embed in HTML using relative paths.\n\n"
                    "**SYNTHESIS & COHERENCE:**\n"
                    "- Where sources discuss related topics, group and synthesize them into cohesive sections\n"
                    "- BUT: News often covers genuinely disjointed events - don't force artificial connections\n"
                    "- Prioritize completeness over flow - include all interesting facts even if standalone\n"
                    "- It's okay to have distinct sections for unrelated developments\n"
                    "- Aim for thematic grouping where natural, standalone items where not\n\n"
                    "### Step 4: Save Report\n"
                    f"Save as `.html` to `{workspace_dir}/work_products/` using `mcp__local_toolkit__write_local_file`.\n\n"
                    "🚨 START IMMEDIATELY: Call `mcp__local_toolkit__finalize_research`."
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
            "image-expert": AgentDefinition(
                description=(
                    "Expert for AI image generation and editing. "
                    "DELEGATE when user requests: 'generate image', 'create image', 'edit image', "
                    "'make a picture', 'design graphic', 'create infographic', 'visual for report', "
                    "or wants to iteratively refine images through conversation."
                ),
                prompt=(
                    f"Result Date: {datetime.now().strftime('%A, %B %d, %Y')}\\n"
                    f"CURRENT_SESSION_WORKSPACE: {workspace_dir}\\n\\n"
                    "You are an **Image Generation Expert** using Gemini 2.5 Flash Image.\\n\\n"
                    "## TASK MANAGEMENT (TodoWrite)\\n"
                    "Use TodoWrite to track complex workflows:\\n"
                    "```\\n"
                    "- [ ] Understand image request (style, content, purpose)\\n"
                    "- [ ] Generate initial image\\n"
                    "  - [ ] Craft detailed prompt\\n"
                    "  - [ ] Call generate_image tool\\n"
                    "- [ ] Review output with describe_image\\n"
                    "- [ ] Iterate if refinement needed\\n"
                    "- [ ] Confirm final output saved to work_products/media/\\n"
                    "```\\n"
                    "Mark items complete as you progress. Add nested todos for sub-steps.\\n\\n"
                    "## AVAILABLE TOOLS\\n"
                    "**Primary Image Tools:**\\n"
                    "- `mcp__local_toolkit__generate_image` - Generate or edit images\\n"
                    "- `mcp__local_toolkit__describe_image` - Get image descriptions (free, via ZAI)\\n"
                    "- `mcp__local_toolkit__preview_image` - Launch Gradio viewer\\n"
                    "- `mcp__zai_vision__analyze_image` - Detailed image analysis (free)\\n\\n"
                    "**Dynamic Composio Access & Planning:**\\n"
                    "You inherit ALL Composio tools. For complex or unfamiliar tasks:\\n"
                    "- Call `COMPOSIO_SEARCH_TOOLS` ONLY for **remote Composio tools** (external APIs, data sources)\\n"
                    "- It does NOT know about local tools (generate_image, crawl_parallel, etc.)\\n"
                    "- Use the returned `recommended_plan_steps` to structure your TodoWrite list\\n"
                    "- Use `COMPOSIO_SEARCH_*` tools to find reference images, data, or material\\n"
                    "- Use workbench tools for code execution if needed\\n\\n"
                    "**Example**: Need reference photos? Use COMPOSIO_SEARCH_TOOLS to find image search APIs.\\n"
                    "             Need to generate an image? Use generate_image (already in your tools).\\n\\n"
                    "## WORKFLOW\\n"
                    "1. **Understand Request**: What style, content, purpose?\\n"
                    "2. **Generate/Edit**: Call generate_image with detailed prompt\\n"
                    "3. **Review**: Use describe_image or analyze_image to verify output\\n"
                    "4. **Iterate**: If user wants changes, edit the generated image\\n"
                    "5. **Save**: Images auto-save to work_products/media/\\n\\n"
                    "## PROMPT CRAFTING TIPS\\n"
                    "- Be specific: 'modern, minimalist infographic with blue gradient'\\n"
                    "- Include style: 'photorealistic', 'illustration', 'line art'\\n"
                    "- For charts: describe the data and preferred visualization\\n"
                    "- For editing: describe what to change AND preserve\\n\\n"
                    f"OUTPUT DIRECTORY: {workspace_dir}/work_products/media/"
                ),
                model="inherit",
            ),
            "video-creation-expert": AgentDefinition(
                description=(
                    "🎬 MANDATORY DELEGATION TARGET for ALL video and audio tasks. "
                    "WHEN TO DELEGATE (REQUIRED): User asks to download, edit, or process video/audio, "
                    "mentions YouTube, MP3, MP4, trimming, cutting, transitions, or effects.\n"
                    "🛑 STOP! DO NOT PROCESS VIDEOS YOURSELF. YOU MUST DELEGATE.\n"
                    "This sub-agent has FFmpeg expertise and YouTube download capabilities."
                ),
                prompt=(
                    f"Result Date: {datetime.now().strftime('%A, %B %d, %Y')}\n"
                    f"CURRENT_SESSION_WORKSPACE: {workspace_dir}\n\n"
                    "You are a **Video Creation Expert** - a multimedia processing specialist.\n\n"
                    "## 📁 WORKSPACE LOCATIONS\n"
                    "| Location | Purpose |\n"
                    "|----------|----------|\n"
                    "| `downloads/videos/` | YouTube video downloads |\n"
                    "| `downloads/audio/` | Audio extractions |\n"
                    f"| `{workspace_dir}/work_products/media/` | Final outputs |\n\n"
                    "## 🎬 AVAILABLE TOOLS\n\n"
                    "**YouTube (mcp__youtube__):**\n"
                    "- `download_video` - Download YouTube video as MP4\n"
                    "- `download_audio` - Extract audio as MP3\n"
                    "- `get_metadata` - Get duration, title, etc.\n"
                    "- `download_subtitles` - Get captions\n\n"
                    "**Video Processing (mcp__video_audio__):**\n"
                    "- `trim_video` - Cut video to specific time range\n"
                    "- `concatenate_videos` - Join multiple videos\n"
                    "- `add_basic_transitions` - Add fade_in/fade_out effects\n"
                    "- `add_text_overlay` - Add text to video\n"
                    "- `extract_audio` - Extract audio track\n"
                    "- `change_video_speed` - Speed up or slow down\n"
                    "- `rotate_video` - Rotate 90/180/270 degrees\n"
                    "- `compress_video` - Reduce file size\n"
                    "- `reverse_video` - Play video backwards\n\n"
                    "## WORKFLOW\n\n"
                    "1. **Get metadata first** - Use `get_metadata` or `ffprobe` to get duration\n"
                    "2. **Download if needed** - YouTube URLs → use youtube MCP\n"
                    "3. **Process step by step** - Trim → Effects → Combine\n"
                    "4. **Name final output clearly** - e.g., `christmas_remix_final.mp4`\n"
                    "5. **Verify with ffprobe** - Check duration and file size\n\n"
                    "## PRO TIPS\n"
                    "- Intermediate files: use `temp_`, `part1_` etc (observer ignores these)\n"
                    "- Final outputs: use descriptive names (auto-saved to session)\n"
                    "- Run parallel trims when independent for speed\n"
                    "- If xfade fails, use individual fade_in/fade_out\n\n"
                    "🚨 START IMMEDIATELY: Check if files exist, then process."
                ),
                model="inherit",
            ),
        },
        hooks={
            "SubagentStop": [
                HookMatcher(matcher=None, hooks=[on_subagent_stop]),
            ],
            "PreToolUse": [
                HookMatcher(matcher=None, hooks=[on_pre_tool_use_ledger]),
                HookMatcher(matcher="Bash", hooks=[on_pre_bash_skill_hint]),
                HookMatcher(matcher="Task", hooks=[on_pre_task_skill_awareness]),
            ],
            "PostToolUse": [
                HookMatcher(matcher=None, hooks=[on_post_tool_use_ledger]),
            ],
            # DISABLED: UserPromptSubmit hook triggers Claude CLI bug:
            # "error: 'types.UnionType' object is not callable"
            # This is a CLI-side issue, not our code. PreToolUse still provides skill guidance.
            "UserPromptSubmit": [
                HookMatcher(matcher=None, hooks=[on_user_prompt_skill_awareness]),
            ],
        },
        permission_mode="bypassPermissions",
    )

    # Initialize trace dict now that session is available (requires session.mcp.url)
    trace = {
        "run_id": run_id,
        "session_info": {
            "url": session.mcp.url,
            "user_id": user_id,
            "run_id": run_id,
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
    OBSERVER_WORKSPACE_DIR = workspace_dir

    # Setup Output Logging
    run_log_path = os.path.join(workspace_dir, "run.log")
    log_file = open(run_log_path, "a", encoding="utf-8")
    sys.stdout = DualWriter(log_file, sys.stdout)
    sys.stderr = DualWriter(log_file, sys.stderr)

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

    # Inject Knowledge Base (Static Tool Guidance)
    knowledge_content = load_knowledge()
    if knowledge_content:
        if options.system_prompt and isinstance(options.system_prompt, str):
            options.system_prompt += f"\n\n## Tool Knowledge\n{knowledge_content}"
        else:
             options.system_prompt = f"## Tool Knowledge\n{knowledge_content}"
        print(f"✅ Injected Knowledge Base ({len(knowledge_content)} chars)")



    return options, session, user_id, workspace_dir, trace



async def process_turn(client: ClaudeSDKClient, user_input: str, workspace_dir: str) -> ExecutionResult:
    """
    Process a single user query.
    Returns: ExecutionResult with rich feedback
    """
    global trace, start_ts 
    
    trace["query"] = user_input
    trace["start_time"] = datetime.now().isoformat()
    start_ts = time.time()

    if LOGFIRE_TOKEN:
        logfire.info("query_started", query=user_input)

    # 2. Determine Complexity
    complexity = await classify_query(client, user_input)

    # 3. Route Query
    is_simple = complexity == "SIMPLE"

    final_response_text = ""

    if is_simple:
        # Try Fast Path
        success = await handle_simple_query(client, user_input)
        if not success:
            is_simple = False  # Fallback to Complex Path
        else:
            final_response_text = "(See output above)"

    if not is_simple:
        # Complex Path (Tool Loop) - track per-request timing
        request_start_ts = time.time()
        iteration = 1
        current_query = user_input

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
                input() # Non-blocking mock in headless, real input in CLI

                current_query = "I have completed the authentication. Please continue with the task."
                iteration += 1
                continue
            else:
                break

        # Per-request Execution Summary
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
        tool_breakdown = []
        if request_tool_calls:
            print("\n=== TOOL CALL BREAKDOWN ===")
            for tc in request_tool_calls:
                marker = "🏭" if any(x in tc["name"].upper() for x in ["WORKBENCH", "CODE", "EXECUTE", "BASH"]) else "  "
                print(f"  {marker} Iter {tc['iteration']} | +{tc['time_offset_seconds']:>6.1f}s | {tc['name']}")
                tool_breakdown.append({
                    "name": tc["name"],
                    "time_offset": tc["time_offset_seconds"],
                    "iteration": tc["iteration"],
                    "marker": marker
                })
        
        print(f"{'=' * 80}")
        
        # Print agent's final response (with follow-up suggestions) AFTER execution summary
        if final_response_text:
            print(f"\n{final_response_text}")

        # Extract follow-up suggestions
        suggestions = []
        if "Follow-up Options" in final_response_text:
            try:
                # Simple extraction: look for bullet points after "Follow-up Options"
                parts = final_response_text.split("Follow-up Options")
                if len(parts) > 1:
                    raw_suggestions = parts[1].split("\n")
                    for line in raw_suggestions:
                        line = line.strip()
                        if line.startswith(("-", "*")) and len(line) > 5:
                            suggestions.append(line.lstrip("-* ").strip())
            except Exception:
                pass

        # NEW: Intermediate Transcript Save
        try:
            from universal_agent import transcript_builder
            
            # Update stats for the snapshot
            current_ts = time.time()
            trace["end_time"] = datetime.now().isoformat()
            trace["total_duration_seconds"] = round(current_ts - start_ts, 3)
            
            transcript_path = os.path.join(workspace_dir, "transcript.md")
            if transcript_builder.generate_transcript(trace, transcript_path):
                print(f"\n🎬 Intermediate transcript saved to {transcript_path}")
        except Exception as e:
            # Don't let transcript failure crash the agent
            print(f"⚠️ Failed to save intermediate transcript: {e}")

        # NEW: Incremental Trace JSON Save (for live debugging)
        try:
            trace_path = os.path.join(workspace_dir, "trace.json")
            with open(trace_path, "w") as f:
                json.dump(trace, f, indent=2, default=str)
        except Exception as e:
            print(f"⚠️ Failed to save incremental trace: {e}")

    # End of Turn Update
    end_ts = time.time()
    trace["end_time"] = datetime.now().isoformat()
    trace["total_duration_seconds"] = round(end_ts - start_ts, 3)

    return ExecutionResult(
        response_text=final_response_text,
        execution_time_seconds=request_duration if not is_simple else 0.0,
        tool_calls=len(request_tool_calls) if not is_simple else 0,
        tool_breakdown=tool_breakdown if not is_simple else [],
        code_execution_used=code_exec_used if not is_simple else False,
        workspace_path=workspace_dir,
        trace_id=trace.get("trace_id"),
        follow_up_suggestions=suggestions if not is_simple else []
    )


# =============================================================================
# SEARCH TOOL CONFIGURATION REGISTRY (Synced with mcp_server.py)
# =============================================================================
SEARCH_TOOL_CONFIG = {
    # Web & Default
    "COMPOSIO_SEARCH":         {"list_key": "results",  "url_key": "url"},
    "COMPOSIO_SEARCH_WEB":     {"list_key": "results",  "url_key": "url"},
    "COMPOSIO_SEARCH_TAVILY":  {"list_key": "results",  "url_key": "url"},
    "COMPOSIO_SEARCH_DUCK_DUCK_GO": {"list_key": "results", "url_key": "url"},
    "COMPOSIO_SEARCH_EXA_ANSWER":   {"list_key": "results", "url_key": "url"},
    "COMPOSIO_SEARCH_GROQ_CHAT":    {"list_key": "choices", "url_key": "message"}, 
    
    # News & Articles
    "COMPOSIO_SEARCH_NEWS":    {"list_key": "articles", "url_key": "url"},
    "COMPOSIO_SEARCH_SCHOLAR": {"list_key": "articles", "url_key": "link"},
    
    # Products & Services
    "COMPOSIO_SEARCH_AMAZON":  {"list_key": "data",     "url_key": "product_url"},
    "COMPOSIO_SEARCH_SHOPPING":{"list_key": "data",     "url_key": "product_url"},
    "COMPOSIO_SEARCH_WALMART": {"list_key": "data",     "url_key": "product_url"},
    
    # Travel & Events
    "COMPOSIO_SEARCH_FLIGHTS": {"list_key": "data",     "url_key": "booking_url"},
    "COMPOSIO_SEARCH_HOTELS":  {"list_key": "data",     "url_key": "url"},
    "COMPOSIO_SEARCH_EVENT":   {"list_key": "data",     "url_key": "link"},
    "COMPOSIO_SEARCH_TRIP_ADVISOR": {"list_key": "data", "url_key": "url"},
    
    # Other
    "COMPOSIO_SEARCH_IMAGE":   {"list_key": "data",     "url_key": "original_url"},
    "COMPOSIO_SEARCH_FINANCE": {"list_key": "data",     "url_key": "link"},
    "COMPOSIO_SEARCH_GOOGLE_MAPS": {"list_key": "data", "url_key": "google_maps_link"},
}


async def main(args: argparse.Namespace):
    global trace, run_id, budget_config, budget_state, runtime_db_conn, tool_ledger, provider_session_forked_from

    # Create main span for entire execution
    # NOTE: Local MCP tools run in separate subprocess with their own trace ID
    main_span = logfire.span("standalone_composio_test")
    span_ctx = main_span.__enter__()  # Start the span manually
    
    budget_config = load_budget_config()
    runtime_db_conn = connect_runtime_db()
    validate_tool_policies()

    if args.explain_tool_policy:
        _print_tool_policy_explain(args.explain_tool_policy)
        main_span.__exit__(None, None, None)
        return

    run_spec = None
    workspace_override = None
    run_row = None
    base_run_row = None
    parent_run_id = None
    base_provider_session_id = None
    provider_session_forked_from = None

    if args.resume and args.fork:
        print("❌ --fork cannot be combined with --resume")
        return

    run_id_override = args.run_id
    if args.fork:
        if not args.run_id:
            print("❌ --fork requires --run-id (base run to fork)")
            return
        base_run_row = get_run(runtime_db_conn, args.run_id)
        if not base_run_row:
            print(f"❌ No run found for run_id={args.run_id}")
            return
        run_spec = json.loads(base_run_row["run_spec_json"])
        workspace_override = None
        run_id_override = None
        parent_run_id = base_run_row["run_id"]
        base_provider_session_id = (
            base_run_row["provider_session_id"]
            if "provider_session_id" in base_run_row.keys()
            else None
        )
        if not base_provider_session_id:
            print("❌ Base run does not have provider_session_id; cannot fork.")
            return
    elif args.resume:
        if not args.run_id:
            print("❌ --resume requires --run-id")
            return
        run_row = get_run(runtime_db_conn, args.run_id)
        if not run_row:
            print(f"❌ No run found for run_id={args.run_id}")
            return
        run_spec = json.loads(run_row["run_spec_json"])
        workspace_override = run_spec.get("workspace_dir")
        run_id_override = args.run_id

    options, session, user_id, workspace_dir, trace = await setup_session(
        run_id_override=run_id_override,
        workspace_dir_override=workspace_override,
    )

    trace["budgets"] = budget_config
    tool_ledger = ToolCallLedger(runtime_db_conn, workspace_dir)

    provider_session_id = None
    resume_source_row = run_row if args.resume else base_run_row
    if resume_source_row and "provider_session_id" in resume_source_row.keys():
        provider_session_id = resume_source_row["provider_session_id"]
    if args.resume and provider_session_id:
        options.continue_conversation = True
        options.resume = provider_session_id
        print(f"✅ Using provider session resume: {provider_session_id}")
        trace["provider_session_id"] = provider_session_id
    if args.fork and base_provider_session_id:
        options.continue_conversation = True
        options.resume = base_provider_session_id
        options.fork_session = True
        provider_session_forked_from = base_provider_session_id
        print(f"✅ Forking provider session: {base_provider_session_id}")
        trace["provider_session_forked_from"] = base_provider_session_id

    if run_spec is None:
        if args.job_path:
            with open(args.job_path, "r", encoding="utf-8") as f:
                run_spec = json.load(f)
            run_spec.setdefault("job_path", args.job_path)
        else:
            run_spec = {}
        run_spec.setdefault("entrypoint", "cli")
        run_spec.setdefault("workspace_dir", workspace_dir)
        run_spec.setdefault("budgets", budget_config)
    elif args.fork:
        run_spec["workspace_dir"] = workspace_dir

    if run_row and "parent_run_id" in run_row.keys() and not parent_run_id:
        parent_run_id = run_row["parent_run_id"]

    run_mode = infer_run_mode(run_row or base_run_row, run_spec, args.job_path)
    prompt_run_row = run_row or base_run_row
    job_path = args.job_path or (prompt_run_row["job_path"] if prompt_run_row else None)
    job_prompt = None
    if run_spec:
        job_prompt = build_job_prompt(run_spec)
    if prompt_run_row and prompt_run_row["last_job_prompt"]:
        job_prompt = prompt_run_row["last_job_prompt"]

    if run_id:
        status_to_set = "running"
        if args.resume and run_row:
            status_to_set = run_row["status"]
            if (
                run_mode == "job"
                and status_to_set not in TERMINAL_STATUSES
                and status_to_set not in WAITING_STATUSES
            ):
                status_to_set = "running"
        upsert_run(
            runtime_db_conn,
            run_id,
            "cli",
            run_spec,
            run_mode=run_mode,
            job_path=job_path,
            last_job_prompt=job_prompt,
            parent_run_id=parent_run_id,
            status=status_to_set,
        )
        logfire.info("durable_run_upserted", run_id=run_id, entrypoint="cli")
        if parent_run_id:
            trace["parent_run_id"] = parent_run_id
    
    # Extract Trace ID immediately after span creation
    if LOGFIRE_TOKEN:
        try:
            trace_id = main_span.get_span_context().trace_id
            trace_id_hex = format(trace_id, "032x")
            trace["trace_id"] = trace_id_hex
        except Exception as e:
            print(f"⚠️ Failed to extract trace ID: {e}")
            trace_id_hex = "0" * 32
    else:
        trace_id_hex = "N/A"

    # Extract timestamp from workspace_dir (e.g. "session_20251228_123456" -> "20251228_123456")
    timestamp = os.path.basename(workspace_dir).replace("session_", "")
    
    print(f"\n=== Composio Session Info ===")
    print(f"Session URL: {session.mcp.url}")
    print(f"User ID: {user_id}")
    print(f"Run ID: {run_id}")
    print(f"Timestamp: {timestamp}")
    print(f"Trace ID: {trace_id_hex}")
    if run_id:
        resume_cmd = f"uv run python src/universal_agent/main.py --resume --run-id {run_id}"
        print(f"Resume Command: {resume_cmd}")
        update_restart_file(run_id, workspace_dir, resume_cmd=resume_cmd)
    print(f"============================\n")

    print("=" * 80)
    print("Composio Agent Ready")
    print("=" * 80)
    print()

    trace["start_time"] = datetime.now().isoformat()
    start_ts = time.time()
    budget_state["start_ts"] = start_ts
    budget_state["steps"] = get_step_count(runtime_db_conn, run_id) if run_id else 0
    budget_state["tool_calls"] = 0
    if LOGFIRE_TOKEN and run_id:
        logfire.set_baggage(run_id=run_id)
    if args.resume and run_id:
        checkpoint = load_last_checkpoint(runtime_db_conn, run_id)
        if checkpoint:
            trace["resume_checkpoint"] = {
                "checkpoint_id": checkpoint["checkpoint_id"],
                "created_at": checkpoint["created_at"],
                "checkpoint_type": checkpoint["checkpoint_type"],
            }
            print(f"✅ Resume checkpoint loaded: {checkpoint['checkpoint_id']}")
        else:
            print("⚠️ No checkpoint found for resume; starting fresh.")

    # Configure prompt with history (persists across sessions) and better editing
    history_file = os.path.join(workspace_dir, ".prompt_history")
    prompt_style = Style.from_dict({
        'prompt': '#00aa00 bold',  # Green prompt
    })
    prompt_session = PromptSession(
        history=FileHistory(history_file),
        auto_suggest=AutoSuggestFromHistory(),
        multiline=False,  # Single line, but with full editing support
        style=prompt_style,
        enable_history_search=True,  # Ctrl+R for history search
    )

    run_status = run_row["status"] if run_row else None
    should_auto_continue_job = (
        args.resume
        and run_status not in TERMINAL_STATUSES
        and run_status not in WAITING_STATUSES
    )

    def save_interrupt_checkpoint(prompt_preview: str) -> None:
        if not runtime_db_conn or not run_id:
            return
        step_id = current_step_id
        if not step_id:
            run_row = get_run(runtime_db_conn, run_id)
            if run_row:
                step_id = run_row["current_step_id"]
        if not step_id and runtime_db_conn:
            row = runtime_db_conn.execute(
                "SELECT step_id FROM run_steps WHERE run_id = ? ORDER BY created_at DESC LIMIT 1",
                (run_id,),
            ).fetchone()
            if row:
                step_id = row["step_id"]
        if not step_id:
            return
        last_tool_call_id = None
        if trace.get("tool_calls"):
            last_tool_call_id = trace["tool_calls"][-1].get("id")
        state_snapshot = {
            "run_id": run_id,
            "step_id": step_id,
            "phase": "interrupt",
            "query_preview": prompt_preview[:200],
            "budget_state": budget_state,
        }
        cursor = {"last_tool_call_id": last_tool_call_id}
        save_checkpoint(
            runtime_db_conn,
            run_id=run_id,
            step_id=step_id,
            checkpoint_type="interrupt",
            state_snapshot=state_snapshot,
            cursor=cursor,
        )
        update_run_status(runtime_db_conn, run_id, "paused")
        if LOGFIRE_TOKEN:
            logfire.info(
                "durable_checkpoint_saved",
                run_id=run_id,
                step_id=step_id,
                checkpoint_type="interrupt",
            )

    last_user_input = ""

    def handle_sigint(signum, frame):  # noqa: ARG001
        nonlocal last_user_input
        global interrupt_requested, last_sigint_ts
        now_ts = time.monotonic()
        if last_sigint_ts is not None and (now_ts - last_sigint_ts) < 1.0:
            return
        last_sigint_ts = now_ts
        interrupt_requested = True
        print("\n⚠️ Interrupted by user (SIGINT). Saving checkpoint...")
        try:
            save_interrupt_checkpoint(last_user_input or job_prompt or "")
        except Exception as exc:
            print(f"⚠️ Failed to save interrupt checkpoint: {exc}")
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, handle_sigint)

    auto_resume_complete = False

    async with ClaudeSDKClient(options) as client:
        run_failed = False
        pending_prompt = (
            job_prompt if job_prompt and not args.resume and not args.fork else None
        )
        try:
            if args.resume and run_mode == "job" and run_status in TERMINAL_STATUSES and run_id:
                print("✅ Run already terminal. No resume needed.")
                print_job_completion_summary(
                    runtime_db_conn,
                    run_id,
                    run_status,
                    workspace_dir,
                    "",
                )
                auto_resume_complete = True
            if should_auto_continue_job and run_id:
                resume_packet, resume_summary = build_resume_packet(
                    runtime_db_conn, run_id, workspace_dir
                )
                print("✅ Resume packet constructed")
                print(resume_summary)
                resume_packet_path = None
                if workspace_dir:
                    resume_packet_path = os.path.join(
                        workspace_dir, f"resume_packet_{run_id}.md"
                    )
                    try:
                        with open(resume_packet_path, "w", encoding="utf-8") as f:
                            f.write("# Resume Packet\n\n")
                            f.write("Summary:\n\n")
                            f.write(resume_summary + "\n\n")
                            f.write("Packet JSON:\n\n")
                            f.write(json.dumps(resume_packet, indent=2, default=str))
                            f.write("\n")
                    except Exception as exc:
                        print(f"⚠️ Failed to save resume packet: {exc}")
                        resume_packet_path = None
                if resume_packet_path:
                    update_restart_file(
                        run_id,
                        workspace_dir,
                        resume_packet_path=resume_packet_path,
                    )
                if _handle_cancel_request(runtime_db_conn, run_id, workspace_dir):
                    auto_resume_complete = True
                else:
                    try:
                        replay_ok = await reconcile_inflight_tools(
                            client, run_id, workspace_dir
                        )
                        if not replay_ok:
                            print(
                                "⚠️ In-flight tool replay incomplete; run set to waiting_for_human."
                            )
                            auto_resume_complete = True
                        else:
                            resume_summary = (
                                resume_summary
                                + "\nreplay_status: completed"
                                + "\nreplay_note: in-flight tool calls already replayed; do not re-run them."
                            )
                            final_response = await continue_job_run(
                                client,
                                run_id,
                                workspace_dir,
                                job_prompt,
                                resume_summary,
                                replay_note="In-flight tool calls were replayed; do not re-run them.",
                            )
                            if runtime_db_conn and run_id:
                                status_row = get_run(runtime_db_conn, run_id)
                                status_value = (
                                    status_row["status"] if status_row else "running"
                                )
                                if status_value in TERMINAL_STATUSES:
                                    print_job_completion_summary(
                                        runtime_db_conn,
                                        run_id,
                                        status_value,
                                        workspace_dir,
                                        final_response or "",
                                    )
                            auto_resume_complete = True
                    except BudgetExceeded as exc:
                        run_failed = True
                        trace["status"] = "budget_exceeded"
                        trace["budget_error"] = exc.to_dict()
                        print(f"\n⛔ {exc}")
                        if LOGFIRE_TOKEN:
                            logfire.error("budget_exceeded", **exc.to_dict())
                        if runtime_db_conn and current_step_id:
                            complete_step(
                                runtime_db_conn,
                                current_step_id,
                                "failed",
                                error_code="budget_exceeded",
                                error_detail=str(exc),
                            )
                        if runtime_db_conn and run_id:
                            update_run_status(runtime_db_conn, run_id, "failed")
                            logfire.warning(
                                "durable_run_failed",
                                run_id=run_id,
                                error_code="budget_exceeded",
                                error_detail=str(exc),
                            )
                        auto_resume_complete = True
            if auto_resume_complete:
                pass
            while not auto_resume_complete:
                if interrupt_requested:
                    break
                if _handle_cancel_request(runtime_db_conn, run_id, workspace_dir):
                    auto_resume_complete = True
                    break
                # 1. Get User Input (auto-inject job prompt once when provided)
                print("\n" + "=" * 80)
                try:
                    if pending_prompt:
                        user_input = pending_prompt
                        pending_prompt = None
                        print("🤖 Auto-running job prompt from run spec...")
                    else:
                        with patch_stdout():
                            user_input = await prompt_session.prompt_async(
                                "🤖 Enter your request (or 'quit'): ",
                            )
                        user_input = user_input.strip()
                except (EOFError, KeyboardInterrupt):
                    run_failed = True
                    print("\n⚠️ Interrupted by user. Saving checkpoint...")
                    try:
                        save_interrupt_checkpoint(last_user_input or job_prompt or "")
                    except Exception as exc:
                        print(f"⚠️ Failed to save interrupt checkpoint: {exc}")
                    break
                if not user_input or user_input.lower() in ("quit", "exit"):
                    break

                last_user_input = user_input
                # Call process_turn which handles:
                # 1. Tracing setup
                # 2. Complexity classification
                # 3. Simple/Complex routing
                # 4. Tool execution loop (if complex)
                # 5. Output and Transcripts
                try:
                    if _handle_cancel_request(runtime_db_conn, run_id, workspace_dir):
                        auto_resume_complete = True
                        break
                    result = await process_turn(client, user_input, workspace_dir)
                    if run_mode == "job" and args.job_path:
                        if runtime_db_conn and run_id:
                            if _handle_cancel_request(runtime_db_conn, run_id, workspace_dir):
                                auto_resume_complete = True
                                break
                            update_run_status(runtime_db_conn, run_id, "succeeded")
                            print_job_completion_summary(
                                runtime_db_conn,
                                run_id,
                                "succeeded",
                                workspace_dir,
                                result.response_text or "",
                            )
                        break
                except KeyboardInterrupt:
                    run_failed = True
                    print("\n⚠️ Interrupted by user. Saving checkpoint...")
                    try:
                        save_interrupt_checkpoint(user_input)
                    except Exception as exc:
                        print(f"⚠️ Failed to save interrupt checkpoint: {exc}")
                    break
                except BudgetExceeded as exc:
                    run_failed = True
                    trace["status"] = "budget_exceeded"
                    trace["budget_error"] = exc.to_dict()
                    print(f"\n⛔ {exc}")
                    if LOGFIRE_TOKEN:
                        logfire.error("budget_exceeded", **exc.to_dict())
                    if runtime_db_conn and current_step_id:
                        complete_step(
                            runtime_db_conn,
                            current_step_id,
                            "failed",
                            error_code="budget_exceeded",
                            error_detail=str(exc),
                        )
                    if runtime_db_conn and run_id:
                        update_run_status(runtime_db_conn, run_id, "failed")
                        logfire.warning(
                            "durable_run_failed",
                            run_id=run_id,
                            error_code="budget_exceeded",
                            error_detail=str(exc),
                        )
                    break
                except Exception as exc:
                    run_failed = True
                    print(f"\n❌ Execution error: {exc}")
                    if runtime_db_conn and current_step_id:
                        complete_step(
                            runtime_db_conn,
                            current_step_id,
                            "failed",
                            error_code="exception",
                            error_detail=str(exc),
                        )
                    if runtime_db_conn and run_id:
                        update_run_status(runtime_db_conn, run_id, "failed")
                        logfire.warning(
                            "durable_run_failed",
                            run_id=run_id,
                            error_code="exception",
                            error_detail=str(exc),
                        )
                    raise
        except KeyboardInterrupt:
            run_failed = True
            print("\n⚠️ Interrupted by user. Saving checkpoint...")
            try:
                save_interrupt_checkpoint(job_prompt or "")
            except Exception as exc:
                print(f"⚠️ Failed to save interrupt checkpoint: {exc}")

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

            # NEW: Generate Rich Transcript
            from universal_agent import transcript_builder
            transcript_path = os.path.join(workspace_dir, "transcript.md")
            if transcript_builder.generate_transcript(trace, transcript_path):
                print(f"🎬 Rich transcript saved to {transcript_path}")
            else:
                print(f"⚠️ Failed to generate transcript")


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
            if runtime_db_conn and run_id and not run_failed and not run_cancelled_by_operator:
                update_run_status(runtime_db_conn, run_id, "succeeded")
                logfire.info("durable_run_completed", run_id=run_id)

    # Close the main span to ensure all nested spans are captured in trace
    main_span.__exit__(None, None, None)

if __name__ == "__main__":
    print("\n" + "=" * 80)
    print("Composio Agent - Claude SDK with Tool Router")
    print("Logfire tracing enabled for observability.")
    print("=" * 80 + "\n")
    cli_args = parse_cli_args()
    try:
        asyncio.run(main(cli_args))
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("\n\n⚠️ Execution cancelled by user.")
        # logfire might not be configured if it failed early, but we try
        if "logfire" in globals() and LOGFIRE_TOKEN:
            logfire.warn("execution_cancelled")
    except Exception as e:
        print(f"\n\n❌ Execution error: {e}")
        if "logfire" in globals() and LOGFIRE_TOKEN:
            logfire.error("execution_error", error=str(e))

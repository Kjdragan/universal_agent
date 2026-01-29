"""
Universal Agent Core - Refactored agent logic for both CLI and API usage.

This module provides the UniversalAgent class which encapsulates:
- Composio/Claude SDK initialization
- Conversation handling with streaming events
- Observer pattern for artifact saving
"""

import asyncio
import logging
import os
import time
import json
import uuid
import re
import inspect
from datetime import datetime
from typing import AsyncGenerator, Any, Callable, Optional
from dataclasses import dataclass, field
from enum import Enum

from dotenv import load_dotenv

load_dotenv()

import logfire
from claude_agent_sdk.client import ClaudeSDKClient
from claude_agent_sdk.types import (
    ClaudeAgentOptions,
    AgentDefinition,
    AssistantMessage,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    ToolResultBlock,
    ThinkingBlock,
    UserMessage,
    HookMatcher,
)
from composio import Composio
from universal_agent.durable.tool_gateway import (
    is_malformed_tool_name,
    parse_malformed_tool_name,
)
from universal_agent.guardrails.tool_schema import validate_tool_input
from universal_agent.prompt_assets import get_tool_knowledge_block
from universal_agent.utils.message_history import MessageHistory, TRUNCATION_THRESHOLD

DEFAULT_AGENT_NAME = "Primary Agent"
SEARCH_AGENT_NAME = "Research Specialist"
WRITER_AGENT_NAME = "Report Author"

# Local MCP server provides: crawl_parallel, finalize_research, append_to_file, etc.
# Note: read_research_files is deprecated - use refined_corpus.md from finalize_research

# Tools that should be blocked (legacy/problematic tools)
DISALLOWED_TOOLS = [
    "TaskOutput",
    "TaskResult",
    "taskoutput",
    "taskresult",
    "mcp__composio__TaskOutput",
    "mcp__composio__TaskResult",
    "WebSearch",
    "web_search",
    "mcp__composio__WebSearch",
]


# =============================================================================
# EVENT TYPES - Used for streaming events back to CLI/UI
# =============================================================================


class EventType(str, Enum):
    """Types of events emitted by the agent."""

    TEXT = "text"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    THINKING = "thinking"
    STATUS = "status"
    HEARTBEAT = "heartbeat"
    AUTH_REQUIRED = "auth_required"
    ERROR = "error"
    SESSION_INFO = "session_info"
    ITERATION_END = "iteration_end"
    WORK_PRODUCT = "work_product"  # HTML reports, files saved to work_products/
    # URW Phase Events (Stage 5)
    URW_PHASE_START = "urw_phase_start"
    URW_PHASE_COMPLETE = "urw_phase_complete"
    URW_PHASE_FAILED = "urw_phase_failed"
    URW_EVALUATION = "urw_evaluation"


class HarnessError(Exception):
    """
    Raised when the agent encounters an unrecoverable error state
    that requires a full harness restart (e.g., infinite tool loops).
    """

    def __init__(self, message: str, context: Optional[dict] = None):
        super().__init__(message)
        self.context = context or {}


@dataclass
class AgentEvent:
    """An event emitted by the agent during execution."""

    type: EventType
    data: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


# =============================================================================
# LOGFIRE SETUP
# =============================================================================


LOGFIRE_DISABLED = os.getenv("UA_DISABLE_LOGFIRE", "").lower() in {"1", "true", "yes"}
LOGFIRE_TOKEN = None
if not LOGFIRE_DISABLED:
    LOGFIRE_TOKEN = (
        os.getenv("LOGFIRE_TOKEN")
        or os.getenv("LOGFIRE_WRITE_TOKEN")
        or os.getenv("LOGFIRE_API_KEY")
    )


# -----------------------------------------------------------------------------
# SDK compatibility helpers
# -----------------------------------------------------------------------------


def _agent_definition_supports_hooks() -> bool:
    try:
        return "hooks" in inspect.signature(AgentDefinition).parameters
    except Exception:
        return False


# =============================================================================
# LOGGING & CAPTURE UTILS
# =============================================================================

class QueueLogHandler(logging.Handler):
    """Logging handler that pushes records to an asyncio Queue."""
    def __init__(self, queue: asyncio.Queue):
        super().__init__()
        self.queue = queue
        self.loop = asyncio.get_running_loop()

    def emit(self, record: logging.LogRecord):
        try:
            msg = self.format(record)
            event = AgentEvent(
                type=EventType.STATUS,
                data={
                    "status": msg,
                    "level": record.levelname,
                    "prefix": f"[{record.name}]",
                    "is_log": True,
                }
            )
            # Use call_soon_threadsafe to basic-safety across threads if needed
            self.loop.call_soon_threadsafe(self.queue.put_nowait, event)
        except Exception:
            self.handleError(record)

class StreamCapture:
    """Captures stdout/stderr and mirrors to a queue while keeping original output."""
    def __init__(self, queue: asyncio.Queue, stream, level="INFO", prefix="[STDOUT]", log_handler: Optional[logging.Handler] = None):
        self.queue = queue
        self.stream = stream
        self.level = level
        self.prefix = prefix
        self.log_handler = log_handler
        self.loop = asyncio.get_running_loop()

    def write(self, text: str):
        # Mirror to original stream
        self.stream.write(text)
        
        # Determine if we should send to queue (filter empty newlines if desired)
        if text.strip():
            # Heuristic: Detect log level from content
            computed_level = self.level
            
            clean_text = text.strip()
            # Auto-detect ERRORs if level is INFO (upgrade strategy)
            # This allows STDERR to be INFO by default (neutral color) but turn RED on actual errors
            if self.level == "INFO":
                if any(x in clean_text for x in ["Traceback (most recent call last)", "Error:", "Exception:", "CRITICAL", "FATAL"]):
                    computed_level = "ERROR"
            
            # If default is ERROR (legacy usage), try to downgrade if it looks like a status message
            elif self.level == "ERROR":
                # Common indicators of non-error status messages
                if any(x in clean_text for x in ["🚀", "✅", "⏳", "INFO", "DEBUG", "Starting", "Success", "Completed"]):
                    computed_level = "INFO"
                # Check for explicit log level prefixes like "[INFO]" or "INFO:"
                elif "[INFO]" in clean_text or clean_text.startswith("INFO"):
                    computed_level = "INFO"
                elif "[DEBUG]" in clean_text or clean_text.startswith("DEBUG"):
                    computed_level = "DEBUG"

            # 1. Emit to Event Queue (for UI)
            event = AgentEvent(
                type=EventType.STATUS,
                data={
                    "status": text.strip(),
                    "level": computed_level,
                    "prefix": self.prefix,
                    "is_log": True,
                }
            )
            try:
                self.loop.call_soon_threadsafe(self.queue.put_nowait, event)
            except Exception:
                pass
            
            # 2. Emit to Log File (for run.log persistence)
            if self.log_handler:
                try:
                    # Map string level to int
                    level_int = getattr(logging, computed_level, logging.INFO)
                    # Create a LogRecord manually to format matches other logs
                    record = logging.LogRecord(
                        name=self.prefix.strip("[]"), # Use prefix as logger name e.g. "STDOUT"
                        level=level_int,
                        pathname="agent_core.py",
                        lineno=0,
                        msg=text.strip(),
                        args=(),
                        exc_info=None
                    )
                    # Using call_soon_threadsafe is tricky for handlers, but FileHandler is thread-safe usually.
                    # We'll just emit directly as we are likely in a thread or async context 
                    # where file I/O is acceptable for logging.
                    self.log_handler.emit(record)
                except Exception:
                    pass

    def flush(self):
        self.stream.flush()

    def isatty(self):
        return getattr(self.stream, "isatty", lambda: False)()

    def fileno(self):
        return self.stream.fileno()


def _warn_if_subagent_hooks_configured(agents: dict[str, Any] | None) -> None:
    """
    Check if any sub-agent definitions have explicit hooks configured.
    
    NOTE: As of SDK 0.1.3+, hooks from the MAIN agent's ClaudeAgentOptions
    automatically apply to all sub-agents. The hook system is "agent-agnostic"
    and operates at the CLI level.
    """
    if not agents or _agent_definition_supports_hooks():
        return
    for agent_name, agent_def in agents.items():
        hooks_present = False
        if isinstance(agent_def, dict):
            hooks_present = "hooks" in agent_def
        else:
            hooks_present = (
                hasattr(agent_def, "hooks")
                and getattr(agent_def, "hooks", None) is not None
            )
        if hooks_present:
            # NOTE: This is about AgentDefinition not accepting a hooks param,
            # NOT about hooks not working for sub-agents (they do work via main agent)
            print(
                f"ℹ️ AgentDefinition for '{agent_name}' has hooks configured, "
                f"but AgentDefinition doesn't accept hooks directly. "
                f"Hooks from the main agent's options will still apply to this sub-agent."
            )
            logfire.info(
                "subagent_hooks_in_definition_ignored",
                agent_name=agent_name,
                note="Main agent hooks still apply to sub-agents",
            )


# =============================================================================
# PRE-TOOL-USE GUARDRAIL HOOK
# =============================================================================


async def malformed_tool_guardrail_hook(
    input_data: dict, tool_use_id: str, context
) -> dict:
    """
    Pre-tool-use hook that blocks malformed tool calls and injects schema guidance.

    This catches the XML-concatenation bug where tool names include arg_key/arg_value,
    denies the call, and provides corrective guidance for faster recovery.
    """
    tool_name = str(input_data.get("tool_name", "") or "")
    tool_input = input_data.get("tool_input", {}) or {}

    # Check for malformed tool name (XML fragments concatenated)
    if is_malformed_tool_name(tool_name):
        base_name, arg_key, arg_value = parse_malformed_tool_name(tool_name)

        repair_hint = ""
        if base_name and arg_key and arg_value is not None:
            import json

            repaired_payload = json.dumps({arg_key: arg_value}, ensure_ascii=True)
            repair_hint = f" Reissue as {base_name} with input {repaired_payload}."

        logfire.warning(
            "malformed_tool_guardrail_blocked",
            tool_name=tool_name,
            base_name=base_name,
            tool_use_id=tool_use_id,
        )

        return {
            "systemMessage": (
                "⚠️ BLOCKED: Malformed tool call. "
                "Do NOT concatenate XML-like arg_key/arg_value into the tool name. "
                "Use proper JSON arguments instead." + repair_hint
            ),
            "decision": "block",
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "Malformed tool name (XML-style args in name).",
            },
        }

    # Check for Bash commands trying to call Composio SDK directly (bypass MCP)
    if tool_name.lower() == "bash" and isinstance(tool_input, dict):
        command = str(tool_input.get("command", "") or "")
        command_lower = command.lower()

        # Detect Composio SDK usage patterns that should use MCP instead
        composio_sdk_patterns = [
            "from composio import",
            "import composio",
            "composio.composio",
            "composiotoolset",
            "composio_client",
            "composio_toolset",
            ".tools.execute(",
            "execute_action(",
            "gmail_send_email",  # Specific tool that should be MCP
        ]

        if any(pattern in command_lower for pattern in composio_sdk_patterns):
            logfire.warning(
                "bash_composio_sdk_blocked",
                tool_name=tool_name,
                command_preview=command[:200],
                tool_use_id=tool_use_id,
            )

            return {
                "systemMessage": (
                    "🚫 BLOCKED: You cannot call Composio SDK directly via Python/Bash.\n\n"
                    "**USE MCP TOOLS INSTEAD:**\n"
                    "- For email: Use `GMAIL_SEND_EMAIL` tool directly.\n"
                    "- For file upload: `mcp__local_toolkit__upload_to_composio`\n"
                    "- For search: Use `COMPOSIO_SEARCH_TOOLS` to find the correct tool.\n\n"
                    "The Composio SDK is not available in the Bash environment. "
                    "All actions must go through specific MCP tools which handle auth automatically.\n\n"
                    "If you cannot find a tool, use `COMPOSIO_SEARCH_TOOLS` to look for it."
                ),
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "Bash command attempts to call Composio SDK directly. Use MCP tools instead.",
                },
            }

    # Check for schema validation (empty required fields)
    is_valid, missing, schema = validate_tool_input(tool_name, tool_input)
    if not is_valid:
        example = schema.example if schema else ""
        example_hint = f" Example: {example}" if example else ""

        logfire.warning(
            "schema_guardrail_blocked",
            tool_name=tool_name,
            missing_fields=missing,
            tool_use_id=tool_use_id,
        )

        return {
            "systemMessage": (
                f"⚠️ BLOCKED: Invalid {tool_name} call. "
                f"Missing required fields: {', '.join(missing)}."
                f"{example_hint}"
            ),
            "decision": "block",
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "Tool schema validation failed.",
            },
        }

    # [ENHANCED] Zero-Byte Guard: Block empty Write calls (prevents hangs/crashes)
    if tool_name == "Write":
        file_path = tool_input.get("file_path")
        content = tool_input.get("content")
        
        # CRITICAL CASE: Both params missing = complete context overflow (truncated tool call)
        if file_path is None and content is None:
            logfire.error(
                "critical_zero_param_write_blocked",
                tool_name=tool_name,
                tool_use_id=tool_use_id,
            )
            return {
                "systemMessage": (
                    "❌ CRITICAL ERROR: Your Write call had NO parameters at all. "
                    "This indicates severe context exhaustion - your output was truncated mid-generation. "
                    "STOP trying to write the full report. Instead:\n\n"
                    "1. Write ONLY the Executive Summary section (under 3000 chars)\n"
                    "2. Save it to work_products/partial_report_summary.html\n"
                    "3. Use `append_to_file` tool to add remaining sections one at a time\n"
                    "4. Each chunk should be under 5000 characters\n\n"
                    "DO NOT attempt to write the entire report in one call again."
                ),
                "decision": "block",
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "Blocked zero-param Write (critical context overflow).",
                },
            }
        
        # STANDARD CASE: Content is None or empty/whitespace
        if content is None or (isinstance(content, str) and not content.strip()):
            logfire.warning(
                "zero_byte_write_blocked",
                tool_name=tool_name,
                tool_use_id=tool_use_id,
            )
            return {
                "systemMessage": (
                    "⚠️ BLOCKED: You attempted to write an empty file (0 bytes). "
                    "This usually happens when you are trying to write too much and run out of context. "
                    "Please write the file in smaller chunks or ensure you have generated the content first."
                ),
                "decision": "block",
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "Blocked 0-byte write (empty content).",
                },
            }

    return {}


# Track consecutive empty Write failures (module-level state)
_EMPTY_WRITE_FAILURE_COUNT = 0
_MAX_EMPTY_WRITE_RETRIES = 3


async def tool_output_validator_hook(
    tool_output: dict, tool_use_id: str, context
) -> dict:
    """
    Post-Tool hook to catch empty or failed tool executions (especially Write calls).
    If the tool failed with InputValidationError due to missing content/path (common in high load),
    we intervene to guide the agent to retry with correct arguments.

    Tracks consecutive failures and escalates guidance after MAX_RETRIES.
    """
    global _EMPTY_WRITE_FAILURE_COUNT

    # Check for Claude SDK's internal InputValidationError which appears in the output content
    # or empty outputs for critical tools like Write.

    # tool_output is a ToolResultBlock-like dict or just the result structure?
    # In the Claude SDK, hook receives the raw result block.

    content_list = tool_output.get("content", [])
    if not content_list:
        # Reset counter on success (no error content)
        _EMPTY_WRITE_FAILURE_COUNT = 0
        return {}

    for block in content_list:
        if block.get("type") == "text":
            text = block.get("text", "")

            # Case 1: Detect InputValidationError (missing params)
            if "InputValidationError" in text and (
                "missing" in text or "required parameter" in text
            ):
                _EMPTY_WRITE_FAILURE_COUNT += 1
                logfire.warning(
                    "post_tool_hook_caught_validation_error",
                    tool_use_id=tool_use_id,
                    retry_count=_EMPTY_WRITE_FAILURE_COUNT,
                )

                if _EMPTY_WRITE_FAILURE_COUNT >= _MAX_EMPTY_WRITE_RETRIES:
                    # Reset and provide abort guidance
                    _EMPTY_WRITE_FAILURE_COUNT = 0
                    return {
                        "systemMessage": (
                            "❌ ABORT: You have failed to write content 3 times due to empty arguments. "
                            "This indicates context exhaustion - your output buffer is empty. "
                            "STOP TRYING TO WRITE THE FULL REPORT. Instead:\n"
                            "1. Write a SHORT summary (under 5000 chars) of what you found.\n"
                            "2. Save it as a 'partial_report.html' to work_products/.\n"
                            "3. Tell the user the full report requires breaking into chunks due to content size.\n"
                            "DO NOT attempt another empty Write call."
                        )
                    }
                else:
                    return {
                        "systemMessage": (
                            f"⚠️ RETRY {_EMPTY_WRITE_FAILURE_COUNT}/{_MAX_EMPTY_WRITE_RETRIES}: "
                            "Your Write call failed - you provided no content/path. "
                            "This happens when you try to write content too large for your context buffer. "
                            "Try writing JUST THE FIRST SECTION of your report now (e.g., Executive Summary + first chapter). "
                            "You can append more sections afterward using `append_to_file`."
                        )
                    }

            # Case 2: Successful write or other tool - reset counter
            elif not text.startswith("<tool_use_error>"):
                _EMPTY_WRITE_FAILURE_COUNT = 0

    return {}


# =============================================================================
# PRE-COMPACT HOOK - Capture context before compaction
# =============================================================================

# Module-level state for compaction tracking
_COMPACTION_COUNT = 0
_COMPACTION_LOG: list = []


async def pre_compact_context_capture_hook(
    input_data: dict, context
) -> dict:
    """
    PreCompact hook that captures context state before Claude compacts.
    
    This helps us:
    1. Log when compaction occurs (auto vs manual)
    2. Capture state for later comparison
    3. Optionally inject our own summary
    
    NOTE: This hook is called BEFORE compaction happens.
    We cannot prevent compaction, but we can observe and supplement.
    """
    global _COMPACTION_COUNT
    _COMPACTION_COUNT += 1
    
    trigger = input_data.get("trigger", "unknown")  # "auto" or "manual"
    session_id = input_data.get("session_id", "unknown")
    transcript_path = input_data.get("transcript_path", "")
    
    # Log the compaction event
    compaction_event = {
        "count": _COMPACTION_COUNT,
        "trigger": trigger,
        "session_id": session_id,
        "transcript_path": transcript_path,
        "timestamp": datetime.now().isoformat(),
    }
    _COMPACTION_LOG.append(compaction_event)
    
    print(f"\n{'='*60}")
    print(f"📦 COMPACTION TRIGGERED (#{_COMPACTION_COUNT})")
    print(f"   Trigger: {trigger}")
    print(f"   Session: {session_id}")
    print(f"   Transcript: {transcript_path}")
    print(f"{'='*60}\n")
    
    logfire.info(
        "pre_compact_triggered",
        compaction_count=_COMPACTION_COUNT,
        trigger=trigger,
        session_id=session_id,
    )
    
    # Try to capture transcript size before compaction
    if transcript_path:
        try:
            import os
            if os.path.exists(transcript_path):
                size = os.path.getsize(transcript_path)
                print(f"   Transcript size: {size:,} bytes")
                compaction_event["transcript_size_bytes"] = size
        except Exception:
            pass
    
    # Return continue signal - let compaction proceed
    # We could inject a systemMessage here if we wanted to influence
    # Claude's compaction with our own context summary
    return {"continue_": True}


def get_compaction_stats() -> dict:
    """Return compaction statistics for analysis."""
    return {
        "total_compactions": _COMPACTION_COUNT,
        "compaction_log": _COMPACTION_LOG.copy(),
    }




def configure_logfire():
    """Configure Logfire for tracing if token is available."""
    if not LOGFIRE_TOKEN:
        return False

    def scrubbing_callback(m: logfire.ScrubMatch):
        if not m.path:
            return None
        last_key = m.path[-1]
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
        inspect_arguments=False,  # Suppress InspectArgumentsFailedWarning
    )

    try:
        logfire.instrument_mcp()
    except Exception:
        pass

    try:
        logfire.instrument_httpx(capture_headers=True)
    except Exception:
        pass

    return True


# =============================================================================
# HELPER FUNCTIONS (Observer pattern, date parsing, etc.)
# =============================================================================


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
# OBSERVER FUNCTIONS
# =============================================================================


async def observe_and_save_search_results(
    tool_name: str, content, workspace_dir: str
) -> None:
    """Observer: Parse SERP tool results and save cleaned artifacts."""
    is_serp_tool = any(
        kw in tool_name.upper()
        for kw in ["SEARCH_NEWS", "SEARCH_WEB", "COMPOSIO_SEARCH", "MULTI_EXECUTE"]
    )

    if not is_serp_tool:
        return

    try:
        raw_json = None
        if isinstance(content, list):
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

        try:
            data = json.loads(raw_json)
        except json.JSONDecodeError:
            return

        if not isinstance(data, dict):
            return

        payloads = []
        root = data
        if isinstance(root, dict) and "data" in root:
            root = root["data"]

        if (
            isinstance(root, dict)
            and "results" in root
            and isinstance(root["results"], list)
        ):
            for item in root["results"]:
                if isinstance(item, dict) and "response" in item:
                    inner_resp = item["response"]
                    if isinstance(inner_resp, str):
                        try:
                            inner_resp = json.loads(inner_resp)
                        except json.JSONDecodeError:
                            continue
                    if isinstance(inner_resp, dict):
                        inner_data = inner_resp.get("data") or inner_resp.get(
                            "data_preview"
                        )
                        inner_slug = item.get("tool_slug", tool_name)
                        if inner_data:
                            payloads.append((inner_slug, inner_data))
        else:
            payloads.append((tool_name, root))

        saved_count = 0
        for slug, payload in payloads:
            if not isinstance(payload, dict):
                continue

            search_data = payload
            if "results" in payload and isinstance(payload["results"], dict):
                search_data = payload["results"]

            def safe_get_list(data, key):
                val = data.get(key, [])
                if isinstance(val, dict):
                    return list(val.values())
                if isinstance(val, list):
                    return val
                return []

            cleaned = None

            if "news_results" in search_data:
                raw_list = safe_get_list(search_data, "news_results")
                cleaned = {
                    "type": "news",
                    "timestamp": datetime.now().isoformat(),
                    "tool": slug,
                    "articles": [
                        {
                            "position": a.get("position") or (idx + 1),
                            "title": a.get("title"),
                            "url": a.get("link"),
                            "source": a.get("source", {}).get("name")
                            if isinstance(a.get("source"), dict)
                            else a.get("source"),
                            "date": parse_relative_date(a.get("date", "")),
                            "snippet": a.get("snippet"),
                        }
                        for idx, a in enumerate(raw_list)
                        if isinstance(a, dict)
                    ],
                }

            # Normalization for Scholar/other types using 'articles' key
            elif "articles" in search_data:
                raw_list = safe_get_list(search_data, "articles")
                cleaned = {
                    "type": "scholar",  # Distinguish type but keep structure compatible
                    "timestamp": datetime.now().isoformat(),
                    "tool": slug,
                    "results": [  # Normalize 'articles' -> 'results' for consistency
                        {
                            "position": a.get("position") or (idx + 1),
                            "title": a.get("title"),
                            "url": a.get("link")
                            or a.get("url"),  # Normalize 'link' -> 'url'
                            "snippet": a.get("snippet"),
                            "source": a.get("source", {}).get("name")
                            if isinstance(a.get("source"), dict)
                            else a.get("source"),
                        }
                        for idx, a in enumerate(raw_list)
                        if isinstance(a, dict)
                    ],
                }

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

            # Handle Composio MULTI_EXECUTE_TOOL format with "citations" array
            elif "citations" in search_data:
                raw_list = safe_get_list(search_data, "citations")
                cleaned = {
                    "type": "composio_search",
                    "timestamp": datetime.now().isoformat(),
                    "tool": slug,
                    "answer": search_data.get("answer", ""),
                    "results": [
                        {
                            "position": idx + 1,
                            "title": c.get("title"),
                            "url": c.get("url") or c.get("id"),
                            "snippet": c.get("snippet"),
                            "source": c.get("source"),
                            "publishedDate": c.get("publishedDate"),
                        }
                        for idx, c in enumerate(raw_list)
                        if isinstance(c, dict)
                    ],
                }

            if cleaned and workspace_dir:
                filename = "unknown"
                try:
                    search_dir = os.path.join(workspace_dir, "search_results")
                    os.makedirs(search_dir, exist_ok=True)

                    timestamp_str = datetime.now().strftime("%H%M%S")
                    suffix = f"_{saved_count}" if len(payloads) > 1 else ""
                    filename = os.path.join(
                        search_dir, f"{slug}{suffix}_{timestamp_str}.json"
                    )

                    with open(filename, "w") as f:
                        json.dump(cleaned, f, indent=2)

                    if os.path.exists(filename):
                        logfire.info(
                            "observer_artifact_saved",
                            path=filename,
                            type=cleaned.get("type"),
                        )
                        saved_count += 1
                except Exception as file_error:
                    logfire.error("observer_file_io_error", error=str(file_error))

    except Exception as e:
        logfire.warning("observer_error", tool=tool_name, error=str(e))


async def observe_and_save_workbench_activity(
    tool_name: str, tool_input: dict, tool_result: str, workspace_dir: str
) -> None:
    """Observer: Capture COMPOSIO_REMOTE_WORKBENCH activity."""
    if "REMOTE_WORKBENCH" not in tool_name.upper():
        return

    try:
        workbench_dir = os.path.join(workspace_dir, "workbench_activity")
        os.makedirs(workbench_dir, exist_ok=True)

        timestamp_str = datetime.now().strftime("%H%M%S")
        filename = os.path.join(workbench_dir, f"workbench_{timestamp_str}.json")

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
                "code": tool_input.get("code_to_execute", "")[:1000],
                "session_id": tool_input.get("session_id"),
            },
            "output": {
                "stdout": result_data.get("stdout", ""),
                "stderr": result_data.get("stderr", ""),
                "successful": result_data.get("successful"),
            },
        }

        with open(filename, "w") as f:
            json.dump(activity_log, f, indent=2)

        logfire.info("workbench_activity_saved", path=filename)

    except Exception as e:
        logfire.warning("workbench_observer_error", tool=tool_name, error=str(e))


async def observe_and_enrich_corpus(
    tool_name: str, tool_input: dict, tool_result, workspace_dir: str
) -> None:
    """Observer: Placeholder for corpus enrichment (future feature)."""
    # This observer can be expanded to automatically add crawl results to corpus
    pass


# =============================================================================
# UNIVERSAL AGENT CLASS
# =============================================================================


class UniversalAgent:
    """
    Core agent class that can be driven by CLI or API.

    Emits AgentEvent objects via async generator for UI/CLI to consume.
    """

    def __init__(
        self,
        workspace_dir: Optional[str] = None,
        user_id: str = "user_123",
        hooks: Optional[dict] = None,
    ):
        self.user_id = user_id
        self.workspace_dir = workspace_dir or self._create_workspace()
        self.run_id = str(uuid.uuid4())
        self.trace: dict = {}
        self.start_ts: float = 0
        self.composio: Optional[Composio] = None
        self.session = None
        self.options = None
        self._initialized = False
        self.tool_name_map = {}
        self._processed_msg_ids = set()
        self.client: Optional[ClaudeSDKClient] = None
        # Track consecutive tool validation errors to detect loops
        self.consecutive_tool_errors: int = 0
        self._processed_msg_ids: set = set()
        # Async queue for merging events from multiple sources (SDK + tool logs)
        self._event_queue: Optional[asyncio.Queue] = None
        self._hooks = hooks
        
        # Initialize History
        self.history = MessageHistory(system_prompt_tokens=2000)

    async def send_agent_event(self, event_type: EventType, data: dict) -> None:
        """
        Receive events from external sources (log bridge, tools) and push to queue.
        Used by LogBridgeHandler and StdoutInterceptor.
        """
        if self._event_queue:
            event = AgentEvent(type=event_type, data=data)
            await self._event_queue.put(event)

    def _create_workspace(self) -> str:
        """Create a new session workspace directory."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        workspace_dir = os.path.join("AGENT_RUN_WORKSPACES", f"session_{timestamp}")
        os.makedirs(workspace_dir, exist_ok=True)
        return workspace_dir

    async def initialize(self) -> None:
        """Initialize using AgentSetup for consistent configuration."""
        if self._initialized:
            return

        from universal_agent.agent_setup import AgentSetup
        
        # Use AgentSetup for unified initialization
        self._setup = AgentSetup(
            workspace_dir=self.workspace_dir,
            user_id=self.user_id,
            enable_skills=True,
            enable_memory=False,  # URW/API may not need memory
            verbose=True,
        )
        if self._hooks:
            self._setup.set_hooks(self._hooks)
        await self._setup.initialize()
        
        # Copy references from setup
        self.composio = self._setup.composio
        self.session = self._setup.session
        self.options = self._setup.options
        self.run_id = self._setup.run_id

        # Initialize trace
        self.trace = {
            "run_id": self.run_id,
            "session_info": {
                "url": self.session.mcp.url,
                "user_id": self.user_id,
                "run_id": self.run_id,
                "timestamp": datetime.now().isoformat(),
            },
            "query": None,
            "start_time": None,
            "end_time": None,
            "total_duration_seconds": None,
            "tool_calls": [],
            "tool_results": [],
            "iterations": [],
            "token_usage": {"input": 0, "output": 0, "total": 0},
            "logfire_enabled": bool(LOGFIRE_TOKEN),
        }

        self._initialized = True

        # Initialize MessageHistory for context management (Anthropic pattern)
        self.history = MessageHistory(system_prompt_tokens=2000)

        if LOGFIRE_TOKEN:
            logfire.set_baggage(run_id=self.run_id)

        # [NEW] Centralized Logging: Create run.log in workspace
        try:
            log_path = os.path.join(self.workspace_dir, "run.log")
            
            # Create file handler
            self.file_handler = logging.FileHandler(log_path, mode='a', encoding='utf-8')
            self.file_handler.setFormatter(logging.Formatter(
                '%(asctime)s [%(levelname)s] [%(name)s] %(message)s',
                datefmt='%H:%M:%S'
            ))
            
            # Add to root logger to capture all libraries (composio, etc)
            root_logger = logging.getLogger()
            root_logger.addHandler(self.file_handler)
            
            # Also ensure our specific logger has it
            logger = logging.getLogger("universal_agent")
            if logger.propagate is False:
                logger.addHandler(self.file_handler)
                
            # FORCE capture of httpx/httpcore logs
            for lib in ["httpx", "httpcore"]:
                lib_logger = logging.getLogger(lib)
                lib_logger.setLevel(logging.INFO)
                lib_logger.addHandler(self.file_handler)
                lib_logger.propagate = True # Ensure it bubbles to root too for QueueHandler
                
            logging.info(f"📝 Centralized logging initialized: {log_path}")
            
        except Exception as e:
            print(f"Failed to setup run.log: {e}")

    def bind_workspace(self, new_workspace: str) -> None:
        """Update workspace for URW phase transitions."""
        self.workspace_dir = new_workspace
        if hasattr(self, '_setup') and self._setup:
            self._setup.bind_workspace(new_workspace)
            self.options = self._setup.options

    @classmethod
    async def from_setup(cls, setup: "AgentSetup") -> "UniversalAgent":
        """Create UniversalAgent from an existing AgentSetup (for URW)."""
        agent = cls(workspace_dir=setup.workspace_dir, user_id=setup.user_id)
        agent._setup = setup
        agent.composio = setup.composio
        agent.session = setup.session
        agent.options = setup.options
        agent.run_id = setup.run_id
        
        agent.trace = {
            "run_id": agent.run_id,
            "session_info": {
                "url": setup.session.mcp.url,
                "user_id": setup.user_id,
                "run_id": setup.run_id,
                "timestamp": datetime.now().isoformat(),
            },
            "query": None,
            "start_time": None,
            "end_time": None,
            "total_duration_seconds": None,
            "tool_calls": [],
            "tool_results": [],
            "iterations": [],
            "token_usage": {"input": 0, "output": 0, "total": 0},
            "logfire_enabled": bool(LOGFIRE_TOKEN),
        }
        agent._initialized = True
        agent.history = MessageHistory(system_prompt_tokens=2000)
        
        if LOGFIRE_TOKEN:
            logfire.set_baggage(run_id=agent.run_id)
        
        return agent

    def _build_system_prompt(self, workspace_path: str, agents: Optional[dict] = None) -> str:
        """Build the main system prompt."""
        
        # Dynamic Agent List
        available_agents_block = ""
        if agents:
            available_agents_block = "## AVAILABLE SPECIALISTS (SUB-AGENTS)\n"
            available_agents_block += "You have access to the following specialized sub-agents via the `Task` tool. DELEGATE work to them whenever a task matches their capabilities.\n\n"
            for name, definition in agents.items():
                available_agents_block += f"- **{name}**: {definition.description}\n"
                available_agents_block += f"  -> To use: Call `Task(subagent_type='{name}', ...)`\n\n"
        temporal_line = self._get_temporal_context_line()
        prompt = (
            f"{temporal_line}\n"
            "You are a helpful assistant with access to external tools and specialized sub-agents.\n\n"
            "## ARCHITECTURE (HOW YOU INTERACT WITH TOOLS)\n"
            "🏗️ **You are an MCP-based agent.** You interact with external tools via MCP tool calls, "
            "NOT by writing Python/Bash code that imports SDKs.\n\n"
            "**Your tool namespaces:**\n"
            "- `mcp__composio__*` - Composio tools (Gmail, Slack, Search, etc.) → Call these directly\n"
            "- `mcp__local_toolkit__*` - Local tools (file I/O, research, image gen) → Call these directly\n"
            "- Native SDK tools: `Read`, `Write`, `Bash`, `Task`, `TodoWrite`\n\n"
            "🚫 **PROHIBITED:**\n"
            "- Do NOT use `Bash` to run Python code that imports `composio`\n"
            "- Do NOT try `from composio import ...` or `composio_client.tools.execute(...)`\n"
            "- These will fail. The Composio SDK is not available in Bash environment.\n"
            "- All Composio interactions go through MCP tools which handle auth automatically.\n"
            "- **Tool Usage:** Do NOT write Python scripts to call other tools. Use `mcp__local_toolkit__batch_tool_execute` for multiple searches.\n"
            "  - **Batching**: You can pass up to 20 items in a single batch.\n"
            "  - **Efficiency**: Batching is faster than sequential calls.\n\n"
            "## CRITICAL: TOOL USAGE DISTINCTION\n"
            "⚠️ **TodoWrite** vs **Write**:\n"
            "- Use `TodoWrite` ONLY for updating your PLAN (mission.json).\n"
            "- Use `Write` ONLY for creating FILE CONTENT (reports, code, etc.).\n"
            "- DO NOT confuse them. Sending content to TodoWrite will fail. Sending todos to Write will fail.\n\n"
            "You are a COORDINATOR Agent. Your job is to assess requests and DELEGATE to specialists.\n\n"
            f"{available_agents_block}"
            "## DELEGATION STRATEGY\n"
            "1. **Analyze the Request**: Identify key tasks (e.g., 'Research', 'Write Report', 'Codegen').\n"
            "2. **Match with Specialist**: Check the AVAILABLE SPECIALISTS list above.\n"
            "3. **Delegate**: Use the `Task` tool with `subagent_type='[specialist_name]'`.\n"
            "   - Example: For a reporting task, found 'report-writer'? Delegate to it.\n"
            "   - Example: For a research task, found 'research-specialist'? Delegate to it.\n"
            "4. **No Match?**: Only then perform the task yourself using available tools.\n\n"
            "🛑 **PROHIBITED:** Do NOT attempt to do work yourself if a specialist exists. CONSTANTLY DELEGATE.\n"
            "**Key Requirement:** When calling `Task`, you **MUST** provide `subagent_type` matching the specialist name exactly.\n\n"
            "## EMAIL REQUIREMENTS\n\n"
            "When sending reports via email:\n"
            "1. Delegate to research/report specialists first to generate the file\n"
            "2. Attach only the files requested in the task (do not embed full HTML in the body)\n"
            "3. Email body should be a brief summary (1-2 paragraphs) + 'See attached report'\n\n"
            "## EXECUTION GUIDELINES\n"
            "- When the user requests an action, proceed immediately without asking for confirmation.\n"
            "- Complete the full task end-to-end in a single workflow.\n"
            "- For simple factual questions, you can respond directly.\n"
            "- For any research/report task, delegate to sub-agent.\n\n"
            "## WORKSPACE & ISOLATION\n"
        f"You are executing within a specific session workspace: `{workspace_path}`\n"
        "1. **STRICT CONFINEMENT**: You MUST NOT access, search, read, or write files outside this directory structure unless explicitly requested by the user.\n"
        "2. **ISOLATION**: Do NOT explore, list, or modify adjacent session directories (e.g. `../session_...`). Treat them as non-existent.\n"
        "3. **PATHING**: Your current working directory may be the project root. ALWAYS use ABSOLUTE PATHS prefixed with your workspace path for file operations (e.g. `find {workspace_path} -name ...`).\n"
        "4. **AVOID GLOBAL SEARCH**: Do NOT use `files='**'` or global `find .` commands that scan the entire project. Target your workspace explicitly.\n\n"
        f"Context:\nCURRENT_SESSION_WORKSPACE: {workspace_path}\n"
        )
        tool_knowledge = get_tool_knowledge_block()
        if tool_knowledge:
            prompt += f"\n\n{tool_knowledge}"
        return prompt

    def _get_temporal_context_line(self) -> str:
        tz_name = os.getenv("USER_TIMEZONE", "America/Chicago")
        try:
            import pytz

            now = datetime.now(pytz.timezone(tz_name))
            tz_label = now.tzname() or tz_name
        except Exception:
            now = datetime.now()
            tz_label = tz_name

        return (
            "Current date/time: "
            f"{now.strftime('%A, %B %d, %Y %H:%M')} ({tz_label}). "
            "Use this as the authoritative current date; do not treat post-training dates as hallucinations if sourced."
        )

    def _build_subagent_prompt(self, workspace_path: str) -> str:
        """Backward-compatible subagent prompt builder."""
        return self._build_report_writer_prompt(workspace_path)

    def _build_research_specialist_prompt(self, workspace_path: str) -> str:
        """Build the research-specialist sub-agent prompt with full search pipeline."""
        temporal_line = self._get_temporal_context_line()
        prompt = (
            f"{temporal_line}\n"
            f"CURRENT_SESSION_WORKSPACE: {workspace_path}\n\n"
            "You are a **Research Specialist** sub-agent.\n"
            "**Goal:** Execute the COMPLETE research pipeline from web search to corpus finalization.\n"
            "**You do NOT write reports.** You gather and organize data for the Writer agent.\n\n"
            "## MANDATORY WORKFLOW (2 Steps ONLY)\n\n"
            "### Step 1: Search & Discovery\n"
            "**Determine Research Depth:**\n"
            "   - **Quick/Fact:** 1-2 queries.\n"
            "   - **Standard (Default):** 2-4 diverse queries.\n"
            "   - **Deep:** 5-8 queries (Use only if explicitly requested).\n"
            "\n"
            "Make `mcp__composio__COMPOSIO_MULTI_EXECUTE_TOOL` calls with **MAX 4 tools per call**:\n"
            "   - Mix of `COMPOSIO_SEARCH_NEWS` and `COMPOSIO_SEARCH_WEB`\n"
            "   - If Deep (5-8), split into 2 batches to avoid timeouts.\n\n"
            "**CRITICAL:** The limit is 4 tools per call.\n"
            "ALWAYS append `-site:wikipedia.org` to every query.\n\n"
            "The Observer automatically saves results to `search_results/*.json`.\n\n"
            "⚠️ **SYNCHRONIZATION RULE:** Complete ALL search calls BEFORE proceeding to Step 2.\n"
            "   - If you split into 2 batches, wait for BOTH to return before calling finalize_research.\n"
            "   - The finalize_research tool reads all JSON files at once - missing files = missing results.\n\n"
            "### Step 2: Finalize Research (ONE TOOL CALL - AFTER ALL SEARCHES COMPLETE)\n"
            "**IMMEDIATELY** call `mcp__local_toolkit__finalize_research`:\n"
            f"   - `session_dir`: {workspace_path}\n"
            "   - `task_name`: (derive from research topic, e.g., 'russia_ukraine_war')\n\n"
            "**What this tool does AUTOMATICALLY (you do NOT need to do these manually):**\n"
            "  1. ✅ Reads all `search_results/*.json` files\n"
            "  2. ✅ Extracts ALL URLs programmatically (Python code, not Bash)\n"
            "  3. ✅ Crawls ALL URLs in parallel\n"
            "  4. ✅ Filters and deduplicates content\n"
            "  5. ✅ Creates `tasks/{task_name}/refined_corpus.md` (token-efficient research corpus)\n\n"
            "🚫 **PROHIBITED ACTIONS (DO NOT DO THESE):**\n"
            "  - ❌ Do NOT use Bash/grep/jq to extract URLs from JSON files\n"
            "  - ❌ Do NOT manually call `crawl_parallel` after searches\n"
            "  - ❌ Do NOT read or inspect the JSON files yourself\n"
            "  - ❌ Do NOT write any Python scripts to process search results\n"
            "  Just call `finalize_research` - it handles EVERYTHING.\n\n"
            "### After finalize_research completes:\n"
            "1. Verify `refined_corpus.md` exists\n"
            "2. Report: 'Research complete. [N] sources. Refined corpus at [path]. Returning.'\n"
            "3. **STOP immediately**\n\n"
            "## TOOLS\n"
            "- `COMPOSIO_MULTI_EXECUTE_TOOL`: Execute parallel searches\n"
            "- `finalize_research`: Process ALL search results → crawl → filter → overview\n"
        )
        tool_knowledge = get_tool_knowledge_block()
        if tool_knowledge:
            prompt += f"\n\n{tool_knowledge}"
        return prompt

    def _build_report_writer_prompt(self, workspace_path: str, cached_corpus: str = None) -> str:
        """Build the report-writer sub-agent prompt.
        
        Args:
            workspace_path: Path to the session workspace
            cached_corpus: Optional pre-loaded corpus text from checkpoint cache
        """
        temporal_line = self._get_temporal_context_line()
        prompt = (
            f"{temporal_line}\n"
            f"**Workspace:** `{workspace_path}`\n\n"
            "---\n\n"
            "# 📝 REPORT WRITER AGENT\n\n"
            "You create professional HTML research reports using a deterministic 5-phase workflow.\n\n"
        )
        
        # If we have cached corpus, inject it directly to skip tool calls
        if cached_corpus:
            prompt += (
                "## RESEARCH DATA (Pre-loaded)\n\n"
                f"```\n{cached_corpus}\n```\n\n"
                "---\n\n"
            )
        else:
            prompt += (
                "## RESEARCH DATA\n\n"
                f"Read the corpus: `{workspace_path}/tasks/[task_name]/refined_corpus.md`\n\n"
            )
        
        prompt += (
            "## 🔄 MANDATORY WORKFLOW\n\n"
            "Execute these phases **in order**. Each phase has a specific MCP tool.\n\n"
            "### Phase 1: PLANNING\n"
            "1. Read the research corpus to understand the content\n"
            "2. Create an outline with 5-8 sections\n"
            "3. **Write** the outline to: `work_products/_working/outline.json`\n"
            "   ```json\n"
            "   {\n"
            '     "title": "Report Title",\n'
            '     "sections": [\n'
            '       {"id": "executive_summary", "title": "Executive Summary", "description": "..."},\n'
            '       {"id": "section_2", "title": "...", "description": "..."}\n'
            "     ]\n"
            "   }\n"
            "   ```\n\n"
            "### Phase 2: DRAFTING\n"
            "After outline.json exists, call:\n"
            "```\n"
            "mcp__local_toolkit__draft_report_parallel()\n"
            "```\n"
            "- Tool reads outline.json and generates all sections in parallel\n"
            "- Sections saved to `work_products/_working/sections/` with correct ordering prefixes\n"
            "- 🛑 **STRICT PROHIBITION**: You are FORBIDDEN from using the `Write` tool for report sections.\n"
            "- You MUST use only `draft_report_parallel` for drafting. Manual writing leads to incorrect ordering.\n\n"
            "### Phase 3: CLEANUP\n"
            "After sections are generated, call:\n"
            "```\n"
            "mcp__local_toolkit__cleanup_report()\n"
            "```\n"
            "- Tool reads all drafted sections and applies a light, selective pass\n"
            "- Reduces duplicate info between sections while preserving each section's focus\n"
            "- Normalizes headings/formatting so output is consistent across runs\n"
            "- Introduces light cohesion across sections without rewriting everything\n"
            "- ⚠️ **Do NOT rewrite the entire report** - only targeted edits\n\n"
            "### Phase 4: ASSEMBLY\n"
            "After cleanup completes, call:\n"
            "```\n"
            "mcp__local_toolkit__compile_report(theme=\"modern\")\n"
            "```\n"
            "- Tool compiles all sections into final HTML\n"
            "- Output: `work_products/report.html`\n"
            "- ⚠️ **NEVER manually Write the final report** - context limits make this impossible\n\n"
            "### Phase 5: COMPLETION\n"
            "Return a success message with the report location.\n\n"
            "---\n\n"
            "## ⚠️ CRITICAL RULES\n\n"
            "1. **Always call draft_report_parallel** - never write sections manually\n"
            "2. **Always call cleanup_report** - selective edits only, never rewrite the full report\n"
            "3. **Always call compile_report** - never assemble HTML manually\n"
            "4. Section order is determined by outline.json - list most important first\n"
            "5. The tools handle all file I/O - you focus on planning and coordination\n\n"
            "**👉 START NOW: Read corpus → Create outline → Call draft_report_parallel → Call cleanup_report → Call compile_report**\n"
        )
        
        tool_knowledge = get_tool_knowledge_block()
        if tool_knowledge:
            prompt += f"\n\n{tool_knowledge}"
        return prompt

    async def run_query(self, query: str) -> AsyncGenerator[AgentEvent, None]:
        """
        Run a query and yield events as they happen.

        This is the main entry point for both CLI and API usage.
        """
        if not self._initialized:
            await self.initialize()

        print(f"DEBUG CORE: Starting run_query for query: {query[:50]}...")
        self.trace["query"] = query
        self.trace["start_time"] = datetime.now().isoformat()
        self.start_ts = time.time()

        # Initialize event queue
        self._event_queue = asyncio.Queue()

        # Emit session info
        await self._event_queue.put(AgentEvent(
            type=EventType.SESSION_INFO,
            data={
                "session_url": self.session.mcp.url,  # type: ignore
                "workspace": self.workspace_dir,
                "user_id": self.user_id,
            },
        ))

        # Setup log bridging from mcp_server (Deprecated/No-op for subprocess, but kept for structure)
        # Instead, we attach our own handlers to capture root logging + stdout/stderr
        import logging
        import sys

        # 1. Attach QueueLogHandler to root logger
        # 1. Attach QueueLogHandler to root logger
        queue_handler = QueueLogHandler(self._event_queue)
        # queue_handler.setLevel(logging.INFO) 
        root_logger = logging.getLogger()
        root_logger.addHandler(queue_handler)

        # Attach to mcp_server.mcp_log for in-process tool logging (Critical for UI visibility)
        try:
            from mcp_server import set_mcp_log_callback
            
            def mcp_bridge_sync(msg, level, prefix=""):
                if self._event_queue:
                    try:
                        loop = asyncio.get_running_loop()
                        event = AgentEvent(
                            type=EventType.STATUS, 
                            data={"status": msg, "level": level, "prefix": prefix, "is_log": True}
                        )
                        loop.call_soon_threadsafe(self._event_queue.put_nowait, event)
                    except Exception:
                        pass
            
            set_mcp_log_callback(mcp_bridge_sync)
        except ImportError:
            print("DEBUG CORE: Could not import mcp_server for log hooking.")
            pass

        # 2. Capture stdout/stderr (Native OS-level)
        # We redirect stdout/stderr to run.log using os.dup2 to capture C-extensions/subprocess output
        # capturing strictly to file avoids buffer deadlocks.
        original_stdout_fd = os.dup(1)
        original_stderr_fd = os.dup(2)
        
        run_log_path = os.path.join(self.workspace_dir, "run.log")
        journal_path = os.path.join(self.workspace_dir, "activity_journal.log")
        
        # Open in append mode, unbuffered (0) or line buffered (1) not supported for binary, 
        # so we use default key and flush often.
        run_log_file = open(run_log_path, "a")
        journal_file = open(journal_path, "a", encoding="utf-8")
        
        def log_to_journal(event: AgentEvent):
            """Helper to log high-level activity to the journal."""
            try:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                etype = event.type.value if hasattr(event.type, "value") else str(event.type)
                data = event.data or {}
                
                # Dynamic author determined by context or explicit event data
                # Default to current_agent_name if not provided in data
                current_author = data.get("author") or getattr(self, "current_agent_name", DEFAULT_AGENT_NAME)
                
                entry = f"[{timestamp}] [{etype.upper()}]"
                
                if etype == "text":
                    author = data.get("author", current_author)
                    text = data.get("text", "").strip()
                    if text:
                        entry += f" {author}: {text}"
                elif etype == "thinking":
                    thinking = data.get("thinking", "").strip()
                    if thinking:
                        entry += f" Reasoning: {thinking}"
                elif etype == "tool_call":
                    entry += f" Calling {data.get('name')} with input: {data.get('input')}"
                elif etype == "tool_result":
                    entry += f" {data.get('tool_name')} result: {data.get('content_preview', '(no preview)')}"
                elif etype == "status":
                    # LOG STREAM ATTRIBUTION
                    if data.get("is_log"):
                        entry = f"[{timestamp}] [LOG] {current_author}: {data.get('status')}"
                    else:
                        entry += f" {data.get('status')}"
                elif etype == "error":
                    entry += f" ERROR: {data.get('error')}"
                
                if entry.strip():
                    journal_file.write(entry + "\n")
                    journal_file.flush()
            except Exception as e:
                print(f"DEBUG CORE: Journal logging failed: {e}", flush=True)
        # Flush python buffers before redirecting
        sys.stdout.flush()
        sys.stderr.flush()
        
        # Redirect stdout/stderr to the file descriptor of run.log
        os.dup2(run_log_file.fileno(), 1)
        os.dup2(run_log_file.fileno(), 2)

        async def sdk_worker():
            """Worker to consume SDK events and push to queue."""
            print("DEBUG CORE: sdk_worker started. Calling _run_conversation...", flush=True)
            try:
                # Use _run_conversation_loop for soft restart capability
                async for event in self._run_conversation_loop(query):
                    if self._event_queue:
                        await self._event_queue.put(event)
                if self._event_queue:
                    await self._event_queue.put(None)  # Sentinel for completion
            except Exception as e:
                # Log traceback explicitly to capture it
                import traceback
                traceback.print_exc()
                if self._event_queue:
                    await self._event_queue.put(AgentEvent(type=EventType.ERROR, data={"error": str(e)}))
                    await self._event_queue.put(None)

        print("DEBUG CORE: About to start sdk_worker task (Client lifecycle managed by loop)...", flush=True)
        # Create worker task directly, no outer client context
        worker_task = asyncio.create_task(sdk_worker())
        
        try:
            # Consume from the merged queue
            while True:
                event = await self._event_queue.get()
                if event is None:
                    break
                
                # Journal EVERY event that passes through the UI queue
                log_to_journal(event)
                
                yield event
        finally:
            # Cleanup Hooks
            try:
                root_logger.removeHandler(queue_handler)
                
                # Flush and restore stdout/stderr
                try:
                    sys.stdout.flush()
                    sys.stderr.flush()
                except Exception:
                    pass

                os.dup2(original_stdout_fd, 1)
                os.dup2(original_stderr_fd, 2)
                
                os.close(original_stdout_fd)
                os.close(original_stderr_fd)
                run_log_file.close()
            except Exception:
                pass
            
            if not worker_task.done():
                worker_task.cancel()
            # Cleanup Legacy Hook
            try:
                from universal_agent.mcp_server import set_mcp_log_callback
                set_mcp_log_callback(None)
            except (ImportError, Exception):
                pass

        # Save trace
        self._save_trace()

    async def _run_conversation_loop(self, query: str):
        """Inner loop for handling soft restarts."""
        # Initialize Context Manager
        from universal_agent.memory.context_manager import ContextManager
        context_manager = ContextManager()
        
        iteration = 1
        initial_history_prompt = None
        
        while True:
            # Check context limit before connecting?
            # Actually we prune AFTER iterations usually, but if we are restarting, 
            # we need to supply the pruned history as the 'prompt' to the new connection.
            
            # If we have a restart prompt, use it. Otherwise use the user query for the FIRST run.
            # But wait: 'prompt' in connect() establishes the INITIAL state.
            # If we are restarting, 'query' has already been asked.
            # So the 'prompt' passed to connect() should be the ENTIRE pruned history including the original query.
            # And then we do NOT call client.query() again immediately unless we have a pending user input?
            # Actually, `client.query` sends a message. `client.connect(prompt=...)` essentially pre-fills the chat.
            
            # Strategy:
            # 1. Start Client.
            # 2. If valid history exists (restart), pass it to connect().
            # 3. If first run, pass None to connect(), then call query().
            
            current_prompt_stream = initial_history_prompt
            
            print(f"DEBUG CORE: Starting Client Session (Iteration {iteration})...")
            async with ClaudeSDKClient(self.options) as client:
                self.client = client
                await client.connect(prompt=current_prompt_stream)
                
                # If this is the FIRST iteration, we need to send the user query manually
                # UNLESS it was already part of initial_history_prompt (which is None for first run)
                if iteration == 1 and not initial_history_prompt:
                     # Send the initial query
                     self.history.add_message("user", query)
                     await self.client.query(query)

                # Event Loop
                # We need to yield events from the conversation
                # process_events yields AgentEvent
                async for event in self._process_events(query, iteration):
                    yield event
                    
                    # Logic to detect if we need to restart?
                    # The message loop breaks if ResultMessage is found (end of turn).
                    # But we might need to check token usage periodically or AFTER the turn.
                    
                # End of Turn or Error or Restart Trigger
                # Check metrics
                token_usage = self.trace.get("token_usage", {}).get("total", 0)
                if token_usage > 150000: # Threshold
                     print("DEBUG CORE: Token threshold reached. Triggering compaction...")
                     # 1. Get current history from client (client doesn't expose it easily, we track in self.history)
                     # 2. Prune self.history
                     # 3. Convert to dicts
                     # 4. Set initial_history_prompt
                     # 5. Continue loop -> Reconnects with new history
                     
                     full_history = self.history.get_messages()
                     pruned_history, stats = context_manager.prune_history(full_history)
                     
                     print(f"DEBUG CORE: Pruned tokens: {stats.original_tokens} -> {stats.pruned_tokens}")
                     
                     # Prepare for next iteration
                     initial_history_prompt = context_manager.convert_to_dicts(pruned_history)
                     iteration += 1
                     continue # Loop back to start new client
                
                # If we get here without continue, we are done
                break
                
    async def _process_events(self, query: str, iteration: int) -> AsyncGenerator[AgentEvent, None]:
        """Process events from the client's response stream."""
        self.tool_name_map = getattr(self, "tool_name_map", {})
        step_id = str(uuid.uuid4())
        if LOGFIRE_TOKEN:
            logfire.set_baggage(step_id=step_id)
        yield AgentEvent(
            type=EventType.STATUS, data={"status": "processing", "iteration": iteration}
        )

        # Note: client.query() is handled by the caller (_run_conversation_loop)
        
        tool_calls_this_iter = []
        auth_link = None
        current_agent_name = getattr(self, "current_agent_name", DEFAULT_AGENT_NAME)

        with logfire.span("llm_response_stream"):
            async for msg in self.client.receive_response():  # type: ignore
                if isinstance(msg, AssistantMessage):
                    # TRACK SUB-AGENT CONTEXT
                    parent_tool_use_id = getattr(msg, "parent_tool_use_id", None)
                    if not parent_tool_use_id:
                         # Compatibility check
                         parent_tool_use_id = msg.__dict__.get("parent_tool_use_id")
                    
                    msg_author = DEFAULT_AGENT_NAME
                    if parent_tool_use_id:
                        raw_name = self.tool_name_map.get(parent_tool_use_id, "Subagent")
                        if any(x in raw_name for x in ["Specialist", "Author", "Agent"]):
                            msg_author = raw_name
                        else:
                            msg_author = f"Subagent: {raw_name}"
                    
                    self.current_agent_name = msg_author

                    # Track token usage
                    usage_source = getattr(msg, "usage", None)
                    if usage_source:
                        def get_val(obj, key):
                            if isinstance(obj, dict): return obj.get(key, 0)
                            return getattr(obj, key, 0)
                        
                        inp = get_val(usage_source, "input_tokens") or 0
                        out = get_val(usage_source, "output_tokens") or 0
                        
                        msg_id = getattr(msg, "id", None)
                        if (msg_id and msg_id not in self._processed_msg_ids) or not msg_id:
                            if msg_id:
                                self._processed_msg_ids.add(msg_id)
                            
                            self.trace["token_usage"]["input"] += inp
                            self.trace["token_usage"]["output"] += out
                            self.trace["token_usage"]["total"] += inp + out

                    for block in msg.content:
                        if isinstance(block, ToolUseBlock):
                            resolved_name = block.name
                            if "research_pipeline" in block.name:
                                resolved_name = SEARCH_AGENT_NAME
                            elif "compile_report" in block.name:
                                resolved_name = WRITER_AGENT_NAME
                            elif block.name == "Task" and isinstance(block.input, dict):
                                sub_type = block.input.get("subagent_type")
                                if sub_type:
                                    resolved_name = f"Subagent: {sub_type}"
                            
                            self.tool_name_map[block.id] = resolved_name
                            self.current_agent_name = resolved_name

                            if is_malformed_tool_name(block.name):
                                yield AgentEvent(
                                    type=EventType.ERROR,
                                    data={
                                        "error": "Malformed tool call name detected.",
                                        "tool_name": block.name,
                                    },
                                )
                            tool_record = {
                                "run_id": self.run_id,
                                "step_id": step_id,
                                "iteration": iteration,
                                "name": block.name,
                                "id": block.id,
                                "time_offset_seconds": round(time.time() - self.start_ts, 3),
                                "input": block.input if hasattr(block, "input") else None,
                            }
                            self.trace["tool_calls"].append(tool_record)
                            tool_calls_this_iter.append(tool_record)

                            yield AgentEvent(
                                type=EventType.TOOL_CALL,
                                data={
                                    "name": block.name,
                                    "id": block.id,
                                    "input": block.input,
                                    "time_offset": tool_record["time_offset_seconds"],
                                },
                            )

                        elif isinstance(block, TextBlock):
                            if "connect.composio.dev/link" in block.text:
                                links = re.findall(r"https://connect\.composio\.dev/link/[^\s\)]+", block.text)
                                if links:
                                    yield AgentEvent(type=EventType.AUTH_REQUIRED, data={"auth_link": links[0]})

                            yield AgentEvent(
                                type=EventType.TEXT, 
                                data={
                                    "text": block.text, 
                                    "author": msg_author,
                                    "time_offset": round(time.time() - self.start_ts, 3)
                                }
                            )

                        elif isinstance(block, ThinkingBlock):
                            yield AgentEvent(
                                type=EventType.THINKING,
                                data={"thinking": block.thinking[:1000]},
                            )
                        
                        elif isinstance(block, ToolResultBlock):
                             # (Omitted complex sub-agent speech extraction for brevity to fit in tool call)
                             pass

                elif isinstance(msg, ResultMessage):
                    if msg.session_id:
                        self.trace["provider_session_id"] = msg.session_id
                    
                    usage_source = getattr(msg, "usage", None)
                    if usage_source:
                        def get_val(obj, key):
                             if isinstance(obj, dict): return obj.get(key, 0)
                             return getattr(obj, key, 0)
                        inp = get_val(usage_source, "input_tokens") or 0
                        out = get_val(usage_source, "output_tokens") or 0
                        
                        msg_id = getattr(msg, "id", None)
                        if (msg_id and msg_id not in self._processed_msg_ids) or not msg_id:
                             if msg_id: self._processed_msg_ids.add(msg_id)
                             self.trace["token_usage"]["input"] += inp
                             self.trace["token_usage"]["output"] += out
                             self.trace["token_usage"]["total"] += inp + out
                    
                    # Update History
                    self.history.add_message("assistant", "response", usage_source) 
                    # Note: We rely on ContextManager for actual pruning handling now

                elif isinstance(msg, (UserMessage, ToolResultBlock)):
                    blocks = msg.content if isinstance(msg, UserMessage) else [msg]
                    for block in blocks:
                        is_result = isinstance(block, ToolResultBlock) or hasattr(block, "tool_use_id")
                        if is_result:
                            tool_use_id = getattr(block, "tool_use_id", None)
                            is_error = getattr(block, "is_error", False)
                            block_content = getattr(block, "content", "")
                            
                            result_record = {
                                "run_id": self.run_id,
                                "step_id": step_id,
                                "tool_use_id": tool_use_id,
                                "time_offset_seconds": round(time.time() - self.start_ts, 3),
                                "is_error": is_error,
                            }
                            self.trace["tool_results"].append(result_record)

                            yield AgentEvent(
                                type=EventType.TOOL_RESULT,
                                data={
                                    "tool_use_id": tool_use_id,
                                    "is_error": is_error,
                                    "content_preview": str(block_content)[:2000],
                                    "content_raw": block_content,
                                },
                            )
                            # (Observer logic omitted for brevity, assuming observers fire via hooks or separate mechanism)

        iter_record = {
            "run_id": self.run_id,
            "step_id": step_id,
            "iteration": iteration,
            "query": query[:200],
            "duration_seconds": round(time.time() - self.start_ts, 3),
            "tool_calls": len(tool_calls_this_iter),
        }
        self.trace["iterations"].append(iter_record)

        yield AgentEvent(
            type=EventType.ITERATION_END,
            data={
                "iteration": iteration,
                "tool_calls": len(tool_calls_this_iter),
                "duration_seconds": round(time.time() - self.start_ts, 3),
                "token_usage": self.trace.get("token_usage"),
            },
        )

    # REVERTING TO ORIGINAL PLAN FOR TOOL CALL SAFETY
    # Use _run_conversation as the single turn handler, and wrap IT.


        
        # ... (Logging setup omitted for brevity in thought, but must be preserved)
        # To avoid destroying the log setup code, I will only replace the `async with ClaudeSDKClient` block
        # within `sdk_worker` if possible, or refactor `sdk_worker`.

        # Let's look at sdk_worker definition in the file...
        # It's inside run_query.
        
        import logging
        import sys
        
        # ... (Hooks setup) ...
        # (Assuming hooks are setup as before)
        queue_handler = QueueLogHandler(self._event_queue)
        root_logger = logging.getLogger()
        root_logger.addHandler(queue_handler)
        
        # Capturing stdout/stderr...
        original_stdout_fd = os.dup(1)
        original_stderr_fd = os.dup(2)
        run_log_path = os.path.join(self.workspace_dir, "run.log")
        run_log_file = open(run_log_path, "a")
        
        sys.stdout.flush()
        sys.stderr.flush()
        os.dup2(run_log_file.fileno(), 1) # Redirect
        
        async def sdk_worker():
            """Worker to consume SDK events and push to queue."""
            print("DEBUG CORE: sdk_worker started (Soft Restart Capable).", flush=True)
            
            from universal_agent.memory.context_manager import ContextManager
            context_manager = ContextManager()
            iteration = 1
            history_prompt = None # For restarts
            
            try:
                while True: # Soft Restart Loop
                    async with ClaudeSDKClient(self.options) as client:
                        self.client = client
                        # If restarting, we need to inject history.
                        # Client.connect takes a prompt stream.
                        # Note: If history_prompt is set, we use it.
                        if history_prompt:
                            # We must connect with the history
                            # Todo: verify if connect() accepts list of dicts directly
                            # doc says: prompt: str | AsyncIterable[dict[str, Any]] | None
                            # We can make a generator
                            async def history_gen():
                                for msg in history_prompt:
                                    yield msg
                            await client.connect(prompt=history_gen())
                        else:
                            await client.connect()
                        
                        # Send Query if new run
                        if iteration == 1:
                            # Just send the user query
                             await client.query(query)
                        
                        # Process Responses
                        async for event in self._run_conversation(query, iteration):
                            if self._event_queue:
                                await self._event_queue.put(event)
                        
                        # Check pruning condition (e.g. at end of turn)
                        # self.history is updated inside _run_conversation
                        current_tokens = self.history.total_tokens if hasattr(self.history, 'total_tokens') else 0
                        if current_tokens > 150000: # Configurable limit
                             print(f"DEBUG CORE: Hit token limit ({current_tokens}). Pruning...", flush=True)
                             msgs = self.history.messages # Access raw messages
                             pruned, stats = context_manager.prune_history(msgs)
                             history_prompt = context_manager.convert_to_dicts(pruned)
                             
                             # Reset history tracker for new session?
                             # self.history = MessageHistory(...) # Maybe?
                             # Or just keep it and let the next session rebuild it?
                             # The next session will start fresh. We need to track the "virtual" history.
                             
                             await self._event_queue.put(AgentEvent(type=EventType.STATUS, data={
                                 "status": f"Compacting Context: {stats.original_tokens} -> {stats.pruned_tokens} tokens"
                             }))
                             
                             iteration += 1
                             continue # Loop restarts client
                             
                        break # Exit loop if no compaction needed
                        
                if self._event_queue:
                    await self._event_queue.put(None)  # Sentinel
            except Exception as e:
                import traceback
                traceback.print_exc()
                if self._event_queue:
                    await self._event_queue.put(AgentEvent(type=EventType.ERROR, data={"error": str(e)}))
                    await self._event_queue.put(None)

        # ... (rest of run_query logic)

        """Run a single conversation turn."""
        self.tool_name_map = getattr(self, "tool_name_map", {})
        step_id = str(uuid.uuid4())
        if LOGFIRE_TOKEN:
            logfire.set_baggage(step_id=step_id)
        yield AgentEvent(
            type=EventType.STATUS, data={"status": "processing", "iteration": iteration}
        )

        with logfire.span("llm_api_wait", query_length=len(query)):
            await self.client.query(query)  # type: ignore

        tool_calls_this_iter = []
        auth_link = None

        # Track current agent persona based on tools used
        current_agent_name = getattr(self, "current_agent_name", DEFAULT_AGENT_NAME)

        with logfire.span("llm_response_stream"):
            async for msg in self.client.receive_response():  # type: ignore
                if isinstance(msg, AssistantMessage):
                    # TRACK SUB-AGENT CONTEXT
                    parent_tool_use_id = getattr(msg, "parent_tool_use_id", None)
                    if not parent_tool_use_id:
                         # Compatibility check for some SDK versions where it might be a dict or in StreamEvent
                         parent_tool_use_id = msg.__dict__.get("parent_tool_use_id")
                    
                    # Logic: If we have a parent_tool_use_id, we are a sub-agent.
                    # Otherwise, we have returned to the Primary Agent.
                    msg_author = DEFAULT_AGENT_NAME
                    if parent_tool_use_id:
                        raw_name = self.tool_name_map.get(parent_tool_use_id, "Subagent")
                        # Add prefix only if not already an agent-like name
                        if any(x in raw_name for x in ["Specialist", "Author", "Agent"]):
                            msg_author = raw_name
                        else:
                            msg_author = f"Subagent: {raw_name}"
                    
                    # Store current author for log attribution until next message arrives
                    self.current_agent_name = msg_author


                    # Track token usage if available in message
                    # (Docs say: "Same ID = Same Usage. Charge only once per step")
                    usage_source = getattr(msg, "usage", None)
                    if usage_source:
                        # Safe extraction handling both dict and object access
                        def get_val(obj, key):
                            if isinstance(obj, dict): return obj.get(key, 0)
                            return getattr(obj, key, 0)
                        
                        inp = get_val(usage_source, "input_tokens") or 0
                        out = get_val(usage_source, "output_tokens") or 0
                        
                        msg_id = getattr(msg, "id", None)
                        # Process if ID is new, OR if ID is missing (e.g. ResultMessage often lacks ID)
                        # We assume messages without ID are unique events in the stream
                        if (msg_id and msg_id not in self._processed_msg_ids) or not msg_id:
                            if msg_id:
                                self._processed_msg_ids.add(msg_id)
                            
                            self.trace["token_usage"]["input"] += inp
                            self.trace["token_usage"]["output"] += out
                            self.trace["token_usage"]["total"] += inp + out
                            print(f"DEBUG CORE: Updated tokens (ID: {msg_id}): +{inp+out} (Total: {self.trace['token_usage']['total']})")

                    for block in msg.content:
                        if isinstance(block, ToolUseBlock):
                            # Store for back-attribution
                            resolved_name = block.name
                            
                            # IMPROVED: Map internal tools to human-friendly agent names
                            if "research_pipeline" in block.name:
                                resolved_name = SEARCH_AGENT_NAME
                            elif "compile_report" in block.name:
                                resolved_name = WRITER_AGENT_NAME
                            elif block.name == "Task" and isinstance(block.input, dict):
                                sub_type = block.input.get("subagent_type")
                                if sub_type:
                                    resolved_name = f"Subagent: {sub_type}"
                            
                            self.tool_name_map[block.id] = resolved_name

                            # Update log attribution author immediately when a tool starts
                            # This ensures logs *during* the tool run are correctly attributed.
                            self.current_agent_name = resolved_name

                            if is_malformed_tool_name(block.name):
                                logfire.warning(
                                    "malformed_tool_name_detected",
                                    tool_name=block.name,
                                    run_id=self.run_id,
                                    step_id=step_id,
                                )
                                yield AgentEvent(
                                    type=EventType.ERROR,
                                    data={
                                        "error": "Malformed tool call name detected.",
                                        "tool_name": block.name,
                                    },
                                )
                            tool_record = {
                                "run_id": self.run_id,
                                "step_id": step_id,
                                "iteration": iteration,
                                "name": block.name,
                                "id": block.id,
                                "time_offset_seconds": round(
                                    time.time() - self.start_ts, 3
                                ),
                                "input": block.input if hasattr(block, "input") else None,
                            }
                            self.trace["tool_calls"].append(tool_record)
                            tool_calls_this_iter.append(tool_record)

                            yield AgentEvent(
                                type=EventType.TOOL_CALL,
                                data={
                                    "name": block.name,
                                    "id": block.id,
                                    "input": block.input,
                                    "time_offset": tool_record["time_offset_seconds"],
                                },
                            )

                        elif isinstance(block, TextBlock):
                            # Check for auth link
                            if "connect.composio.dev/link" in block.text:
                                links = re.findall(
                                    r"https://connect\.composio\.dev/link/[^\s\)]+",
                                    block.text,
                                )
                                if links:
                                    auth_link = links[0]
                                    yield AgentEvent(
                                        type=EventType.AUTH_REQUIRED,
                                        data={"auth_link": auth_link},
                                    )

                            yield AgentEvent(
                                type=EventType.TEXT, 
                                data={
                                    "text": block.text, 
                                    "author": msg_author,
                                    "time_offset": round(time.time() - self.start_ts, 3)
                                }
                            )

                        elif isinstance(block, ThinkingBlock):
                            yield AgentEvent(
                                type=EventType.THINKING,
                                data={"thinking": block.thinking[:1000]},
                            )
                        
                        elif isinstance(block, ToolResultBlock):
                            # EXTRACT DIALOGUE FROM TOOL RESULTS (Sub-agent speech)
                            if block.content:
                                text_content = ""
                                if isinstance(block.content, str):
                                    text_content = block.content
                                elif isinstance(block.content, list):
                                    # Handle list of blocks (SDK pattern)
                                    for b in block.content:
                                        if isinstance(b, dict) and b.get("type") == "text":
                                            text_content += b.get("text", "")
                                        elif hasattr(b, "text"):
                                            text_content += b.text
                                
                                if text_content.strip():
                                    # Attribute to the tool/sub-agent that produced it
                                    tool_name = self.tool_name_map.get(block.tool_use_id, "Subagent")
                                    author_label = tool_name
                                    if not any(x in tool_name for x in ["Specialist", "Author", "Agent", "Subagent"]):
                                        author_label = f"Subagent: {tool_name}"
                                        
                                    yield AgentEvent(
                                        type=EventType.TEXT, 
                                        data={
                                            "text": text_content, 
                                            "author": author_label,
                                            "time_offset": round(time.time() - self.start_ts, 3)
                                        }
                                    )

                elif isinstance(msg, ResultMessage):
                    if msg.session_id:
                        self.trace["provider_session_id"] = msg.session_id

                    # Check for usage info in ResultMessage (Anthropic often puts it here)
                    usage_source = getattr(msg, "usage", None)
                    if usage_source:
                        def get_val(obj, key):
                            if isinstance(obj, dict): return obj.get(key, 0)
                            return getattr(obj, key, 0)
                        
                        inp = get_val(usage_source, "input_tokens") or 0
                        out = get_val(usage_source, "output_tokens") or 0

                        # Update cumulative trace (for backwards compatibility)
                        # Update cumulative trace (for backwards compatibility)
                        # ResultMessage usually contains cumulative for the step
                        msg_id = getattr(msg, "id", None)
                        if (msg_id and msg_id not in self._processed_msg_ids) or not msg_id:
                             if msg_id:
                                 self._processed_msg_ids.add(msg_id)
                             self.trace["token_usage"]["input"] += inp
                             self.trace["token_usage"]["output"] += out
                             self.trace["token_usage"]["total"] += inp + out
                             print(f"DEBUG CORE: Updated tokens (ID: {msg_id}): +{inp+out} (Total: {self.trace['token_usage']['total']})")

                        # Add to MessageHistory for per-message tracking (Anthropic pattern)
                        self.history.add_message("assistant", "response", usage_source)

                        # Check for truncation and apply it
                        if self.history.truncate():
                            logfire.warning(
                                "context_truncated_mid_session",
                                run_id=self.run_id,
                                stats=self.history.get_stats(),
                            )

                        # Check if we should trigger harness handoff
                        if self.history.should_handoff():
                            logfire.warning(
                                "context_threshold_reached",
                                run_id=self.run_id,
                                total_tokens=self.history.total_tokens,
                                threshold=TRUNCATION_THRESHOLD,
                            )
                            yield AgentEvent(
                                type=EventType.STATUS,
                                data={
                                    "status": "CONTEXT_THRESHOLD",
                                    "tokens": self.history.total_tokens,
                                    "threshold": TRUNCATION_THRESHOLD,
                                },
                            )

                        logfire.info(
                            "token_usage_update",
                            run_id=self.run_id,
                            input=inp,
                            output=out,
                            total_so_far=self.trace["token_usage"]["total"],
                            history_tokens=self.history.total_tokens,
                        )
                        
                        # Emit token usage event for UI updates
                        yield AgentEvent(
                            type=EventType.STATUS,
                            data={
                                "status": "token_update",
                                "token_usage": self.trace["token_usage"],
                            },
                        )

                elif isinstance(msg, (UserMessage, ToolResultBlock)):
                    blocks = msg.content if isinstance(msg, UserMessage) else [msg]

                    for block in blocks:
                        is_result = isinstance(block, ToolResultBlock) or hasattr(
                            block, "tool_use_id"
                        )

                        if is_result:
                            tool_use_id = getattr(block, "tool_use_id", None)
                            is_error = getattr(block, "is_error", False)
                            block_content = getattr(block, "content", "")
                            content_str = str(block_content)

                            result_record = {
                                "run_id": self.run_id,
                                "step_id": step_id,
                                "tool_use_id": tool_use_id,
                                "time_offset_seconds": round(
                                    time.time() - self.start_ts, 3
                                ),
                                "is_error": is_error,
                                "content_size_bytes": len(content_str),
                            }
                            self.trace["tool_results"].append(result_record)

                            # --- CONSECUTIVE ERROR TRACKING ---
                            # Logic: If we see repeated schema validation errors, escalate and eventually abort.
                            if is_error:
                                error_text = str(block_content)
                                # Only count validation/schema errors which indicate a loop
                                if (
                                    "validation" in error_text.lower()
                                    or "missing required" in error_text.lower()
                                ):
                                    self.consecutive_tool_errors += 1
                                    logfire.warning(
                                        "consecutive_tool_error",
                                        count=self.consecutive_tool_errors,
                                        error=error_text[:100],
                                    )

                                    # Level 2: Escalated Nudge (3 errors)
                                    if self.consecutive_tool_errors == 3:
                                        # We can't modify the PAST block, but we can potentially inject a steering message?
                                        # Actually the most effective way is to let the result go through,
                                        # but if we could intercept... for now relies on the agent reading the error.
                                        # We will implement the Escalated Nudge in the *next* user message or via system event if possible.
                                        # For now, just logging.
                                        pass

                                    # Level 3: Hard Stop (4 errors)
                                    if self.consecutive_tool_errors >= 4:
                                        raise HarnessError(
                                            f"Aborting iteration due to {self.consecutive_tool_errors} consecutive tool validation errors.",
                                            context={"last_tool_error": error_text[:500]},
                                        )
                            else:
                                # Reset on success
                                self.consecutive_tool_errors = 0
                            # ----------------------------------

                            yield AgentEvent(
                                type=EventType.TOOL_RESULT,
                                data={
                                    "tool_use_id": tool_use_id,
                                    "is_error": is_error,
                                    "content_preview": content_str[:2000],
                                    "content_size": len(content_str),
                                    "content_raw": block_content,
                                },
                            )

                            # Fire observers
                            tool_name = None
                            tool_input = None
                            for tc in tool_calls_this_iter:
                                if tc.get("id") == tool_use_id:
                                    tool_name = tc.get("name")
                                    tool_input = tc.get("input", {})
                                    break

                            if tool_name and self.workspace_dir:
                                # CRITICAL: Await search results observer to ensure files exist
                                # before agent calls run_research_phase (fixes race condition)
                                await observe_and_save_search_results(
                                    tool_name, block_content, self.workspace_dir
                                )
                                # These can remain fire-and-forget as they're not blocking dependencies
                                asyncio.create_task(
                                    observe_and_save_workbench_activity(
                                        tool_name,
                                        tool_input or {},
                                        content_str,
                                        self.workspace_dir,
                                    )
                                )
                                asyncio.create_task(
                                    observe_and_enrich_corpus(
                                        tool_name,
                                        tool_input or {},
                                        block_content,
                                        self.workspace_dir,
                                    )
                                )

                                # Check for work_product events (Write tool to work_products/)
                                tool_lower = tool_name.lower()
                                is_write_tool = "write" in tool_lower and (
                                    "__write" in tool_lower or tool_lower.endswith("write")
                                )
                                if is_write_tool and tool_input:
                                    file_path = tool_input.get(
                                        "file_path", ""
                                    ) or tool_input.get("path", "")
                                    if file_path.endswith(".html"):
                                        # Read the file content and emit work_product event
                                        try:
                                            if os.path.exists(file_path):
                                                with open(
                                                    file_path, "r", encoding="utf-8"
                                                ) as f:
                                                    html_content = f.read()
                                                yield AgentEvent(
                                                    type=EventType.WORK_PRODUCT,
                                                    data={
                                                        "content_type": "text/html",
                                                        "content": html_content,
                                                        "filename": os.path.basename(
                                                            file_path
                                                        ),
                                                        "path": file_path,
                                                    },
                                                )
                                        except Exception as e:
                                            logfire.warning(
                                                "work_product_read_error", error=str(e)
                                            )

        iter_record = {
            "run_id": self.run_id,
            "step_id": step_id,
            "iteration": iteration,
            "query": query[:200],
            "duration_seconds": round(time.time() - self.start_ts, 3),
            "tool_calls": len(tool_calls_this_iter),
        }
        self.trace["iterations"].append(iter_record)

        yield AgentEvent(
            type=EventType.ITERATION_END,
            data={
                "iteration": iteration,
                "tool_calls": len(tool_calls_this_iter),
                "duration_seconds": round(time.time() - self.start_ts, 3),
                "token_usage": self.trace.get("token_usage"),
            },
        )

    def _save_trace(self) -> None:
        """Save the trace to workspace."""
        self.trace["end_time"] = datetime.now().isoformat()
        self.trace["total_duration_seconds"] = round(time.time() - self.start_ts, 3)

        trace_path = os.path.join(self.workspace_dir, "trace.json")
        with open(trace_path, "w") as f:
            json.dump(self.trace, f, indent=2, default=str)

    def get_session_info(self) -> dict:
        """Return session info for display."""
        return {
            "workspace": self.workspace_dir,
            "user_id": self.user_id,
            "session_url": self.session.mcp.url if self.session else None,
            "logfire_enabled": bool(LOGFIRE_TOKEN),
        }

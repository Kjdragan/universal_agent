"""
Composio Agent - Claude SDK with Tool Router
A standalone agent using Claude Agent SDK with Composio MCP integration.
Traces are sent to Logfire for observability.
"""

import sys
import os

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

import asyncio
import copy
import signal
import sqlite3
import time
import json
import re
import uuid
import inspect
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Callable
from dotenv import load_dotenv
from universal_agent.utils.message_history import (
    TRUNCATION_THRESHOLD,
    CONTEXT_WINDOW_TOKENS,
)
import glob
from universal_agent.harness.verifier import TaskVerifier
from universal_agent.agent_core import UniversalAgent
from claude_agent_sdk import create_sdk_mcp_server, HookMatcher
from universal_agent.tools.research_bridge import (
    run_research_pipeline_wrapper,
    crawl_parallel_wrapper,
    run_research_phase_wrapper,
    run_report_generation_wrapper,
)
from universal_agent.gateway import InProcessGateway, ExternalGateway, GatewayRequest
from universal_agent.hooks import AgentHookSet

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

    response_text: str  # Agent's final response
    execution_time_seconds: float = 0.0  # Total execution time
    tool_calls: int = 0  # Number of tool calls
    tool_breakdown: list = field(
        default_factory=list
    )  # [{name, time_offset, iteration}]
    code_execution_used: bool = False  # Whether code exec tools were used
    workspace_path: str = ""  # Session workspace directory
    trace_id: Optional[str] = None  # Logfire trace ID for deep linking
    follow_up_suggestions: list = field(
        default_factory=list
    )  # Extracted follow-up options



from dotenv import load_dotenv

# Load environment FIRST
load_dotenv()

import sys
import argparse

# prompt_toolkit for better terminal input (arrow keys, history, multiline)
from universal_agent.prompt_assets import (
    discover_skills,
    generate_skills_xml,
    get_tool_knowledge_block,
    get_tool_knowledge_content,
)
from universal_agent.search_config import SEARCH_TOOL_CONFIG
from universal_agent.observers import (
    observe_and_save_search_results,
    observe_and_save_workbench_activity,
    observe_and_save_work_products,
    observe_and_save_video_outputs,
    verify_subagent_compliance,
)
from universal_agent.cli_io import (
    attach_run_log,
    open_run_log,
    read_run_log_tail,
    collect_local_tool_trace_ids,
    build_prompt_session,
    read_prompt_input,
    print_job_completion_summary,
    list_workspace_artifacts,
    render_agent_events,
    print_execution_summary_from_events,
    print_job_completion_summary_from_events,
)
from universal_agent.execution_context import bind_workspace
from universal_agent.execution_session import ExecutionSession
from universal_agent.trace_utils import write_trace


# Global State Initialization
tool_ledger = None
budget_state = {}
trace = {}
run_id = None
runtime_db_conn = None
budget_config = {}
provider_session_forked_from = None
current_execution_session = None
gateway_mode_active = False
interrupt_requested = False
last_sigint_ts = None
current_step_id = None


class BudgetExceeded(RuntimeError):
    def __init__(
        self, budget_name: str, limit: float, current: float, detail: str = ""
    ):
        self.budget_name = budget_name
        self.limit = limit
        self.current = current
        self.detail = detail
        message = f"Budget exceeded: {budget_name} (limit={limit}, current={current})"
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


def build_cli_hooks() -> dict:
    return {
        "AgentStop": [
            HookMatcher(matcher=None, hooks=[on_agent_stop]),
        ],
        "SubagentStop": [
            HookMatcher(matcher=None, hooks=[on_subagent_stop]),
        ],
        "PreToolUse": [
            HookMatcher(matcher=None, hooks=[on_pre_tool_use_ledger]),
            HookMatcher(
                matcher="Bash",
                hooks=[on_pre_bash_block_composio_sdk, on_pre_bash_skill_hint],
            ),
            HookMatcher(matcher="Task", hooks=[on_pre_task_skill_awareness]),
        ],
        "PostToolUse": [
            HookMatcher(matcher=None, hooks=[on_post_tool_use_ledger]),
            HookMatcher(matcher=None, hooks=[on_post_tool_use_validation]),
            HookMatcher(
                matcher=None, hooks=[on_post_research_finalized_cache]
            ),  # Cache refined_corpus.md on finalize
            HookMatcher(matcher=None, hooks=[on_post_email_send_artifact]),
            HookMatcher(matcher="Task", hooks=[on_post_task_guidance]),
        ],
        # DISABLED: UserPromptSubmit hook triggers Claude CLI bug:
        # "error: 'types.UnionType' object is not callable"
        # This is a CLI-side issue, not our code. PreToolUse still provides skill guidance.
        "UserPromptSubmit": [
            HookMatcher(matcher=None, hooks=[on_user_prompt_skill_awareness]),
        ],
    }


from universal_agent.agent_setup import DISALLOWED_TOOLS


def _normalize_gateway_tool_call_id(raw_id: object) -> str:
    base = str(raw_id or uuid.uuid4())
    return f"gw-{run_id or 'unknown'}-{base}"


def _get_gateway_tool_call_id(raw_id: object) -> str:
    key = str(raw_id or uuid.uuid4())
    if key in gateway_tool_call_map:
        return gateway_tool_call_map[key]
    tool_call_id = f"gw-{run_id or 'unknown'}-{uuid.uuid4()}"
    gateway_tool_call_map[key] = tool_call_id
    return tool_call_id



def safe_clear_history(client: Any) -> bool:
    """
    Safely clear message history for many client types (ClaudeSDKClient, UniversalAgent, etc).
    Returns True if history was found and cleared.
    """
    if not client or not hasattr(client, "history"):
        return False
        
    cleared = False
    hist = client.history
    
    # 1. Try explicit clear_history() (SDK Hook pattern)
    if hasattr(hist, "clear_history") and callable(hist.clear_history):
        try:
            hist.clear_history()
            cleared = True
        except Exception:
            pass
            
    # 2. Try reset() (MessageHistory pattern)
    if not cleared and hasattr(hist, "reset") and callable(hist.reset):
        try:
            hist.reset()
            cleared = True
        except Exception:
            pass
            
    # 3. Try to set to empty list (Native history / generic pattern)
    if not cleared:
        try:
            client.history = []
            cleared = True
        except Exception:
            pass
            
    return cleared


def _normalize_gateway_file_path(path_value: Any) -> Any:
    if not isinstance(path_value, str) or not path_value:
        return path_value
    if not OBSERVER_WORKSPACE_DIR:
        return path_value
    if "/.claude/sessions/" not in path_value and f"{os.sep}sessions{os.sep}" not in path_value:
        return path_value
    for marker in (
        "work_products",
        "search_results",
        "search_results_filtered_best",
        "workbench",
        "downloads",
    ):
        marker_token = f"{os.sep}{marker}{os.sep}"
        if marker_token in path_value:
            suffix = path_value.split(marker_token, 1)[1].lstrip(os.sep)
            return os.path.join(OBSERVER_WORKSPACE_DIR, marker, suffix)
    return os.path.join(OBSERVER_WORKSPACE_DIR, "work_products", os.path.basename(path_value))


def _ensure_gateway_step() -> str:
    global current_step_id
    if not runtime_db_conn or not run_id:
        return current_step_id or "unknown"
    if not current_step_id or current_step_id == "unknown":
        current_step_id = str(uuid.uuid4())
    try:
        row = runtime_db_conn.execute(
            "SELECT 1 FROM run_steps WHERE step_id = ? LIMIT 1",
            (current_step_id,),
        ).fetchone()
    except Exception:
        row = None
    if not row:
        try:
            step_index = _next_step_index(runtime_db_conn, run_id)
            start_step(runtime_db_conn, run_id, current_step_id, step_index)
            logfire.info(
                "durable_step_started_gateway_hook",
                run_id=run_id,
                step_id=current_step_id,
                step_index=step_index,
            )
        except Exception as exc:
            logfire.warning(
                "runtime_step_insert_failed_gateway_hook",
                step_id=current_step_id,
                error=str(exc),
            )
    return current_step_id


# Configure Logfire BEFORE importing Claude SDK
import logfire

# Configure Logfire for tracing
LOGFIRE_DISABLED = os.getenv("UA_DISABLE_LOGFIRE", "").lower() in {"1", "true", "yes"}
LOGFIRE_TOKEN = None
if not LOGFIRE_DISABLED:
    LOGFIRE_TOKEN = (
        os.getenv("LOGFIRE_TOKEN")
        or os.getenv("LOGFIRE_WRITE_TOKEN")
        or os.getenv("LOGFIRE_API_KEY")
    )

# TIMEOUT CONFIGURATION (Critical for Large Reports)
# Default is 60s (60000ms).
# We set to 60 minutes (3600000ms) to allow "Deep Thinking" and long tool loops.
if "CLAUDE_CODE_STREAM_CLOSE_TIMEOUT" not in os.environ:
    os.environ["CLAUDE_CODE_STREAM_CLOSE_TIMEOUT"] = "3600000"

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
        inspect_arguments=False,  # Suppress InspectArgumentsFailedWarning
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
elif not LOGFIRE_DISABLED:
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
from universal_agent.harness.verifier import TaskVerifier

# Durable runtime support
from universal_agent.durable.db import connect_runtime_db, get_runtime_db_path
from universal_agent.durable.migrations import ensure_schema
import logging
import sys
from universal_agent.agent_core import (
    UniversalAgent,
    EventType,
    HarnessError,
)
from universal_agent.utils.log_bridge import LogBridgeHandler, StdoutInterceptor


def setup_log_bridge(agent: UniversalAgent):
    """Attach log bridge to capturing loggers and intercept stdout/stderr."""
    if not agent:
        return
        
    bridge = LogBridgeHandler(agent.send_agent_event)
    bridge.setLevel(logging.INFO)
    
    # Attach to httpx
    httpx_logger = logging.getLogger("httpx")
    httpx_logger.addHandler(bridge)
    httpx_logger.setLevel(logging.INFO)
    
    # Attach to composio if needed
    # logging.getLogger("composio").addHandler(bridge)
    
    # Intercept stdout/stderr for rich terminal output (e.g. from local tools)
    sys.stdout = StdoutInterceptor(agent.send_agent_event, sys.stdout, prefix="Console")
    sys.stderr = StdoutInterceptor(agent.send_agent_event, sys.stderr, prefix="System")
    
    return bridge

from universal_agent.durable.ledger import ToolCallLedger
from universal_agent.durable.tool_gateway import (
    prepare_tool_call,
    parse_tool_identity,
    is_malformed_tool_name,
    parse_malformed_tool_name,
    is_invalid_tool_name,
)
from universal_agent.durable.normalize import deterministic_task_key, normalize_json
from universal_agent.durable.classification import (
    classify_replay_policy,
    classify_tool,
    resolve_tool_policy,
    validate_tool_policies,
)
from universal_agent.durable.state import (
    upsert_run,
    update_run_tokens,
    update_run_status,
    update_run_provider_session,
    start_step,
    complete_step,
    update_step_phase,
    get_run,
    get_run_status,
    get_step_count,
    is_cancel_requested,
    is_cancel_requested,
    mark_run_cancelled,
    increment_iteration_count,
    get_iteration_info,
)
from universal_agent.durable.checkpointing import save_checkpoint, load_last_checkpoint, load_corpus_cache
# Local MCP server provides: crawl_parallel, finalize_research, read_research_files, append_to_file, etc.\n# Note: File Read/Write now uses native Claude SDK tools

# Composio client - will be initialized in main() with file_download_dir
composio = None

# =============================================================================
# MEMORY SYSTEM INTEGRATION
# =============================================================================
from Memory_System.manager import MemoryManager
from Memory_System.tools import get_memory_tool_map
from universal_agent.guardrails.tool_schema import (
    ToolSchema,
    build_tool_schema_from_raw_composio,
    post_tool_use_schema_nudge,
    pre_tool_use_schema_guardrail,
)
from universal_agent.identity import (
    resolve_email_recipients,
    validate_recipient_policy,
    load_identity_registry,
    resolve_user_id,
)
from universal_agent.harness import ask_user_questions, present_plan_summary

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
# LETTA LEARNING SDK INTEGRATION
# =============================================================================
# The Letta Learning SDK intercepts Claude Agent SDK transport layer
# to automatically capture conversations and inject memory.
LETTA_ENABLED = True
LETTA_AGENT_NAME = "universal_agent"
LETTA_MEMORY_BLOCKS = [
    "human",
    "system_rules",
    "project_context",
    "failure_patterns",
    "recovery_patterns",
    "recent_queries",
    "recent_reports",
]
LETTA_MEMORY_BLOCK_DESCRIPTIONS = {
    "system_rules": (
        "Capture stable operational rules and constraints for the Universal Agent. "
        "Include tool usage conventions, workspace expectations, and do/don't guidance."
    ),
    "project_context": (
        "Capture concise, high-signal project context: architecture notes, key paths, "
        "and workflow conventions that help the agent stay aligned."
    ),
    "failure_patterns": (
        "Track recurring failure modes, symptoms, and suspected causes. "
        "Note impact and any follow-up needed to prevent repeats."
    ),
    "recovery_patterns": (
        "Track successful recoveries or improvised workflows that worked well. "
        "These are candidates for future skills or formalized procedures."
    ),
    "recent_queries": (
        "Track recent user requests and tasks run in the Universal Agent. "
        "Keep a short rolling list with timestamps, request summaries, and outcomes."
    ),
    "recent_reports": (
        "Track the latest reports generated (topic, sub-agent, date, file path, "
        "recipient or destination). Keep the last few entries."
    ),
}
_LETTA_CONTEXT = None
_LETTA_CONTEXT_READY = False
_letta_client = None
_letta_async_client = None
_LETTA_SUBAGENT_ENABLED = os.getenv("UA_LETTA_SUBAGENT_MEMORY", "0").lower() in {
    "1",
    "true",
    "yes",
}

try:
    from agentic_learning import learning, AgenticLearning, AsyncAgenticLearning
    
    # Only attempt initialization if explicitly enabled or if we want soft degradation
    if _LETTA_SUBAGENT_ENABLED:
        try:
            _letta_client = AgenticLearning()
            _letta_async_client = AsyncAgenticLearning()

            # Ensure agent exists
            try:
                _letta_client.agents.retrieve(LETTA_AGENT_NAME)
            except Exception:
                _letta_client.agents.create(
                    agent=LETTA_AGENT_NAME,
                    memory=LETTA_MEMORY_BLOCKS,
                    model="anthropic/claude-sonnet-4-20250514",
                )
                print(f"✅ Letta agent '{LETTA_AGENT_NAME}' created")
        except Exception as e:
            print(f"⚠️ Letta initialization failed (soft fail): {e}")
            _letta_client = None
            _letta_async_client = None

    
    # Log subagent memory status
    if _LETTA_SUBAGENT_ENABLED:
        print("🧠 Letta subagent memory: ENABLED (set UA_LETTA_SUBAGENT_MEMORY=0 to disable)")
    else:
        print("🧠 Letta subagent memory: DISABLED (set UA_LETTA_SUBAGENT_MEMORY=1 to enable)")

except ImportError:
    LETTA_ENABLED = False
    print("⚠️ Letta Learning SDK not installed - using local memory")
except Exception as e:
    LETTA_ENABLED = False
    print(f"⚠️ Letta initialization error: {e}")


async def _ensure_letta_context() -> None:
    """Initialize Letta learning context inside the running event loop."""
    global _LETTA_CONTEXT_READY, _LETTA_CONTEXT
    if not LETTA_ENABLED or _LETTA_CONTEXT_READY:
        return
    if _letta_async_client is None:
        return

    capture_only = os.getenv("UA_LETTA_CAPTURE_ONLY", "0").lower() in {"1", "true", "yes"}
    if capture_only:
        print("🧠 Letta mode: LEARNING ONLY (Injection disabled)")

    _LETTA_CONTEXT = learning(
        agent=LETTA_AGENT_NAME,
        memory=LETTA_MEMORY_BLOCKS,
        client=_letta_async_client,
        capture_only=capture_only,
    )
    await _LETTA_CONTEXT.__aenter__()
    _LETTA_CONTEXT_READY = True
    await _ensure_letta_memory_blocks(LETTA_AGENT_NAME)
    print(f"🧠 Letta memory active for '{LETTA_AGENT_NAME}'")


async def _shutdown_letta() -> None:
    """Cleanly shutdown Letta clients before the event loop closes."""
    global _LETTA_CONTEXT_READY, _LETTA_CONTEXT, _letta_async_client, _letta_client
    if not LETTA_ENABLED:
        return
    if _LETTA_CONTEXT_READY and _LETTA_CONTEXT:
        try:
            await _LETTA_CONTEXT.__aexit__(None, None, None)
        except Exception:
            pass
        _LETTA_CONTEXT_READY = False
        _LETTA_CONTEXT = None
    if _letta_async_client:
        try:
            if hasattr(_letta_async_client, "aclose"):
                await _letta_async_client.aclose()
            elif hasattr(_letta_async_client, "close"):
                _letta_async_client.close()
        except Exception:
            pass
        _letta_async_client = None
    if _letta_client:
        try:
            if hasattr(_letta_client, "close"):
                _letta_client.close()
        except Exception:
            pass
        _letta_client = None


def _sanitize_letta_agent_name(name: str) -> str:
    if not name:
        return name
    sanitized = re.sub(r"[^\w\s\-']", "-", name)
    sanitized = re.sub(r"\s+", " ", sanitized).strip()
    sanitized = re.sub(r"-{2,}", "-", sanitized)
    return sanitized or name


def _letta_subagent_name(tool_input: dict[str, Any]) -> str:
    subagent_type = ""
    if isinstance(tool_input, dict):
        subagent_type = tool_input.get("subagent_type", "") or ""
    if subagent_type:
        return _sanitize_letta_agent_name(f"{LETTA_AGENT_NAME} {subagent_type}")
    return _sanitize_letta_agent_name(
        f"{LETTA_AGENT_NAME} {deterministic_task_key(tool_input or {})}"
    )


async def _ensure_letta_agent(agent_name: str) -> None:
    if not LETTA_ENABLED or not _letta_async_client:
        return
    try:
        agent = await _letta_async_client.agents.retrieve(agent=agent_name)
        if agent:
            await _ensure_letta_memory_blocks(agent_name)
            return
    except Exception:
        pass
    try:
        await _letta_async_client.agents.create(
            agent=agent_name,
            memory=LETTA_MEMORY_BLOCKS,
            model="anthropic/claude-sonnet-4-20250514",
        )
    except Exception:
        return
    await _ensure_letta_memory_blocks(agent_name)


async def _ensure_letta_memory_blocks(agent_name: str) -> None:
    if not (LETTA_ENABLED and _letta_async_client and LETTA_MEMORY_BLOCK_DESCRIPTIONS):
        return
    try:
        blocks = await _letta_async_client.memory.list(agent=agent_name)
    except Exception:
        return

    existing = {}
    for block in blocks:
        label = getattr(block, "label", None)
        if label:
            existing[label] = block

    for label, description in LETTA_MEMORY_BLOCK_DESCRIPTIONS.items():
        if label not in existing:
            try:
                await _letta_async_client.memory.create(
                    agent=agent_name,
                    label=label,
                    description=description,
                )
            except Exception:
                continue
            continue

        block = existing[label]
        block_description = getattr(block, "description", "") or ""
        if block_description:
            continue
        block_value = getattr(block, "value", "") or ""
        try:
            await _letta_async_client.memory.upsert(
                agent=agent_name,
                label=label,
                value=block_value,
                description=description,
            )
        except Exception:
            continue


async def _get_subagent_memory_context(tool_input: dict[str, Any]) -> str:
    if not (LETTA_ENABLED and _letta_async_client and _LETTA_SUBAGENT_ENABLED):
        return ""
    agent_name = _letta_subagent_name(tool_input)
    await _ensure_letta_agent(agent_name)
    try:
        return await _letta_async_client.memory.context.retrieve(agent=agent_name) or ""
    except Exception:
        return ""


async def _capture_subagent_memory(
    tool_input: dict[str, Any],
    output_text: str,
) -> None:
    if not (LETTA_ENABLED and _letta_async_client and _LETTA_SUBAGENT_ENABLED):
        return
    agent_name = _letta_subagent_name(tool_input)
    await _ensure_letta_agent(agent_name)
    prompt = ""
    if isinstance(tool_input, dict):
        prompt = tool_input.get("prompt") or tool_input.get("description") or ""
    if prompt:
        subagent_type = (
            tool_input.get("subagent_type") if isinstance(tool_input, dict) else None
        )
        if subagent_type:
            prompt = f"[Subagent: {subagent_type}]\n{prompt}"
    request_messages = [{"role": "user", "content": prompt}] if prompt else []
    try:
        await _letta_async_client.messages.capture(
            agent=agent_name,
            request_messages=request_messages,
            response_dict={"role": "assistant", "content": output_text or ""},
            model="claude",
            provider="claude",
        )
    except Exception:
        return


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
    global \
        tool_ledger, \
        run_id, \
        current_step_id, \
        forced_tool_queue, \
        forced_tool_active_ids, \
        forced_tool_mode_active, \
        gateway_mode_active, \
        runtime_db_conn
    if tool_ledger is None or run_id is None:
        return {}
    if runtime_db_conn and run_id and is_cancel_requested(runtime_db_conn, run_id):
        return {
            "systemMessage": (
                "⚠️ Run cancellation requested. "
                "Do not call any more tools; end the turn."
            ),
            "decision": "block",
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "Run cancellation requested.",
            },
        }

    tool_name = input_data.get("tool_name", "")
    tool_call_id = str(tool_use_id or uuid.uuid4())
    if gateway_mode_active:
        tool_call_id = _get_gateway_tool_call_id(tool_use_id)
    transcript_path = input_data.get("transcript_path", "")
    session_id = input_data.get("session_id", "")
    if transcript_path:
        global _primary_transcript_path, _seen_transcript_paths
        if _primary_transcript_path is None:
            _primary_transcript_path = transcript_path
        if transcript_path not in _seen_transcript_paths:
            _seen_transcript_paths.add(transcript_path)
            transcript_role = "primary"
            if _primary_transcript_path and transcript_path != _primary_transcript_path:
                transcript_role = "secondary"
                print(f"🧭 Hook fired for secondary transcript: {tool_name}")
            logfire.info(
                "hook_transcript_path_seen",
                tool_name=tool_name,
                tool_use_id=tool_call_id,
                session_id=session_id,
                transcript_path=transcript_path,
                transcript_role=transcript_role,
            )
    if _is_task_output_name(tool_name):
        _mark_run_waiting_for_human(
            "task_output_tool_call",
            tool_name=tool_name,
            tool_call_id=tool_call_id,
        )
        return {
            "systemMessage": (
                "⚠️ TaskOutput/TaskResult is not a callable tool. "
                "Relaunch the subagent using the Task tool instead."
            ),
            "decision": "block",
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "TaskOutput/TaskResult is not a tool call.",
            },
        }

    # Check for disallowed/hallucinated tools
    if tool_name in DISALLOWED_TOOLS:
        print(f"DEBUG: Checking disallowed tool: {tool_name}")
        # Context detection for sub-agents (Allow Research Specialist to search)
        parent_tool_use_id = input_data.get("parent_tool_use_id")
        transcript_path_chk = input_data.get("transcript_path", "")
        # Safety check for _primary_transcript_path existence
        primary_path = globals().get("_primary_transcript_path")
        
        is_subagent_context = bool(parent_tool_use_id) or (
            primary_path is not None 
            and transcript_path_chk 
            and transcript_path_chk != primary_path
        )
        print(f"DEBUG: is_subagent_context={is_subagent_context} (parent={parent_tool_use_id}, path={transcript_path_chk}, primary={primary_path})")
        
        # If in sub-agent, ALLOW the tool (Specialists need access to tools blocked for Primary)
        if is_subagent_context:
             pass
        else:
            return {
                "systemMessage": (
                    f"⚠️ Tool '{tool_name}' is not available for the Primary Agent. "
                    "You must DELEGATE this task to a specialist (e.g., use the 'Task' tool with 'research-specialist')."
                ),
                "decision": "block",
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": f"Tool '{tool_name}' is disallowed for Primary Agent.",
                },
            }

    # Block Write calls with empty/missing required params (context exhaustion symptom)
    if tool_name == "Write":
        tool_input = input_data.get("tool_input", {}) or {}
        file_path = tool_input.get("file_path", "")
        content = tool_input.get("content", "")
        
        # Detect sub-agent context using multiple signals
        # 1. parent_tool_use_id is set when running inside a sub-agent
        parent_tool_use_id = input_data.get("parent_tool_use_id")
        # 2. Secondary transcript path (different from first seen)
        transcript_path = input_data.get("transcript_path", "")
        is_secondary_transcript = (
            _primary_transcript_path is not None 
            and transcript_path 
            and transcript_path != _primary_transcript_path
        )
        # Combined detection: either signal indicates sub-agent context
        is_subagent_context = bool(parent_tool_use_id) or is_secondary_transcript
        
        content_len = len(content) if content else 0
        
        # Log all Write hook invocations for debugging
        print(f"🔧 Write Hook: file_path={bool(file_path)}, content_len={content_len}, subagent={is_subagent_context}, parent_id={parent_tool_use_id}")
        logfire.info(
            "write_hook_fired",
            tool_name=tool_name,
            is_subagent_context=is_subagent_context,
            parent_tool_use_id=parent_tool_use_id,
            has_file_path=bool(file_path),
            content_length=content_len,
            transcript_path=transcript_path[:50] if transcript_path else None,
        )

        missing_params = []
        if not file_path:
            missing_params.append("file_path")
        if not content:
            missing_params.append("content")

        if missing_params:
            # Print to console for immediate visibility
            print(f"🛡️ HOOK BLOCKED Write: missing {missing_params} (subagent={is_subagent_context}, parent_id={parent_tool_use_id})")
            logfire.warning(
                "empty_write_params_blocked",
                tool_name=tool_name,
                missing=missing_params,
                is_subagent_context=is_subagent_context,
                parent_tool_use_id=parent_tool_use_id,
                run_id=run_id,
                step_id=current_step_id,
            )
            workspace_hint = (
                f"WORKSPACE: {OBSERVER_WORKSPACE_DIR}/work_products/\n"
                if OBSERVER_WORKSPACE_DIR
                else ""
            )
            return {
                "systemMessage": (
                    f"⚠️ BLOCKED: Write call missing required params: {', '.join(missing_params)}.\n\n"
                    f"This often happens after reading large research content. Your context may be exhausted.\n\n"
                    f"TO FIX:\n"
                    f"1. Take a breath - don't just retry immediately\n"
                    f"2. Write a SHORTER version first (executive summary only)\n"
                    f"3. Use these exact params:\n"
                    f'   - file_path: "{OBSERVER_WORKSPACE_DIR or "WORKSPACE"}/work_products/report.html"\n'
                    f'   - content: "<html>...your report content...</html>"\n\n'
                    f"{workspace_hint}"
                    f"Retry now with BOTH file_path AND content filled in."
                ),
                "decision": "block",
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": f"Write missing params: {', '.join(missing_params)}",
                },
            }

    if is_malformed_tool_name(tool_name):
        base_name, arg_key, arg_value = parse_malformed_tool_name(tool_name)
        is_forced_replay = bool(forced_tool_queue or forced_tool_mode_active)
        expected = forced_tool_queue[0] if forced_tool_queue else None
        expected_tool = (
            (expected or {}).get("raw_tool_name")
            or (expected or {}).get("tool_name")
            or base_name
            or tool_name
        )
        expected_input = json.dumps((expected or {}).get("tool_input") or {}, indent=2)
        repair_hint = ""
        if base_name and arg_key and arg_value is not None:
            repaired_payload = json.dumps({arg_key: arg_value}, ensure_ascii=True)
            repair_hint = f" Reissue as {base_name} with input {repaired_payload}."
        logfire.warning(
            "malformed_tool_name_guardrail",
            tool_name=tool_name,
            run_id=run_id,
            step_id=current_step_id,
            forced_replay=is_forced_replay,
        )
        if not is_forced_replay:
            _mark_run_waiting_for_human(
                "malformed_tool_name",
                tool_name=tool_name,
                tool_call_id=tool_call_id,
            )
        return {
            "systemMessage": (
                "⚠️ Malformed tool call name detected. "
                "Reissue the tool call with proper JSON arguments and do NOT "
                "concatenate XML-like arg_key/arg_value into the tool name."
                + (
                    repair_hint
                    or f" Next required tool: {expected_tool} with input {expected_input}"
                )
            ),
            "decision": "block",
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "Malformed tool name (XML-style args detected).",
            },
        }
    if is_invalid_tool_name(tool_name):
        is_forced_replay = bool(forced_tool_queue or forced_tool_mode_active)
        expected = forced_tool_queue[0] if forced_tool_queue else None
        expected_tool = (
            (expected or {}).get("raw_tool_name")
            or (expected or {}).get("tool_name")
            or tool_name
        )
        expected_input = json.dumps((expected or {}).get("tool_input") or {}, indent=2)
        logfire.warning(
            "invalid_tool_name_guardrail",
            tool_name=tool_name,
            run_id=run_id,
            step_id=current_step_id,
            forced_replay=is_forced_replay,
        )
        if not is_forced_replay:
            _mark_run_waiting_for_human(
                "invalid_tool_name",
                tool_name=tool_name,
                tool_call_id=tool_call_id,
            )
        return {
            "systemMessage": (
                "⚠️ Invalid tool name detected. Tool names must not include "
                "angle brackets or JSON fragments. Reissue the tool call with "
                "a valid tool name and JSON input."
                + f" Next required tool: {expected_tool} with input {expected_input}"
            ),
            "decision": "block",
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "Invalid tool name syntax.",
            },
        }
    tool_input = input_data.get("tool_input", {}) or {}
    updated_tool_input = None
    if (
        isinstance(tool_input, dict)
        and tool_name.lower() == "task"
        and "resume" in tool_input
    ):
        updated_tool_input = dict(tool_input)
        resume_key = updated_tool_input.pop("resume", None)
        if resume_key and not updated_tool_input.get("task_key"):
            updated_tool_input["task_key"] = resume_key
        tool_input = updated_tool_input

    if gateway_mode_active and tool_name in ("Write", "Read", "Edit", "MultiEdit"):
        if isinstance(tool_input, dict):
            normalized_input = dict(tool_input)
            changed = False
            for key in ("file_path", "path", "old_path", "new_path"):
                if key in normalized_input:
                    normalized_value = _normalize_gateway_file_path(normalized_input.get(key))
                    if normalized_value != normalized_input.get(key):
                        normalized_input[key] = normalized_value
                        changed = True
            if changed:
                updated_tool_input = normalized_input
                tool_input = normalized_input

    raw_tool_input = tool_input if isinstance(tool_input, dict) else {}
    email_updated_input, email_errors, email_replacements = resolve_email_recipients(
        tool_name,
        tool_input if isinstance(tool_input, dict) else {},
    )
    if email_errors:
        logfire.warning(
            "identity_recipient_unresolved",
            tool_name=tool_name,
            unresolved_aliases=email_errors,
            run_id=run_id,
            step_id=current_step_id,
        )
        alias_list = ", ".join(email_errors)
        return {
            "systemMessage": (
                "⚠️ Email recipient alias could not be resolved. "
                f"Unresolved: {alias_list}. "
                "Set UA_PRIMARY_EMAIL (and optional UA_SECONDARY_EMAILS) "
                "or provide a full email address."
            ),
            "decision": "block",
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "Unresolved email alias.",
            },
        }
    if email_updated_input is not None:
        updated_tool_input = email_updated_input
        tool_input = email_updated_input
        if email_replacements:
            logfire.info(
                "identity_recipient_resolved",
                tool_name=tool_name,
                replacements=email_replacements,
                run_id=run_id,
                step_id=current_step_id,
            )

    user_query = ""
    try:
        if isinstance(trace, dict):
            user_query = str(trace.get("query") or "")
    except Exception:
        user_query = ""

    invalid_recipients = validate_recipient_policy(
        tool_name,
        tool_input if isinstance(tool_input, dict) else {},
        user_query,
    )
    if invalid_recipients:
        logfire.warning(
            "identity_recipient_policy_denied",
            tool_name=tool_name,
            recipients=invalid_recipients,
            run_id=run_id,
            step_id=current_step_id,
        )
        recipient_list = ", ".join(sorted(set(invalid_recipients)))
        return {
            "systemMessage": (
                "⚠️ Email recipient not allowed by policy. "
                f"Recipients: {recipient_list}. "
                "Set UA_PRIMARY_EMAIL/UA_SECONDARY_EMAILS or include the address "
                "explicitly in the user request."
            ),
            "decision": "block",
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "Recipient policy violation.",
            },
        }

    def _allow_with_updated_input() -> dict:
        if updated_tool_input is None:
            return {}
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "updatedInput": updated_tool_input,
            }
        }

    if gateway_mode_active:
        step_id = _ensure_gateway_step()
    else:
        step_id = current_step_id or "unknown"

    if forced_tool_mode_active and not forced_tool_queue:
        forced_tool_mode_active = False
    if gateway_mode_active:
        forced_tool_queue = []
        forced_tool_mode_active = False

    # Early bypass: Allow Task/Bash in harness mode (not crash recovery)
    # This ensures sub-agent delegation and shell commands work during
    # harness execution, even if there are stale 'prepared' entries in DB.
    if (
        tool_name in ("Task", "Bash")
        and _is_harness_mode()
        and not forced_tool_mode_active
    ):
        # Fall through to normal ledger preparation below
        pass
    elif forced_tool_queue:
        expected = forced_tool_queue[0]
        if _forced_tool_matches(tool_name, tool_input, expected):
            forced_tool_active_ids[tool_call_id] = expected
            expected["attempts"] = expected.get("attempts", 0) + 1
            try:
                _assert_prepared_tool_row(
                    expected["tool_call_id"],
                    expected.get("raw_tool_name")
                    or expected.get("tool_name")
                    or tool_name,
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
                    "decision": "block",
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": "Prepared ledger row missing.",
                    },
                }
            return _allow_with_updated_input()
        if raw_tool_input is not tool_input and _forced_tool_matches(
            tool_name, raw_tool_input, expected
        ):
            forced_tool_active_ids[tool_call_id] = expected
            updated_tool_input = raw_tool_input
            tool_input = raw_tool_input
            expected["attempts"] = expected.get("attempts", 0) + 1
            try:
                _assert_prepared_tool_row(
                    expected["tool_call_id"],
                    expected.get("raw_tool_name")
                    or expected.get("tool_name")
                    or tool_name,
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
                    "decision": "block",
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": "Prepared ledger row missing.",
                    },
                }
            return _allow_with_updated_input()

        # Note: Task/Bash harness bypass moved to line ~820 (early bypass before forced_tool_queue)

        if tool_name in ("Write", "Edit", "MultiEdit"):
            if _forced_task_active():
                return _allow_with_updated_input()
            return {
                "systemMessage": (
                    "Recovery in progress: do not use file edit tools. "
                    "Re-run the exact in-flight tool call shown in the replay queue."
                ),
                "decision": "block",
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "Forced replay active; file edit tool blocked.",
                },
            }
        if not _forced_task_active():
            expected_input = json.dumps(expected.get("tool_input") or {}, indent=2)
            expected_tool = expected.get("raw_tool_name") or expected.get("tool_name")
            return {
                "systemMessage": (
                    "Recovery in progress: re-run the exact in-flight tool call. "
                    f"Next required tool: {expected_tool} with input {expected_input}"
                ),
                "decision": "block",
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "Forced in-flight replay active.",
                },
            }

    if _is_job_run() and tool_name in (
        "Write",
        "Edit",
        "MultiEdit",
        "Read",
        "COMPOSIO_REMOTE_WORKBENCH",
    ):
        if tool_name == "COMPOSIO_REMOTE_WORKBENCH":
            return {
                "systemMessage": (
                    "⚠️ Durable job mode: COMPOSIO_REMOTE_WORKBENCH is disabled. "
                    "Use local toolkit tools or the allowed Composio tools instead."
                ),
                "decision": "block",
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "Remote workbench blocked in durable job mode.",
                },
            }
        return {
            "systemMessage": (
                "⚠️ Durable job mode: use the local toolkit file tools instead of "
                f"{tool_name}. For example, use the native `Read` tool "
                "or native `Write` tool."
            ),
            "decision": "block",
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "File tool blocked in durable job mode.",
            },
        }

    schema_input_data = dict(input_data)
    schema_input_data["tool_input"] = tool_input
    schema_guardrail = await pre_tool_use_schema_guardrail(
        schema_input_data,
        run_id=run_id,
        step_id=current_step_id,
        logger=logfire,
        skip_guardrail=forced_tool_mode_active,
        schema_fetcher=_fetch_composio_tool_schema,
    )
    if schema_guardrail:
        hook_output = schema_guardrail.get("hookSpecificOutput", {}) or {}
        updated_input = hook_output.get("updatedInput")
        if (
            updated_input is not None
            and schema_guardrail.get("decision") != "block"
            and hook_output.get("permissionDecision") != "deny"
        ):
            updated_tool_input = updated_input
            tool_input = updated_input
        else:
            return schema_guardrail

    side_effect_class = "unknown"
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
        identity = parse_tool_identity(tool_name)
        side_effect_class = classify_tool(
            identity.tool_name,
            identity.tool_namespace,
            {"raw_tool_name": tool_name},
        )
        side_effect_class = _normalize_side_effect_class(side_effect_class, tool_name)
        if _should_inject_provider_idempotency(tool_name, side_effect_class):
            _inject_provider_idempotency(
                tool_name, tool_input, decision.idempotency_key
            )
    except Exception as exc:
        logfire.warning("ledger_prepare_failed", tool_name=tool_name, error=str(exc))
        return {
            "systemMessage": (
                "⚠️ Tool ledger prepare failed; refusing to execute tool without a "
                "prepared ledger row. Try again or end the turn."
            ),
            "decision": "block",
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "Ledger prepare failed.",
            },
        }

    if gateway_mode_active and tool_ledger.get_tool_call(tool_call_id) is None:
        try:
            identity = parse_tool_identity(tool_name)
            tool_ledger.prepare_tool_call(
                tool_call_id=tool_call_id,
                run_id=run_id,
                step_id=step_id,
                tool_name=identity.tool_name,
                tool_namespace=identity.tool_namespace,
                raw_tool_name=tool_name,
                tool_input=tool_input,
                metadata={"raw_tool_name": tool_name},
                allow_duplicate=True,
                idempotency_nonce=tool_call_id,
            )
            logfire.info(
                "ledger_prepare_gateway_recover",
                tool_name=tool_name,
                tool_use_id=tool_call_id,
                run_id=run_id,
                step_id=step_id,
            )
        except Exception as exc:
            logfire.warning(
                "ledger_prepare_gateway_recover_failed",
                tool_name=tool_name,
                tool_use_id=tool_call_id,
                error=str(exc),
            )

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
            # [ENHANCED IDEMPOTENCY]
            # If the previous result was None, empty, or an error, we SHOULD allow the retry.
            # This handles cases where a tool timed out or failed silently (common with search)
            # and the agent wants to try again with the exact same arguments.
            cached_response = decision.receipt.response_ref or ""
            is_valid_cache = (
                cached_response 
                and str(cached_response).strip() 
                and str(cached_response).strip().lower() != "none"
                and "error" not in str(cached_response).lower()[:50]
            )
            
            if is_valid_cache:
                logfire.warning(
                    "tool_deduped",
                    tool_name=tool_name,
                    tool_use_id=tool_call_id,
                    idempotency_key=decision.idempotency_key,
                )
                receipt_preview = cached_response[:500]
                return {
                    "systemMessage": (
                        "⚠️ Idempotency guard: tool call already succeeded. "
                        "Skipping execution and using cached receipt."
                    ),
                    "reason": receipt_preview,
                    "decision": "block",
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": "Idempotent tool call detected.",
                    },
                }
            else:
                logfire.info(
                    "idempotency_bypass_retry", 
                    tool_name=tool_name,
                    reason="Cached result was empty/error - allowing retry"
                )
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
                "decision": "block",
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "Ledger prepare failed.",
                },
            }

    try:
        _assert_prepared_tool_row(tool_call_id, tool_name)
        tool_ledger.mark_running(tool_call_id)
        # Record tool execution start time for duration tracking
        tool_execution_start_times[tool_call_id] = (time.time(), tool_name)
        logfire.info(
            "ledger_mark_running",
            tool_use_id=tool_call_id,
            run_id=run_id,
            step_id=step_id,
        )
    except Exception as exc:
        logfire.warning(
            "ledger_mark_running_failed", tool_use_id=tool_call_id, error=str(exc)
        )
        return {
            "systemMessage": (
                "⚠️ Tool ledger missing prepared entry; refusing to execute tool. "
                "Try again or end the turn."
            ),
            "decision": "block",
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "Prepared ledger row missing.",
            },
        }
    if not forced_tool_mode_active and not forced_tool_queue:
        if side_effect_class == "read_only":
            _ensure_phase_checkpoint(
                run_id=run_id,
                step_id=step_id,
                checkpoint_type="pre_read_only",
                phase="pre_read_only",
                tool_name=tool_name,
                note="first_read_only_in_step",
            )
        else:
            _ensure_phase_checkpoint(
                run_id=run_id,
                step_id=step_id,
                checkpoint_type="pre_side_effect",
                phase="pre_side_effect",
                tool_name=tool_name,
                note="first_side_effect_in_step",
            )

    # [Bash Scaffolding] Audit log all shell commands for visibility
    bash_workspace = input_data.get("workspace_dir") or OBSERVER_WORKSPACE_DIR
    if tool_name.upper() == "BASH" and bash_workspace:
        try:
            cmd = tool_input.get("command") or tool_input.get("cmd") or str(tool_input)
            audit_file = os.path.join(bash_workspace, "bash_audit.log")
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(audit_file, "a", encoding="utf-8") as af:
                af.write(f"[{ts}] {cmd}\n")
            
            # [PDF Rendering Instrumentation] Log PDF render start
            cmd_lower = cmd.lower()
            if "print-to-pdf" in cmd_lower or ("pdf" in cmd_lower and ("chrome" in cmd_lower or "chromium" in cmd_lower)):
                # Extract output path from command (e.g., --print-to-pdf="output.pdf")
                import re
                pdf_match = re.search(r'print-to-pdf[="\s]+([^\s"]+)', cmd)
                pdf_output = pdf_match.group(1).strip('"\'') if pdf_match else "unknown"
                logfire.info(
                    "pdf_render_started",
                    tool_call_id=tool_call_id,
                    command_preview=cmd[:200],
                    output_path=pdf_output,
                    run_id=run_id,
                )
        except Exception:
            pass  # Non-critical visibility feature

    return _allow_with_updated_input()


async def on_post_tool_use_ledger(
    input_data: dict, tool_use_id: object, context: dict
) -> dict:
    """
    PostToolUse Hook: persist tool response to the ledger.
    """
    global \
        tool_ledger, \
        forced_tool_active_ids, \
        forced_tool_queue, \
        runtime_db_conn, \
        run_id, \
        gateway_mode_active
    if tool_ledger is None:
        return {}

    tool_call_id = str(tool_use_id or "")
    if gateway_mode_active:
        tool_call_id = _get_gateway_tool_call_id(tool_use_id)
    if not tool_call_id:
        return {}

    ledger_entry = tool_ledger.get_tool_call(tool_call_id) if tool_ledger else None
    raw_tool_name = (
        (ledger_entry or {}).get("raw_tool_name")
        or input_data.get("tool_name", "")
        or ""
    )
    side_effect_class = (ledger_entry or {}).get("side_effect_class")
    tool_input = input_data.get("tool_input", {}) or {}

    tool_response = input_data.get("tool_response")
    is_error = False
    error_detail = ""
    if input_data.get("is_error"):
        is_error = True
    if isinstance(tool_response, dict):
        is_error = bool(tool_response.get("is_error") or tool_response.get("error"))
        if tool_response.get("error"):
            error_detail = str(tool_response.get("error"))

    if is_malformed_tool_name(raw_tool_name):
        if not (forced_tool_queue or forced_tool_mode_active):
            _mark_run_waiting_for_human(
                "malformed_tool_name",
                tool_name=raw_tool_name,
                tool_call_id=tool_call_id,
            )

    expected = None
    if tool_call_id in forced_tool_active_ids:
        expected = forced_tool_active_ids.pop(tool_call_id)
    elif forced_tool_queue and _forced_tool_matches(
        raw_tool_name, tool_input, forced_tool_queue[0]
    ):
        expected = forced_tool_queue[0]
        logfire.info(
            "replay_tool_use_id_missing",
            tool_use_id=tool_call_id,
            replay_tool_call_id=expected.get("tool_call_id"),
            raw_tool_name=raw_tool_name,
        )

    if expected is not None:
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
                external_id = None
                if isinstance(tool_response, dict):
                    external_id = (
                        tool_response.get("id")
                        or tool_response.get("message_id")
                        or tool_response.get("request_id")
                    )
                _maybe_crash_after_tool(
                    raw_tool_name=expected.get("raw_tool_name") or "",
                    tool_call_id=expected["tool_call_id"],
                    stage="after_tool_success_before_receipt",
                    tool_input=expected.get("tool_input") or {},
                )
                if tool_ledger:
                    recorded = tool_ledger.record_receipt_pending(
                        expected["tool_call_id"], tool_response, external_id
                    )
                    if not recorded:
                        logfire.warning(
                            "receipt_pending_record_failed",
                            tool_call_id=expected["tool_call_id"],
                        )
                _maybe_crash_after_tool(
                    raw_tool_name=expected.get("raw_tool_name") or "",
                    tool_call_id=expected["tool_call_id"],
                    stage="after_tool_success_before_ledger_commit",
                    tool_input=expected.get("tool_input") or {},
                )
                tool_ledger.mark_succeeded(
                    expected["tool_call_id"], tool_response, external_id
                )
                if tool_ledger:
                    tool_ledger.clear_pending_receipt(expected["tool_call_id"])
                tool_ledger.mark_replay_status(expected["tool_call_id"], "succeeded")
                _maybe_crash_after_tool(
                    raw_tool_name=expected.get("raw_tool_name") or "",
                    tool_call_id=expected["tool_call_id"],
                    stage="after_ledger_mark_succeeded",
                    tool_input=expected.get("tool_input") or {},
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
                if not forced_tool_queue:
                    forced_tool_mode_active = False
                    forced_tool_active_ids = {}
                    logfire.info(
                        "replay_queue_drained",
                        run_id=run_id,
                        step_id=current_step_id,
                        tool_call_id=expected["tool_call_id"],
                    )
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
            if side_effect_class and side_effect_class != "read_only":
                _mark_run_waiting_for_human(
                    "side_effect_tool_failed",
                    tool_name=raw_tool_name,
                    tool_call_id=tool_call_id,
                )
        else:
            if not raw_tool_name:
                if tool_ledger:
                    ledger_entry = tool_ledger.get_tool_call(tool_call_id)
                    raw_tool_name = (ledger_entry or {}).get("raw_tool_name") or ""
                if not raw_tool_name:
                    raw_tool_name = input_data.get("tool_name", "") or ""
            external_id = None
            if isinstance(tool_response, dict):
                external_id = (
                    tool_response.get("id")
                    or tool_response.get("message_id")
                    or tool_response.get("request_id")
                )
            _maybe_crash_after_tool(
                raw_tool_name=raw_tool_name,
                tool_call_id=tool_call_id,
                stage="after_tool_success_before_receipt",
                tool_input=tool_input,
            )
            if tool_ledger:
                recorded = tool_ledger.record_receipt_pending(
                    tool_call_id, tool_response, external_id
                )
                if not recorded:
                    logfire.warning(
                        "receipt_pending_record_failed",
                        tool_call_id=tool_call_id,
                    )
            _maybe_crash_after_tool(
                raw_tool_name=raw_tool_name,
                tool_call_id=tool_call_id,
                stage="after_tool_success_before_ledger_commit",
                tool_input=tool_input,
            )
            tool_ledger.mark_succeeded(tool_call_id, tool_response, external_id)
            if tool_ledger:
                tool_ledger.clear_pending_receipt(tool_call_id)
            _maybe_crash_after_tool(
                raw_tool_name=raw_tool_name,
                tool_call_id=tool_call_id,
                stage="after_ledger_mark_succeeded",
                tool_input=tool_input,
            )
            logfire.info(
                "ledger_mark_succeeded",
                tool_use_id=tool_call_id,
                run_id=run_id,
                step_id=current_step_id,
                external_correlation_id=external_id,
            )
            # Log tool execution span with duration
            start_info = tool_execution_start_times.pop(tool_call_id, None)
            if start_info:
                start_time, tool_name_recorded = start_info
                duration_seconds = time.time() - start_time
                logfire.info(
                    "tool_execution_completed",
                    tool_name=tool_name_recorded,
                    tool_use_id=tool_call_id,
                    duration_seconds=round(duration_seconds, 3),
                    run_id=run_id,
                    step_id=current_step_id,
                    status="succeeded",
                )
                
            # [PDF Rendering Instrumentation] Log PDF render completion with output size
            if raw_tool_name and raw_tool_name.upper() == "BASH":
                cmd = tool_input.get("command") or tool_input.get("cmd") or ""
                cmd_lower = cmd.lower()
                if "print-to-pdf" in cmd_lower or ("pdf" in cmd_lower and ("chrome" in cmd_lower or "chromium" in cmd_lower)):
                    try:
                        import re
                        pdf_match = re.search(r'print-to-pdf[="\s]+([^\s"]+)', cmd)
                        if pdf_match:
                            pdf_output = pdf_match.group(1).strip('"\'')
                            # Try to get file size if path is accessible
                            pdf_size = None
                            workspace = OBSERVER_WORKSPACE_DIR or ""
                            if workspace and not os.path.isabs(pdf_output):
                                pdf_path = os.path.join(workspace, pdf_output)
                            else:
                                pdf_path = pdf_output
                            if os.path.exists(pdf_path):
                                pdf_size = os.path.getsize(pdf_path)
                            logfire.info(
                                "pdf_render_completed",
                                tool_call_id=tool_call_id,
                                output_path=pdf_output,
                                output_size_bytes=pdf_size,
                                duration_seconds=round(duration_seconds, 3) if start_info else None,
                                run_id=run_id,
                            )
                    except Exception:
                        pass  # Non-critical
    except Exception as exc:
        logfire.warning(
            "ledger_mark_result_failed", tool_use_id=tool_call_id, error=str(exc)
        )
        # Log failed tool execution with duration if we have start time
        start_info = tool_execution_start_times.pop(tool_call_id, None)
        if start_info:
            start_time, tool_name_recorded = start_info
            duration_seconds = time.time() - start_time
            logfire.info(
                "tool_execution_completed",
                tool_name=tool_name_recorded,
                tool_use_id=tool_call_id,
                duration_seconds=round(duration_seconds, 3),
                run_id=run_id,
                step_id=current_step_id,
                status="failed",
                error=str(exc),
            )
    finally:
        if gateway_mode_active:
            gateway_tool_call_map.pop(str(tool_use_id or ""), None)
        # Cleanup any orphaned start times
        tool_execution_start_times.pop(tool_call_id, None)

    return {}


async def on_post_tool_use_validation(
    input_data: dict, tool_use_id: object, context: dict
) -> dict:
    """
    PostToolUse Hook: nudge the model when schema validation fails.
    """
    return await post_tool_use_schema_nudge(
        input_data,
        run_id=run_id,
        step_id=current_step_id,
        logger=logfire,
    )


async def on_post_research_finalized_cache(
    input_data: dict, tool_use_id: object, context: dict
) -> dict:
    """
    PostToolUse Hook: Cache refined_corpus.md for sub-agent context restoration.
    
    When finalize_research completes successfully, save the refined corpus to the
    checkpoints table. This is THE canonical research checkpoint - if the harness
    restarts after research but before report generation, this is all we need.
    
    The refined_corpus.md is:
    - Token-efficient (~10K tokens vs ~50K raw)
    - Citation-preserving (sources, dates, URLs)
    - LLM-ready (extracted facts, quotes, statistics)
    """
    global run_id, current_step_id, runtime_db_conn
    
    # Only process finalize_research tool results
    tool_name = context.get("tool_name", "") or input_data.get("tool_name", "")
    if "finalize_research" not in tool_name:
        return {}
    
    # Extract the result to find the refined corpus path
    result = input_data.get("tool_result", "") or input_data.get("result", "")
    if not result:
        return {}
    
    # Parse the JSON result to get refined_corpus path
    refined_corpus_path = None
    try:
        import json
        if isinstance(result, str):
            data = json.loads(result)
        elif isinstance(result, dict):
            data = result
        else:
            return {}
        
        # Extract path from the refined_corpus section
        if isinstance(data, dict):
            refined_info = data.get("refined_corpus", {})
            if refined_info:
                refined_corpus_path = refined_info.get("path")
    except (json.JSONDecodeError, KeyError):
        return {}
    
    if not refined_corpus_path:
        return {}
    
    # Read the refined corpus content
    try:
        if os.path.exists(refined_corpus_path):
            with open(refined_corpus_path, "r", encoding="utf-8") as f:
                corpus_text = f.read()
        else:
            logfire.warning("refined_corpus_not_found", path=refined_corpus_path)
            return {}
    except Exception as exc:
        logfire.warning("refined_corpus_read_failed", error=str(exc))
        return {}
    
    # Only cache if we have substantial content
    if len(corpus_text) < 500:
        logfire.warning("refined_corpus_too_small", chars=len(corpus_text))
        return {}
    
    # Save to checkpoint database - this is THE canonical research checkpoint
    try:
        if runtime_db_conn and run_id and current_step_id:
            save_checkpoint(
                runtime_db_conn,
                run_id=run_id,
                step_id=current_step_id,
                checkpoint_type="refined_corpus_cache",
                state_snapshot={
                    "source": "refined_corpus.md",
                    "path": refined_corpus_path,
                    "chars": len(corpus_text),
                    "words": len(corpus_text.split()),
                },
                corpus_data=corpus_text,
            )
            logfire.info(
                "refined_corpus_cached",
                run_id=run_id,
                corpus_chars=len(corpus_text),
                path=refined_corpus_path,
            )
            print(f"\n✅ Research checkpoint saved: refined_corpus.md ({len(corpus_text):,} chars)")
    except Exception as exc:
        logfire.warning("refined_corpus_cache_failed", error=str(exc))
    
    return {}


async def on_post_email_send_artifact(
    input_data: dict, tool_use_id: object, context: dict
) -> dict:
    """PostToolUse Hook: persist Gmail send evidence to workspace artifacts."""
    tool_name = context.get("tool_name", "") or input_data.get("tool_name", "")
    tool_input = input_data.get("tool_input", {}) or {}
    tool_response = input_data.get("tool_response") or input_data.get("tool_result")

    if not OBSERVER_WORKSPACE_DIR:
        return {}

    def _load_json_payload(payload):
        if isinstance(payload, dict):
            return payload
        if isinstance(payload, str):
            try:
                return json.loads(payload)
            except json.JSONDecodeError:
                return None
        if isinstance(payload, list):
            for item in payload:
                if isinstance(item, dict) and "text" in item:
                    try:
                        return json.loads(item.get("text") or "")
                    except json.JSONDecodeError:
                        continue
        return None

    def _write_artifact(records):
        if not records:
            return
        base_dir = os.path.join(
            OBSERVER_WORKSPACE_DIR, "work_products", "email_verification"
        )
        os.makedirs(base_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = os.path.join(base_dir, f"email_send_{ts}.json")
        try:
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump({"emails": records}, f, indent=2)
        except Exception:
            return

    records = []

    if "COMPOSIO_MULTI_EXECUTE_TOOL" in tool_name:
        tools = tool_input.get("tools") if isinstance(tool_input, dict) else None
        payload = _load_json_payload(tool_response)
        results = []
        if isinstance(payload, dict):
            results = payload.get("data", {}).get("results", [])
        if isinstance(tools, list):
            for idx, tool in enumerate(tools):
                if tool.get("tool_slug") != "GMAIL_SEND_EMAIL":
                    continue
                args = tool.get("arguments", {}) or {}
                response = None
                for res in results:
                    if res.get("index") == idx:
                        response = res.get("response", {}).get("data", {})
                        break
                records.append(
                    {
                        "tool": "GMAIL_SEND_EMAIL",
                        "recipient_email": args.get("recipient_email"),
                        "subject": args.get("subject"),
                        "body_preview": (args.get("body") or "")[:2000],
                        "attachment": args.get("attachment"),
                        "response": response,
                    }
                )
    elif "GMAIL_SEND_EMAIL" in tool_name:
        payload = _load_json_payload(tool_response)
        response = None
        if isinstance(payload, dict):
            response = payload.get("data") or payload
        if isinstance(tool_input, dict):
            records.append(
                {
                    "tool": "GMAIL_SEND_EMAIL",
                    "recipient_email": tool_input.get("recipient_email"),
                    "subject": tool_input.get("subject"),
                    "body_preview": (tool_input.get("body") or "")[:2000],
                    "attachment": tool_input.get("attachment"),
                    "response": response,
                }
            )

    _write_artifact(records)
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
                ".claude",
                "skills",
                skill_name,
                "SKILL.md",
            )
            if os.path.exists(skill_path):
                logfire.info(
                    "skill_hint_backup_injected",
                    skill=skill_name,
                    command_preview=command[:100],
                )
                return {
                    "systemMessage": (
                        f"⚠️ SKILL REMINDER: You're about to create {skill_name.upper()} content. "
                        f"The `{skill_name}` skill at `{skill_path}` has proven patterns. "
                        f"Consider reading it FIRST to avoid common issues."
                    )
                }

    return {}


async def on_pre_bash_block_composio_sdk(
    input_data: dict, tool_use_id: object, context: dict
) -> dict:
    """
    PreToolUse Hook: Block Bash commands that attempt to call Composio SDK directly.

    This prevents agents from bypassing the MCP architecture by brute-forcing
    Python/SDK calls through Bash. All Composio interactions should go through
    MCP tools (mcp__composio__*) which handle auth and sessions properly.
    """
    command = str(input_data.get("command", "") or "")
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
        "slack_send_message",  # Other common Composio tools
    ]

    if any(pattern in command_lower for pattern in composio_sdk_patterns):
        logfire.warning(
            "bash_composio_sdk_blocked",
            command_preview=command[:200],
            tool_use_id=str(tool_use_id),
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

    return {}


# Prompt keywords that suggest skill-relevant tasks
# These are auto-generated from skill descriptions + skill names
# Override specific skills here if needed
SKILL_PROMPT_TRIGGERS_OVERRIDE = {
    # Format: "skill-name": ["keyword1", "keyword2", ...]
    # Leave empty to use auto-generated triggers from description
    "image-generation": [
        "image",
        "generate image",
        "create image",
        "edit image",
        "picture",
        "photo",
        "illustration",
        "graphic",
        "infographic",
        "visual",
        "design",
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
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
                        words = re.findall(r"\b[a-zA-Z]{4,}\b", description.lower())
                        # Filter common words, keep domain-specific terms
                        stop_words = {
                            "this",
                            "that",
                            "with",
                            "from",
                            "have",
                            "when",
                            "should",
                            "used",
                            "using",
                            "skill",
                            "create",
                            "comprehensive",
                            "editing",
                            "creation",
                        }
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
        logfire.info(
            "skill_awareness_registry_initialized", skill_count=len(self.skills)
        )

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

        expected_str = (
            ", ".join(expected_skills) if expected_skills else "none specified"
        )

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
    "browserbase": [],  # Uses Composio MCP tools, not skills
    "video-remotion-expert": ["video-remotion"],
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

    tool_knowledge = get_tool_knowledge_block()
    combined_context = ""
    if awareness_context:
        combined_context = awareness_context
    if tool_knowledge:
        combined_context = (
            f"{combined_context}\n\n{tool_knowledge}".strip()
            if combined_context
            else tool_knowledge
        )

    # Inject Letta memory for sub-agent (if enabled).
    memory_context = await _get_subagent_memory_context(tool_input)
    if memory_context:
        combined_context = (
            f"{combined_context}\n\n# 🧠 LETTA MEMORY\n{memory_context}".strip()
            if combined_context
            else f"# 🧠 LETTA MEMORY\n{memory_context}"
        )

    if combined_context:
        logfire.info(
            "skill_awareness_injected_to_subagent",
            subagent_type=subagent_type,
            expected_skills=expected_skills,
        )
        if memory_context:
            print(
                f"🧠 Injected Letta memory for sub-agent: {subagent_type or 'unknown'} "
                f"({len(memory_context)} chars)"
            )
        return {"systemMessage": combined_context}

    return {}


async def on_post_task_guidance(
    input_data: dict, tool_use_id: object, context: dict
) -> dict:
    """
    PostToolUse Hook for Task (sub-agent) calls.
    
    1. Prevent TaskOutput usage by reinforcing relaunch-only guidance
    2. Detect sub-agent Write failures and provide recovery guidance
    """
    tool_name = str(input_data.get("tool_name", "") or "")
    if tool_name.lower() != "task":
        return {}
    
    # Check if the Task result contains error indicators
    tool_result = input_data.get("tool_result", {})
    result_content = ""
    
    # Extract result content (could be in various formats)
    if isinstance(tool_result, dict):
        content_list = tool_result.get("content", [])
        for block in content_list:
            if isinstance(block, dict) and block.get("type") == "text":
                result_content += block.get("text", "")
    elif isinstance(tool_result, str):
        result_content = tool_result
    
    # Detect sub-agent Write failures
    error_indicators = [
        "InputValidationError",
        "required parameter `file_path` is missing",
        "required parameter `content` is missing",
        "0 bytes",
    ]
    
    sub_agent_write_failed = any(
        indicator.lower() in result_content.lower() 
        for indicator in error_indicators
    )
    
    if sub_agent_write_failed:
        logfire.warning(
            "subagent_write_failure_detected",
            tool_use_id=str(tool_use_id),
        )
        return {
            "systemMessage": (
                "⚠️ SUB-AGENT WRITE FAILURE DETECTED\n\n"
                "The report-creation-expert sub-agent failed to write the report due to context overflow. "
                "This happens when the report content is too large.\n\n"
                "**Recovery Options:**\n"
                "1. **Re-launch sub-agent** with explicit chunking instruction:\n"
                "   'Write the report in chunks of under 5000 chars using append_to_file'\n"
                "2. **Create partial report** yourself using the research notes\n"
                "3. **Check work_products/** for any partial files the sub-agent created\n\n"
                "DO NOT simply re-run the same Task call - it will fail again."
            ),
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": "Sub-agent Write failure - retry with chunking.",
            },
        }
    
    # Standard guidance: prevent TaskOutput usage
    return {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": (
                "TaskOutput/TaskResult are disabled and will not work. "
                "Do NOT call them. Wait for SubagentStop guidance or relaunch the Task "
                "with the original inputs if output is needed."
            ),
        }
    }


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
                skill_path = os.path.join(
                    project_root, ".claude", "skills", skill_name, "SKILL.md"
                )
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
                                    description = (
                                        desc[:100] + "..." if len(desc) > 100 else desc
                                    )
                    except Exception:
                        pass
                    matched_skills.append((skill_name, skill_path, description))

        if matched_skills:
            skill_list = "\n".join(
                [
                    f"  - {name}: {desc}\n    Path: {path}"
                    for name, path, desc in matched_skills
                ]
            )
            logfire.info(
                "skill_awareness_early_injected", skills=[s[0] for s in matched_skills]
            )

            # UserPromptSubmit hooks use hookSpecificOutput with additionalContext
            return {
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": (
                        f"SKILL GUIDANCE: This task involves document creation. "
                        f"BEFORE writing code, read the relevant skill(s) for proven patterns:\n"
                        f"{skill_list}\n"
                        f"Use read_local_file on the SKILL.md path to load full instructions."
                    ),
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

    # Stop the subagent
    return {
        "systemMessage": "Subagent completed task successfully.",
        "hookSpecificOutput": {
            "hookEventName": "SubagentStop",
            "action": "stop",
        },
    }


def check_harness_threshold(
    token_usage_approx: int,
    iteration: int,
    max_iterations: int,
    force_debug: bool = False,
) -> bool:
    """
    Hybrid Trigger for Harness Handoff.
    Returns True if we should force a restart.

    Conditions:
    1. Debug flag is set (UA_DEBUG_FORCE_HANDOFF)
    2. Context usage is high (>90% = 180k of 200k tokens) AND we are at a natural break
    """
    if force_debug or os.getenv("UA_DEBUG_FORCE_HANDOFF") == "1":
        return True

    # 90% of 200k context window (aligned with Anthropic pattern)
    TOKEN_THRESHOLD = 180000
    if token_usage_approx > TOKEN_THRESHOLD:
        return True

    return False


def _mark_tasks_for_retry(mission_path: str, task_ids: list) -> None:
    """
    Update mission.json to mark specific tasks for re-execution.
    Changes status from 'COMPLETED' to 'RETRY' for failed tasks.
    """
    if not os.path.exists(mission_path):
        return
    
    try:
        with open(mission_path, "r") as f:
            mission = json.load(f)
        
        def mark_retry(node):
            if isinstance(node, dict):
                if node.get("id") in task_ids:
                    node["status"] = "RETRY"
                for v in node.values():
                    if isinstance(v, (dict, list)):
                        mark_retry(v)
            elif isinstance(node, list):
                for item in node:
                    mark_retry(item)
        
        mark_retry(mission)
        
        with open(mission_path, "w") as f:
            json.dump(mission, f, indent=2)
        
        print(f"📝 Marked {len(task_ids)} tasks for retry: {task_ids}")
    except Exception as e:
        print(f"⚠️ Failed to mark tasks for retry: {e}")


def _extract_promise_from_log(workspace_dir: str, tail_lines: int = 100) -> str:
    """
    Fallback: Extract recent content from run.log if response_text is empty.
    
    This handles the race condition where the agent's output is printed
    but not captured in the return value (the "harness stdout bug").
    
    Args:
        workspace_dir: Path to the workspace directory containing run.log
        tail_lines: Number of lines to read from the end of the log
        
    Returns:
        The last N lines of run.log as a string, or empty string on failure
    """
    return read_run_log_tail(workspace_dir, tail_lines=tail_lines)


def on_agent_stop(context: HookContext, run_id: str = None, db_conn=None) -> dict:
    """
    Post-Run Hook: Checks for Harness Loop conditions.
    Prioritizes Verification (Artifacts -> Quality) BEFORE enforcing Protocol (Promise).
    If verification fails, restarts with specific feedback.
    If protocol triggers early, verifies first.
    """
    _emit_composio_schema_metrics()

    # Resolve dependencies (Args > Globals)
    use_run_id = run_id or globals().get("run_id")
    use_db = db_conn or globals().get("runtime_db_conn")

    if not use_run_id or not use_db:
        return {}

    # Load run spec to check for harness config
    info = get_iteration_info(use_db, use_run_id)
    max_iter = info.get("max_iterations") or 10
    current_iter = info.get("iteration_count") or 0
    promise = info.get("completion_promise")

    if not promise:
        # Normal stop, no harness
        return {}

    # Check if promise is fulfilled in output
    # context.output is expected to be the final agent text response
    final_output = context.output if hasattr(context, "output") else ""
    if isinstance(final_output, dict):
        final_output = str(final_output)  # fallback
    
    # [FALLBACK] If output is empty, try reading from run.log
    # This handles the race condition where the agent's output is printed
    # but not captured in the return value (the "harness stdout bug")
    if not final_output.strip() and promise:
        workspace_dir = globals().get("workspace_dir")
        if workspace_dir:
            print("🔄 Primary output capture empty, checking run.log fallback...")
            log_content = _extract_promise_from_log(workspace_dir)
            if log_content:
                final_output = log_content
                print(f"📋 Fallback captured {len(final_output)} chars from run.log")
    
    # Normalize output for logging/debugging
    if len(final_output) > 500:
        preview = final_output[:500] + "..."
    else:
        preview = final_output
    print(f"🔍 Harness Checking Output (Length: {len(final_output)}): {preview!r}")

    # [Detection] Check for Promise Tag (Case Insensitive)
    import re
    # Look for last occurrence if possible, or just search. 
    # Using case-insensitive (?i) and DOTALL.
    # We use findall to get all candidates and check the last one to avoid "I will output..." preamble issues.
    matches = re.findall(r"(?i)<promise>(.*?)</promise>", final_output, re.DOTALL)
    
    extracted_promise = None
    if matches:
        # Take the last one to avoid preamble mentions
        extracted_promise = matches[-1].strip()
        # Collapse whitespace
        extracted_promise = " ".join(extracted_promise.split())
    
    promise_normalized = " ".join(promise.split())
    
    # Determine Intent to Complete
    # 1. Explicit <promise> tag matches
    # 2. Implicit signals (we could check for 'mission complete' but relying on the tag is safer for the protocol)
    # The agent might think it's done but forgot the tag.
    
    # We run verification if the agent *claims* to be done via tag OR if we want to be proactive.
    # For now, let's trigger verification if we found *any* promise tag (even mismatch) or if this is the last iteration.
    
    intent_to_complete = bool(matches)
    
    if intent_to_complete:
        print(f"🕵️ Completion Intent Detected. Candidates: {len(matches)}. Last: '{extracted_promise}'")
        
        # [Step 1] Tiered Verification
        from universal_agent.harness.verifier import TaskVerifier
        
        workspace_dir = globals().get("workspace_dir")
        if not workspace_dir:
            print("⚠️ Harness: No workspace_dir global, skipping verification.")
        else:
            import json as _json
            import glob as _glob
            mission_path = os.path.join(workspace_dir, "mission.json")
            if os.path.exists(mission_path):
                try:
                    with open(mission_path, "r") as f:
                        mission_data = _json.load(f)
                    
                    verifier = TaskVerifier(client=None)  # Tier 2 only (no LLM needed)
                    
                    # Find all COMPLETED tasks with output_artifacts
                    tasks_to_verify = []
                    def find_completed_tasks(node):
                        if isinstance(node, dict):
                            if node.get("status") == "COMPLETED" and node.get("output_artifacts"):
                                tasks_to_verify.append(node)
                            for v in node.values():
                                if isinstance(v, (dict, list)):
                                    find_completed_tasks(v)
                        elif isinstance(node, list):
                            for item in node:
                                find_completed_tasks(item)
                    
                    find_completed_tasks(mission_data)
                    
                    print(f"🧪 Verifying {len(tasks_to_verify)} COMPLETED tasks (Tier 2: Binary + Format)...")
                    
                    failed_tasks = {}  # {task_id: failure_info}
                    
                    for t in tasks_to_verify:
                        task_id = t.get("id", "unknown")
                        # Use tiered verification (default Tier 2 = Binary + Format)
                        passed, msg, failure_info = verifier.verify_task(t, workspace_dir, tier=2)
                        if not passed:
                            failed_tasks[task_id] = {
                                "tier": failure_info.get("tier", "UNKNOWN"),
                                "issue": failure_info.get("issue", msg),
                                "description": t.get("description", ""),
                            }
                    
                    if failed_tasks:
                        # VERIFICATION FAILED - Inject specific feedback
                        feedback_lines = []
                        task_ids_to_retry = []
                        
                        for task_id, info in failed_tasks.items():
                            tier = info.get("tier", "UNKNOWN")
                            issue = info.get("issue", "unknown issue")
                            desc = info.get("description", "")[:50]
                            
                            if tier == "BINARY":
                                feedback_lines.append(f"• {task_id} ({desc}...): MISSING FILES - {issue}")
                            elif tier == "FORMAT":
                                feedback_lines.append(f"• {task_id} ({desc}...): FORMAT ERROR - {issue}")
                            else:
                                feedback_lines.append(f"• {task_id}: {issue}")
                            
                            task_ids_to_retry.append(task_id)
                        
                        # Mark tasks for retry in mission.json
                        _mark_tasks_for_retry(mission_path, task_ids_to_retry)
                        
                        feedback = "\n".join(feedback_lines)
                        print(f"❌ Verification Failed:\n{feedback}")
                        
                        return {
                            "hookSpecificOutput": {
                                "hookEventName": "AgentStop",
                                "action": "restart",
                                "nextPrompt": (
                                    f"VERIFICATION FAILED. Do NOT output the completion promise yet.\n\n"
                                    f"[ISSUES TO FIX]\n{feedback}\n\n"
                                    f"[INSTRUCTION]\n"
                                    f"1. Re-read mission.json - tasks marked 'RETRY' need work\n"
                                    f"2. Execute ONLY the tasks marked as 'RETRY'\n"
                                    f"3. Once fixed, output <promise>{promise}</promise>"
                                )
                            }
                        }
                    
                    print("✅ Verification Passed (Tier 2: Binary + Format).")
                    
                except Exception as e:
                    print(f"⚠️ Harness Verification Error: {e}")

    # [Step 2] Protocol Enforcement (Promise Check)
    if extracted_promise == promise_normalized:
        promise_met = True
        logfire.info("harness_completion_promise_met", run_id=use_run_id)
        return {
            "hookSpecificOutput": {
                "hookEventName": "AgentStop",
                "action": "complete",  # Let it stop naturally
            }
        }
    else:
        # Determine feedback message
        if intent_to_complete:
            msg = f"Promise Mismatch: Expected '<promise>{promise}</promise>', Got '<promise>{extracted_promise}</promise>'"
        else:
            msg = f"The previous attempt did not include the required completion promise <promise>{promise}</promise>"
            
        print(f"⚠️ {msg}")
        return {
            "hookSpecificOutput": {
                "hookEventName": "AgentStop",
                "action": "restart",
                "nextPrompt": f"RESUMING: {msg}\n\n[INSTRUCTION]\nIf you have completed the objective, you MUST output the EXACT promise tag:\n<promise>{promise}</promise>\n\nEnsure all artifacts are created before doing this."
            }
        }
    # If we get here, the promise was NOT met.
    # We must RESTART the agent to force it to finish.

    # Check limits first
    if current_iter >= max_iter:
        logfire.warning(
            "harness_max_iterations_reached",
            run_id=use_run_id,
            current=current_iter,
            max=max_iter,
        )
        return {
            "systemMessage": f"⚠️ Max iterations ({max_iter}) reached without completion promise '{promise}'. Stopping.",
            "hookSpecificOutput": {
                "hookEventName": "AgentStop",
                "action": "stop",
            },
        }

    # Force Restart / Nudge
    return {
        "systemMessage": f"REJECTED: You claimed to be done, but did not provide the required completion promise: <promise>{promise}</promise>. You must complete the task and output the exact promise tag.",
        "hookSpecificOutput": {
            "hookEventName": "AgentStop",
            "action": "restart",
            "nextPrompt": (
                f"RESUMING: The previous attempt did not include the required completion promise <promise>{promise}</promise>. "
                "Continue working until the task is fully complete, then output the promise.\n\n"
                "RUTHLESS AUTONOMY: Do NOT ask the user for guidance. Make reasonable decisions and continue. "
                "You have full authority to proceed. Never output 'Would you like me to...' questions."
            ),
        },
    }

    # PROCEED TO HANDOFF
    # 1. Save checkpoint (happens automatically in main loop mostly, but good to ensure)
    # 2. Increment iteration
    new_iter = increment_iteration_count(use_db, use_run_id)

    # 3. Construct Continuation Prompt
    import json

    run_spec_json = info.get("run_spec_json") or "{}"
    try:
        run_spec = json.loads(run_spec_json)
    except:
        run_spec = {}

    original_objective = run_spec.get(
        "original_objective", "(See system prompt or previous context)"
    )

    continuation_prompt = f"""
You are continuing a long-running task.
Current Iteration: {new_iter} / {max_iter}

## Original Objective
{original_objective}

## Required Completion Artifact
You must output exactly "{promise}" when you are fully done.
You have NOT output this yet, so you must continue.

## Instructions
1. Review your workspace files to see what has been done.
2. Continue the work. Do NOT start over.
"""

    logfire.info("harness_handoff_triggered", run_id=run_id, new_iteration=new_iter)

    return {
        "systemMessage": continuation_prompt,
        "hookSpecificOutput": {
            "hookEventName": "AgentStop",
            "action": "restart",  # Signal to main loop to clear history and restart
            "nextPrompt": continuation_prompt,
        },
    }

    return {}


# =============================================================================

# Session and options will be created in main() after Composio initialization
user_id = "user_123"
session = None


# Options will be created in main() after session is initialized
options = None


# Global session state
run_id: str = "unknown"
current_step_id: Optional[str] = None
trace: dict = {"tool_calls": [], "token_usage": {"input": 0, "output": 0, "total": 0}}
start_ts: float = time.time()
runtime_db_conn: Optional[sqlite3.Connection] = None
current_execution_session: Optional[ExecutionSession] = None
interrupt_requested = False
last_sigint_ts: float | None = None
OBSERVER_WORKSPACE_DIR = os.getcwd() # Default baseline
provider_session_forked_from: Optional[str] = None
gateway_mode_active = False
gateway_tool_call_map: dict[str, str] = {}
tool_execution_start_times: dict[str, tuple[float, str]] = {}  # tool_call_id -> (start_time, tool_name)
forced_tool_queue: list[dict[str, Any]] = []
forced_tool_active_ids: dict[str, dict[str, Any]] = {}
forced_tool_mode_active = False
run_cancelled_by_operator = False
FORCED_TOOL_MAX_ATTEMPTS = 2
TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}
WAITING_STATUSES = {"waiting_for_human"}
_primary_transcript_path: Optional[str] = None
_seen_transcript_paths: set[str] = set()


def _mark_run_waiting_for_human(
    reason: str, *, tool_name: str = "", tool_call_id: str = ""
) -> None:
    if runtime_db_conn and run_id:
        update_run_status(runtime_db_conn, run_id, "waiting_for_human")
    logfire.warning(
        "run_waiting_for_human",
        run_id=run_id,
        reason=reason,
        tool_name=tool_name,
        tool_call_id=tool_call_id,
    )


def _maybe_mark_run_succeeded() -> None:
    if not runtime_db_conn or not run_id:
        return
    status = get_run_status(runtime_db_conn, run_id)
    if status in WAITING_STATUSES or status in TERMINAL_STATUSES:
        return
    update_run_status(runtime_db_conn, run_id, "succeeded")


def _resolve_input_paths(inputs: dict, workspace_dir: Optional[str]) -> dict:
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
        job_prompt += "\n\nConstraints:\n" + json.dumps(
            run_spec["constraints"], indent=2
        )
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
    print_job_completion_summary(
        conn, run_id, "cancelled", workspace_dir, "", trace, update_restart_file
    )
    return True


def _strip_idempotency_fields(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _strip_idempotency_fields(val)
            for key, val in value.items()
            if key not in ("idempotency_key", "client_request_id")
        }
    if isinstance(value, list):
        return [_strip_idempotency_fields(item) for item in value]
    if isinstance(value, tuple):
        return [_strip_idempotency_fields(item) for item in value]
    if isinstance(value, set):
        return sorted(_strip_idempotency_fields(item) for item in value)
    return value


def _normalize_tool_input(tool_input: Any) -> str:
    try:
        sanitized = _strip_idempotency_fields(tool_input or {})
        return normalize_json(sanitized)
    except Exception:
        return json.dumps(tool_input or {}, default=str)


def _inject_provider_idempotency(
    raw_tool_name: str, tool_input: Any, idempotency_key: str
) -> None:
    if not idempotency_key:
        return
    if not isinstance(tool_input, dict):
        return
    if "client_request_id" not in tool_input:
        tool_input["client_request_id"] = idempotency_key
    upper = raw_tool_name.upper()
    if "COMPOSIO_MULTI_EXECUTE_TOOL" in upper:
        tools = tool_input.get("tools")
        if not isinstance(tools, list):
            return
        for idx, entry in enumerate(tools):
            if not isinstance(entry, dict):
                continue
            args = entry.get("arguments")
            if not isinstance(args, dict):
                continue
            if "client_request_id" in args or "idempotency_key" in args:
                continue
            args["client_request_id"] = f"{idempotency_key}:{idx}"


def _looks_like_composio_tool(raw_tool_name: str) -> bool:
    upper = raw_tool_name.upper()
    return upper.startswith("COMPOSIO_") or upper.startswith("MCP__COMPOSIO__")


_COMPOSIO_SCHEMA_CACHE: dict[str, ToolSchema] = {}
_COMPOSIO_SCHEMA_ALLOWLIST: Optional[set[str]] = None
_COMPOSIO_SCHEMA_METRICS = {
    "fetches": 0,
    "cache_hits": 0,
    "total_ms": 0.0,
    "max_ms": 0.0,
    "reported": False,
}


def _get_composio_schema_allowlist() -> set[str]:
    global _COMPOSIO_SCHEMA_ALLOWLIST
    if _COMPOSIO_SCHEMA_ALLOWLIST is not None:
        return _COMPOSIO_SCHEMA_ALLOWLIST
    raw = os.getenv("UA_COMPOSIO_SCHEMA_ALLOWLIST", "")
    allowlist = {item.strip().upper() for item in raw.split(",") if item.strip()}
    _COMPOSIO_SCHEMA_ALLOWLIST = allowlist
    return allowlist


def _composio_schema_fetch_enabled() -> bool:
    return os.getenv("UA_COMPOSIO_SCHEMA_FETCH", "1").strip().lower() not in {
        "0",
        "false",
        "no",
    }


def _fetch_composio_tool_schema(tool_name: str) -> Optional[ToolSchema]:
    if not _composio_schema_fetch_enabled():
        return None
    if not _looks_like_composio_tool(tool_name):
        return None

    slug = tool_name.split("__")[-1].upper()
    allowlist = _get_composio_schema_allowlist()
    if allowlist and slug not in allowlist:
        return None
    if slug in _COMPOSIO_SCHEMA_CACHE:
        _COMPOSIO_SCHEMA_METRICS["cache_hits"] += 1
        if os.getenv("UA_COMPOSIO_SCHEMA_LOG", "").lower() in {"1", "true", "yes"}:
            print(f"🧭 Composio schema cache hit: {slug}")
        return _COMPOSIO_SCHEMA_CACHE[slug]
    if composio is None:
        return None

    start = time.perf_counter()
    try:
        raw_tool = composio.tools.get_raw_composio_tool_by_slug(slug)
    except Exception as exc:
        duration_ms = (time.perf_counter() - start) * 1000
        logfire.warning(
            "composio_schema_fetch_failed",
            tool_name=slug,
            duration_ms=round(duration_ms, 2),
            error=str(exc),
        )
        return None

    duration_ms = (time.perf_counter() - start) * 1000
    _COMPOSIO_SCHEMA_METRICS["fetches"] += 1
    _COMPOSIO_SCHEMA_METRICS["total_ms"] += duration_ms
    _COMPOSIO_SCHEMA_METRICS["max_ms"] = max(
        _COMPOSIO_SCHEMA_METRICS["max_ms"], duration_ms
    )
    logfire.info(
        "composio_schema_fetch",
        tool_name=slug,
        duration_ms=round(duration_ms, 2),
    )
    if os.getenv("UA_COMPOSIO_SCHEMA_LOG", "").lower() in {"1", "true", "yes"}:
        print(f"⏱️ Composio schema fetch {slug}: {duration_ms:.1f}ms")

    schema = build_tool_schema_from_raw_composio(raw_tool, slug)
    if schema:
        if len(_COMPOSIO_SCHEMA_CACHE) > 200:
            _COMPOSIO_SCHEMA_CACHE.clear()
        _COMPOSIO_SCHEMA_CACHE[slug] = schema
    return schema


def _emit_composio_schema_metrics() -> None:
    if _COMPOSIO_SCHEMA_METRICS.get("reported"):
        return
    fetches = int(_COMPOSIO_SCHEMA_METRICS.get("fetches") or 0)
    cache_hits = int(_COMPOSIO_SCHEMA_METRICS.get("cache_hits") or 0)
    total_lookups = fetches + cache_hits
    if total_lookups == 0:
        return
    total_ms = float(_COMPOSIO_SCHEMA_METRICS.get("total_ms") or 0.0)
    max_ms = float(_COMPOSIO_SCHEMA_METRICS.get("max_ms") or 0.0)
    avg_ms = total_ms / fetches if fetches else 0.0
    hit_rate = cache_hits / total_lookups if total_lookups else 0.0
    _COMPOSIO_SCHEMA_METRICS["reported"] = True

    logfire.info(
        "composio_schema_summary",
        fetches=fetches,
        cache_hits=cache_hits,
        total_lookups=total_lookups,
        avg_ms=round(avg_ms, 2),
        max_ms=round(max_ms, 2),
        hit_rate=round(hit_rate, 4),
    )
    print(
        "📊 Composio schema fetch summary: "
        f"lookups={total_lookups} fetches={fetches} cache_hits={cache_hits} "
        f"avg_ms={avg_ms:.1f} max_ms={max_ms:.1f} hit_rate={hit_rate:.0%}"
    )


def _should_inject_provider_idempotency(
    raw_tool_name: str, side_effect_class: Optional[str]
) -> bool:
    if side_effect_class == "read_only":
        return False
    return _looks_like_composio_tool(raw_tool_name)


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


def _normalized_tool_candidates(raw_tool_name: str) -> set[str]:
    candidates: set[str] = set()
    normalized = _normalize_crash_tool_name(raw_tool_name)
    if normalized:
        candidates.add(normalized)
    if raw_tool_name.startswith("mcp__"):
        parts = raw_tool_name.split("__")
        if len(parts) >= 3:
            candidates.add(_normalize_crash_tool_name(parts[-1]))
    return candidates


def _allows_slug_match_for_tool(raw_tool_name: str) -> bool:
    if not raw_tool_name:
        return False
    normalized_candidates = _normalized_tool_candidates(raw_tool_name)
    for candidate in normalized_candidates:
        if candidate == _normalize_crash_tool_name("COMPOSIO_MULTI_EXECUTE_TOOL"):
            return True
        if candidate == _normalize_crash_tool_name("COMPOSIO_SEARCH_TOOLS"):
            return True
    return False


def _tool_input_slug_matches(
    tool_input: Optional[dict],
    normalized_crash_tool: str,
    raw_tool_name: str,
) -> bool:
    if not tool_input or not normalized_crash_tool:
        return False
    if not _allows_slug_match_for_tool(raw_tool_name):
        return False

    def matches(value: Optional[str]) -> bool:
        if not value:
            return False
        return _normalize_crash_tool_name(value) == normalized_crash_tool

    if isinstance(tool_input, dict):
        if matches(tool_input.get("tool_slug")):
            return True
        tools = tool_input.get("tools")
        if isinstance(tools, str):
            try:
                tools = json.loads(tools)
            except Exception:
                tools = None
        if isinstance(tools, list):
            for item in tools:
                if isinstance(item, dict) and matches(item.get("tool_slug")):
                    return True
    return False


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
    tool_input: Optional[dict] = None,
) -> tuple[bool, dict[str, Optional[str]]]:
    crash_tool = os.getenv("UA_TEST_CRASH_AFTER_TOOL")
    crash_id = os.getenv("UA_TEST_CRASH_AFTER_TOOL_CALL_ID")
    crash_stage = os.getenv("UA_TEST_CRASH_AFTER_STAGE")
    crash_phase = os.getenv("UA_TEST_CRASH_AFTER_PHASE")
    crash_step = os.getenv("UA_TEST_CRASH_AFTER_STEP")
    crash_match = (os.getenv("UA_TEST_CRASH_MATCH") or "raw").strip().lower()
    if crash_match not in ("raw", "slug", "any"):
        crash_match = "raw"
    if not any([crash_tool, crash_id, crash_stage, crash_phase, crash_step]):
        return False, {}

    normalized_candidates = _normalized_tool_candidates(raw_tool_name)
    normalized_crash_tool = _normalize_crash_tool_name(crash_tool or "")
    current_phase = None
    if crash_phase or crash_step:
        current_phase = _get_current_step_phase()

    if crash_tool:
        raw_match = normalized_crash_tool in normalized_candidates
        slug_match = _tool_input_slug_matches(
            tool_input,
            normalized_crash_tool,
            raw_tool_name,
        )
        if crash_match == "raw" and not raw_match:
            return False, {}
        if crash_match == "slug" and not slug_match:
            return False, {}
        if crash_match == "any" and not (raw_match or slug_match):
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
        "crash_match": crash_match,
        "current_step_id": current_step_id,
        "current_phase": current_phase,
        "normalized_tool_name": ",".join(sorted(normalized_candidates))
        if normalized_candidates
        else "",
    }


def _maybe_crash_after_tool(
    *,
    raw_tool_name: str,
    tool_call_id: str,
    stage: str,
    tool_input: Optional[dict] = None,
) -> None:
    should_crash, crash_context = _should_trigger_test_crash(
        raw_tool_name=raw_tool_name,
        tool_call_id=tool_call_id,
        stage=stage,
        tool_input=tool_input,
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
            crash_match=crash_context.get("crash_match"),
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


def _ensure_phase_checkpoint(
    *,
    run_id: Optional[str],
    step_id: Optional[str],
    checkpoint_type: str,
    phase: str,
    tool_name: Optional[str] = None,
    note: Optional[str] = None,
) -> None:
    if not runtime_db_conn or not run_id or not step_id or step_id == "unknown":
        return
    try:
        row = runtime_db_conn.execute(
            "SELECT 1 FROM checkpoints WHERE run_id = ? AND step_id = ? AND checkpoint_type = ? LIMIT 1",
            (run_id, step_id, checkpoint_type),
        ).fetchone()
        if row:
            return
        update_step_phase(runtime_db_conn, step_id, phase)
        state_snapshot = {
            "run_id": run_id,
            "step_id": step_id,
            "phase": phase,
            "checkpoint_type": checkpoint_type,
            "tool_name": tool_name,
            "note": note,
        }
        cursor = {}
        if tool_name:
            cursor["tool_name"] = tool_name
        save_checkpoint(
            runtime_db_conn,
            run_id=run_id,
            step_id=step_id,
            checkpoint_type=checkpoint_type,
            state_snapshot=state_snapshot,
            cursor=cursor,
        )
        logfire.info(
            "durable_checkpoint_saved",
            run_id=run_id,
            step_id=step_id,
            checkpoint_type=checkpoint_type,
            phase=phase,
        )
    except Exception as exc:
        logfire.warning(
            "checkpoint_save_failed",
            step_id=step_id,
            error=str(exc),
        )


def _is_job_run() -> bool:
    if not runtime_db_conn or not run_id:
        return False
    try:
        row = get_run(runtime_db_conn, run_id)
    except Exception:
        return False
    return bool(row and row["run_mode"] == "job")


def _is_harness_mode() -> bool:
    """
    Check if running in harness mode (long-running task orchestration).
    Returns True if max_iterations is set in the run config.
    This is distinct from crash recovery (forced_tool_mode_active).
    """
    if not runtime_db_conn or not run_id:
        return False
    try:
        info = get_iteration_info(runtime_db_conn, run_id)
        return bool(info.get("max_iterations"))
    except Exception:
        return False


def _agent_definition_supports_hooks() -> bool:
    try:
        return "hooks" in inspect.signature(AgentDefinition).parameters
    except Exception:
        return False


def _warn_if_subagent_hooks_configured(agents: Optional[dict[str, Any]]) -> None:
    """
    Check if any sub-agent definitions have explicit hooks configured.
    
    NOTE: As of SDK 0.1.3+, hooks from the MAIN agent's ClaudeAgentOptions
    automatically apply to all sub-agents. The hook system is "agent-agnostic".
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
            print(
                f"ℹ️ AgentDefinition for '{agent_name}' has hooks configured, "
                f"but AgentDefinition doesn't accept hooks directly. "
                f"Main agent hooks still apply to sub-agents."
            )
            logfire.info(
                "subagent_hooks_in_definition_ignored",
                agent_name=agent_name,
            )


_INTERVIEW_KEY_ALIASES = {
    "date_range": "timeframe",
    "date": "timeframe",
    "time_range": "timeframe",
    "time_period": "timeframe",
    "timeframe": "timeframe",
    "period": "timeframe",
    "focus": "focus",
    "topics": "focus",
    "scope": "focus",
    "depth": "depth",
    "detail": "depth",
    "level_of_detail": "depth",
    "delivery": "delivery",
    "delivery_method": "delivery",
    "output": "delivery",
    "format": "delivery",
}


def _slugify_interview_key(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", (value or "").lower()).strip("_")
    return slug or "unknown"


def _canonical_interview_key(value: str) -> str:
    slug = _slugify_interview_key(value)
    if slug in _INTERVIEW_KEY_ALIASES:
        return _INTERVIEW_KEY_ALIASES[slug]
    if "timeframe" in slug or (
        "time" in slug and ("range" in slug or "period" in slug or "window" in slug)
    ):
        return "timeframe"
    if "focus" in slug or "topic" in slug or "scope" in slug:
        return "focus"
    if "depth" in slug or "detail" in slug or "level" in slug:
        return "depth"
    if "delivery" in slug or "output" in slug or "format" in slug or "receive" in slug:
        return "delivery"
    return slug


def _build_interview_answers_payload(
    questions: list[dict[str, Any]],
    answers: dict[str, str],
) -> dict[str, Any]:
    answers_by_header: dict[str, str] = {}
    for question in questions:
        question_text = question.get("question", "")
        answer = answers.get(question_text)
        if not answer:
            continue
        header = question.get("header") or question_text
        key = _canonical_interview_key(header)
        answers_by_header[key] = answer
    return {
        "answers": answers,
        "answers_by_header": answers_by_header,
        "questions": questions,
    }


def _load_interview_answers_payload(workspace_dir: str) -> Optional[dict[str, Any]]:
    answers_file = os.path.join(workspace_dir, "interview_answers.json")
    if not os.path.exists(answers_file):
        return None
    try:
        with open(answers_file, "r") as f:
            data = json.load(f)
    except Exception:
        return None
    if isinstance(data, dict) and "answers" in data:
        return data
    if isinstance(data, dict):
        return {"answers": data}
    return None


def _is_placeholder_value(value: Any) -> bool:
    if value is None:
        return True
    if not isinstance(value, str):
        return False
    normalized = value.strip().lower()
    return normalized in {
        "awaiting user selection",
        "awaiting selection",
        "tbd",
        "pending",
        "unknown",
        "n/a",
        "na",
    }


def _normalize_clarifications(clarifications: Any) -> dict[str, Any]:
    if isinstance(clarifications, list):
        normalized: dict[str, Any] = {}
        for item in clarifications:
            if not isinstance(item, dict):
                continue
            key = item.get("question") or item.get("key")
            value = item.get("answer") or item.get("value")
            if key is not None:
                normalized[str(key)] = value
        clarifications = normalized
    if not isinstance(clarifications, dict):
        return {}

    normalized: dict[str, Any] = {}
    for key, value in clarifications.items():
        canonical_key = _canonical_interview_key(str(key))
        if canonical_key in normalized:
            if _is_placeholder_value(
                normalized[canonical_key]
            ) and not _is_placeholder_value(value):
                normalized[canonical_key] = value
            continue
        normalized[canonical_key] = value
    return normalized


def _merge_interview_answers_into_mission(
    mission_data: dict[str, Any],
    interview_payload: dict[str, Any],
) -> bool:
    clarifications = _normalize_clarifications(mission_data.get("clarifications"))

    answers_by_header = interview_payload.get("answers_by_header")
    if not isinstance(answers_by_header, dict) or not answers_by_header:
        answers = interview_payload.get("answers")
        if isinstance(answers, dict):
            answers_by_header = {
                _canonical_interview_key(str(question)): answer
                for question, answer in answers.items()
                if answer is not None
            }
        else:
            answers_by_header = {}

    changed = False
    for key, value in answers_by_header.items():
        canonical_key = _canonical_interview_key(str(key))
        if clarifications.get(canonical_key) != value:
            clarifications[canonical_key] = value
            changed = True

    if changed:
        mission_data["clarifications"] = clarifications
    return changed


def _apply_interview_answers_to_mission(
    workspace_dir: str,
    mission_data: Optional[dict[str, Any]] = None,
) -> tuple[Optional[dict[str, Any]], bool]:
    interview_payload = _load_interview_answers_payload(workspace_dir)
    if not interview_payload:
        return mission_data, False

    mission_file = os.path.join(workspace_dir, "mission.json")
    if mission_data is None:
        if not os.path.exists(mission_file):
            return None, False
        try:
            with open(mission_file, "r") as mf:
                mission_data = json.load(mf)
        except Exception:
            return None, False

    if not isinstance(mission_data, dict):
        return mission_data, False
    if mission_data.get("status") != "PLANNING":
        return mission_data, False

    changed = _merge_interview_answers_into_mission(mission_data, interview_payload)
    if changed:
        try:
            with open(mission_file, "w") as mf:
                json.dump(mission_data, mf, indent=2)
        except Exception:
            return mission_data, False
    return mission_data, changed


def _process_pending_interview(
    workspace_dir: str,
    ask_fn: Optional[Callable[[list[dict[str, Any]]], dict[str, str]]] = None,
) -> Optional[str]:
    pending_interview_file = os.path.join(workspace_dir, "pending_interview.json")
    if not os.path.exists(pending_interview_file):
        return None

    try:
        with open(pending_interview_file, "r") as f:
            interview_data = json.load(f)
        questions = interview_data.get("questions", [])

        if not questions:
            os.remove(pending_interview_file)
            return None

        print("\n" + "=" * 60)
        print("📋 PLANNING PHASE - Interview Required")
        print("=" * 60)

        skip_interview = False
        mission_file = os.path.join(workspace_dir, "mission.json")
        if os.path.exists(mission_file):
            try:
                with open(mission_file, "r") as mf:
                    m_data = json.load(mf)
                    if m_data.get("status") == "IN_PROGRESS":
                        skip_interview = True
            except Exception:
                pass

        if skip_interview:
            print(
                "\n⚡ RUTHLESS AUTONOMY: Skipping user interview. Mission is I_PROGRESS."
            )
            answers = {
                q.get(
                    "question", f"Q{idx + 1}"
                ): "Proceed with best judgment (Autonomy Mode)"
                for idx, q in enumerate(questions)
            }
        else:
            if ask_fn is None:
                from universal_agent.harness import ask_user_questions as do_interview

                ask_fn = do_interview
            answers = ask_fn(questions)

        print("=" * 60 + "\n")
        print("   ✅ Interview answers collected")

        answers_payload = _build_interview_answers_payload(questions, answers)
        answers_file = os.path.join(workspace_dir, "interview_answers.json")
        with open(answers_file, "w") as f:
            json.dump(answers_payload, f, indent=2)

        if os.path.exists(mission_file):
            try:
                with open(mission_file, "r") as mf:
                    m_data = json.load(mf)
                if _merge_interview_answers_into_mission(m_data, answers_payload):
                    with open(mission_file, "w") as mf:
                        json.dump(m_data, mf, indent=2)
                    print("   ✅ Updated mission.json with clarifications")
            except Exception as e:
                print(f"   ⚠️ Failed to update mission.json with answers: {e}")

        summary_map = answers_payload.get("answers_by_header") or answers
        answers_summary = "\n".join([f"- {q}: {a}" for q, a in summary_map.items()])
        pending_prompt = (
            "USER INTERVIEW ANSWERS:\n"
            "The user has answered your clarifying questions:\n\n"
            f"{answers_summary}\n\n"
            "IMPORTANT: You MUST now UPDATE the existing mission.json file. "
            "Re-read mission.json, update the clarifications section with these answers, "
            "and write the updated file back. Keep status as 'PLANNING' until the plan is approved."
        )

        os.remove(pending_interview_file)
        return pending_prompt
    except Exception as e:
        print(f"   ⚠️ Interview error: {e}")
        return None


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
    normalized = (raw_tool_name or "").lower()
    if normalized in ("taskoutput", "taskresult"):
        return True
    if normalized.startswith("mcp__"):
        parts = normalized.split("__")
        if len(parts) >= 3 and parts[-1] in ("taskoutput", "taskresult"):
            return True
    return False


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


def _extract_task_output_paths(tool_input: dict[str, Any]) -> list[str]:
    if not isinstance(tool_input, dict):
        return []
    prompt = tool_input.get("prompt")
    if not isinstance(prompt, str):
        return []
    candidates = []
    for match in re.findall(r"/[^\s\"']+\.(?:html|pdf|md|json|txt)", prompt):
        if os.path.exists(match):
            candidates.append(match)
    return candidates


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


def _normalize_task_prompt(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())


def _normalize_task_input(tool_input: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(tool_input or {})
    normalized.pop("task_key", None)
    normalized.pop("resume", None)
    if "prompt" in normalized:
        normalized["prompt"] = _normalize_task_prompt(normalized.get("prompt"))
    return normalized


def _parse_created_at(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _partition_inflight_for_relaunch(
    inflight: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Skip in-flight tool calls that likely belong to a running RELAUNCH task."""
    relaunch_tasks = [
        item
        for item in inflight
        if item.get("replay_policy") == "RELAUNCH" and item.get("status") == "running"
    ]
    if not relaunch_tasks:
        return inflight, []
    cutoff_by_step: dict[str, datetime] = {}
    for task in relaunch_tasks:
        step_id = task.get("step_id")
        created_at = _parse_created_at(task.get("created_at"))
        if not step_id or not created_at:
            continue
        existing = cutoff_by_step.get(step_id)
        cutoff_by_step[step_id] = (
            created_at if existing is None else min(existing, created_at)
        )
    if not cutoff_by_step:
        return inflight, []
    replay: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for item in inflight:
        if item in relaunch_tasks:
            replay.append(item)
            continue
        step_id = item.get("step_id")
        created_at = _parse_created_at(item.get("created_at"))
        cutoff = cutoff_by_step.get(step_id)
        if cutoff and created_at and created_at >= cutoff:
            skipped.append(item)
        else:
            replay.append(item)
    return replay, skipped


def _forced_tool_matches(
    raw_tool_name: str, tool_input: dict[str, Any], expected: dict[str, Any]
) -> bool:
    identity = parse_tool_identity(raw_tool_name or "")
    if identity.tool_name != expected.get(
        "tool_name"
    ) or identity.tool_namespace != expected.get("tool_namespace"):
        return False
    if identity.tool_name == "task":
        actual = _normalize_task_input(tool_input or {})
        expected_input = _normalize_task_input(expected.get("tool_input") or {})
        normalized_actual = _normalize_tool_input(actual)
        normalized_expected = _normalize_tool_input(expected_input)
        return normalized_actual == normalized_expected
    normalized = _normalize_tool_input(tool_input)
    return normalized == expected.get("normalized_input")


def _forced_task_active() -> bool:
    return any(
        item.get("tool_name") == "task" and item.get("tool_namespace") == "claude_code"
        for item in forced_tool_active_ids.values()
    )


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
        side_effect_class = None
        try:
            side_effect_class = row["side_effect_class"]
        except Exception:
            side_effect_class = None
        if _should_inject_provider_idempotency(raw_name, side_effect_class):
            _inject_provider_idempotency(
                raw_name, tool_input, row["idempotency_key"] or ""
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
        "Do NOT run Bash or diagnostic commands unless explicitly listed.",
        "Do NOT call TaskOutput/TaskResult; they are disabled.",
        "Do NOT call TodoWrite or provide analysis; run only the listed tool calls.",
        "Use the exact tool name and input shown.",
        "If a tool is denied, stop and respond with DONE.",
        "After completing the listed tool calls, respond with DONE and do not invoke any other tools.",
    ]
    for idx, item in enumerate(queue, 1):
        raw_name = item.get("raw_tool_name") or item.get("tool_name")
        lines.append(f"{idx}) tool: {raw_name}")
        lines.append(f"   input: {json.dumps(item.get('tool_input') or {}, indent=2)}")
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
    global \
        forced_tool_queue, \
        forced_tool_active_ids, \
        forced_tool_mode_active, \
        runtime_db_conn
    if not runtime_db_conn:
        return True
    inflight = _load_inflight_tool_calls(runtime_db_conn, run_id)
    if not inflight:
        return True
    replay_step_id = inflight[0].get("step_id")
    inflight, skipped = _partition_inflight_for_relaunch(inflight)
    for item in skipped:
        if tool_ledger:
            tool_ledger.mark_abandoned_on_resume(
                item["tool_call_id"], "relaunch_parent_task"
            )
            tool_ledger.mark_replay_status(
                item["tool_call_id"], "skipped_relaunch_parent"
            )
        logfire.info(
            "relaunch_parent_skip",
            tool_call_id=item["tool_call_id"],
            step_id=item.get("step_id"),
        )
    if not inflight:
        return True
    forced_tool_queue = []
    for item in inflight:
        if tool_ledger and tool_ledger.promote_pending_receipt(item["tool_call_id"]):
            tool_ledger.mark_replay_status(item["tool_call_id"], "succeeded_pending")
            logfire.info(
                "pending_receipt_promoted",
                tool_call_id=item["tool_call_id"],
                idempotency_key=item.get("idempotency_key"),
            )
            continue
        if item.get("replay_policy") == "RELAUNCH":
            relaunch_step_id = item.get("step_id") or current_step_id or "unknown"
            if tool_ledger and workspace_dir:
                tool_input = (
                    item.get("tool_input")
                    if isinstance(item.get("tool_input"), dict)
                    else {}
                )
                _, task_key = _ensure_task_key(tool_input)
                output_paths = _extract_task_output_paths(tool_input)
                if output_paths:
                    tool_ledger.mark_succeeded(
                        item["tool_call_id"],
                        {
                            "status": "subagent_output_reused",
                            "task_key": task_key,
                            "output_paths": output_paths,
                        },
                    )
                    tool_ledger.mark_replay_status(
                        item["tool_call_id"], "skipped_output_present"
                    )
                    logfire.info(
                        "relaunch_skipped_output_present",
                        tool_call_id=item["tool_call_id"],
                        task_key=task_key,
                        output_paths=output_paths,
                    )
                    continue
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
                await process_turn(
                    active_client, prompt, workspace_dir, force_complex=True
                )
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
    _ensure_phase_checkpoint(
        run_id=run_id,
        step_id=replay_step_id,
        checkpoint_type="post_replay",
        phase="post_replay",
        note="replay_queue_drained",
    )
    return True


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
                "idempotency_key": row["idency_key"],
                "created_at": row["created_at"],
            }
        )

    inflight = []
    inflight_rows = conn.execute(
        """
        SELECT tool_call_id, tool_name, status, idempotency_key, created_at, replay_policy, step_id
        FROM tool_calls
        WHERE run_id = ? AND status IN ('prepared', 'running')
        ORDER BY created_at DESC
        """,
        (run_id,),
    ).fetchall()
    inflight_items: list[dict[str, Any]] = []
    for row in inflight_rows:
        inflight_items.append(
            {
                "tool_call_id": row["tool_call_id"],
                "tool_name": row["tool_name"],
                "status": row["status"],
                "idempotency_key": row["idempotency_key"],
                "created_at": row["created_at"],
                "replay_policy": row["replay_policy"],
                "step_id": row["step_id"],
            }
        )
    inflight_items, _ = _partition_inflight_for_relaunch(inflight_items)
    for row in inflight_items:
        inflight.append(
            {
                "tool_name": row["tool_name"],
                "status": row["status"],
                "idempotency_key": row["idempotency_key"],
                "created_at": row["created_at"],
                "replay_policy": row["replay_policy"],
            }
        )

    artifacts = list_workspace_artifacts(workspace_dir)

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
            if (row["tool_name"] or "").lower() == "task":
                summary_lines.append(
                    f"- {row['tool_name']} | {row['status']} (do NOT call TaskOutput/TaskResult)"
                )
                continue
            summary_lines.append(
                f"- {row['tool_name']} | {row['status']} | {row['idempotency_key']}"
            )
    if inflight:
        summary_lines.append("in_flight_tool_calls:")
        for row in inflight:
            if (row["tool_name"] or "").lower() == "task":
                summary_lines.append(
                    f"- {row['tool_name']} | {row['status']} (relaunch Task; do NOT call TaskOutput)"
                )
                continue
            summary_lines.append(
                f"- {row['tool_name']} | {row['status']} | {row['idempotency_key']}"
            )
    if artifacts:
        summary_lines.append("artifacts:")
        for name in artifacts:
            summary_lines.append(f"- {name}")

    return packet, "\n".join(summary_lines)


def update_restart_file(
    run_id: str,
    workspace_dir: str,
    resume_cmd: Optional[str] = None,
    resume_packet_path: Optional[str] = None,
    job_summary_path: Optional[str] = None,
    runwide_summary_line: Optional[str] = None,
) -> None:
    # Logic removed per user request to avoid file path errors
    pass

async def continue_job_run(
    client: ClaudeSDKClient,
    run_id: str,
    workspace_dir: str,
    last_job_prompt: Optional[str],
    resume_packet_summary: str,
    replay_note: Optional[str] = None,
    max_error_retries: int = 3,
    execution_session: Optional[ExecutionSession] = None,
) -> Optional[str]:
    error_retries = 0
    last_error = None
    final_response_text = ""
    resume_fallback_attempted = False

    resume_message = (
        f"Resuming job run {run_id}. Continue executing the existing job to completion. "
        "You have access to tool receipts; do not repeat side-effecting tool calls that already "
        "succeeded—reuse receipts. If blocked, set status waiting_for_human with a clear request."
        "Focus strictly on the job objective and ignore unrelated topics or context."
        f"\n\nWorkspace: {workspace_dir}\n"
        "Use absolute paths rooted in the workspace for any local file operations.\n"
        "Do NOT look for receipt_*.json files on disk; rely on the ledger receipts in the resume packet.\n"
        "Do NOT call TaskOutput/TaskResult; they are not callable tools. "
        "If you need subagent output, relaunch the Task tool instead.\n"
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
            result = await process_turn(
                client,
                user_input,
                workspace_dir,
                force_complex=True,
                execution_session=execution_session,
            )
            final_response_text = result.response_text or ""
            _maybe_mark_run_succeeded()
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
                            fallback_client,
                            user_input,
                            workspace_dir,
                            force_complex=True,
                            execution_session=execution_session,
                        )
                    final_response_text = result.response_text or ""
                    _maybe_mark_run_succeeded()
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
                    "\n⚠️ Repeated errors detected. Run status set to waiting_for_human."
                )
                return final_response_text
            print(
                f"\n⚠️ Job run error (attempt {error_retries}/{max_error_retries}): {exc}"
            )
            continue


async def run_conversation(client, query: str, start_ts: float, iteration: int = 1, max_iterations: int = 20):
    """Run a single conversation turn with full tracing."""
    global \
        trace, \
        run_id, \
        budget_config, \
        budget_state, \
        runtime_db_conn, \
        current_step_id, \
        tool_ledger, \
        provider_session_forked_from
    step_id = str(uuid.uuid4())
    current_step_id = step_id
    step_index = iteration

    if iteration > max_iterations:
        print(f"⚠️ Max iterations ({max_iterations}) reached. Forcing stop.")
        # Return equivalent of "done"
        return False, None, "Max iterations reached."

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
            logfire.warning(
                "runtime_step_insert_failed", step_id=step_id, error=str(exc)
            )

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

    if (
        budget_config.get("max_steps")
        and budget_state["steps"] >= budget_config["max_steps"]
    ):
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
        logfire.set_baggage(step="planning")  # Initial state
        logfire.set_baggage(loop="1")
    else:
        # Update loop count for main agent
        logfire.set_baggage(loop=str(iteration))
        logfire.set_baggage(step="execution")  # Default for subsequent turns
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
        with logfire.span("llm_api_wait", query_length=len(query)):
            await client.query(query)

        tool_calls_this_iter = []
        needs_user_input = False
        auth_link = None
        final_text = ""  # Buffer to capture final agent response for printing after execution summary

        with logfire.span("llm_response_stream"):
            async for msg in client.receive_response():
                if isinstance(msg, ResultMessage):
                    # Track token usage from the final ResultMessage of the turn
                    # This explicitly contains the usage statistics for the turn
                    if hasattr(msg, "usage") and msg.usage:
                        u = msg.usage
                        inp = u.get("input_tokens", 0) or 0
                        out = u.get("output_tokens", 0) or 0

                        # Update local trace counters
                        if trace and "token_usage" in trace:
                            trace["token_usage"]["input"] += inp
                            trace["token_usage"]["output"] += out
                            trace["token_usage"]["total"] += inp + out

                        logfire.info(
                            "token_usage_update",
                            run_id=run_id,
                            input=inp,
                            output=out,
                            total_so_far=trace["token_usage"]["total"] if trace else 0,
                        )

                        # Durability: Update DB with latest token count
                        if run_id and runtime_db_conn:
                            update_run_tokens(
                                runtime_db_conn, run_id, trace["token_usage"]["total"]
                            )

                        # Emit token usage for UI consumers (CLI/harness path)
                        if "agent" in globals() and agent is not None:
                            try:
                                await agent.send_agent_event(
                                    EventType.STATUS,
                                    {"token_usage": trace.get("token_usage")},
                                )
                            except Exception:
                                pass

                        # Proactive context warning (CLI/harness visibility)
                        if trace and "token_usage" in trace:
                            usage_total = trace["token_usage"]["total"]
                            flags = trace.setdefault("token_usage_flags", {})
                            if CONTEXT_WINDOW_TOKENS:
                                utilization = usage_total / CONTEXT_WINDOW_TOKENS
                                if utilization >= 0.7 and not flags.get("warned_70"):
                                    print(
                                        f"\n⚠️ CONTEXT USAGE HIGH ({int(utilization*100)}% of {CONTEXT_WINDOW_TOKENS} tokens). "
                                        "Consider wrapping the phase soon."
                                    )
                                    flags["warned_70"] = True
                                if utilization >= 0.9 and not flags.get("warned_90"):
                                    print(
                                        f"\n⚠️ CONTEXT USAGE VERY HIGH ({int(utilization*100)}% of {CONTEXT_WINDOW_TOKENS} tokens). "
                                        "Expect compaction or reset soon."
                                    )
                                    flags["warned_90"] = True

                        # [FIX 7] Token-Based Harness Trigger
                        # Connect the Brain (Token Count) to the Body (Harness Action)
                        total_tokens = trace["token_usage"]["total"]
                        if total_tokens > TRUNCATION_THRESHOLD:
                            print(
                                f"\n⚠️ CONTEXT THRESHOLD REACHED ({total_tokens} > {TRUNCATION_THRESHOLD})"
                            )
                            print("🔄 Triggering Harness Iteration (Context Reset)...")

                            # Synthesize a "context_exhausted" event for the hook/logic
                            # Instead of calling the hook, we trigger the restart specific logic directly here
                            # to align with the "Autonomous Execution Protocol"

                            # 1. Increment Iteration
                            if run_id and runtime_db_conn:
                                current_iter = get_iteration_info(
                                    runtime_db_conn, run_id
                                ).get("iteration_count", 0)
                                max_iter = get_iteration_info(runtime_db_conn, run_id).get(
                                    "max_iterations", 10
                                )

                                if current_iter >= max_iter:
                                    print(
                                        f"⛔ Max iterations ({max_iter}) reached despite context exhaustion. Stopping."
                                    )
                                    break

                                new_iter = increment_iteration_count(
                                    runtime_db_conn, run_id
                                )

                                # 2. Construct Handoff Prompt (Ledger-Aware)
                                # Check for handoff.json written by build_evidence_ledger
                                handoff_path = os.path.join(workspace_dir, "handoff.json") if workspace_dir else None
                                handoff_state = None
                            
                                if handoff_path and os.path.exists(handoff_path):
                                    try:
                                        with open(handoff_path, "r") as hf:
                                            handoff_state = json.load(hf)
                                    except Exception as e:
                                        logfire.warning("handoff_json_read_error", error=str(e))
                            
                                if handoff_state and handoff_state.get("phase") == "ledger_complete":
                                    # Ledger-aware restart: guide agent to read only the ledger
                                    ledger_path = handoff_state.get("ledger_path", "")
                                    topic = handoff_state.get("topic", "the research topic")
                                    next_prompt = (
                                        f"RESUMING (Phase: SYNTHESIS):\n"
                                        f"Context was reset after {total_tokens} tokens.\n"
                                        f"Evidence ledger is ready at: {ledger_path}\n\n"
                                        f"INSTRUCTIONS:\n"
                                        f"1. READ ONLY the evidence ledger file (do NOT re-read raw corpus)\n"
                                        f"2. Use EVID-XXX references when citing evidence\n"
                                        f"3. Write the report based on ledger evidence for: {topic}\n\n"
                                        f"Status: Iteration {current_iter + 1}/{max_iter}"
                                    )
                                    logfire.info("harness_handoff_ledger_aware", ledger_path=ledger_path, topic=topic)
                                else:
                                    # Generic restart: no ledger available
                                    next_prompt = f"RESUMING (Context Limit Reached): You exceeded the context limit ({total_tokens} tokens). I have reset your memory. Continue the mission.json tasks from where you left off. Status: {current_iter + 1}/{max_iter}."

                                pending_prompt = next_prompt

                                # 3. Clear Context
                                if safe_clear_history(client):
                                    print("🧹 Client history cleared (Context Reset).")
                                else:
                                    print("⚠️ Could not clear history: client.history not available")

                                # 4. Reset Token Counter for new session
                                trace["token_usage"] = {"input": 0, "output": 0, "total": 0}
                                trace["token_usage_flags"] = {}

                                continue  # Restart inner loop with new prompt

                if isinstance(msg, AssistantMessage):
                    # Wrapped in span for full visibility of assistant's turn
                    with logfire.span("assistant_message", model=msg.model):
                        for block in msg.content:
                            if isinstance(block, ToolUseBlock):
                                if is_malformed_tool_name(block.name):
                                    logfire.warning(
                                        "malformed_tool_name_detected",
                                        tool_name=block.name,
                                        run_id=run_id,
                                        step_id=step_id,
                                    )
                                    _mark_run_waiting_for_human(
                                        "malformed_tool_name_detected",
                                        tool_name=block.name,
                                        tool_call_id=str(block.id),
                                    )
                                # Nested tool_use span
                                with logfire.span(
                                    "tool_use", tool_name=block.name, tool_id=block.id
                                ):
                                    tool_record = {
                                        "run_id": run_id,
                                        "step_id": step_id,
                                        "iteration": iteration,
                                        "name": block.name,
                                        "id": block.id,
                                        "time_offset_seconds": round(
                                            time.time() - start_ts, 3
                                        ),
                                        "input": block.input
                                        if hasattr(block, "input")
                                        else None,
                                        "input_size_bytes": len(json.dumps(block.input))
                                        if hasattr(block, "input") and block.input
                                        else 0,
                                    }
                                    if tool_ledger:
                                        ledger_entry = tool_ledger.get_tool_call(
                                            str(block.id)
                                        )
                                        if ledger_entry:
                                            tool_record["idempotency_key"] = (
                                                ledger_entry.get("idempotency_key")
                                            )
                                            tool_record["side_effect_class"] = (
                                                ledger_entry.get("side_effect_class")
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
                                    if (
                                        budget_config.get("max_tool_calls")
                                        and budget_state["tool_calls"]
                                        > budget_config["max_tool_calls"]
                                    ):
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
                                    parent_tool_id = getattr(
                                        msg, "parent_tool_use_id", None
                                    )

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
                                        if hasattr(block, "input") and isinstance(
                                            block.input, dict
                                        ):
                                            subagent_type = block.input.get(
                                                "subagent_type", "unknown"
                                            )

                                        logfire.set_baggage(agent=subagent_type)
                                        logfire.set_baggage(is_subagent="true")
                                        logfire.set_baggage(
                                            loop="1"
                                        )  # Reset loop for the sub-agent

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
                                    print(
                                        f"   Input size: {tool_record['input_size_bytes']} bytes"
                                    )

                                    if tool_record["input"]:
                                        input_preview = json.dumps(
                                            tool_record["input"], indent=2
                                        )
                                        max_len = 3000 if is_code_exec else 500
                                        if len(input_preview) > max_len:
                                            input_preview = input_preview[:max_len] + "..."
                                        print(f"   Input: {input_preview}")
                                
                                    # Show what we're waiting for
                                    print(f"   ⏳ Waiting for {block.name} response...")

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
                                final_text = block.text[:3000] + (
                                    "..." if len(block.text) > 3000 else ""
                                )

                                # Log text block
                                logfire.info(
                                    "text_block",
                                    length=len(block.text),
                                    text_preview=block.text[:500],
                                )

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
                                    ledger_entry = tool_ledger.get_tool_call(
                                        str(tool_use_id)
                                    )
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
                                preview = result_record.get("content_preview", "")[:2000]
                                if preview:
                                    print(
                                        f"   Preview: {preview}{'...' if len(result_record.get('content_preview', '')) > 2000 else ''}"
                                    )

                                # [Interview Tool Interception] Check if this is an interview request
                                # NON-BLOCKING: Save questions to file, let iteration complete,
                                # then display interview between iterations
                                try:
                                    full_result_content = content_str  # Use full content, not truncated preview
                                    if "__INTERVIEW_REQUEST__" in full_result_content:
                                        print(
                                            f"\n   📋 Interview questions detected - will display after this iteration"
                                        )

                                        # Parse the outer wrapper (may be {"result": "..."})
                                        try:
                                            outer = json.loads(full_result_content)
                                            inner_content = outer.get(
                                                "result", full_result_content
                                            )
                                        except json.JSONDecodeError:
                                            inner_content = full_result_content

                                        # Strip trace ID prefix if present (format: "[local-toolkit-trace-id: ...]\n{...")
                                        if isinstance(
                                            inner_content, str
                                        ) and inner_content.startswith("["):
                                            json_start = inner_content.find("{")
                                            if json_start != -1:
                                                inner_content = inner_content[json_start:]

                                        # Parse the interview data
                                        interview_data = (
                                            json.loads(inner_content)
                                            if isinstance(inner_content, str)
                                            else inner_content
                                        )

                                        if interview_data.get("__INTERVIEW_REQUEST__"):
                                            questions = interview_data.get("questions", [])
                                            if questions and OBSERVER_WORKSPACE_DIR:
                                                # Save questions to workspace for post-iteration processing
                                                pending_interview_file = os.path.join(
                                                    OBSERVER_WORKSPACE_DIR,
                                                    "pending_interview.json",
                                                )
                                                with open(pending_interview_file, "w") as f:
                                                    json.dump(
                                                        {"questions": questions},
                                                        f,
                                                        indent=2,
                                                    )

                                                # Tell agent to wait for user answers
                                                waiting_msg = "Waiting for user to answer interview questions. Answers will be provided in the next message."
                                                # Only modify block_content if it has a content attribute
                                                if hasattr(block_content, "content"):
                                                    block_content.content = waiting_msg
                                                result_record["content_preview"] = (
                                                    waiting_msg
                                                )
                                except (json.JSONDecodeError, Exception) as e:
                                    print(f"   ⚠️ Interview setup error: {e}")

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
                                            tool_use_id=str(tool_use_id)
                                            if tool_use_id
                                            else None,
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
                                    if not is_error:
                                        asyncio.create_task(
                                            _capture_subagent_memory(
                                                tool_input or {}, content_str
                                            )
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
                    logfire.info(
                        "result_message",
                        duration_ms=msg.duration_ms,
                        total_cost_usd=msg.total_cost_usd,
                        num_turns=msg.num_turns,
                        is_error=msg.is_error,
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
                logfire.warning(
                    "checkpoint_save_failed", step_id=step_id, error=str(exc)
                )
        current_step_id = None

        return needs_user_input, auth_link, final_text


def _is_memory_intent(query: str) -> bool:
    lowered = query.lower()
    memory_phrases = [
        "please remember",
        "remember this",
        "my favorite",
        "my favourite",
        "my preferences",
        "my preference",
        "what are my",
        "what's my",
        "whats my",
        "when do i like",
        "do i like to",
        "my coding preferences",
        "my work environment",
        "my name is",
    ]
    return any(phrase in lowered for phrase in memory_phrases)


async def classify_query(client: ClaudeSDKClient, query: str) -> str:
    """Determine if a query is SIMPLE (direct answer) or COMPLEX (needs tools)."""
    if _is_memory_intent(query):
        print("\n🤔 Query Classification: SIMPLE (Heuristic: memory_intent)")
        if LOGFIRE_TOKEN:
            logfire.info(
                "query_classification",
                query=query,
                decision="SIMPLE",
                raw_response="HEURISTIC_MEMORY_INTENT",
            )
        return "SIMPLE"

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
    with logfire.span("llm_api_wait", context="classification"):
        await client.query(classification_prompt)

    result_text = ""
    with logfire.span("llm_response_stream", context="classification"):
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


async def handle_simple_query(client: ClaudeSDKClient, query: str) -> tuple[bool, str]:
    """
    Handle simple queries directly without complex tool loops.
    Returns True if handled successfully, False if tool use was attempted (fallback needed).
    Also returns the full response text.
    """
    print(f"\n⚡ Direct Answer (Fast Path):")
    print("-" * 40)

    with logfire.span("llm_api_wait", context="direct_answer"):
        await client.query(query)

    full_response = ""
    tool_use_detected = False
    disable_local_memory = os.getenv("UA_DISABLE_LOCAL_MEMORY", "").lower() in {
        "1",
        "true",
        "yes",
    }
    ignored_tool_names = set()
    if disable_local_memory:
        ignored_tool_names.update(
            [
                "mcp__local_toolkit__core_memory_replace",
                "mcp__local_toolkit__core_memory_append",
                "mcp__local_toolkit__archival_memory_insert",
                "mcp__local_toolkit__archival_memory_search",
                "mcp__local_toolkit__get_core_memory_blocks",
            ]
        )

    with logfire.span("llm_response_stream", context="direct_answer"):
        async for msg in client.receive_response():
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        print(block.text, end="", flush=True)
                        full_response += block.text
                    elif isinstance(block, ToolUseBlock):
                        if disable_local_memory and block.name in ignored_tool_names:
                            # Ignore local memory tool attempts when local memory is disabled.
                            continue
                        # ABORT! The model wants to use a tool.
                        tool_use_detected = True
                        break

                if tool_use_detected:
                    break
            elif isinstance(msg, ResultMessage):
                pass  # stream end

    print("\n" + "-" * 40)  # separator

    if tool_use_detected:
        print(
            f"\n⚠️  Model attempted tool use in Fast Path. Redirecting to Complex Path..."
        )
        print("=" * 80)
        if LOGFIRE_TOKEN:
            logfire.warn("fast_path_fallback", reason="tool_use_detected")
        return False, full_response

    if LOGFIRE_TOKEN:
        logfire.info("direct_answer", length=len(full_response))
    return True, full_response


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
        "--job-path",
        help="Path to a JSON file containing job specification (implies --run-mode=job)",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=20,
        help="Maximum number of harness iterations (default: 20)",
    )
    parser.add_argument(
        "--completion-promise",
        type=str,
        help="Wait for this string in output before true completion",
    )
    parser.add_argument(
        "--fork",
        action="store_true",
        help="Fork an existing run using provider session state (requires --run-id).",
    )
    parser.add_argument(
        "--workspace",
        dest="workspace",
        help="Override workspace directory.",
    )
    parser.add_argument(
        "--harness",
        dest="harness_objective",
        help="Run in harness mode with the specified objective.",
    )
    # [NEW] Auto-Interview support
    parser.add_argument(
        "--interview-auto",
        type=str,
        help="Comma-separated list of answers for automated interview (e.g., '1,2,1')",
    )
    parser.add_argument(
        "--harness-template",
        type=str,
        help="Path to a plan JSON file to skip interview (e.g., 'urw/sample_plan.json')",
    )
    parser.add_argument(
        "--explain-tool-policy",
        dest="explain_tool_policy",
        help="Explain resolved tool policy for a raw tool name.",
    )
    parser.add_argument(
        "--reset-letta",
        action="store_true",
        help="Reset Letta memory for all agents (fresh start).",
    )
    parser.add_argument(
        "--urw",
        dest="urw_request",
        help="Run URW orchestrator for the provided request (opt-in).",
    )
    parser.add_argument(
        "--urw-mock",
        action="store_true",
        help="Use mock adapter instead of Universal Agent for URW.",
    )
    parser.add_argument(
        "--urw-max-iterations-per-task",
        type=int,
        default=15,
        help="Max iterations per URW task.",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=3,
        help="Maximum concurrent workers (default: 3).",
    )
    parser.add_argument(
        "--urw-iteration-timeout",
        type=int,
        default=600,
        help="URW iteration timeout in seconds.",
    )
    parser.add_argument(
        "--urw-task-timeout",
        type=int,
        default=3600,
        help="URW task timeout in seconds.",
    )
    parser.add_argument(
        "--urw-verbose",
        action="store_true",
        help="Enable verbose URW logging.",
    )
    parser.add_argument(
        "--use-gateway",
        action="store_true",
        help="Use the in-process Gateway for CLI execution (experimental).",
    )
    parser.add_argument(
        "--gateway-use-cli-workspace",
        action="store_true",
        help="Use the CLI workspace directory for Gateway sessions (experimental).",
    )
    parser.add_argument(
        "--gateway-url",
        type=str,
        default=None,
        help="URL of external gateway server (e.g., http://localhost:8002). Implies --use-gateway.",
    )
    return parser.parse_args()


async def _run_urw_from_cli(args: argparse.Namespace) -> None:
    from pathlib import Path
    from anthropic import Anthropic

    from universal_agent.urw import URWConfig, URWOrchestrator
    from universal_agent.urw.integration import MockAgentAdapter, UniversalAgentAdapter

    api_key = (
        os.getenv("ANTHROPIC_API_KEY")
        or os.getenv("ANTHROPIC_AUTH_TOKEN")
        or os.getenv("ZAI_API_KEY")
    )
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is required for URW mode")

    if args.workspace:
        workspace_path = Path(args.workspace).expanduser().resolve()
    else:
        import datetime
        import uuid
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        run_uuid = str(uuid.uuid4())[:8]
        workspace_path = Path(f"./urw_sessions/session_{ts}_{run_uuid}").expanduser().resolve()
        
    print(f"[URW] Workspace initialized at: {workspace_path}")
    workspace_path.mkdir(parents=True, exist_ok=True)

    base_url = os.getenv("ANTHROPIC_BASE_URL")
    client_kwargs = {"api_key": api_key}
    if base_url:
        client_kwargs["base_url"] = base_url
    llm_client = Anthropic(**client_kwargs)

    if args.urw_mock:
        adapter = MockAgentAdapter({"success_rate": 0.9, "produce_artifacts": True})
    else:
        adapter = UniversalAgentAdapter({})

    config = URWConfig(
        max_iterations_per_task=args.urw_max_iterations_per_task,
        max_total_iterations=args.urw_max_total_iterations,
        iteration_timeout=args.urw_iteration_timeout,
        task_timeout=args.urw_task_timeout,
        verbose=args.urw_verbose,
    )

    orchestrator = URWOrchestrator(
        agent_loop=adapter,
        llm_client=llm_client,
        workspace_path=workspace_path,
        config=config,
    )

    result = await orchestrator.run(args.urw_request)
    print(json.dumps(result, indent=2))


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
    global \
        trace, \
        composio, \
        user_id, \
        session, \
        options, \
        OBSERVER_WORKSPACE_DIR, \
        run_id, \
        workspace_dir, \
        current_execution_session
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
            for base_dir in [
                "/app",
                os.path.dirname(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                ),
                "/tmp",
            ]:
                workspace_dir = os.path.join(
                    base_dir, "AGENT_RUN_WORKSPACES", f"session_{timestamp}"
                )
                try:
                    os.makedirs(workspace_dir, exist_ok=True)
                    break  # Success
                except PermissionError:
                    continue
            else:
                raise RuntimeError("Cannot create workspace directory in any location")

    os.makedirs(workspace_dir, exist_ok=True)
    
    # Set workspace in environment EARLY so MCP server subprocess inherits it
    def _set_observer_workspace(path: str) -> None:
        global OBSERVER_WORKSPACE_DIR
        OBSERVER_WORKSPACE_DIR = path

    bind_workspace(workspace_dir, observer_setter=_set_observer_workspace)

    # Initialize Composio with automatic file downloads to this workspace
    downloads_dir = os.path.join(workspace_dir, "downloads")
    os.makedirs(downloads_dir, exist_ok=True)

    # Pre-create standard work_products directories to avoid runtime interruptions
    work_products_dir = os.path.join(workspace_dir, "work_products", "media")
    os.makedirs(work_products_dir, exist_ok=True)

    # NOTE: Git checkpoint pattern removed - causes nested repo issues
    # File-based state (macro_tasks.json, session_progress.md) used instead

    # =========================================================================
    # =========================================================================
    # 2. NON-BLOCKING AUTH FIX
    # (Removed to restore interactive input for /harness command)
    # =========================================================================
    import builtins

    # original_input = builtins.input (Not needed, keeping standard input)
    # print("✅ Non-blocking input handler REMOVED (Interactive Mode)")

    # =========================================================================
    # 3. Initialize Composio    # User Identity
    # Using specific entity ID that holds the active integrations (GitHub, Linear, Notion, etc.)
    # user_id = "user_123"  # Consolidated to the primary admin identity
    user_id = resolve_user_id()
    print(f"DEBUG: Resolved Identity -> {user_id}")

    from universal_agent.utils.composio_discovery import (
        discover_connected_toolkits,
        get_local_tools,
    )

    # Initialize Client
    composio = Composio(
        api_key=os.getenv("COMPOSIO_API_KEY"), file_download_dir=downloads_dir
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
        composio.create, user_id=user_id, toolkits={"disable": ["firecrawl", "exa"]}
    )

    # Task 2: dynamic Remote Discovery (INFORMATIONAL - Can happen in background)
    # Using client.connected_accounts.list(user_ids=[user_id]) for reliable persistent connection check
    print("⏳ Discovering connected apps...", flush=True)
    discovery_future = asyncio.to_thread(discover_connected_toolkits, composio, user_id)

    # Await the Critical Path (Session) first
    session = await session_future
    print("✅ Composio Session Created")

    # Await Discovery (Informational)
    # Ideally we'd let this run even longer, but we print it right here.
    # Even just overlapping the API calls saves ~1.5s.
    ALLOWED_APPS = await discovery_future

    if not ALLOWED_APPS:
        # Fallback if discovery completely fails or no apps connected
        ALLOWED_APPS = [
            "gmail",
            "github",
            "codeinterpreter",
            "slack",
            "composio_search",
        ]
        print(
            f"⚠️ Discovery returned 0 apps (or only codeinterpreter). Using defaults: {ALLOWED_APPS}"
        )
    else:
        print(f"✅ Discovered Active Composio Apps: {ALLOWED_APPS}")

    # 2. Local MCP Discovery
    local_tools = get_local_tools()
    print(f"✅ Active Local MCP Tools: {local_tools}")

    # 3. External MCP Servers (registered in mcp_servers config)
    external_mcps = [
        "edgartools",
        "video_audio",
        "youtube",
        "zai_vision",
    ]  # List of external MCPs we've configured
    print(f"✅ External MCP Servers: {external_mcps}")

    # 4. Skill Discovery - Parse .claude/skills/ for progressive disclosure
    discovered_skills = discover_skills()
    skill_names = [s["name"] for s in discovered_skills]
    print(f"✅ Discovered Skills: {skill_names}")
    skills_xml = generate_skills_xml(discovered_skills)
    templates_path = os.path.join(
        src_dir,
        ".claude",
        "skills",
        "massive-report-writing",
        "references",
        "massive_report_templates.md",
    )
    tool_knowledge_content = get_tool_knowledge_content()
    tool_knowledge_block = get_tool_knowledge_block()
    tool_knowledge_suffix = (
        f"\n\n{tool_knowledge_block}" if tool_knowledge_block else ""
    )

    # Create ClaudeAgentOptions now that session is available
    global options

    # --- MEMORY SYSTEM CONTEXT INJECTION ---
    memory_context_str = ""
    disable_local_memory = os.getenv("UA_DISABLE_LOCAL_MEMORY", "").lower() in {
        "1",
        "true",
        "yes",
    }
    if disable_local_memory:
        print("⚠️ Local memory system disabled via UA_DISABLE_LOCAL_MEMORY.")
    else:
        try:
            from Memory_System.manager import MemoryManager
            from universal_agent.agent_college.integration import setup_agent_college

            # Initialize strictly for reading context (shared storage) - Use src_dir (Repo Root)
            storage_path = os.getenv(
                "PERSIST_DIRECTORY", os.path.join(src_dir, "Memory_System_Data")
            )
            mem_mgr = MemoryManager(storage_dir=storage_path)

            # Initialize Agent College (Sandbox)
            setup_agent_college(mem_mgr)

            memory_context_str = mem_mgr.get_system_prompt_addition()
            print(f"🧠 Injected Core Memory Context ({len(memory_context_str)} chars)")
        except Exception as e:
            print(f"⚠️ Failed to load Memory Context/Agent College: {e}")

    try:
        registry = load_identity_registry()
        alias_keys = sorted(registry.aliases.keys())
        print(
            "✅ Identity registry loaded: "
            f"primary_email={registry.primary_email or 'unset'}, "
            f"aliases={alias_keys}"
        )
    except Exception as e:
        print(f"⚠️ Failed to load identity registry: {e}")

    # Use timezone-aware datetime for consistent results across deployments
    user_now = get_user_datetime()
    today_str = user_now.strftime("%A, %B %d, %Y")
    tomorrow_str = (user_now + timedelta(days=1)).strftime("%A, %B %d, %Y")

    disallowed_tools = list(DISALLOWED_TOOLS)
    if disable_local_memory:
        disallowed_tools.extend(
            [
                "mcp__local_toolkit__core_memory_replace",
                "mcp__local_toolkit__core_memory_append",
                "mcp__local_toolkit__archival_memory_insert",
                "mcp__local_toolkit__archival_memory_search",
                "mcp__local_toolkit__get_core_memory_blocks",
            ]
        )

    # Unify architecture: Use shared hooks from hooks.py
    # Initialize hooks manager (passing global run context)
    hooks_manager = AgentHookSet(
        run_id=run_id,
        tool_ledger=tool_ledger,
        runtime_db_conn=runtime_db_conn,
        enable_skills=True,
        active_workspace=str(workspace_dir)
    )

    agent = UniversalAgent(workspace_dir=workspace_dir)
    setup_log_bridge(agent)
    options = ClaudeAgentOptions(
        model="claude-3-5-sonnet-20241022",
        add_dirs=[os.path.join(src_dir, ".claude")],
        setting_sources=["project"],  # Enable loading agents from .claude/agents/
        disallowed_tools=disallowed_tools,
        # Read token limits from env vars (default 64K for good balance of capacity vs safety)
        env={
            "CLAUDE_CODE_MAX_OUTPUT_TOKENS": os.getenv("CLAUDE_CODE_MAX_OUTPUT_TOKENS", "64000"),
            "MAX_MCP_OUTPUT_TOKENS": os.getenv("MAX_MCP_OUTPUT_TOKENS", "64000"),
        },
        system_prompt=(
            f"Current Date: {today_str}\n"
            f"Tomorrow is: {tomorrow_str}\n"
            f"{memory_context_str}\n"
            "TEMPORAL CONTEXT: Use the current date above as authoritative. "
            "Do not treat post-training dates as hallucinations if they are supported by tool results. "
            "If sources are older or dated, note that explicitly rather than dismissing them.\n\n"
            "You are a helpful assistant with access to external tools. "
            "You can execute code when needed using COMPOSIO_REMOTE_WORKBENCH or any available code execution tool.\n\n"
            "🔍 SEARCH TOOL PREFERENCE:\n"
            "- For web/news research, ALWAYS use Composio search tools (SERPAPI_SEARCH, COMPOSIO_SEARCH_NEWS, etc.).\n"
            "- Do NOT use native 'WebSearch' - it bypasses our artifact saving system.\n"
            "- Composio search results are auto-saved by the Observer for sub-agent access.\n\n"
            "🔒 SEARCH HYGIENE (MANDATORY):\n"
            "- ALWAYS append `-site:wikipedia.org` to EVERY search query (COMPOSIO_SEARCH_NEWS, COMPOSIO_SEARCH_WEB, SERPAPI_SEARCH).\n"
            "- This is MANDATORY, not optional. Wikipedia wastes search query slots.\n"
            "- The only exception is if the user explicitly requests Wikipedia content.\n"
            "IMPORTANT EXECUTION GUIDELINES:\n"
            "- When the user requests an action (send email, upload file, execute code), proceed immediately without asking for confirmation.\n"
            "- The user has already authorized these actions by making the request.\n"
            "- Do not ask 'Should I proceed?' or 'Do you want me to send this?'\n"
            "- Complete the full task end-to-end in a single workflow.\n"
            "- If authentication is required, guide the user through it, then continue automatically.\n\n"
            "⚡ AUTONOMOUS EXECUTION PROTOCOL:\n"
            "- If you calculate a large scope (e.g. >10 hours), DO NOT ASK FOR PERMISSION. EXECUTE IT.\n"
            "- You have FULL AUTHORITY to commit resources. Do not pause. Do not offer options.\n"
            "- If faced with a choice (e.g. Batch vs Full), choose the most aggressive safe option (Full) to maximize results.\n"
            "- The goal is to COMPLETE THE MISSION, not be polite.\n\n"
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
            "4. 🚨 MANDATORY DELEGATION FOR RESEARCH & REPORTS:\n"
            "   - Role: You are the COORDINATOR. You delegate work to specialists.\n"
            "   - DO NOT perform web searches yourself. Delegate to `research-specialist`.\n"
            "   - PROCEDURE:\n"
            "     1. **STEP 1:** Delegate to `research-specialist` using `Task` IMMEDIATELY.\n"
            "        PROMPT: 'Research [topic]: execute searches, crawl sources, finalize corpus.'\n"
            "        (The research-specialist handles: COMPOSIO search → crawl → filter → overview)\n"
            "     2. **STEP 2:** When Step 1 completes, delegate to `report-writer` using `Task`.\n"
            "        PROMPT: 'Write the full HTML report using refined_corpus.md.'\n"
            "   - ✅ SubagentStop HOOK: When the sub-agent finishes, a hook will inject next steps.\n"
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
            "   - HOW: Use the native `Write` tool with:\n"
            "     - `file_path`: CURRENT_SESSION_WORKSPACE + '/work_products/' + descriptive_name\n"
            "     - `content`: The full output you're about to show the user\n"
            "   - NAMING: `dependency_summary.md`, `calendar_events.txt`, `generated_script.py`\n"
            "   - `work_products` dir is auto-created. Just save there.\n"
            "     Wait for 'File saved...' confirmation before proceeding.\n\n"
            "9. 🔗 MANDATORY REPORT DELEGATION (YOU MUST DELEGATE):\n"
            "   🚨 TRIGGER KEYWORDS REQUIRING DELEGATION: 'report', 'comprehensive', 'detailed', 'in-depth', 'analysis', 'research'.\n"
            "   IF the user query contains ANY of these keywords, you MUST delegate to 'report-creation-expert'.\n"
            "   - After a Composio search, the Observer AUTO-SAVES results to `search_results/` directory.\n"
            "   - You will see: '📁 [OBSERVER] Saved: search_results/xxx.json'.\n"
            "   - DO NOT write the report yourself. DO NOT call `crawl_parallel` yourself.\n"
            "   - IMMEDIATELY delegate to 'report-creation-expert' with: 'Call finalize_research, then use refined_corpus.md to generate the report.'\n"
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
                "args": [
                    os.path.join(
                        os.path.dirname(os.path.dirname(__file__)), "mcp_server.py"
                    )
                ],
                # Pass Logfire token for observability
                "env": {
                    "LOGFIRE_TOKEN": os.environ.get("LOGFIRE_TOKEN", ""),
                },
            },
            # External MCP: SEC Edgar Tools for financial research
            "edgartools": {
                "type": "stdio",
                "command": sys.executable,
                "args": ["-m", "edgar.ai"],
                "env": {
                    "EDGAR_IDENTITY": os.environ.get(
                        "EDGAR_IDENTITY", "Agent agent@example.com"
                    )
                },
            },
            # External MCP: Video & Audio editing via FFmpeg
            "video_audio": {
                "type": "stdio",
                "command": sys.executable,
                "args": [
                    os.path.join(
                        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                        "external_mcps",
                        "video-audio-mcp",
                        "server.py",
                    )
                ],
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
            "internal": create_sdk_mcp_server(
                name="internal",
                version="1.0.0",
                tools=[
                    run_research_pipeline_wrapper,
                    crawl_parallel_wrapper,
                    run_research_phase_wrapper,
                    run_report_generation_wrapper,
                ]
            ),
        },
        # NOTE: Do NOT set allowed_tools here - that would restrict to ONLY those tools.
        # The agent needs access to BOTH Composio tools (COMPOSIO_SEARCH_NEWS, etc.)
        # AND local_toolkit tools (crawl_parallel, read_local_file, etc.)
        # Sub-agents inherit all tools via model="inherit".

        hooks=hooks_manager.build_hooks(),
        permission_mode="bypassPermissions",
    )
    _warn_if_subagent_hooks_configured(options.agents)

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
        "token_usage": {"input": 0, "output": 0, "total": 0},
        "logfire_enabled": bool(LOGFIRE_TOKEN),
    }

    # Observer workspace already bound during session setup

    # Setup Output Logging
    log_file = open_run_log(workspace_dir)
    attach_run_log(log_file)

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
    if tool_knowledge_block:
        if options.system_prompt and isinstance(options.system_prompt, str):
            options.system_prompt += f"\n\n{tool_knowledge_block}"
        else:
            options.system_prompt = tool_knowledge_block
        print(f"✅ Injected Knowledge Base ({len(tool_knowledge_content)} chars)")


    return options, session, user_id, workspace_dir, trace, agent


async def process_turn(
    client: ClaudeSDKClient,
    user_input: str,
    workspace_dir: str,
    force_complex: bool = False,
    execution_session: Optional[ExecutionSession] = None,
    max_iterations: int = 20,
) -> ExecutionResult:
    """
    Process a single user query.
    Returns: ExecutionResult with rich feedback
    """
    global trace, start_ts, run_id, runtime_db_conn, current_execution_session

    session_ctx = execution_session or current_execution_session
    if session_ctx:
        if session_ctx.trace is not None:
            trace = session_ctx.trace
        if session_ctx.run_id is not None:
            run_id = session_ctx.run_id
        if session_ctx.runtime_db_conn is not None:
            runtime_db_conn = session_ctx.runtime_db_conn

    def _set_observer_workspace(path: str) -> None:
        global OBSERVER_WORKSPACE_DIR
        OBSERVER_WORKSPACE_DIR = path

    abs_workspace_dir = bind_workspace(
        workspace_dir, absolute=True, observer_setter=_set_observer_workspace
    )

    trace["query"] = user_input
    trace["start_time"] = datetime.now().isoformat()
    start_ts = time.time()

    if LOGFIRE_TOKEN:
        logfire.info("query_started", query=user_input)

    # 1a. Check for /harness command (massive request handler)
    if user_input.strip().startswith("/harness "):
        from .urw.harness_orchestrator import run_harness
        
        massive_request = user_input.strip()[9:]  # Strip "/harness " prefix
        print(f"\n{'=' * 60}")
        print("🎯 HARNESS MODE ACTIVATED")
        print(f"{'=' * 60}")
        print(f"Request: {massive_request[:100]}...")
        
        workspaces_root = Path(workspace_dir).parent
        
        try:
            result = await run_harness(
                massive_request=massive_request,
                workspaces_root=workspaces_root,
                process_turn=process_turn,
                client=client,
                max_iterations=args.max_iterations,
            )
            
            summary = f"Harness complete: {result.get('phases_completed', 0)} phases completed"
            return ExecutionResult(
                success=result.get("status") == "complete",
                final_output=summary,
                query=user_input,
                time_taken=time.time() - start_ts,
                error=result.get("error"),
            )
        except Exception as e:
            print(f"\n❌ Harness error: {e}")
            import traceback
            traceback.print_exc()
            return ExecutionResult(
                success=False,
                final_output="",
                query=user_input,
                time_taken=time.time() - start_ts,
                error=str(e),
            )

    # 2. Determine Complexity
    complexity = (
        "COMPLEX" if force_complex else await classify_query(client, user_input)
    )

    # 3. Route Query
    is_simple = (complexity == "SIMPLE") and not force_complex

    final_response_text = ""

    if is_simple:
        # Try Fast Path
        # Try Fast Path
        success, fast_path_text = await handle_simple_query(client, user_input)
        if not success:
            is_simple = False  # Fallback to Complex Path
        else:
            final_response_text = fast_path_text

    if not is_simple:
        # Complex Path (Tool Loop) - track per-request timing
        request_start_ts = time.time()
        iteration = 1
        current_query = user_input

        while True:
            needs_input, auth_link, final_text = await run_conversation(
                client, current_query, start_ts, iteration, max_iterations=max_iterations
            )
            final_response_text = final_text  # Capture for printing after summary

            if needs_input and auth_link:
                print(f"\n{'=' * 80}")
                print("🔐 AUTHENTICATION REQUIRED")
                print(f"{'=' * 80}")
                print(f"\nPlease open this link in your browser:\n")
                print(f"  {auth_link}\n")
                print("After completing authentication, press Enter to continue...")
                input()  # Non-blocking mock in headless, real input in CLI

                current_query = "I have completed the authentication. Please continue with the task."
                iteration += 1
                continue
            else:
                break

        # Per-request Execution Summary
        request_end_ts = time.time()
        request_duration = round(request_end_ts - request_start_ts, 3)

        # Collect tool calls for this request
        request_tool_calls = [
            tc
            for tc in trace["tool_calls"]
            if tc.get("time_offset_seconds", 0) >= (request_start_ts - start_ts)
        ]

        print(f"\n{'=' * 80}")
        print("=== EXECUTION SUMMARY ===")
        print(f"{'=' * 80}")
        print(f"Execution Time: {request_duration} seconds")
        print(f"Tool Calls: {len(request_tool_calls)}")

        # Check for code execution
        code_exec_used = any(
            any(
                x in tc["name"].upper()
                for x in ["WORKBENCH", "CODE", "EXECUTE", "PYTHON", "SANDBOX", "BASH"]
            )
            for tc in request_tool_calls
        )
        if code_exec_used:
            print("🏭 Code execution was used")

        # Tool breakdown
        tool_breakdown = []
        if request_tool_calls:
            print("\n=== TOOL CALL BREAKDOWN ===")
            for tc in request_tool_calls:
                name = tc.get("name", "")
                input_payload = tc.get("input") or {}
                bash_snippet = ""
                if "BASH" in name.upper() and isinstance(input_payload, dict):
                    command = input_payload.get("command")
                    description = input_payload.get("description")
                    snippet_source = command or description or ""
                    if snippet_source:
                        snippet = str(snippet_source).replace("\n", " ").strip()
                        if len(snippet) > 80:
                            snippet = snippet[:77] + "..."
                        bash_snippet = f" ({snippet})"
                marker = (
                    "🏭"
                    if any(
                        x in tc["name"].upper()
                        for x in ["WORKBENCH", "CODE", "EXECUTE", "BASH"]
                    )
                    else "  "
                )
                print(
                    f"  {marker} Iter {tc['iteration']} | +{tc['time_offset_seconds']:>6.1f}s | {tc['name']}{bash_snippet}"
                )
                tool_breakdown.append(
                    {
                        "name": tc["name"],
                        "time_offset": tc["time_offset_seconds"],
                        "iteration": tc["iteration"],
                        "marker": marker,
                    }
                )

        # Collect and display all trace IDs for debugging
        local_trace_ids = collect_local_tool_trace_ids(workspace_dir)
        print("\n=== TRACE IDS (for Logfire debugging) ===")
        print(f"  Main Agent:     {trace.get('trace_id', 'N/A')}")
        if local_trace_ids:
            # Always list all unique local-toolkit trace IDs (don't hide if matches main)
            print(f"  Local Toolkit:  {', '.join(local_trace_ids[:5])}")
            if len(local_trace_ids) > 5:
                print(f"                  (+{len(local_trace_ids) - 5} more)")
        else:
            print(f"  Local Toolkit:  (no local tool calls)")

        # Store in trace for transcript/evaluation
        trace["local_toolkit_trace_ids"] = local_trace_ids

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
            write_trace(trace, workspace_dir)
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
        follow_up_suggestions=suggestions if not is_simple else [],
    )


async def main(args: argparse.Namespace):
    global \
        trace, \
        run_id, \
        budget_config, \
        budget_state, \
        runtime_db_conn, \
        tool_ledger, \
        provider_session_forked_from, \
        current_execution_session, \
        gateway_mode_active, \
        current_step_id

    # Create main span for entire execution
    main_span = logfire.span("standalone_composio_test")
    span_ctx = main_span.__enter__()  # Start the span manually

    # Extract trace ID for display
    main_trace_id_hex = "0" * 32
    if LOGFIRE_TOKEN:
        try:
            trace_id = main_span.get_span_context().trace_id
            main_trace_id_hex = format(trace_id, "032x")
        except Exception as e:
            print(f"⚠️ Failed to extract main trace ID: {e}")

    budget_config = load_budget_config()
    db_path = get_runtime_db_path()
    print(f"DEBUG: Connecting to DB at {db_path}", flush=True)
    runtime_db_conn = connect_runtime_db(db_path)
    ensure_schema(runtime_db_conn)
    validate_tool_policies()

    # Ensure Letta context is initialized inside the event loop.
    if LETTA_ENABLED:
        try:
            await _ensure_letta_context()
        except Exception as exc:
            print(f"⚠️ Letta context init failed: {exc}")

    if args.explain_tool_policy:
        _print_tool_policy_explain(args.explain_tool_policy)
        main_span.__exit__(None, None, None)
        return

    gateway_url = getattr(args, 'gateway_url', None) or os.getenv("UA_GATEWAY_URL")
    use_gateway = args.use_gateway or gateway_url or os.getenv("UA_USE_GATEWAY", "").lower() in {
        "1",
        "true",
        "yes",
    } or os.getenv("UA_DEV_GATEWAY_DEFAULT", "").lower() in {
        "1",
        "true",
        "yes",
    }
    gateway_use_cli_workspace = (
        args.gateway_use_cli_workspace
        or os.getenv("UA_GATEWAY_USE_CLI_WORKSPACE", "").lower() in {"1", "true", "yes"}
    )
    if use_gateway and (
        args.resume
        or args.fork
        or args.harness_objective
        or args.urw_request
    ):
        print(
            "⚠️ Gateway preview disabled for resume/fork/harness/URW modes; using CLI path."
        )
        use_gateway = False
        gateway_use_cli_workspace = False
    
    # Handle --reset-letta: clear all Letta memory
    if getattr(args, 'reset_letta', False) and LETTA_ENABLED and _letta_client:
        print("🧹 Resetting Letta memory...")
        try:
            # Delete and recreate the main agent
            try:
                _letta_client.agents.delete(LETTA_AGENT_NAME)
                print(f"   Deleted agent: {LETTA_AGENT_NAME}")
            except Exception:
                pass
            _letta_client.agents.create(
                agent=LETTA_AGENT_NAME,
                memory=LETTA_MEMORY_BLOCKS,
                model="anthropic/claude-sonnet-4-20250514",
            )
            print(f"   Recreated agent: {LETTA_AGENT_NAME}")
            
            # List and delete any subagent agents
            try:
                all_agents = _letta_client.agents.list()
                for agent in all_agents:
                    agent_name = getattr(agent, 'name', '') or getattr(agent, 'agent', '')
                    if agent_name and agent_name != LETTA_AGENT_NAME:
                        try:
                            _letta_client.agents.delete(agent_name)
                            print(f"   Deleted subagent: {agent_name}")
                        except Exception:
                            pass
            except Exception:
                pass
            
            print("✅ Letta memory reset complete!")
        except Exception as e:
            print(f"⚠️ Letta reset error: {e}")
        main_span.__exit__(None, None, None)
        return

    if args.urw_request:
        await _run_urw_from_cli(args)
        main_span.__exit__(None, None, None)
        return

    run_spec = None
    workspace_override = args.workspace
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

    options, session, user_id, workspace_dir, trace, agent = await setup_session(
        run_id_override=run_id_override,
        workspace_dir_override=workspace_override,
    )
    current_execution_session = ExecutionSession(
        workspace_dir=workspace_dir,
        run_id=run_id,
        trace=trace,
        runtime_db_conn=runtime_db_conn,
    )

    trace["budgets"] = budget_config
    tool_ledger = ToolCallLedger(runtime_db_conn, workspace_dir)

    gateway = None
    if use_gateway:
        if gateway_url:
            try:
                gateway = ExternalGateway(base_url=gateway_url)
                print(f"🌐 Using external gateway: {gateway_url}")
                logfire.info(
                    "gateway_mode_selected",
                    mode="external",
                    gateway_url=gateway_url,
                    run_id=run_id,
                )
            except RuntimeError as e:
                print(f"⚠️ External gateway unavailable ({e}), falling back to in-process")
                gateway = InProcessGateway(hooks=build_cli_hooks())
                logfire.info(
                    "gateway_mode_selected",
                    mode="in_process",
                    fallback="external_unavailable",
                    run_id=run_id,
                )
        else:
            gateway = InProcessGateway(hooks=build_cli_hooks())
            logfire.info(
                "gateway_mode_selected",
                mode="in_process",
                run_id=run_id,
            )
    else:
        logfire.info("gateway_mode_selected", mode="cli_direct", run_id=run_id)
    gateway_session = None
    async def _ensure_gateway_session() -> bool:
        nonlocal gateway_session, gateway
        if not gateway:
            return False
        if gateway_session:
            return True
        try:
            gateway_session = await gateway.create_session(
                user_id=user_id or "user_cli",
                workspace_dir=workspace_dir if gateway_use_cli_workspace else None,
            )
            verbose = getattr(args, 'verbose', False) or os.getenv("UA_VERBOSE", "").lower() in {"1", "true", "yes"}
            if verbose:
                print("🧭 Gateway preview enabled (in-process).")
                print(f"Gateway Session: {gateway_session.session_id}")
                print(f"Gateway Workspace: {gateway_session.workspace_dir}")
            print(
                "🧭 Gateway session active: "
                f"{gateway_session.session_id} "
                f"({gateway_session.workspace_dir})"
            )
            logfire.info(
                "gateway_session_created",
                session_id=gateway_session.session_id,
                workspace_dir=gateway_session.workspace_dir,
                requested_workspace=workspace_dir if gateway_use_cli_workspace else None,
                run_id=run_id,
            )

            def _set_gateway_observer_workspace(path: str) -> None:
                global OBSERVER_WORKSPACE_DIR
                OBSERVER_WORKSPACE_DIR = path

            bind_workspace(
                gateway_session.workspace_dir,
                absolute=True,
                observer_setter=_set_gateway_observer_workspace,
            )
            return True
        except Exception as exc:
            print(f"⚠️ Gateway initialization failed, falling back to CLI: {exc}")
            gateway = None
            gateway_session = None
            return False

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

    print(f"DEBUG: After setups - run_id global is {run_id}", flush=True)

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
            max_iterations=args.max_iterations,
            completion_promise=args.completion_promise,
        )
        logfire.info("durable_run_upserted", run_id=run_id, entrypoint="cli")
        if parent_run_id:
            trace["parent_run_id"] = parent_run_id

    # Use the trace ID extracted earlier (now stored in main_trace_id_hex and env var)
    trace["trace_id"] = main_trace_id_hex

    # Extract timestamp from workspace_dir (e.g. "session_20251228_123456" -> "20251228_123456")
    timestamp = os.path.basename(workspace_dir).replace("session_", "")

    print(f"\n{'=' * 60}")
    print("         🔍 TRACE IDS (Logfire context)")
    print(f"{'=' * 60}")
    print(f"  Main Agent Trace ID:    {main_trace_id_hex}")
    if use_gateway:
        gateway_mode = "external" if gateway_url else "in_process"
        print(f"  Gateway Mode:           {gateway_mode}")
        if gateway_url:
            print(f"  Gateway URL:            {gateway_url}")
    else:
        print("  Gateway Mode:           cli_direct")
    print("  Local Toolkit Trace IDs: emitted inline as")
    print("                           [local-toolkit-trace-id: <id>] in tool results")
    print(f"{'=' * 60}")
    print(f"Run ID: {run_id}")
    print(f"Timestamp: {timestamp}")
    print(f"Trace ID: {main_trace_id_hex}")
    if run_id:
        resume_cmd = (
            "PYTHONPATH=src uv run python -m universal_agent.main "
            f"--resume --run-id {run_id}"
        )
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
    prompt_session = build_prompt_session(history_file)

    run_status = run_row["status"] if run_row else None
    should_auto_continue_job = (
        args.resume
        and run_status not in TERMINAL_STATUSES
        and run_status not in WAITING_STATUSES
    )

    def save_interrupt_checkpoint(prompt_preview: str) -> None:
        global current_step_id
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

    first_attempt_client = None
    try:
        first_attempt_client = ClaudeSDKClient(options)
        await first_attempt_client.__aenter__()
        client = first_attempt_client
    except Exception as e:
        error_msg = str(e)
        # If we fail to connect AND we were trying to resume, assume the resume token is stale/invalid.
        # This is safer than string matching specific error messages which might change.
        should_fallback = options.resume is not None
        
        if should_fallback:
            print(f"\n❌ Provider session resume failed: {error_msg} ({type(e).__name__})")
            print("⚠️ Falling back to NEW provider session (local workspace artifacts preserved)...")
            # Disable provider resume but keep local resume flow
            options.resume = None
            if first_attempt_client:
                # Cleanup if partially initialized
                try: 
                    await first_attempt_client.__aexit__(None, None, None)
                except: 
                    pass
            # Retry with clean options
            client = ClaudeSDKClient(options)
            await client.__aenter__()
        else:
            raise

    # We use a try/finally to ensure __aexit__ is called since we manually called __aenter__
    try:
        run_failed = False
        if args.harness_objective:
            pending_prompt = f"/harness {args.harness_objective}"
        else:
            pending_prompt = (
                job_prompt if job_prompt and not args.resume and not args.fork else None
            )
        try:
            if (
                args.resume
                and run_mode == "job"
                and run_status in TERMINAL_STATUSES
                and run_id
            ):
                print("✅ Run already terminal. No resume needed.")
                print_job_completion_summary(
                    runtime_db_conn,
                    run_id,
                    run_status,
                    workspace_dir,
                    "",
                    trace,
                    update_restart_file,
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
                                execution_session=current_execution_session,
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
                                        trace,
                                        update_restart_file,
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
                        user_input = await read_prompt_input(
                            prompt_session,
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

                # [DEBUG] Skip interview and use sample plan for testing
                if user_input.lower().strip().startswith("/harness-test"):
                    print("\n⚙️  Activating Universal Agent Harness (TEST MODE - Skip Interview)...")
                    
                    try:
                        from pathlib import Path
                        from .urw.harness_orchestrator import run_harness
                        
                        workspaces_root = Path(workspace_dir).parent
                        
                        # Use sample plan path
                        sample_plan_path = Path(__file__).parent / "urw" / "sample_plan.json"
                        print(f"📋 Loading sample plan: {sample_plan_path}")
                        
                        result = await run_harness(
                            massive_request="[TEST] Solar energy benefits report",
                            workspaces_root=workspaces_root,
                            process_turn=process_turn,
                            client=client,
                            skip_interview=True,
                            plan_file=sample_plan_path,
                        )
                        
                        print(f"\n{'=' * 60}")
                        print("🏁 HARNESS TEST COMPLETE")
                        print(f"{'=' * 60}")
                        print(f"Status: {result.get('status', 'unknown')}")
                        print(f"Phases completed: {result.get('phases_completed', 0)}")
                        if result.get('harness_dir'):
                            print(f"Work products: {result.get('harness_dir')}")
                        
                    except Exception as e:
                        print(f"\n❌ Harness test error: {e}")
                        import traceback
                        traceback.print_exc()
                    
                    continue

                # [DEBUG] Skip interview and generate plan from template (sample_interview.json)
                if user_input.lower().strip().startswith("/harness-template"):
                    print("\n⚙️  Activating Universal Agent Harness (TEMPLATE MODE - Generate Plan)...")
                    
                    try:
                        from pathlib import Path
                        from .urw.harness_orchestrator import run_harness
                        
                        workspaces_root = Path(workspace_dir).parent
                        
                        # Use sample interview transcript
                        template_path = Path(__file__).parent / "urw" / "sample_interview.json"
                        print(f"📋 Loading interview template: {template_path}")
                        
                        result = await run_harness(
                            massive_request="[TEMPLATE] Solar energy benefits report",
                            workspaces_root=workspaces_root,
                            process_turn=process_turn,
                            client=client,
                            template_file=template_path,
                        )
                        
                        print(f"\n{'=' * 60}")
                        print("🏁 HARNESS TEMPLATE TEST COMPLETE")
                        print(f"{'=' * 60}")
                        print(f"Status: {result.get('status', 'unknown')}")
                        if result.get('harness_dir'):
                            print(f"Work products: {result.get('harness_dir')}")
                        
                    except Exception as e:
                        print(f"\n❌ Harness template error: {e}")
                        import traceback
                        traceback.print_exc()
                    
                    continue

                if user_input.lower().strip().startswith("/harness"):
                    print("\n⚙️  Activating Universal Agent Harness (Phase-Based)...")

                    # Check if user provided an objective in the command
                    parts = user_input.strip().split(" ", 1)
                    if len(parts) > 1:
                        target_objective = parts[1]
                    else:
                        print(
                            "📝 Please enter the OBJECTIVE for this long-running task:"
                        )
                        print(
                            "(Paste multi-line text, then press Enter twice to confirm)"
                        )
                        lines = []
                        consecutive_blanks = 0
                        while True:
                            try:
                                line = input("Objective > " if not lines else "... ")
                                if line.strip().upper() == "END":
                                    break
                                if not line.strip():
                                    consecutive_blanks += 1
                                    if consecutive_blanks >= 2:
                                        break  # Two blank lines = done
                                    lines.append(
                                        line
                                    )  # Keep single blank lines in content
                                else:
                                    consecutive_blanks = 0
                                    lines.append(line)
                            except EOFError:
                                break
                        target_objective = "\n".join(lines).strip()

                    if not target_objective:
                        print("⚠️ No objective provided. Harness activation cancelled.")
                        continue

                    print(f"🎯 Objective: {target_objective}")

                    from pathlib import Path

                    # [NEW] Configure Auto-Interview
                    if args.interview_auto:
                        from .urw import interview
                        answers = [a.strip() for a in args.interview_auto.split(",") if a.strip()]
                        interview.set_auto_answers(answers)
                        print(f"⏩ Auto-Interview Enabled: {len(answers)} pre-filled answers loaded.")

                    # [NEW] Template Mode
                    template_file = None
                    # [NEW] Template Mode
                    template_file = None
                    if args.harness_template:
                         # Resolve the path relative to CWD or absolute
                         given_path = Path(args.harness_template) 
                         if not given_path.exists():
                             # Try relative to module if not found
                             module_path = Path(__file__).parent / "urw" / args.harness_template
                             if module_path.exists():
                                 given_path = module_path
                         
                         template_file = given_path
                         if not template_file.exists():
                             print(f"❌ Template file not found: {args.harness_template}")
                             return 
                         
                    # Route to HarnessOrchestrator for phase-based execution
                    try:
                        from .urw.harness_orchestrator import run_harness
                        
                        workspaces_root = Path(workspace_dir).parent
                        
                        run_kwargs = {
                            "massive_request": target_objective,
                            "workspaces_root": workspaces_root,
                            "process_turn": process_turn,
                            "client": client,
                            "agent": None,
                        }

                        if template_file:
                             # Pass as template_file (transcript) for generation
                             run_kwargs["template_file"] = template_file
                             print(f"📋 Running with interview template: {template_file}")

                        result = await run_harness(**run_kwargs)
                        
                        print(f"\n{'=' * 60}")
                        print("🏁 HARNESS COMPLETE")
                        print(f"{'=' * 60}")
                        print(f"Status: {result.get('status', 'unknown')}")
                        print(f"Phases completed: {result.get('phases_completed', 0)}")
                        if result.get('harness_dir'):
                            print(f"Work products: {result.get('harness_dir')}")
                        
                    except Exception as e:
                        print(f"\n❌ Harness error: {e}")
                        import traceback
                        traceback.print_exc()
                    
                    continue  # Return to prompt after harness completes

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

                    try:
                        # [Harness Mode] Force Complex Path to avoid Fast Path output capture bug
                        harness_info = get_iteration_info(runtime_db_conn, run_id)
                        force_complex_for_harness = bool(harness_info.get("completion_promise"))
                        if force_complex_for_harness:
                            print("📋 Harness mode active - forcing Complex Path")

                        if gateway:
                            gateway_used_fast_path = False
                            if not force_complex_for_harness:
                                gateway_decision = await classify_query(client, user_input)
                                if gateway_decision == "SIMPLE":
                                    handled, fast_path_text = await handle_simple_query(
                                        client, user_input
                                    )
                                    if handled:
                                        result = ExecutionResult(
                                            response_text=fast_path_text,
                                            execution_time_seconds=0.0,
                                            tool_calls=0,
                                            tool_breakdown=[],
                                            code_execution_used=False,
                                            workspace_path=workspace_dir,
                                            trace_id=None,
                                            follow_up_suggestions=[],
                                        )
                                        gateway_used_fast_path = True

                            if not gateway_used_fast_path:
                                if not await _ensure_gateway_session():
                                    result = await process_turn(
                                        client,
                                        user_input,
                                        workspace_dir,
                                        force_complex=force_complex_for_harness,
                                        execution_session=current_execution_session,
                                        max_iterations=args.max_iterations,
                                    )
                                else:
                                    gateway_step_id = str(uuid.uuid4())
                                    current_step_id = gateway_step_id
                                    if runtime_db_conn and run_id:
                                        try:
                                            step_index = _next_step_index(runtime_db_conn, run_id)
                                            start_step(runtime_db_conn, run_id, gateway_step_id, step_index)
                                            logfire.info(
                                                "durable_step_started_gateway",
                                                run_id=run_id,
                                                step_id=gateway_step_id,
                                                step_index=step_index,
                                            )
                                        except Exception as exc:
                                            logfire.warning(
                                                "runtime_step_insert_failed_gateway",
                                                step_id=gateway_step_id,
                                                error=str(exc),
                                            )
                                    next_input = user_input
                                    final_response_text = ""
                                    tool_calls = 0
                                    gateway_mode_active = True
                                    try:
                                        while True:
                                            request_start_ts = time.time()
                                            event_result = await render_agent_events(
                                                gateway.execute(
                                                    gateway_session,
                                                    GatewayRequest(
                                                        user_input=next_input,
                                                        force_complex=force_complex_for_harness,
                                                    ),
                                                ),
                                                workspace_dir=gateway_session.workspace_dir,
                                            )
                                            final_response_text = event_result["response_text"] or ""
                                            tool_calls = event_result["tool_calls"]
                                            if event_result["auth_required"] and event_result["auth_link"]:
                                                print(f"\n{'=' * 80}")
                                                print("🔐 AUTHENTICATION REQUIRED")
                                                print(f"{'=' * 80}")
                                                print(f"\nPlease open this link in your browser:\n")
                                                print(f"  {event_result['auth_link']}\n")
                                                print("After completing authentication, press Enter to continue...")
                                                input()
                                                next_input = "I have completed the authentication. Please continue with the task."
                                                continue
                                            request_duration = time.time() - request_start_ts
                                            trace_id = None
                                            try:
                                                if gateway._bridge.current_agent and hasattr(
                                                    gateway._bridge.current_agent, "trace"
                                                ):
                                                    trace_id = gateway._bridge.current_agent.trace.get(
                                                        "trace_id"
                                                    )
                                            except Exception:
                                                trace_id = None
                                            if event_result["tool_calls"]:
                                                print_execution_summary_from_events(
                                                    request_duration=request_duration,
                                                    tool_call_entries=event_result.get(
                                                        "tool_call_entries", []
                                                    ),
                                                    workspace_dir=gateway_session.workspace_dir,
                                                    trace_id=trace_id,
                                                    work_products=event_result.get("work_products"),
                                                )
                                            if run_mode == "job" and args.job_path:
                                                print_job_completion_summary_from_events(
                                                    session_id=gateway_session.session_id,
                                                    workspace_dir=gateway_session.workspace_dir,
                                                    response_text=final_response_text,
                                                    tool_call_entries=event_result.get(
                                                        "tool_call_entries", []
                                                    ),
                                                    tool_results=event_result.get("tool_results", 0),
                                                    work_products=event_result.get("work_products"),
                                                    errors=event_result.get("errors"),
                                                    trace_id=trace_id,
                                                )
                                            break
                                    finally:
                                        gateway_mode_active = False

                                    result = ExecutionResult(
                                        response_text=final_response_text,
                                        execution_time_seconds=0.0,
                                        tool_calls=tool_calls,
                                        tool_breakdown=[],
                                        code_execution_used=False,
                                        workspace_path=gateway_session.workspace_dir,
                                        trace_id=None,
                                        follow_up_suggestions=[],
                                    )
                        else:
                            result = await process_turn(
                                client,
                                user_input,
                                workspace_dir,
                                force_complex=force_complex_for_harness,
                                execution_session=current_execution_session,
                                max_iterations=args.max_iterations,
                            )
                    except HarnessError as he:
                        # [Harness Error Recovery]
                        # 1. Capture context
                        failure_ctx = he.context
                        last_error = failure_ctx.get("last_tool_error", "Unknown error")
                        print(f"\n\n🔴 HARNESS ABORT: {str(he)}")

                        # 2. Set alert for NEXT iteration
                        failure_alert = (
                            f"⚠️ SYSTEM ALERT (AUTOMATED RECOVERY):\n"
                            f"The previous agent iteration was ABORTED due to a CRITICAL FAILURE.\n"
                            f"REASON: {str(he)}\n"
                            f"LAST ERROR: {last_error}\n"
                            "You are a NEW AGENT instance spawned to recover from this failure.\n"
                            "• Analyze the error to avoid the same mistake.\n"
                            "• If the previous agent got stuck in a loop, try a different approach.\n"
                            "• Check tool arguments carefully."
                        )

                        # 3. Trigger Restart via pending_prompt
                        print(
                            "🔄 Harness triggering immediate restart with failure context..."
                        )
                        pending_prompt = failure_alert

                        # 4. Clear Client History (Force restart)
                        if safe_clear_history(client):
                            print("🧹 Client history cleared (Force restart).")
                        else:
                            logfire.warning("harness_restart_history_clear_failed")

                        # 5. Continue loop (skips existing post-turn logic, goes to next iteration)
                        continue

                    # [PLANNING PHASE CHECK] After each iteration, check if agent created mission.json with PLANNING status
                    # This catches the case where the agent creates a plan on the FIRST iteration
                    planning_mission_file = os.path.join(workspace_dir, "mission.json")
                    if os.path.exists(planning_mission_file):
                        try:
                            with open(planning_mission_file, "r") as f:
                                mission_check = json.load(f)
                            if mission_check.get("status") == "PLANNING":
                                print(
                                    "\n📋 Planning Phase Complete - Awaiting User Approval"
                                )
                                approved = present_plan_summary(mission_check)
                                if approved:
                                    mission_check["status"] = "IN_PROGRESS"
                                    with open(planning_mission_file, "w") as f:
                                        json.dump(mission_check, f, indent=2)
                                    print(
                                        "✅ Plan approved. Transitioning to IN_PROGRESS."
                                    )
                                    # Set next prompt to kick off execution
                                    pending_prompt = "Execute the mission.json tasks starting now."
                                else:
                                    print(
                                        "⏸️ Plan not approved. Please edit mission.json and restart."
                                    )
                                    # Wait for user changes - continue to next prompt
                                    pending_prompt = None
                                    continue
                        except Exception as e:
                            print(f"⚠️ Failed to check mission.json for PLANNING status: {e}")

                    # [Non-Blocking Interview] Check for pending interview after iteration
                    interview_prompt = _process_pending_interview(workspace_dir)
                    if interview_prompt:
                        pending_prompt = interview_prompt

                    # HARNESS LOOP: Manual check (since AgentStop hook is unreliable)
                    if hasattr(result, "response_text"):
                        # Synthesize context for the hook
                        h_ctx = HookContext(
                            systemMessage="",
                            toolCalls=[],
                            toolResults=[],
                            output=result.response_text,
                        )
                        hook_res = on_agent_stop(
                            h_ctx, run_id=run_id, db_conn=runtime_db_conn
                        )

                        hook_out = hook_res.get("hookSpecificOutput", {})
                        action = hook_out.get("action")

                        if action == "complete":
                            print(
                                f"\n✅ HARNESS: Completion promise met. Finishing run."
                            )
                            # Clear harness config to prevent future restarts if loop continues
                            upsert_run(
                                runtime_db_conn,
                                run_id,
                                "cli",
                                run_spec or {},
                                completion_promise=None,
                            )
                            # Optional: break if we want to stop the CLI entirely, but for now just don't restart
                            pass

                        if action == "restart":
                            next_prompt = hook_out.get("nextPrompt")

                            # [Mission Manifest Injection]
                            # Detect strict handoff files and inject them into the system prompt
                            mission_file = os.path.join(workspace_dir, "mission.json")
                            progress_file = os.path.join(
                                workspace_dir, "mission_progress.txt"
                            )

                            manifest_context = ""
                            if os.path.exists(mission_file):
                                try:
                                    with open(mission_file, "r") as f:
                                        manifest_context += f"\n\n[RESUMING MISSION]\nHere is the official Mission Manifest (mission.json): \n```json\n{f.read()}\n```"
                                except Exception as e:
                                    print(f"⚠️ Failed to read mission.json: {e}")

                            if os.path.exists(progress_file):
                                try:
                                    with open(progress_file, "r") as f:
                                        manifest_context += f"\n\n[PREVIOUS NOTES]\nHere are your notes from the previous session (mission_progress.txt): \n```text\n{f.read()}\n```"
                                except Exception as e:
                                    print(f"⚠️ Failed to read mission_progress.txt: {e}")

                            if manifest_context:
                                print(
                                    f"📥 Injecting Mission Manifest context ({len(manifest_context)} chars)"
                                )
                                next_prompt += manifest_context

                                # [V2 Approval Gate] Check if mission is in PLANNING status
                                if os.path.exists(mission_file):
                                    try:
                                        with open(mission_file, "r") as f:
                                            raw_content = f.read()

                                        # [JSON Validation] Multi-step repair chain
                                        mission_data = None

                                        # Step 1: Try standard json
                                        try:
                                            mission_data = json.loads(raw_content)
                                        except json.JSONDecodeError as je:
                                            print(
                                                f"⚠️ Mission JSON has syntax error: {je}"
                                            )

                                            # Step 2: Try json5 (handles trailing commas, comments, unquoted strings)
                                            try:
                                                import json5

                                                mission_data = json5.loads(raw_content)
                                                print("✅ JSON5 parsed successfully!")
                                                # Save as valid JSON
                                                with open(mission_file, "w") as f:
                                                    json.dump(mission_data, f, indent=2)
                                                print(
                                                    "📝 Saved repaired mission.json (via json5)"
                                                )
                                            except Exception as j5e:
                                                print(f"⚠️ JSON5 also failed: {j5e}")

                                                # Step 3: Regex repair for common LLM errors
                                                print("🔧 Attempting regex repair...")
                                                import re

                                                repaired = raw_content

                                                # Fix: values starting with digit but containing non-numeric chars
                                                # e.g., "key": 8-12, -> "key": "8-12",
                                                # Using lookahead to avoid consuming/mangling delimiters
                                                repaired = re.sub(
                                                    r":[ \t]*(\d+[\-–]\d+)(?=[ \t]*[,}\]\n])",
                                                    r': "\1"',
                                                    repaired,
                                                )

                                                # Fix: unquoted values like  key: value" -> key: "value"
                                                repaired = re.sub(
                                                    r':\s*([^"\[\]{}\d][^,}\]]*)"',
                                                    r': "\1"',
                                                    repaired,
                                                )

                                                # Fix: completely unquoted values like key: value, -> key: "value",
                                                repaired = re.sub(
                                                    r":\s*(\d+\s+[^,}\]]+)([,}\]])",
                                                    r': "\1"\2',
                                                    repaired,
                                                )

                                                # Fix: trailing commas before } or ]
                                                repaired = re.sub(
                                                    r",(\s*[}\]])", r"\1", repaired
                                                )

                                                try:
                                                    mission_data = json.loads(repaired)
                                                    print("✅ Regex repair successful!")
                                                    with open(mission_file, "w") as f:
                                                        json.dump(
                                                            mission_data, f, indent=2
                                                        )
                                                    print(
                                                        "📝 Saved repaired mission.json (via regex)"
                                                    )
                                                except json.JSONDecodeError as je2:
                                                    print(
                                                        f"❌ All repair attempts failed: {je2}"
                                                    )
                                                    print(
                                                        "🚫 BLOCKING execution - agent must regenerate mission.json"
                                                    )
                                                    # Delete malformed file
                                                    os.remove(mission_file)
                                                    # BLOCK the restart - provide detailed error feedback
                                                    next_prompt = (
                                                        f"CRITICAL JSON ERROR: Your mission.json was INVALID and has been deleted.\n\n"
                                                        f"SPECIFIC ERROR: {je}\n\n"
                                                        f"COMMON MISTAKES TO AVOID:\n"
                                                        f'- WRONG: "duration": 8-12 hours  (unquoted value)\n'
                                                        f'- RIGHT: "duration": "8-12 hours"\n'
                                                        f'- WRONG: "count": 5 items per batch\n'
                                                        f'- RIGHT: "count": "5 items per batch"\n\n'
                                                        f"REQUIRED STRUCTURE (all string values must be quoted):\n"
                                                        f"{{\n"
                                                        f'  "mission_root": "string description",\n'
                                                        f'  "status": "PLANNING",\n'
                                                        f'  "clarifications": {{"key": "value"}},\n'
                                                        f'  "tasks": [\n'
                                                        f'    {{"id": "topic_001", "description": "string", "use_case": "string", "success_criteria": "string"}}\n'
                                                        f"  ]\n"
                                                        f"}}\n\n"
                                                        f"BE EXTREMELY CAREFUL with JSON syntax. Regenerate mission.json now with status: PLANNING"
                                                    )
                                                    mission_data = None

                                        if mission_data:
                                            mission_data, _ = (
                                                _apply_interview_answers_to_mission(
                                                    workspace_dir,
                                                    mission_data=mission_data,
                                                )
                                            )
                                        if (
                                            mission_data
                                            and mission_data.get("status") == "PLANNING"
                                        ):
                                            print(
                                                "\n📋 Planning Phase Complete - Awaiting User Approval"
                                            )
                                            approved = present_plan_summary(
                                                mission_data
                                            )
                                            if approved:
                                                mission_data["status"] = "IN_PROGRESS"
                                                with open(mission_file, "w") as f:
                                                    json.dump(mission_data, f, indent=2)
                                                print(
                                                    "✅ Plan approved. Transitioning to IN_PROGRESS."
                                                )
                                                # Ensure we don't drop to interactive prompt if we have a mission
                                                if not next_prompt:
                                                    # Default kickoff prompt if the hook didn't provide one
                                                    next_prompt = "Execute the mission.json tasks starting now."
                                            else:
                                                print(
                                                    "⏸️ Plan not approved. Waiting for user changes..."
                                                )
                                                continue  # Wait for another iteration
                                    except Exception as e:
                                        print(f"⚠️ Failed to check mission status: {e}")

                            if next_prompt:
                                print(f"\n🔄 HARNESS RESTART TRIGGERED")
                                print(
                                    f"Next Prompt: {next_prompt.splitlines()[0][:100]}..."
                                )
                                # CRITICAL: Preserve interview answers if already set
                                # Interview answers come from line 6064, don't overwrite them
                                if (
                                    pending_prompt
                                    and "USER INTERVIEW ANSWERS" in pending_prompt
                                ):
                                    # Prepend interview answers to the hook's prompt
                                    pending_prompt = (
                                        pending_prompt + "\n\n" + next_prompt
                                    )
                                    print(
                                        "📋 Interview answers preserved in restart prompt"
                                    )
                                else:
                                    pending_prompt = next_prompt

                                # Clear Client History
                                if safe_clear_history(client):
                                    print("🧹 Client history cleared.")

                                continue
                        elif action == "complete":
                            # Harness satisfied - Perform Verification
                            print("🔍 performing final verification...")
                            verifier = TaskVerifier(client=client)
                            
                            mission_path = os.path.join(workspace_dir, "mission.json")
                            verification_passed = True
                            failure_reason = ""
                            
                            if os.path.exists(mission_path):
                                try:
                                    with open(mission_path, "r") as f:
                                        m_data = json.load(f)
                                    
                                    # [Mission Completeness Check]
                                    # Ensure ALL tasks are COMPLETED before accepting termination
                                    incomplete_tasks = [
                                        t.get("id") for t in m_data.get("tasks", [])
                                        if t.get("status") not in ("COMPLETED", "SKIPPED")
                                    ]
                                    if incomplete_tasks:
                                        verification_passed = False
                                        # Construct a directive prompt for the FRESH agent
                                        next_task = incomplete_tasks[0]
                                        failure_reason = (
                                            f"Mission Incomplete: The following tasks are still pending: {', '.join(incomplete_tasks)}.\n"
                                            f"You cannot finish until these are COMPLETED."
                                        )
                                        
                                        # Smart Restart: Tell the new agent exactly what to do
                                        action = "restart"
                                        
                                        # [Context Injection] Why did the previous agent think it was done?
                                        rationale_context = ""
                                        progress_path = os.path.join(workspace_dir, "mission_progress.txt")
                                        if os.path.exists(progress_path):
                                            try:
                                                with open(progress_path, "r") as pf:
                                                    rationale_context = f"\n📄 PREVIOUS COMPLETION RATIONALE (from mission_progress.txt):\n```text\n{pf.read()[-2000:]}\n```\n(The above rationale was REJECTED because tasks are still pending.)"
                                            except Exception:
                                                pass

                                        next_prompt = (
                                            f"⛔ MISSION INCOMPLETE (HALLUCINATION DETECTED)\n"
                                            f"The previous agent attempted to finish, but tasks remain pending: {', '.join(incomplete_tasks)}.\n"
                                            f"{rationale_context}\n\n"
                                            f"⚡ ACTION REQUIRED (FRESH CONTEXT):\n"
                                            f"You are a new agent instance with a fresh context window.\n"
                                            f"1. Read `mission.json` to get the full details for Task '{next_task}'.\n"
                                            f"2. EXECUTE Task '{next_task}' IMMEDIATELY.\n"
                                            f"   - Do not ask for permission.\n"
                                            f"   - Do not re-plan.\n"
                                            f"   - Just do the work.\n\n"
                                            f"Restarting execution to finish Task '{next_task}'..."
                                        )
                                        # Trigger restart
                                        pending_prompt = next_prompt
                                        if safe_clear_history(client):
                                            pass # Cleared successfully
                                        continue

                                    # Verify all completed tasks
                                    if verification_passed:
                                        for task in m_data.get("tasks", []):
                                            if task.get("status") == "COMPLETED":
                                                # Binary Check
                                                v_ok, v_msg = verifier.verify_artifacts(task, workspace_dir)
                                                if not v_ok:
                                                    verification_passed = False
                                                    failure_reason = f"Artifact Verification Failed for task '{task.get('id')}': {v_msg}"
                                                    break
                                except Exception as ve:
                                    print(f"⚠️ Verification System Error: {ve}")
                                    # We don't block on system error, but we log it

                                if not verification_passed:
                                    print(f"❌ VERIFICATION FAILED: {failure_reason}")
                                    action = "restart"
                                    
                                    # [Context Injection] Why did the previous agent think it was done?
                                    rationale_context = ""
                                    progress_path = os.path.join(workspace_dir, "mission_progress.txt")
                                    if os.path.exists(progress_path):
                                        try:
                                            with open(progress_path, "r") as pf:
                                                rationale_context = f"\n📄 PREVIOUS COMPLETION RATIONALE (from mission_progress.txt):\n```text\n{pf.read()[-2000:]}\n```\n(The above rationale was REJECTED because tasks are still pending.)"
                                        except Exception:
                                            pass

                                    next_prompt = (
                                        f"⛔ SYSTEM VERIFICATION FAILED\n"
                                        f"The task cannot be marked complete because of the following error:\n"
                                        f"{failure_reason}\n\n"
                                        f"You must FIX this issue before the mission is accepted.\n"
                                        f"Restarting execution..."
                                    )
                                    # Continue to restart logic (handled below)
                                    pending_prompt = next_prompt
                                    if safe_clear_history(client):
                                        pass # Cleared successfully
                                    continue
                                
                                print("✅ Verification Passed. Finishing run.")

                    if run_mode == "job" and args.job_path:
                        if runtime_db_conn and run_id:
                            if _handle_cancel_request(
                                runtime_db_conn, run_id, workspace_dir
                            ):
                                auto_resume_complete = True
                                break
                            _maybe_mark_run_succeeded()
                            status = (
                                get_run_status(runtime_db_conn, run_id) or "succeeded"
                            )
                            print_job_completion_summary(
                                runtime_db_conn,
                                run_id,
                                status,
                                workspace_dir,
                                result.response_text or "",
                                trace,
                                update_restart_file,
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
            trace_path = write_trace(trace, workspace_dir)
            print(f"\n📊 Full trace saved to {trace_path}")

            # Save comprehensive session summary
            summary_lines = []
            summary_lines.append("=" * 60)
            summary_lines.append("SESSION SUMMARY")
            summary_lines.append("=" * 60 + "\n")
            summary_lines.append(f"Session Start: {trace.get('start_time', 'N/A')}")
            summary_lines.append(f"Session End: {trace.get('end_time', 'N/A')}")
            summary_lines.append(f"Execution Time: {trace['total_duration_seconds']}s")
            summary_lines.append(f"Total Iterations: {len(trace['iterations'])}")
            summary_lines.append(f"Total Tool Calls: {len(trace['tool_calls'])}")
            summary_lines.append(f"Total Tool Results: {len(trace['tool_results'])}")
            summary_lines.append(f"Status: {trace.get('status', 'complete')}\n")

            # Code execution check
            code_exec_tools = [
                "WORKBENCH",
                "CODE",
                "EXECUTE",
                "PYTHON",
                "SANDBOX",
                "BASH",
            ]
            code_exec_used = any(
                any(x in tc["name"].upper() for x in code_exec_tools)
                for tc in trace["tool_calls"]
            )
            summary_lines.append(f"Code Execution Used: {'Yes' if code_exec_used else 'No'}\n")

            # Tool call breakdown
            summary_lines.append("=" * 60)
            summary_lines.append("TOOL CALL BREAKDOWN")
            summary_lines.append("=" * 60)
            for tc in trace["tool_calls"]:
                marker = (
                    "🏭 "
                    if any(
                        x in tc["name"].upper()
                        for x in ["WORKBENCH", "CODE", "EXECUTE", "BASH"]
                    )
                    else "   "
                )
                summary_lines.append(
                    f"{marker}Iter {tc['iteration']} | +{tc['time_offset_seconds']:>6.1f}s | {tc['name']}"
                )

            # Logfire trace link
            if LOGFIRE_TOKEN and "trace_id" in trace:
                project_slug = os.getenv(
                    "LOGFIRE_PROJECT_SLUG", "Kjdragan/composio-claudemultiagent"
                )
                logfire_url = f"https://logfire.pydantic.dev/{project_slug}?q=trace_id%3D%27{trace['trace_id']}%27"
                summary_lines.append(f"\nLogfire Trace: {logfire_url}")

            summary_content = "\n".join(summary_lines)
            summary_path = os.path.join(workspace_dir, "session_summary.txt")
            
            with open(summary_path, "w") as f:
                f.write(summary_content)

            # Emit summary to UI Logs
            try:
                await agent.send_agent_event(EventType.STATUS, {
                    "status": summary_content,
                    "level": "INFO", 
                    "prefix": "SYSTEM",
                    "is_log": True
                })
            except Exception as e:
                print(f"⚠️ Failed to emit summary to UI: {e}")

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
            if (
                runtime_db_conn
                and run_id
                and not run_failed
                and not run_cancelled_by_operator
            ):
                _maybe_mark_run_succeeded()
                logfire.info("durable_run_completed", run_id=run_id)

    finally:
        # Ensure the client is closed since we manually called __aenter__
        if 'client' in locals() and client:
            try:
                await client.__aexit__(None, None, None)
            except Exception as e:
                print(f"⚠️ Error closing client: {e}")
        # Ensure Letta async clients exit before loop shutdown
        if LETTA_ENABLED:
            try:
                await _shutdown_letta()
            except Exception as e:
                print(f"⚠️ Error shutting down Letta clients: {e}")

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

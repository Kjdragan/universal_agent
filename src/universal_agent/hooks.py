import asyncio
import sys
import os
import uuid
import json
import logging
import inspect
import time
import ast
import re
from typing import Any, Optional, Callable
from dataclasses import dataclass
import shlex
from pathlib import Path
from claude_agent_sdk import HookMatcher
from universal_agent.agent_core import (
    AgentEvent,
    EventType,
    pre_compact_context_capture_hook,
)
from universal_agent.execution_context import get_current_workspace
from universal_agent.guardrails.tool_schema import pre_tool_use_schema_guardrail
from universal_agent.guardrails.workspace_guard import (
    validate_tool_paths,
    WorkspaceGuardError,
)
from universal_agent.durable.tool_gateway import (
    prepare_tool_call,
    parse_tool_identity,
    is_malformed_tool_name,
    parse_malformed_tool_name,
    is_invalid_tool_name,
)
from universal_agent.durable.classification import classify_tool
from universal_agent.identity import (
    resolve_email_recipients,
    validate_recipient_policy,
)
import logfire

logger = logging.getLogger(__name__)

# Constants
from contextvars import ContextVar
from universal_agent.agent_core import AgentEvent, EventType
from universal_agent.constants import DISALLOWED_TOOLS
_TOOL_EVENT_START_TS: Optional[float] = None

# -----------------------------------------------------------------------------

# Context-local event callback to support nested/concurrent runs
_TOOL_EVENT_CALLBACK_VAR: ContextVar[Optional[Callable[[AgentEvent], None]]] = ContextVar("_TOOL_EVENT_CALLBACK", default=None)

# Context-local sets to prevent duplicate event emission within a single turn
_EMITTED_TOOL_CALL_IDS_VAR: ContextVar[set] = ContextVar("_EMITTED_TOOL_CALL_IDS", default=set())
_EMITTED_TOOL_RESULT_IDS_VAR: ContextVar[set] = ContextVar("_EMITTED_TOOL_RESULT_IDS", default=set())
_EMITTED_ITERATION_END_VAR: ContextVar[bool] = ContextVar("_EMITTED_ITERATION_END", default=False)


def set_event_callback(callback: Optional[Callable[[AgentEvent], None]]) -> None:
    """Set the context-local callback for tool events."""
    _TOOL_EVENT_CALLBACK_VAR.set(callback)


def set_event_start_ts(start_ts: Optional[float]) -> None:
    """Set the start timestamp for time_offset calculation."""
    global _TOOL_EVENT_START_TS
    _TOOL_EVENT_START_TS = start_ts


def reset_tool_event_tracking() -> None:
    """
    Reset context-local deduplication sets for a new turn.
    """
    _EMITTED_TOOL_CALL_IDS_VAR.set(set())
    _EMITTED_TOOL_RESULT_IDS_VAR.set(set())
    _EMITTED_ITERATION_END_VAR.set(False)


def _clear_hook_events() -> None:
    """Clear callback and tracking for current context."""
    _TOOL_EVENT_CALLBACK_VAR.set(None)
    reset_tool_event_tracking()


def _emit_event(event: AgentEvent) -> None:
    callback = _TOOL_EVENT_CALLBACK_VAR.get()
    if callback is None:
        return
    try:
        callback(event)
    except Exception:
        pass


def _tool_time_offset() -> float:
    if _TOOL_EVENT_START_TS is None:
        return 0.0
    return round(time.time() - _TOOL_EVENT_START_TS, 3)


def _normalize_tool_use_id(tool_use_id: object, input_data: Optional[dict] = None) -> str:
    if tool_use_id:
        return str(tool_use_id)
    if input_data:
        for key in ("tool_use_id", "tool_call_id", "id"):
            value = input_data.get(key)
            if value:
                return str(value)
    return ""


def emit_tool_call_event(
    *,
    tool_use_id: object,
    tool_name: str,
    tool_input: Any,
    input_data: Optional[dict] = None,
    time_offset: Optional[float] = None,
) -> bool:
    """Emit a TOOL_CALL event if not already emitted for this tool_use_id."""
    tool_id = _normalize_tool_use_id(tool_use_id, input_data)
    emitted_calls = _EMITTED_TOOL_CALL_IDS_VAR.get()
    if not tool_id or tool_id in emitted_calls:
        return False
    
    # Update local set
    emitted_calls.add(tool_id)
    _EMITTED_TOOL_CALL_IDS_VAR.set(emitted_calls)
    input_payload = tool_input if isinstance(tool_input, dict) else {"value": tool_input}
    _emit_event(
        AgentEvent(
            type=EventType.TOOL_CALL,
            data={
                "id": tool_id,
                "name": tool_name or "",
                "input": input_payload,
                "time_offset": time_offset if time_offset is not None else _tool_time_offset(),
            },
        )
    )
    return True


def _extract_tool_result_text(result: Any) -> str:
    """Refined extraction logic ported from transcript_builder."""
    # 1. Basic Content Extraction
    content = result
    if isinstance(result, dict):
        content = result.get("content", result.get("result", result))
    
    # Handle TextBlock list or list of dicts
    final_str = ""
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                 # Try 'text' then 'json' then str
                 parts.append(str(block.get("text", block)))
            elif hasattr(block, "text"):
                parts.append(block.text)
            else:
                parts.append(str(block))
        final_str = "\n".join(parts)
    else:
        final_str = str(content)

    # 2. Smasher Logic (Unwrap nested JSON/Python repr)
    # Attempt to unwrap Python list of TextBlocks string representation
    if final_str.strip().startswith("[") and "'type':" in final_str:
         try:
             parsed = ast.literal_eval(final_str)
             if isinstance(parsed, list) and len(parsed) > 0 and isinstance(parsed[0], dict):
                 if parsed[0].get("type") == "text":
                     final_str = parsed[0].get("text", "")
         except Exception:
             pass
    
    # Regex fallback for truncated logs
    match = re.search(r"^\s*\[\s*\{\s*['\"]type['\"]\s*:\s*['\"]text['\"]\s*,\s*['\"]text['\"]\s*:\s*['\"](.*)", final_str, re.DOTALL)
    if match:
        inner = match.group(1)
        # Try to find clean end
        clean_end_match = re.search(r"(.*)['\"]\s*\}\s*\]\s*$", inner, re.DOTALL)
        if clean_end_match:
            final_str = clean_end_match.group(1)
        else:
            final_str = inner

    # 3. JSON Formatting (try to make it pretty if it's JSON)
    try:
        parsed_json = json.loads(final_str)
        return json.dumps(parsed_json, indent=2)
    except Exception:
        return final_str


_READ_BLOCK_IMAGE_EXTS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".gif",
    ".bmp",
    ".tiff",
    ".tif",
    ".ico",
    ".heic",
    ".heif",
}


def _tool_read_path_from_input(tool_input: Any) -> str | None:
    if not isinstance(tool_input, dict):
        return None
    for key in ("file_path", "path"):
        val = tool_input.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def _maybe_abs_path(path_value: str, workspace_dir: str | None) -> str:
    try:
        if os.path.isabs(path_value):
            return path_value
        root = workspace_dir or os.getcwd()
        return os.path.abspath(os.path.join(root, path_value))
    except Exception:
        return path_value


def _is_heartbeat_investigation_mode() -> bool:
    run_source = str(os.getenv("UA_RUN_SOURCE") or "").strip().lower()
    if run_source != "heartbeat":
        return False
    raw = str(os.getenv("UA_HEARTBEAT_INVESTIGATION_ONLY", "1") or "1").strip().lower()
    return raw not in {"0", "false", "no", "off", ""}


def _resolve_tool_target_path(path_value: Any, workspace_dir: str | None) -> Path | None:
    if not isinstance(path_value, str) or not path_value.strip():
        return None
    raw = path_value.strip()
    try:
        candidate = Path(raw)
        if candidate.is_absolute():
            return candidate.resolve()
        root = Path(workspace_dir or get_current_workspace() or os.getcwd()).resolve()
        return (root / candidate).resolve()
    except Exception:
        return None


def _is_allowed_heartbeat_write_path(path_value: Any, workspace_dir: str | None) -> bool:
    resolved = _resolve_tool_target_path(path_value, workspace_dir)
    if resolved is None:
        return False

    workspace_root = Path(workspace_dir or get_current_workspace() or os.getcwd()).resolve()
    try:
        from universal_agent.artifacts import repo_root, resolve_artifacts_dir

        artifacts_root = resolve_artifacts_dir()
        repo_memory_root = (repo_root() / "memory").resolve()
    except Exception:
        artifacts_root = None
        repo_memory_root = None

    allowed_roots = [
        (workspace_root / "work_products").resolve(),
    ]
    if artifacts_root is not None:
        allowed_roots.append(artifacts_root.resolve())
    if repo_memory_root is not None:
        allowed_roots.append(repo_memory_root)

    for root in allowed_roots:
        try:
            resolved.relative_to(root)
            return True
        except Exception:
            continue

    # Keep HEARTBEAT.md updates allowed inside workspace root.
    if resolved.name.lower() == "heartbeat.md":
        try:
            resolved.relative_to(workspace_root)
            return True
        except Exception:
            return False

    return False


def _should_block_large_image_read(file_path: str, workspace_dir: str | None) -> tuple[bool, dict]:
    try:
        # Default 0 means "block all image reads" (prevents base64 injection entirely).
        max_bytes = int(os.getenv("UA_READ_IMAGE_MAX_BYTES", "0"))
    except Exception:
        max_bytes = 0
    max_bytes = max(0, min(max_bytes, 5_000_000))
    enabled = str(os.getenv("UA_BLOCK_LARGE_IMAGE_READS", "1") or "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }
    if not enabled:
        return False, {}

    abs_path = _maybe_abs_path(file_path, workspace_dir)
    ext = Path(abs_path).suffix.lower()
    if ext not in _READ_BLOCK_IMAGE_EXTS:
        return False, {}

    try:
        if os.path.exists(abs_path):
            size = os.path.getsize(abs_path)
            if size > max_bytes:
                return True, {"abs_path": abs_path, "size_bytes": size, "max_bytes": max_bytes, "ext": ext}
    except Exception:
        return False, {}
    return False, {}


def _rewrite_literal_artifacts_dir_paths(command: str, artifacts_root: str) -> tuple[str, bool]:
    """Rewrite common literal UA_ARTIFACTS_DIR path mistakes in Bash commands."""
    if not command or not artifacts_root:
        return command, False

    root = artifacts_root.rstrip("/")
    updated = command.replace("/opt/universal_agent/UA_ARTIFACTS_DIR", root)
    updated = re.sub(r"(?<![$\{])\bUA_ARTIFACTS_DIR\b", root, updated)
    return updated, updated != command


def _extract_bash_command(input_data: dict) -> tuple[str, str, dict]:
    """Return (command, source, tool_input_dict) from a Bash hook payload."""
    tool_input = input_data.get("tool_input", {}) if isinstance(input_data, dict) else {}
    if not isinstance(tool_input, dict):
        tool_input = {}

    nested = tool_input.get("command")
    if isinstance(nested, str):
        return nested, "tool_input", tool_input

    top_level = input_data.get("command") if isinstance(input_data, dict) else None
    if isinstance(top_level, str):
        return top_level, "top_level", tool_input

    return "", "none", tool_input


def _build_bash_command_update(command_source: str, tool_input: dict, updated_command: str) -> dict:
    if command_source == "tool_input":
        updated = dict(tool_input)
        updated["command"] = updated_command
        return {"tool_input": updated}
    return {"command": updated_command}


def emit_tool_result_event(
    *,
    tool_use_id: object,
    is_error: bool,
    tool_result: Any,
    input_data: Optional[dict] = None,
    time_offset: Optional[float] = None,
) -> bool:
    """Emit a TOOL_RESULT event if not already emitted for this tool_use_id."""
    tool_id = _normalize_tool_use_id(tool_use_id, input_data)
    emitted_results = _EMITTED_TOOL_RESULT_IDS_VAR.get()
    if not tool_id or tool_id in emitted_results:
        return False
    
    # Update local set
    emitted_results.add(tool_id)
    _EMITTED_TOOL_RESULT_IDS_VAR.set(emitted_results)
    content_text = _extract_tool_result_text(tool_result)
    _emit_event(
        AgentEvent(
            type=EventType.TOOL_RESULT,
            data={
                "tool_use_id": tool_id,
                "is_error": bool(is_error),
                "content_preview": content_text[:2500],
                "content_size": len(content_text),
                "time_offset": time_offset if time_offset is not None else _tool_time_offset(),
            },
        )
    )
    return True

# Helper methods (adapted from main.py)
def _is_task_output_name(name: str) -> bool:
    return name in DISALLOWED_TOOLS or "TaskOutput" in name

def _normalize_gateway_file_path(path_value: Any) -> Any:
    # Basic implementation, can be expanded if needed
    if not isinstance(path_value, str) or not path_value:
        return path_value
    # In hooks.py we might not have full observer workspace access yet,
    # so we'll keep this simple or allow dependency injection.
    return path_value


class AgentHookSet:
    """
    Shared hook logic for Universal Agent (CLI & Web).
    Encapsulates state needed for guardrails, skills, and ledger.
    """
    def __init__(
        self,
        run_id: Optional[str] = None,
        tool_ledger: Optional[Any] = None,
        runtime_db_conn: Optional[Any] = None,
        enable_skills: bool = True,
        active_workspace: Optional[str] = None,
    ):
        self.run_id = run_id or str(uuid.uuid4())
        self.tool_ledger = tool_ledger
        self.runtime_db_conn = runtime_db_conn
        self.enable_skills = enable_skills
        self.workspace_dir = active_workspace
        
        # Internal state
        self.forced_tool_queue = []
        self.forced_tool_active_ids = {}
        self.forced_tool_mode_active = False
        self.gateway_mode_active = False # Default off for now unless injected
        self.current_step_id = None
        
        # Skill-specific state (from prompt_assets)
        self._seen_transcript_paths = set()
        self._seen_transcript_paths = set()
        self._primary_transcript_path = None
        
        # Skill Candidate Detection state
        self._current_turn_tool_count = 0
        self._current_turn_history = []
        self._skill_candidate_log_path = None

    def build_hooks(self) -> dict:
        """Return the hook dictionary compatible with UniversalAgent."""
        return {
            "AgentStop": [
                HookMatcher(matcher=None, hooks=[self.on_agent_stop]),
            ],
            "SubagentStop": [
                HookMatcher(matcher=None, hooks=[self.on_subagent_stop]),
            ],
            "PreToolUse": [
                # Schema guardrails first (blocks malformed or missing inputs)
                HookMatcher(matcher="*", hooks=[self.on_pre_tool_use_schema_guardrail]),
                # Workspace path enforcement (rewrites relative paths, blocks escapes)
                HookMatcher(matcher="*", hooks=[self.on_pre_tool_use_workspace_guard]),
                # Ledger/Guardrails
                HookMatcher(matcher="*", hooks=[self.on_pre_tool_use_ledger]),
                # Bash Skills
                HookMatcher(
                    matcher="Bash",
                    hooks=[
                        self.on_pre_bash_inject_workspace_env,
                        self.on_pre_bash_warn_dependency_installs,
                        self.on_pre_bash_block_composio_sdk,
                        self.on_pre_bash_block_playwright_non_html,
                        self.on_pre_bash_skill_hint,
                    ],
                ),
                # Task Skills
                HookMatcher(matcher="Task", hooks=[self.on_pre_task_skill_awareness]),
                # Skill Candidate Detection (monitor tool chains)
                HookMatcher(matcher="*", hooks=[self.on_pre_tool_use_skill_detection]),
                # Emit tool_call after other pre-hooks allow it
                HookMatcher(matcher="*", hooks=[self.on_pre_tool_use_emit_event]),
            ],
            "PreCompact": [
                HookMatcher(matcher="*", hooks=[self.on_pre_compact_capture]),
            ],
            "PostToolUse": [
                HookMatcher(matcher=None, hooks=[self.on_post_tool_use_emit_event]),
                HookMatcher(matcher=None, hooks=[self.on_post_tool_use_ledger]),
                HookMatcher(matcher=None, hooks=[self.on_post_tool_use_validation]),
                HookMatcher(
                    matcher=None, hooks=[self.on_post_research_finalized_cache]
                ),
                HookMatcher(matcher=None, hooks=[self.on_post_email_send_artifact]),
                HookMatcher(matcher="Task", hooks=[self.on_post_task_guidance]),
            ],
            "UserPromptSubmit": [
                HookMatcher(matcher=None, hooks=[self.on_user_prompt_skill_awareness]),
            ],
        }

    # =========================================================================
    # CORE HOOKS (Ported from main.py)
    # =========================================================================

    async def on_agent_stop(self, input_data: dict, *args) -> dict:
        logfire.info("agent_stop_hook", run_id=self.run_id)
        return {}

    async def on_subagent_stop(self, input_data: dict, *args) -> dict:
        return {}

    async def on_pre_tool_use_schema_guardrail(
        self, input_data: dict, tool_use_id: object, context: dict
    ) -> dict:
        """PreToolUse guardrail to validate schema and workspace prerequisites."""
        return await pre_tool_use_schema_guardrail(
            input_data,
            run_id=self.run_id,
            step_id=self.current_step_id,
            logger=logger,
        )

    async def on_pre_compact_capture(self, input_data: dict, context: dict) -> dict:
        """PreCompact hook to capture compaction events in CLI/harness."""
        return await pre_compact_context_capture_hook(input_data, context)

    async def on_pre_tool_use_workspace_guard(self, input_data: dict, tool_use_id: object, context: dict) -> dict:
        """PreToolUse hook to enforce workspace-scoped file paths for WRITE operations only."""
        current_workspace = get_current_workspace()
        if not current_workspace:
            return {}  # No workspace bound yet
        
        tool_name = input_data.get("tool_name", "")
        tool_input = input_data.get("tool_input", {})
        if not isinstance(tool_input, dict):
            return {}
        
        # Read-only tools are allowed to access paths outside workspace
        READ_ONLY_TOOLS = {
            "mcp__internal__list_directory",
            "Read",
            "View",
            "ListDir",
            "read_file",
            "list_dir",
        }
        
        # Check if tool name contains read-only patterns
        tool_lower = tool_name.lower()
        is_read_only = (
            tool_name in READ_ONLY_TOOLS
            or "list" in tool_lower
            or "read" in tool_lower
            or "view" in tool_lower
            or "cat" in tool_lower
            or "head" in tool_lower
            or "tail" in tool_lower
        )
        
        if is_read_only:
            return {}  # Allow reads from anywhere

        # Whitelist the /memory directory for heartbeat-related writes
        # This allows the heartbeat system to update HEARTBEAT.md and create briefing markers
        from pathlib import Path
        file_path = tool_input.get("file_path") or tool_input.get("path") or ""
        if isinstance(file_path, str) and file_path:
            path_obj = Path(file_path).resolve()
            # Get the repo root (parent of AGENT_RUN_WORKSPACES)
            repo_root = Path(current_workspace).parent.parent
            memory_dir = (repo_root / "memory").resolve()
            try:
                path_obj.relative_to(memory_dir)
                return {}  # Allow writes to /memory directory
            except ValueError:
                pass  # Not in memory dir, continue with normal checks

        # Special-case: `mcp__internal__write_text_file` is explicitly designed to write
        # to either the session workspace *or* the durable artifacts root. It performs its
        # own allowlist enforcement in the tool implementation, so we must not rewrite its
        # paths to be workspace-scoped. However, we still block obvious escapes for safety.
        tool_lower = tool_name.lower()
        if tool_lower.endswith("write_text_file") or tool_lower == "write":
            # Native Write uses `file_path`; internal write_text_file uses `path`.
            raw_path = tool_input.get("path") or tool_input.get("file_path")
            if isinstance(raw_path, str) and raw_path:
                from pathlib import Path
                from universal_agent.artifacts import resolve_artifacts_dir

                ws_root = Path(self.workspace_dir or current_workspace).resolve()
                # Always allow the default artifacts root, even if UA_ARTIFACTS_DIR isn't present
                # in this process environment (it may exist only in the agent subprocess env).
                artifacts_root = resolve_artifacts_dir()

                path_obj = Path(raw_path)
                if path_obj.is_absolute():
                    resolved = path_obj.resolve()
                    allowed = False
                    try:
                        resolved.relative_to(ws_root)
                        allowed = True
                    except Exception:
                        pass
                    if not allowed:
                        try:
                            resolved.relative_to(artifacts_root)
                            allowed = True
                        except Exception:
                            pass
                    if not allowed:
                        msg = (
                            f"Tool input 'path' contains path '{raw_path}' which is outside the "
                            "session workspace and UA_ARTIFACTS_DIR."
                        )
                        logfire.warning(
                            "workspace_guard_blocked",
                            error=msg,
                            tool_use_id=str(tool_use_id),
                            tool_name=tool_name,
                        )
                        return {
                            "systemMessage": f"⚠️ {msg}",
                            "decision": "block",
                            "hookSpecificOutput": {
                                "hookEventName": "PreToolUse",
                                "permissionDecision": "deny",
                                "permissionDecisionReason": msg,
                            },
                        }

                    # Allow absolute paths under workspace or artifacts as-is (no rewrite).
                    return {}

            # For relative paths (or missing), fall back to workspace scoping to avoid
            # accidental repo-root writes due to tool cwd.

        try:
            from pathlib import Path
            workspace_path = Path(self.workspace_dir)
            validated_input = validate_tool_paths(tool_input, workspace_path)
            # If paths were modified, update the tool input
            if validated_input != tool_input:
                return {"tool_input": validated_input}
        except WorkspaceGuardError as e:
            logfire.warning("workspace_guard_blocked", error=str(e), tool_use_id=str(tool_use_id), tool_name=tool_name)
            return {
                "systemMessage": f"⚠️ {e}",
                "decision": "block",
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": str(e),
                },
            }
        except Exception:
            pass  # Don't block on guard errors
        
        return {}

    async def on_pre_tool_use_ledger(self, input_data: dict, tool_use_id: object, context: dict) -> dict:
        """Main guardrail and ledger hook."""
        tool_name = str(input_data.get("tool_name", "") or "")
        tool_input = input_data.get("tool_input", {}) or {}
        if not isinstance(tool_input, dict):
            tool_input = {}

        if _is_heartbeat_investigation_mode():
            upper_name = tool_name.upper()
            if upper_name in {"BASH"} or upper_name.endswith("__COMPOSIO_MULTI_EXECUTE_TOOL"):
                return {
                    "systemMessage": (
                        "⚠️ BLOCKED: Heartbeat is running in investigation-only mode. "
                        "Mutating shell commands and generic Composio execute calls are disabled."
                    ),
                    "decision": "block",
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": "Heartbeat investigation-only mode blocks mutating execution tools.",
                    },
                }

            is_write_like = (
                upper_name in {"WRITE", "EDIT", "MULTIEDIT"}
                or upper_name.endswith("__WRITE_TEXT_FILE")
                or upper_name.endswith("__APPEND_TO_FILE")
            )
            if is_write_like:
                target_path = tool_input.get("file_path") or tool_input.get("path")
                if not _is_allowed_heartbeat_write_path(target_path, self.workspace_dir):
                    return {
                        "systemMessage": (
                            "⚠️ BLOCKED: Heartbeat investigation-only mode allows writes only to draft-safe "
                            "locations (work_products/, UA_ARTIFACTS_DIR, or memory/)."
                        ),
                        "decision": "block",
                        "hookSpecificOutput": {
                            "hookEventName": "PreToolUse",
                            "permissionDecision": "deny",
                            "permissionDecisionReason": "Write/edit path is outside heartbeat draft-safe locations.",
                        },
                    }

        if tool_name == "Read":
            file_path = _tool_read_path_from_input(tool_input)
            if file_path:
                should_block, meta = _should_block_large_image_read(file_path, self.workspace_dir)
                if should_block:
                    abs_path = meta.get("abs_path") or file_path
                    size_bytes = meta.get("size_bytes")
                    max_bytes = meta.get("max_bytes")
                    reason = f"Refusing to Read image file (size {size_bytes:,} bytes > {max_bytes:,} bytes)."
                    return {
                        "systemMessage": (
                            "⚠️ Blocked: `Read` on a large image file would inject base64 into the model context and "
                            "can overflow the context window.\n\n"
                            f"{reason}\n"
                            f"Path: {abs_path}\n\n"
                            "Use one of these instead (no base64 in context):\n"
                            "1) Vision (preferred): use the external vision MCP server `zai_vision` to analyze the image\n"
                            f"2) Vision (fallback): `describe_image` with `{{\"image_path\": \"{abs_path}\"}}`\n"
                            f"3) Human viewer: `preview_image` with `{{\"image_path\": \"{abs_path}\"}}`\n"
                            "4) Shell metadata: `Bash` `ls -lh` / `file`\n"
                            "5) For edits: `generate_image` with `input_image_path` (do not Read the bytes)\n"
                        ),
                        "decision": "block",
                        "hookSpecificOutput": {
                            "hookEventName": "PreToolUse",
                            "permissionDecision": "deny",
                            "permissionDecisionReason": "Blocked Read of large image file (base64 context blowup).",
                        },
                    }
        
        # 1. Disallowed Tools Guardrail
        if tool_name in DISALLOWED_TOOLS:
             # Context detection for sub-agents (Allow Research Specialist to search)
             parent_tool_use_id = input_data.get("parent_tool_use_id")
             transcript_path_chk = input_data.get("transcript_path", "")
             # Safety check - _primary_transcript_path is instance state in this class
             primary_path = self._primary_transcript_path
            
             is_subagent_context = bool(parent_tool_use_id) or (
                primary_path is not None 
                and transcript_path_chk 
                and transcript_path_chk != primary_path
             )
            
             if is_subagent_context:
                 pass
             else:
                 emit_status_event(f"Hook: blocked '{tool_name}' for Primary Agent (must delegate)", level="WARNING", prefix="Hook")
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
            
        # 2. Ledger integration (only if ledger is active)
        if self.tool_ledger and self.run_id:
             # Full ledger logic from main.py would go here. 
             # For this refactor step, we focus on enabling skills for Web even if ledger is missing.
             pass

        return {}

    async def on_pre_tool_use_emit_event(self, input_data: dict, tool_use_id: object, context: dict) -> dict:
        """Emit TOOL_CALL for UI/gateway streaming once tool use is allowed."""
        tool_name = input_data.get("tool_name", "")
        tool_input = input_data.get("tool_input", {}) or {}

        # Extract sub-agent "thought" fields from tool inputs and emit as
        # THINKING events to the chat panel. These are the agent's reasoning
        # about what it's about to do (common in COMPOSIO_MULTI_EXECUTE_TOOL).
        if isinstance(tool_input, dict):
            thought = tool_input.get("thought")
            if thought and isinstance(thought, str) and thought.strip():
                # Resolve author from sub-agent context
                parent_tool_use_id = input_data.get("parent_tool_use_id")
                if parent_tool_use_id:
                    # Sub-agent thought — try to resolve from transcript path or default
                    transcript_path = input_data.get("transcript_path", "")
                    if "research" in transcript_path.lower() if transcript_path else False:
                        thought_author = "Research Specialist"
                    elif "report" in transcript_path.lower() if transcript_path else False:
                        thought_author = "Report Writer"
                    else:
                        thought_author = "Subagent"
                else:
                    thought_author = "Primary Agent"
                emit_thinking_event(thought, author=thought_author)

        emit_tool_call_event(
            tool_use_id=tool_use_id,
            tool_name=tool_name,
            tool_input=tool_input,
            input_data=input_data,
        )
        return {}

    # =========================================================================
    # SKILL HINTS (The "Smart" part user requested)
    # =========================================================================

    async def on_pre_bash_block_composio_sdk(self, input_data: dict, tool_use_id: object, context: dict) -> dict:
        """
        PreToolUse Hook: Block Bash commands that attempt to call Composio SDK directly.
        This prevents agents from bypassing the MCP architecture by brute-forcing
        Python/SDK calls through Bash.
        """
        command, _, _ = _extract_bash_command(input_data)
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
            emit_status_event("Hook: blocked direct Composio SDK usage in Bash", level="WARNING", prefix="Hook")
            logfire.warning(
                "bash_composio_sdk_blocked",
                command_preview=command[:200],
                tool_use_id=str(tool_use_id),
            )
            return {
                "systemMessage": (
                    "⚠️ BLOCK: Do not use the `composio` Python SDK or CLI directly from Bash. "
                    "Your environment is NOT configured for direct SDK usage.\n"
                    "✅ REQUIRED PATH (attachments):\n"
                    "1) Upload local file with `mcp__internal__upload_to_composio({path, tool_slug:'GMAIL_SEND_EMAIL', toolkit_slug:'gmail'})`\n"
                    "2) Send with `mcp__composio__COMPOSIO_MULTI_EXECUTE_TOOL` using `GMAIL_SEND_EMAIL` and the returned `attachment.s3key`\n"
                    "Use MCP tools only; they are pre-authenticated and reliable."
                ),
                "decision": "block",
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "Direct Composio SDK usage blocked.",
                },
            }
        return {}

    async def on_pre_bash_block_playwright_non_html(
        self, input_data: dict, tool_use_id: object, context: dict
    ) -> dict:
        """
        PreToolUse Hook: Block Playwright-based PDF conversion for non-HTML inputs.
        HTML -> PDF should use Chrome headless (Playwright) only when HTML is explicit.
        """
        command, _, _ = _extract_bash_command(input_data)
        command_lower = command.lower()

        if "playwright" not in command_lower:
            return {}

        if "pdf" not in command_lower:
            return {}

        html_markers = ["file://", ".html", "html_path"]
        if any(marker in command_lower for marker in html_markers):
            return {}

        return {
            "systemMessage": (
                "🚫 BLOCKED: Playwright should be used for HTML → PDF only.\n\n"
                "For Markdown/other → PDF, use WeasyPrint (Python-native) or the "
                "`mcp__internal__html_to_pdf` tool after converting to HTML.\n"
            ),
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "Playwright PDF conversion without explicit HTML input.",
            },
        }

    async def on_pre_bash_skill_hint(self, input_data: dict, tool_use_id: object, context: dict) -> dict:
        """
        PreToolUse Hook: Suggest specific skills based on Bash usage patterns.
        """
        command, _, _ = _extract_bash_command(input_data)
        
        # Skill Hint 1: Git Commit
        if "git commit" in command and "mcp-builder" not in command:
             # Just a lightweight hint to remind them of the skill
             pass

        # Skill Hint 2: Document Creation (PDF, DOCX, PPTX)
        DOCUMENT_SKILL_TRIGGERS = {
            "pdf": ["pdf", "reportlab", "pypdf", "pdfplumber"],
            "docx": ["docx", "python-docx", "word document"],
            "pptx": ["pptx", "python-pptx", "powerpoint", "presentation"],
        }
        
        for skill_name, triggers in DOCUMENT_SKILL_TRIGGERS.items():
            if any(trigger in command for trigger in triggers):
                emit_status_event(f"Hook: skill hint injected for {skill_name.upper()}", prefix="Hook")
                logfire.info(
                    "skill_hint_injected",
                    skill=skill_name,
                    command_preview=command[:100],
                )
                return {
                    "systemMessage": (
                        f" (Skill Hint): You appear to be working with {skill_name.upper()}. "
                        f"Check if there is a `{skill_name}` skill available to help you."
                    )
                }

        return {}

    async def on_pre_task_skill_awareness(self, input_data: dict, *args) -> dict:
        return {}

    async def on_user_prompt_skill_awareness(self, input_data: dict, *args) -> dict:
        """
        UserPromptSubmit Hook: Reset tool counters for new turn and setup usage logging.
        """
        # Reset counters for the new user turn
        self._current_turn_tool_count = 0
        self._current_turn_history = []
        
        # Determine log path if not set (lazy init)
        if not self._skill_candidate_log_path:
            # Use centralized log directory: <repo_root>/logs/skill_candidates/
            try:
                # Resolve repo root relative to this file: src/universal_agent/hooks.py -> repo root
                current_file = Path(__file__).resolve()
                # src/universal_agent/hooks.py -> src/universal_agent -> src -> repo_root
                repo_root = current_file.parent.parent.parent
                
                log_dir = repo_root / "logs" / "skill_candidates"
                log_dir.mkdir(parents=True, exist_ok=True)
                
                # Unique filename per session: candidate_<timestamp>_<session_id>.log
                timestamp = time.strftime('%Y%m%d_%H%M%S')
                filename = f"candidate_{timestamp}_{self.run_id}.log"
                self._skill_candidate_log_path = str(log_dir / filename)
                
            except Exception:
                # Fallback to workspace root if path resolution fails
                ws = get_current_workspace() or self.workspace_dir
                if ws:
                    self._skill_candidate_log_path = str(Path(ws) / "skill_candidates.log")
        
        return {}

    async def on_pre_tool_use_skill_detection(self, input_data: dict, tool_use_id: object, context: dict) -> dict:
        """
        PreToolUse Hook: Monitor tool chain length to identify potential skills.
        """
        self._current_turn_tool_count += 1
        
        # Track history
        tool_name = input_data.get("tool_name", "unknown")
        tool_input = input_data.get("tool_input", {})
        self._current_turn_history.append({
            "tool": tool_name,
            "input": tool_input,
            "timestamp": time.strftime('%H:%M:%S')
        })
        
        # Check threshold
        try:
            threshold = int(os.getenv("UA_SKILL_CANDIDATE_THRESHOLD", "5"))
        except (ValueError, TypeError):
            threshold = 5
            
        if self._current_turn_tool_count == threshold:
            # Threshold hit - log it with full history
            msg = (
                f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] "
                f"Session: {self.run_id} | "
                f"Threshold Reached: {threshold} tools | "
                f"Potential Skill Candidate Detected\n"
                f"--- Tool Sequence ---\n"
            )
            
            for idx, entry in enumerate(self._current_turn_history):
                # Format input compactly
                inp_str = str(entry['input'])
                if len(inp_str) > 200:
                   inp_str = inp_str[:200] + "... (truncated)"
                   
                msg += f"{idx+1}. {entry['tool']} (at {entry['timestamp']}): {inp_str}\n"
            
            msg += "---------------------\n"
            
            # Log to file if path is available
            if self._skill_candidate_log_path:
                try:
                    with open(self._skill_candidate_log_path, "a") as f:
                        f.write(msg)
                except Exception:
                    pass
            
            # Log to internal telemetry
            logfire.info(
                "skill_candidate_detected",
                run_id=self.run_id,
                tool_count=self._current_turn_tool_count,
                trigger_tool=tool_name,
                sequence_length=len(self._current_turn_history)
            )
            
        return {}

    async def on_pre_bash_inject_workspace_env(
        self, input_data: dict, tool_use_id: object, context: dict
    ) -> dict:
        """
        Ensure Bash has stable access to our key per-run environment variables.

        In some gateway/SDK configurations, the Bash execution environment can miss
        variables that were injected into the agent subprocess environment.

        This hook makes Bash commands resilient by prefixing:
        - CURRENT_SESSION_WORKSPACE
        - UA_ARTIFACTS_DIR (defaulting to <repo>/artifacts if env not set)
        """
        command, command_source, tool_input = _extract_bash_command(input_data)
        if not command.strip():
            return {}

        try:
            ws = get_current_workspace() or self.workspace_dir or ""
            if not ws:
                return {}
            from universal_agent.artifacts import resolve_artifacts_dir

            artifacts_root = str(resolve_artifacts_dir())
            rewritten_command, rewrote_artifacts_paths = _rewrite_literal_artifacts_dir_paths(
                command,
                artifacts_root,
            )
            if rewrote_artifacts_paths:
                logfire.info(
                    "bash_artifacts_path_rewritten_hookset",
                    tool_use_id=str(tool_use_id),
                    command_source=command_source,
                    before_preview=command[:200],
                    after_preview=rewritten_command[:200],
                )

            cmd_stripped = rewritten_command.lstrip()
            has_cd_prefix = cmd_stripped.startswith(("cd ", "pushd ", "popd "))

            auto_cd_env = str(os.getenv("UA_BASH_AUTO_CD_WORKSPACE", "1") or "1").strip().lower()
            auto_cd_enabled = auto_cd_env not in {"0", "false", "no", "off"}

            prefix_parts: list[str] = []
            if "CURRENT_SESSION_WORKSPACE=" not in command:
                prefix_parts.append(f"export CURRENT_SESSION_WORKSPACE={shlex.quote(ws)}")
            if "UA_ARTIFACTS_DIR=" not in command:
                prefix_parts.append(f"export UA_ARTIFACTS_DIR={shlex.quote(artifacts_root)}")
            if auto_cd_enabled and not has_cd_prefix:
                prefix_parts.append(f"cd {shlex.quote(ws)}")

            if not prefix_parts:
                if not rewrote_artifacts_paths:
                    return {}
                return _build_bash_command_update(command_source, tool_input, rewritten_command)

            updated_command = "; ".join(prefix_parts) + "; " + rewritten_command
            return _build_bash_command_update(command_source, tool_input, updated_command)
        except Exception:
            return {}

    async def on_pre_bash_warn_dependency_installs(
        self, input_data: dict, tool_use_id: object, context: dict
    ) -> dict:
        """
        Gentle guardrail: discourage mutating the repo's Python environment during runs.
        Prefer PEP 723 + `uv run` for runnable artifacts.
        """
        command, _, _ = _extract_bash_command(input_data)
        if not command.strip():
            return {}

        cmd_lower = command.lower()
        if "pip install" in cmd_lower or "uv pip install" in cmd_lower or "uv add" in cmd_lower:
            return {
                "systemMessage": (
                    "⚠️ Dependency install detected in Bash. Prefer PEP 723 inline dependencies "
                    "+ `uv run <script>.py` so artifacts are runnable without mutating the repo environment."
                )
            }
        return {}
        
    async def on_post_tool_use_emit_event(self, input_data: dict, tool_use_id: object, context: dict) -> dict:
        """Emit TOOL_RESULT for UI/gateway streaming."""
        tool_result = input_data.get("tool_result")
        if tool_result is None:
            tool_result = input_data.get("tool_response")
        is_error = bool(input_data.get("is_error"))
        if isinstance(tool_result, dict):
            is_error = bool(is_error or tool_result.get("is_error") or tool_result.get("error"))
        emit_tool_result_event(
            tool_use_id=tool_use_id,
            is_error=is_error,
            tool_result=tool_result,
            input_data=input_data,
        )
        return {}

    async def on_post_tool_use_ledger(self, *args) -> dict:
        if self.tool_ledger:
            pass # Ledger completion logic
        return {}

    async def on_post_tool_use_validation(self, input_data: dict, tool_use_id: object, context: dict) -> dict:
        """
        PostToolUse Hook: Detect validation errors and inject corrective hints.
        This provides 'Corrective Schema Advice' to help the agent recover autonomously.
        """
        tool_result = input_data.get("tool_result", {})
        is_error = False
        error_text = ""
        
        # Extract error from standard SDK result format
        if isinstance(tool_result, dict):
             is_error = tool_result.get("is_error", False)
             content = tool_result.get("content", [])
             if is_error and isinstance(content, list):
                 for block in content:
                     if isinstance(block, dict) and block.get("type") == "text":
                         error_text += block.get("text", "")
        
        if is_error and error_text:
            emit_status_event(f"Hook: tool validation error detected", level="WARNING", prefix="Hook")
            # 1. Detect common schema/validation errors
            validation_hints = {
                "required parameter": "⚠️ Schema Validation Failed: You missed a mandatory argument.",
                "missing required parameter": "⚠️ Schema Validation Failed: You missed a mandatory argument.",
                "InputValidationError": "⚠️ Input Validation Failed: The arguments provided do not match the expected schema.",
                "invalid type": "⚠️ Type Mismatch: Check the schema for correct argument types (e.g., string vs list).",
            }
            
            hint = "⚠️ Tool call failed validation."
            for pattern, advice in validation_hints.items():
                if pattern.lower() in error_text.lower():
                    hint = advice
                    break
            
            # Suggest remedial action
            hint += f"\n\n**Corrective Advice:**\n1. Re-read the tool definition using `?tool_name` (if available) or check the error message carefully: `{error_text[:200]}`.\n2. Ensure ALL required parameters are present.\n3. Do not assume values; if unsure, search for the correct data first."
            
            logfire.warning(
                "tool_validation_error_detected",
                tool_use_id=str(tool_use_id),
                error_preview=error_text[:100],
            )
            
            return {
                "systemMessage": hint
            }

        return {}

    async def on_post_research_finalized_cache(self, *args) -> dict:
        return {}

    async def on_post_email_send_artifact(self, *args) -> dict:
        return {}

    async def on_post_task_guidance(self, *args) -> dict:
        return {}



def emit_text_event(text: str, author: Optional[str] = None) -> None:
    """Emit a TEXT event."""
    _emit_event(
        AgentEvent(
            type=EventType.TEXT,
            data={
                "text": text,
                "author": author or "Primary Agent",
                "time_offset": _tool_time_offset(),
            },
        )
    )

def emit_thinking_event(thinking: str, signature: Optional[str] = None, author: Optional[str] = None) -> None:
    """Emit a THINKING event."""
    # We do not dedup thinking events as they are sequential parts of the stream
    _emit_event(
        AgentEvent(
            type=EventType.THINKING,
            data={
                "thinking": thinking,
                "signature": signature,
                "author": author or "Primary Agent",
                "time_offset": _tool_time_offset(),
            },
        )
    )

def emit_status_event(message: str, level: str = "INFO", prefix: Optional[str] = None, is_log: bool = True) -> None:
    """
    Emit a status event.
    By default is_log=True so it shows in the UI Activity Log panel.
    """
    _emit_event(
        AgentEvent(
            type=EventType.STATUS,
            data={
                "status": message,
                "level": level,
                "prefix": prefix,
                "is_log": is_log,
                "time_offset": _tool_time_offset(),
            },
        )
    )


class StdoutToEventStream:
    """Context manager to capture stdout/stderr and emit STATUS events."""
    def __init__(self, prefix="[LOG]", level="INFO"):
        self.prefix = prefix
        self.level = level
        self._stdout = sys.stdout
        self._stderr = sys.stderr
        self._emitting = False

    def __enter__(self):
        sys.stdout = self._make_stream(self._stdout)
        sys.stderr = self._make_stream(self._stderr)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        sys.stdout = self._stdout
        sys.stderr = self._stderr

    def _make_stream(self, original):
        # Create a proxy that writes to original AND emits event
        parent = self
        class StreamProxy:
            def write(self, text):
                original.write(text)
                # Reentrancy guard: if we are already emitting, don't emit again
                # This prevents infinite recursion if emit_status_event prints to stdout
                if text.strip() and not parent._emitting:
                     try:
                         parent._emitting = True
                         emit_status_event(text.strip(), parent.level, parent.prefix)
                     finally:
                         parent._emitting = False

            def flush(self):
                original.flush()
            def isatty(self):
                return getattr(original, "isatty", lambda: False)()
        return StreamProxy()

import asyncio
import os
import uuid
import json
import logging
import inspect
from typing import Any, Optional
from dataclasses import dataclass
from claude_agent_sdk import HookMatcher
from universal_agent.agent_core import AgentEvent, EventType
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

# Constants (moved from main.py)
from universal_agent.agent_setup import DISALLOWED_TOOLS

OBSERVER_WORKSPACE_DIR = None 

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
        self._primary_transcript_path = None

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
                # Ledger/Guardrails first
                HookMatcher(matcher="*", hooks=[self.on_pre_tool_use_ledger]),
                # Bash Skills
                HookMatcher(
                    matcher="Bash",
                    hooks=[self.on_pre_bash_block_composio_sdk, self.on_pre_bash_skill_hint],
                ),
                # Task Skills
                HookMatcher(matcher="Task", hooks=[self.on_pre_task_skill_awareness]),
            ],
            "PostToolUse": [
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

    async def on_pre_tool_use_ledger(self, input_data: dict, tool_use_id: object, context: dict) -> dict:
        """Main guardrail and ledger hook."""
        tool_name = input_data.get("tool_name", "")
        
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

    # =========================================================================
    # SKILL HINTS (The "Smart" part user requested)
    # =========================================================================

    async def on_pre_bash_block_composio_sdk(self, input_data: dict, tool_use_id: object, context: dict) -> dict:
        """
        PreToolUse Hook: Block Bash commands that attempt to call Composio SDK directly.
        This prevents agents from bypassing the MCP architecture by brute-forcing
        Python/SDK calls through Bash.
        """
        tool_input = input_data.get("tool_input", {})
        command = str(tool_input.get("command", "") or "") if isinstance(tool_input, dict) else ""
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
                    "⚠️ BLOCK: Do not use the `composio` Python SDK or CLI directly from Bash. "
                    "Your environment is NOT configured for direct SDK usage.\n"
                    "INSTEAD: Use the available MCP tools (e.g., `mcp__composio__...`) which are "
                    "pre-authenticated and reliable."
                ),
                "decision": "block",
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "Direct Composio SDK usage blocked.",
                },
            }
        return {}

    async def on_pre_bash_skill_hint(self, input_data: dict, tool_use_id: object, context: dict) -> dict:
        """
        PreToolUse Hook: Suggest specific skills based on Bash usage patterns.
        """
        tool_input = input_data.get("tool_input", {})
        command = str(tool_input.get("command", "") or "") if isinstance(tool_input, dict) else ""
        
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
                # We assume skills are in the standard location relative to repo root
                # Since we don't assume cwd, we skip file check or keep it simple
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

    async def on_user_prompt_skill_awareness(self, *args) -> dict:
        return {}

"""
URW Harness Helpers

Helper functions for managing agent context and session transitions during harness execution.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from universal_agent.execution_context import bind_workspace_env

def toggle_session(
    harness_dir: Path,
    phase_num: int,
) -> str:
    """
    Create a new session directory for the next phase and update globals.
    
    Args:
        harness_dir: Path to the harness directory
        phase_num: Phase number (1-indexed)
        
    Returns:
        Path string to the new session directory
    """
    session_name = f"session_phase_{phase_num}"
    session_path = harness_dir / session_name
    session_path.mkdir(parents=True, exist_ok=True)
    
    # Create standard subdirectories
    (session_path / "work_products" / "media").mkdir(parents=True, exist_ok=True)
    (session_path / "downloads").mkdir(parents=True, exist_ok=True)
    (session_path / "search_results").mkdir(parents=True, exist_ok=True)
    
    new_workspace = str(session_path)
    
    # Update environment variable so MCP servers pick it up
    bind_workspace_env(new_workspace)
    
    return new_workspace


def compact_agent_context(client: Any, force_new_client: bool = False) -> dict:
    """
    Manage agent context compaction for phase transitions.
    
    Based on Claude Agent SDK / Claude Code research:
    - `/compact` command is REPL-only, no SDK method to trigger it
    - Auto-compaction triggers at ~80% context window usage
    - PreCompact hook exists for custom logic before compaction
    - Creating new ClaudeSDKClient = completely fresh context (no memory)
    
    Strategy for harness:
    1. KEEP same client to preserve agent's summarized memory via auto-compaction
    2. Toggle to new SESSION DIRECTORY (separate from client lifecycle)
    3. Only create new client if explicitly requested (force_new_client=True)
    
    Args:
        client: The ClaudeSDKClient instance
        force_new_client: If True, signals caller should create entirely new client
        
    Returns:
        dict with:
        - keep_client: True if current client should be kept
        - notes: explanation of action taken
    """
    # Updated Strategy for harness (User Request 2026-01-26):
    # 1. DEFAULT to hard reset (keep_client=False) for phase transitions.
    #    Phases are intended to be "natural breaks" with clean context windows.
    # 2. Injection logic in build_harness_context_injection() provides the necessary
    #    continuity context (prior session paths).
    
    if force_new_client:
        return {
            "keep_client": False,
            "notes": "Hard reset (Explicit) - new client will be created"
        }
    
    # Default: Clear client history for cleaner context between phases
    return {
        "keep_client": False,
        "notes": "Hard reset (Default) - clearing history for clean phase start"
    }


def build_harness_context_injection(
    phase_num: int,
    total_phases: int,
    phase_title: str,
    phase_instructions: str,
    prior_session_paths: list[str],
    expected_artifacts: list[str],
    tasks: Optional[list[Any]] = None,
    current_session_path: Optional[str] = None,
) -> str:
    """
    Build the context injection for a new phase.
    
    Follows the minimal + perspective approach:
    - Give perspective that this is part of a larger project
    - Reference prior sessions (paths only, not content)
    - Focus on the current phase task
    - Provide explicit workspace path for file operations
    
    Args:
        phase_num: Current phase number (1-indexed)
        total_phases: Total number of phases
        phase_title: Title of this phase
        phase_instructions: Detailed instructions for this phase
        prior_session_paths: List of paths to prior session directories
        expected_artifacts: List of expected output artifacts
        tasks: Optional list of AtomicTask objects
        current_session_path: Absolute path to the current phase session directory
        
    Returns:
        Formatted prompt string ready to be fed to the multi-agent system
    """
    prompt_parts = []
    
    # 1. Handoffs from prior sessions (The "Semantic Handoff" Logic)
    handoff_section = ""
    if prior_session_paths:
        handoffs = []
        for p in prior_session_paths:
            path = Path(p)
            handoff_file = path / "phase_handoff.md"
            if handoff_file.exists():
                try:
                    summary = handoff_file.read_text().strip()
                    handoffs.append(f"### From {path.name}:\n{summary}")
                except Exception:
                    pass
        
        if handoffs:
            handoff_section = "## 📜 Prior Phase Summaries\n" + "\n\n".join(handoffs) + "\n"

    prior_section = ""
    if prior_session_paths:
        paths_list = "\n".join(f"- {p}" for p in prior_session_paths)
        prior_section = f"""**Prior work:** Sessions at paths:
{paths_list}
(Consult ONLY if needed for continuity)

{handoff_section}
"""
    
    # Format atomic tasks
    tasks_section = ""
    if tasks:
        tasks_section = "## Atomic Tasks to Execute\n"
        for t in tasks:
            tasks_section += f"- **{t.name}**: {t.description}\n"
            if t.use_case:
                tasks_section += f"  - *Use Case/Review:* {t.use_case}\n"
            if t.success_criteria:
                tasks_section += f"  - *Success Criteria:* {'; '.join(t.success_criteria)}\n"
        tasks_section += "\n"
    
    artifacts_section = "\n".join(f"- {a}" for a in expected_artifacts) if expected_artifacts else "- Complete the phase successfully"
    
    tz_name = os.getenv("USER_TIMEZONE", "America/Chicago")
    try:
        import pytz

        now = datetime.now(pytz.timezone(tz_name))
        tz_label = now.tzname() or tz_name
    except Exception:
        now = datetime.now()
        tz_label = tz_name

    config_section = ""
    if current_session_path:
        config_section = f"""
## Configuration
**CURRENT WORKSPACE**: `{current_session_path}`
**CURRENT DATE/TIME**: {now.strftime('%A, %B %d, %Y %H:%M')} ({tz_label})
IMPORTANT: You MUST use absolute paths starting with this workspace for all file operations (reading, writing, researching).
Use the current date/time above as authoritative; do not treat post-training dates as hallucinations if sourced.
"""

    return f"""# Phase {phase_num} of {total_phases}: {phase_title}

You are working through a larger multi-phase project. Your current phase is this one.
Complete this phase by executing the atomic tasks below.
{config_section}
{prior_section}
{tasks_section}

## Phase Instructions
{phase_instructions}

## Expected Outputs
{artifacts_section}
"""


async def generate_phase_summary(
    client: Any,
    session_path: str,
    phase_name: str,
    artifacts: list[str],
) -> None:
    """
    Generate a semantic summary of the phase for the next agent.
    Writes 'phase_handoff.md' to the session directory.
    
    Args:
        client: ClaudeSDKClient instance
        session_path: Path to the current session (where handoff file lives)
        phase_name: Name of the phase just completed
        artifacts: List of relative paths to artifacts produced
    """
    import asyncio
    
    # Simple prompt for the agent to summarize its own work
    # We rely on the client's current context which *should* contain the recent history
    # of the phase execution.
    
    prompt = f"""
SYSTEM: You have just completed Phase: "{phase_name}".
OBJECTIVE: Write a brief "Handoff Summary" for the next agent who will pick up your work.

REQUIREMENTS:
1. Summarize what was accomplished (1-2 sentences).
2. List key decisions or findings that the next agent MUST know.
3. Mention the most important artifacts you created (referenced from `{session_path}`).
4. Keep it under 200 words.
5. Format as Markdown.

Write ONLY the markdown content for 'phase_handoff.md'. Do not include conversational filler.
"""
    
    try:
        # We need to send this as a user message to the agent/client
        # Depending on client type (SDK vs Agent), method differs.
        # Assuming ClaudeSDKClient or similar that behaves like one.
        
        # We use a direct "create_message" if available to avoid side-effects of tool use?
        # Or just use the standard interface if possible.
        # For SDK client: client.create_message(messages=[...])
        
        # NOTE: harness_orchestrator passes 'client' which is typically ClaudeSDKClient.
        # It has .create_message()
        
        from claude_agent_sdk.types import UserMessage
        
        response = await client.create_message(
            messages=[UserMessage(content=prompt)],
            system="You are a helpful assistant writing a handover report.",
            stop_sequences=None,
            max_tokens=1000,
            tool_choice={"type": "none"} # Force text only
        )
        
        # Extract text
        content = ""
        for block in response.content:
            if hasattr(block, "text"):
                content += block.text
        
        if content:
            # Write to file
            out_path = Path(session_path) / "phase_handoff.md"
            out_path.write_text(content)
            print(f"✅ Generated Phase Handoff: {out_path}")
            
    except Exception as e:
        print(f"⚠️ Failed to generate phase summary: {e}")



def create_harness_workspace(
    workspaces_root: Path,
    harness_id: Optional[str] = None,
) -> Path:
    """
    Create a harness workspace directory structure.
    
    Args:
        workspaces_root: Root directory for all workspaces (AGENT_RUN_WORKSPACES)
        harness_id: Optional ID, defaults to timestamp
        
    Returns:
        Path to the created harness directory
    """
    if not harness_id:
        harness_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    harness_dir = workspaces_root / f"harness_{harness_id}"
    harness_dir.mkdir(parents=True, exist_ok=True)
    
    return harness_dir

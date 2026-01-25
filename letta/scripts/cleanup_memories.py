#!/usr/bin/env python3
"""
Cleanup and seed Letta memory blocks for Universal Agent agents.

Features:
- Ensure required memory blocks exist (including failure/recovery patterns)
- Populate empty system_rules and project_context with useful baseline context
- Optionally delete test agents (universal_agent_test*)

Usage:
  python letta/scripts/cleanup_memories.py --dry-run
  python letta/scripts/cleanup_memories.py --apply
  python letta/scripts/cleanup_memories.py --apply --only universal_agent
  python letta/scripts/cleanup_memories.py --delete-test-agents --apply
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

# Load .env
try:
    from dotenv import load_dotenv

    env_path = Path(__file__).resolve().parents[2] / ".env"
    if env_path.exists():
        load_dotenv(env_path)
except ImportError:
    pass

try:
    from agentic_learning import AgenticLearning
except ImportError:
    print("❌ agentic_learning SDK not installed. Install with: pip install agentic-learning")
    sys.exit(1)

PREFIX = "universal_agent"
REQUIRED_BLOCKS = [
    "human",
    "system_rules",
    "project_context",
    "failure_patterns",
    "recovery_patterns",
    "recent_queries",
    "recent_reports",
]

BLOCK_DESCRIPTIONS = {
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

DEFAULT_SYSTEM_RULES = """Operational rules and constraints:
- Use session workspace paths for all outputs (CURRENT_SESSION_WORKSPACE).
- Prefer tool-based operations over ad-hoc Bash for file handling.
- Keep reports and artifacts inside AGENT_RUN_WORKSPACES/{session_id}.
- Avoid parallel tool calls unless explicitly requested; keep tool usage minimal and purposeful.
- Do not disable Letta or memory features unless explicitly asked.
"""

DEFAULT_PROJECT_CONTEXT = """Project context:
- Universal Agent system with CLI, FastAPI, and URW harness orchestration.
- Core sources: src/universal_agent, src/mcp_server.py, Memory_System/.
- Run outputs are stored in AGENT_RUN_WORKSPACES/{session_id}.
- Reports, workflows, and Logfire tracing are central to evaluation runs.
"""

DEFAULT_FAILURE_PATTERNS = """Failure patterns (start log):
- [YYYY-MM-DD] <failure name>: <symptom> | <suspected cause> | <impact> | <follow-up>
"""

DEFAULT_RECOVERY_PATTERNS = """Recovery patterns (start log):
- [YYYY-MM-DD] <recovery name>: <what worked> | <conditions> | <why it helped> | <candidate skill?>
"""

SEED_VALUES = {
    "system_rules": DEFAULT_SYSTEM_RULES,
    "project_context": DEFAULT_PROJECT_CONTEXT,
    "failure_patterns": DEFAULT_FAILURE_PATTERNS,
    "recovery_patterns": DEFAULT_RECOVERY_PATTERNS,
}


class MemoryAction:
    def __init__(self, agent: str, action: str, label: str, detail: str):
        self.agent = agent
        self.action = action
        self.label = label
        self.detail = detail


def is_placeholder(value: str) -> bool:
    if not value or not value.strip():
        return True

    normalized = value.strip().lower()
    return (
        "section of core memory devoted" in normalized
        and "i don't yet know anything about" in normalized
    )


def update_block(
    client: AgenticLearning,
    agent_name: str,
    label: str,
    block: object,
    value: str,
    description: str,
    dry_run: bool,
) -> bool:
    if dry_run:
        return True

    try:
        client._letta.blocks.update(
            block_id=block.id,
            value=value,
            description=description,
        )
        return True
    except Exception as exc:
        print(f"⚠️ update failed for {agent_name}:{label} ({exc}) - recreating block")

    try:
        client.memory.delete(agent=agent_name, label=label)
        client.memory.create(
            agent=agent_name,
            label=label,
            value=value,
            description=description,
        )
        return True
    except Exception as exc:
        print(f"❌ recreate failed for {agent_name}:{label} ({exc})")
        return False


def list_agents(client: AgenticLearning) -> list[str]:
    agents = client.agents.list()
    names = [getattr(agent, "name", "") for agent in agents]
    return [name for name in names if name.startswith(PREFIX)]


def get_blocks(client: AgenticLearning, agent_name: str) -> dict:
    blocks = client.memory.list(agent=agent_name)
    result = {}
    for block in blocks:
        label = getattr(block, "label", None)
        if label:
            result[label] = block
    return result


def ensure_blocks(client: AgenticLearning, agent_name: str, dry_run: bool) -> list[MemoryAction]:
    actions = []
    existing = get_blocks(client, agent_name)
    for label in REQUIRED_BLOCKS:
        if label in existing:
            continue
        detail = "missing"
        if label in SEED_VALUES:
            detail = "missing (seed)"
        actions.append(MemoryAction(agent_name, "create_block", label, detail))
        if dry_run:
            continue
        seed_value = SEED_VALUES.get(label, "")
        client.memory.create(
            agent=agent_name,
            label=label,
            value=seed_value,
            description=BLOCK_DESCRIPTIONS.get(label, ""),
        )
    return actions


def seed_block(client: AgenticLearning, agent_name: str, label: str, value: str, dry_run: bool) -> list[MemoryAction]:
    actions = []
    existing = get_blocks(client, agent_name)
    block = existing.get(label)
    if not block:
        return actions
    current_value = getattr(block, "value", "") or ""
    if current_value.strip() and not is_placeholder(current_value):
        return actions

    detail = "empty"
    if is_placeholder(current_value):
        detail = "placeholder"
    actions.append(MemoryAction(agent_name, "seed_value", label, detail))
    if dry_run:
        return actions

    update_block(
        client,
        agent_name,
        label,
        block,
        value=value,
        description=BLOCK_DESCRIPTIONS.get(label, ""),
        dry_run=dry_run,
    )
    return actions


def update_description(client: AgenticLearning, agent_name: str, label: str, dry_run: bool) -> list[MemoryAction]:
    actions = []
    existing = get_blocks(client, agent_name)
    block = existing.get(label)
    if not block:
        return actions

    description = getattr(block, "description", "") or ""
    desired = BLOCK_DESCRIPTIONS.get(label, "")
    if description.strip() or not desired:
        return actions

    actions.append(MemoryAction(agent_name, "set_description", label, "missing"))
    if dry_run:
        return actions

    current_value = getattr(block, "value", "") or ""
    update_block(
        client,
        agent_name,
        label,
        block,
        value=current_value,
        description=desired,
        dry_run=dry_run,
    )
    return actions


def delete_test_agents(client: AgenticLearning, dry_run: bool) -> list[MemoryAction]:
    actions = []
    for agent_name in list_agents(client):
        if agent_name.startswith("universal_agent_test"):
            actions.append(MemoryAction(agent_name, "delete_agent", "-", "test agent"))
            if not dry_run:
                client.agents.delete(agent=agent_name)
    return actions


def print_actions(actions: list[MemoryAction]) -> None:
    if not actions:
        print("✅ No changes needed")
        return

    print("\nPlanned actions:")
    for action in actions:
        print(f"- [{action.agent}] {action.action}: {action.label} ({action.detail})")


def main() -> None:
    parser = argparse.ArgumentParser(description="Cleanup Letta memory blocks")
    parser.add_argument("--apply", action="store_true", help="Apply changes (default is dry-run)")
    parser.add_argument("--only", type=str, help="Only process a single agent name")
    parser.add_argument("--delete-test-agents", action="store_true", help="Delete universal_agent_test* agents")
    args = parser.parse_args()

    dry_run = not args.apply

    print("🧠 Letta Memory Cleanup")
    print("=" * 50)
    print(f"Mode: {'DRY-RUN' if dry_run else 'APPLY'}")

    client = AgenticLearning()

    all_actions: list[MemoryAction] = []
    if args.delete_test_agents:
        all_actions.extend(delete_test_agents(client, dry_run))

    agents = list_agents(client)
    if args.only:
        agents = [name for name in agents if name == args.only]

    if not agents:
        print("No agents found.")
        return

    for agent_name in agents:
        all_actions.extend(ensure_blocks(client, agent_name, dry_run))
        all_actions.extend(update_description(client, agent_name, "system_rules", dry_run))
        all_actions.extend(update_description(client, agent_name, "project_context", dry_run))
        all_actions.extend(update_description(client, agent_name, "failure_patterns", dry_run))
        all_actions.extend(update_description(client, agent_name, "recovery_patterns", dry_run))
        all_actions.extend(seed_block(client, agent_name, "system_rules", DEFAULT_SYSTEM_RULES, dry_run))
        all_actions.extend(seed_block(client, agent_name, "project_context", DEFAULT_PROJECT_CONTEXT, dry_run))
        all_actions.extend(seed_block(client, agent_name, "failure_patterns", DEFAULT_FAILURE_PATTERNS, dry_run))
        all_actions.extend(seed_block(client, agent_name, "recovery_patterns", DEFAULT_RECOVERY_PATTERNS, dry_run))

    print_actions(all_actions)
    if dry_run:
        print("\nℹ️ Re-run with --apply to write updates")


if __name__ == "__main__":
    main()

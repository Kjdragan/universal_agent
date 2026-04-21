"""Tests for _agent_has_pending_todo_items — the multi-phase pipeline guard.

This guard prevents premature auto-completion of sessions where the agent
has outstanding work declared via the Claude Code TodoWrite tool.
"""

import sys
from pathlib import Path

# Ensure the source tree is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from universal_agent.gateway_server import _agent_has_pending_todo_items


# ── positive cases: should detect pending work ──────────────────────────


def test_detects_pending_items():
    """Standard case: agent has a mix of completed and pending items."""
    tool_calls = {
        "tc_1": {
            "name": "Read",
            "input": {"file_path": "/some/file"},
        },
        "tc_2": {
            "name": "TodoWrite",
            "input": {
                "todos": [
                    {"content": "Phase 1: scaffold SKILL.md", "status": "completed"},
                    {"content": "Phase 2: execute health check", "status": "in_progress"},
                    {"content": "Phase 3: quality gate", "status": "pending"},
                    {"content": "Phase 4: polish skill", "status": "pending"},
                ]
            },
        },
    }
    assert _agent_has_pending_todo_items(tool_calls) is True


def test_detects_in_progress():
    """Even a single in_progress item should block auto-completion."""
    tool_calls = {
        "tc_1": {
            "name": "TodoWrite",
            "input": {
                "todos": [
                    {"content": "Research topic", "status": "completed"},
                    {"content": "Write report", "status": "in_progress"},
                ]
            },
        },
    }
    assert _agent_has_pending_todo_items(tool_calls) is True


def test_detects_pending_only():
    """Pure pending list (nothing started yet)."""
    tool_calls = {
        "tc_1": {
            "name": "TodoWrite",
            "input": {
                "todos": [
                    {"content": "Do thing A", "status": "pending"},
                    {"content": "Do thing B", "status": "pending"},
                ]
            },
        },
    }
    assert _agent_has_pending_todo_items(tool_calls) is True


def test_uses_last_todowrite_call():
    """If multiple TodoWrite calls exist, use the LAST one."""
    tool_calls = {
        "tc_1": {
            "name": "TodoWrite",
            "input": {
                "todos": [
                    {"content": "Step 1", "status": "pending"},
                    {"content": "Step 2", "status": "pending"},
                ]
            },
        },
        "tc_2": {"name": "Bash", "input": {"command": "echo test"}},
        "tc_3": {
            "name": "TodoWrite",
            "input": {
                "todos": [
                    {"content": "Step 1", "status": "completed"},
                    {"content": "Step 2", "status": "completed"},
                ]
            },
        },
    }
    # Last TodoWrite has all completed → no pending work
    assert _agent_has_pending_todo_items(tool_calls) is False


def test_handles_hyphenated_in_progress():
    """Some SDK versions may use 'in-progress' instead of 'in_progress'."""
    tool_calls = {
        "tc_1": {
            "name": "TodoWrite",
            "input": {
                "todos": [
                    {"content": "Step 1", "status": "completed"},
                    {"content": "Step 2", "status": "in-progress"},
                ]
            },
        },
    }
    assert _agent_has_pending_todo_items(tool_calls) is True


# ── negative cases: should NOT flag pending work ────────────────────────


def test_all_completed():
    """All items completed → agent is done."""
    tool_calls = {
        "tc_1": {
            "name": "TodoWrite",
            "input": {
                "todos": [
                    {"content": "Research", "status": "completed"},
                    {"content": "Report", "status": "completed"},
                ]
            },
        },
    }
    assert _agent_has_pending_todo_items(tool_calls) is False


def test_no_todowrite_calls():
    """No TodoWrite calls at all → no evidence of pending work."""
    tool_calls = {
        "tc_1": {"name": "Bash", "input": {"command": "ls"}},
        "tc_2": {"name": "Read", "input": {"file_path": "/a/b"}},
    }
    assert _agent_has_pending_todo_items(tool_calls) is False


def test_empty_tool_calls():
    """Empty dict → no evidence."""
    assert _agent_has_pending_todo_items({}) is False


def test_todowrite_with_empty_todos():
    """TodoWrite with empty todo list."""
    tool_calls = {
        "tc_1": {
            "name": "TodoWrite",
            "input": {"todos": []},
        },
    }
    assert _agent_has_pending_todo_items(tool_calls) is False


def test_todowrite_with_string_input():
    """TodoWrite input as JSON string (should be parsed)."""
    import json

    tool_calls = {
        "tc_1": {
            "name": "TodoWrite",
            "input": json.dumps(
                {
                    "todos": [
                        {"content": "Task A", "status": "pending"},
                    ]
                }
            ),
        },
    }
    assert _agent_has_pending_todo_items(tool_calls) is True


def test_todowrite_with_malformed_input():
    """Gracefully handle malformed input."""
    tool_calls = {
        "tc_1": {
            "name": "TodoWrite",
            "input": "not valid json {{{{",
        },
    }
    assert _agent_has_pending_todo_items(tool_calls) is False


def test_todowrite_with_none_input():
    """TodoWrite with None input."""
    tool_calls = {
        "tc_1": {
            "name": "TodoWrite",
            "input": None,
        },
    }
    assert _agent_has_pending_todo_items(tool_calls) is False

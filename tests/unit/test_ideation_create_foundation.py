"""Phase 2a — reflection ideation can actually CREATE structured, held proposals.

Before this: the ideation prompt told the agent to create with
`task_hub_task_action` (lifecycle-only, cannot create) and the real
`task_hub_create` tool was never registered — so 0 `source_kind='reflection'`
rows were ever produced. These tests pin the fix:
- `task_hub_create` is registered in the core tool surface.
- the prompt names `task_hub_create` for creation (and warns off task_hub_task_action).
- reflection-sourced creates land in a HOLDING state (agent_ready=False) so they
  await operator review in the morning report instead of auto-dispatching.
"""

import asyncio
import sqlite3

from universal_agent import task_hub
from universal_agent.tools import task_hub_bridge
from universal_agent.tools.internal_registry import get_core_internal_tools
from universal_agent.services.reflection_engine import _format_reflection_prompt


def test_create_tool_is_registered():
    # Identity check — the wrappers are decorated, so __name__ isn't reliable.
    assert task_hub_bridge.task_hub_create_wrapper in get_core_internal_tools()


def test_prompt_names_create_tool_and_warns_off_action():
    prompt = _format_reflection_prompt(
        recent_completions=[],
        stalled_brainstorms=[],
        open_task_count=0,
        memory_context=[],
        budget_remaining=5,
    )
    assert "task_hub_create" in prompt
    # task_hub_task_action is mentioned only as the "do NOT create with it" warning.
    assert "only transitions" in prompt
    assert "Rationale:" in prompt and "Suggested executor:" in prompt


def _create(db: str, args: dict) -> None:
    seed = sqlite3.connect(db)
    task_hub.ensure_schema(seed)
    seed.close()
    asyncio.run(task_hub_bridge._task_hub_create_impl(args))


def test_reflection_item_is_held(tmp_path, monkeypatch):
    db = str(tmp_path / "reflect.db")
    monkeypatch.setattr(task_hub_bridge, "get_activity_db_path", lambda: db)
    _create(db, {"title": "Idea X", "description": "...", "source_kind": "reflection"})
    conn = sqlite3.connect(db)
    row = conn.execute(
        "SELECT agent_ready, labels_json FROM task_hub_items"
    ).fetchone()
    conn.close()
    assert row is not None
    assert int(row[0]) == 0, "reflection proposals must be held (agent_ready=False)"
    assert "ideation" in (row[1] or "")


def test_non_reflection_item_is_dispatchable(tmp_path, monkeypatch):
    db = str(tmp_path / "manual.db")
    monkeypatch.setattr(task_hub_bridge, "get_activity_db_path", lambda: db)
    _create(db, {"title": "Y", "source_kind": "manual_test"})
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT agent_ready FROM task_hub_items").fetchone()
    conn.close()
    assert int(row[0]) == 1, "non-reflection creates keep prior behaviour (agent_ready=True)"

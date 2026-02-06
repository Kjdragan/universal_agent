from pathlib import Path

from universal_agent.utils.task_guardrails import normalize_task_name, resolve_best_task_match


def test_normalize_task_name_snake_case():
    assert normalize_task_name("Russia-Ukraine War Jan 2026") == "russia_ukraine_war_jan_2026"


def test_resolve_best_task_match_uses_canonical_name_when_no_tasks(tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    assert resolve_best_task_match("My-Task Name", workspace_root=workspace) == "my_task_name"


def test_resolve_best_task_match_prefers_existing_task_dir(tmp_path: Path):
    workspace = tmp_path / "ws"
    tasks = workspace / "tasks"
    tasks.mkdir(parents=True, exist_ok=True)
    (tasks / "russia_ukraine_war_jan_2026").mkdir()
    resolved = resolve_best_task_match("russia-ukraine-war-jan-2026", workspace_root=workspace)
    assert resolved == "russia_ukraine_war_jan_2026"


from __future__ import annotations

from pathlib import Path

from universal_agent.workspace.bootstrap import seed_workspace_bootstrap


def test_seed_workspace_bootstrap_creates_required_files(tmp_path: Path) -> None:
    workspace = tmp_path / "session_ws"
    result = seed_workspace_bootstrap(str(workspace))

    assert workspace.exists()
    assert (workspace / "memory").exists()

    required = {
        "AGENTS.md",
        "SOUL.md",
        "TOOLS.md",
        "IDENTITY.md",
        "USER.md",
        "HEARTBEAT.md",
        "BOOTSTRAP.md",
        "MEMORY.md",
    }
    assert required.issubset(set(result["created"]))
    for name in required:
        assert (workspace / name).exists()


def test_seed_workspace_bootstrap_is_non_destructive(tmp_path: Path) -> None:
    workspace = tmp_path / "session_ws"
    first = seed_workspace_bootstrap(str(workspace))
    assert "USER.md" in set(first["created"])

    user_file = workspace / "USER.md"
    user_file.write_text("# USER.md\n\ncustom user profile\n", encoding="utf-8")

    second = seed_workspace_bootstrap(str(workspace))
    assert "USER.md" in set(second["skipped"])
    assert "custom user profile" in user_file.read_text(encoding="utf-8")

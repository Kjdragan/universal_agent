from __future__ import annotations

from pathlib import Path

import mcp_server


def test_write_text_file_rewrites_literal_absolute_ua_artifacts_dir_path(
    monkeypatch, tmp_path: Path
) -> None:
    artifacts_root = tmp_path / "artifacts"
    workspace_root = tmp_path / "workspace"
    artifacts_root.mkdir(parents=True, exist_ok=True)
    workspace_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("UA_ARTIFACTS_DIR", str(artifacts_root))
    monkeypatch.setenv("CURRENT_RUN_WORKSPACE", str(workspace_root))
    monkeypatch.delenv("CURRENT_SESSION_WORKSPACE", raising=False)

    raw_path = "/opt/universal_agent/UA_ARTIFACTS_DIR/youtube-tutorial-creation/test-run/manifest.json"
    result = mcp_server.write_text_file(raw_path, '{"ok": true}')

    expected = artifacts_root / "youtube-tutorial-creation/test-run/manifest.json"
    assert expected.exists()
    assert expected.read_text(encoding="utf-8") == '{"ok": true}'
    assert str(expected) in result


def test_write_text_file_rewrites_relative_ua_artifacts_dir_path(
    monkeypatch, tmp_path: Path
) -> None:
    artifacts_root = tmp_path / "artifacts"
    workspace_root = tmp_path / "workspace"
    artifacts_root.mkdir(parents=True, exist_ok=True)
    workspace_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("UA_ARTIFACTS_DIR", str(artifacts_root))
    monkeypatch.setenv("CURRENT_RUN_WORKSPACE", str(workspace_root))
    monkeypatch.delenv("CURRENT_SESSION_WORKSPACE", raising=False)

    raw_path = "UA_ARTIFACTS_DIR/youtube-tutorial-creation/test-run/README.md"
    result = mcp_server.write_text_file(raw_path, "hello")

    expected = artifacts_root / "youtube-tutorial-creation/test-run/README.md"
    assert expected.exists()
    assert expected.read_text(encoding="utf-8") == "hello"
    assert str(expected) in result


def test_write_text_file_resolves_workspace_relative_path(
    monkeypatch, tmp_path: Path
) -> None:
    """Regression guard for the atom-poem startup-hiccup: an agent that
    types ``work_products/ai_poem.html`` must land inside its own
    CURRENT_RUN_WORKSPACE, not the API server's cwd. Pre-fix, the
    write was rejected because ``os.path.abspath`` anchored at
    ``/opt/universal_agent`` (the host process's cwd) and the safety
    check rightly refused that escape.
    """
    artifacts_root = tmp_path / "artifacts"
    workspace_root = tmp_path / "workspace"
    artifacts_root.mkdir(parents=True, exist_ok=True)
    workspace_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("UA_ARTIFACTS_DIR", str(artifacts_root))
    monkeypatch.setenv("CURRENT_RUN_WORKSPACE", str(workspace_root))
    monkeypatch.delenv("CURRENT_SESSION_WORKSPACE", raising=False)
    # Move cwd to a totally unrelated directory so an unfixed
    # implementation would unambiguously fail.
    monkeypatch.chdir(tmp_path)

    result = mcp_server.write_text_file(
        "work_products/ai_poem.html", "<html>poem</html>"
    )

    expected = workspace_root / "work_products" / "ai_poem.html"
    assert expected.exists(), f"expected workspace-relative write to {expected}; got {result!r}"
    assert expected.read_text(encoding="utf-8") == "<html>poem</html>"
    assert "write denied" not in result.lower()


def test_write_text_file_rejects_path_escaping_workspace(
    monkeypatch, tmp_path: Path
) -> None:
    """Defense in depth: an absolute path outside both roots must still
    be rejected even though relative-path resolution is now permissive."""
    artifacts_root = tmp_path / "artifacts"
    workspace_root = tmp_path / "workspace"
    artifacts_root.mkdir(parents=True, exist_ok=True)
    workspace_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("UA_ARTIFACTS_DIR", str(artifacts_root))
    monkeypatch.setenv("CURRENT_RUN_WORKSPACE", str(workspace_root))
    monkeypatch.delenv("CURRENT_SESSION_WORKSPACE", raising=False)

    escape_target = tmp_path / "outside.html"
    result = mcp_server.write_text_file(str(escape_target), "nope")
    assert "write denied" in result.lower()
    assert not escape_target.exists()

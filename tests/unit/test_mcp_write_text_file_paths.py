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
    monkeypatch.setenv("CURRENT_SESSION_WORKSPACE", str(workspace_root))

    raw_path = "/opt/universal_agent/UA_ARTIFACTS_DIR/youtube-tutorial-learning/test-run/manifest.json"
    result = mcp_server.write_text_file(raw_path, '{"ok": true}')

    expected = artifacts_root / "youtube-tutorial-learning/test-run/manifest.json"
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
    monkeypatch.setenv("CURRENT_SESSION_WORKSPACE", str(workspace_root))

    raw_path = "UA_ARTIFACTS_DIR/youtube-tutorial-learning/test-run/README.md"
    result = mcp_server.write_text_file(raw_path, "hello")

    expected = artifacts_root / "youtube-tutorial-learning/test-run/README.md"
    assert expected.exists()
    assert expected.read_text(encoding="utf-8") == "hello"
    assert str(expected) in result

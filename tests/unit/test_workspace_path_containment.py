"""Regression: workspace file helpers must reject sibling-prefix path escapes.

`str(target).startswith(str(workspace))` accepted a sibling directory whose name
shared the workspace's prefix (e.g. workspace `session_123`, path
`../session_123_other/secret.txt` → a *different* directory) — a cross-session
read. Both helpers now use `_resolve_path_under_root` (root in target.parents).
"""

from pathlib import Path

from fastapi import HTTPException
import pytest

from universal_agent.api.server import (
    _list_workspace_files_payload,
    _read_workspace_file_response,
)


@pytest.fixture
def workspaces(tmp_path):
    ws = tmp_path / "session_123"
    (ws / "sub").mkdir(parents=True)
    (ws / "ok.txt").write_text("mine")
    (ws / "sub" / "nested.txt").write_text("nested-mine")
    sibling = tmp_path / "session_123_other"  # shares the "session_123" prefix
    sibling.mkdir()
    (sibling / "secret.txt").write_text("SECRET-not-mine")
    return ws, sibling


def test_read_rejects_sibling_prefix_escape(workspaces):
    ws, _ = workspaces
    with pytest.raises(HTTPException) as ei:
        _read_workspace_file_response(ws, "../session_123_other/secret.txt")
    assert ei.value.status_code == 403


def test_list_rejects_sibling_prefix_escape(workspaces):
    ws, _ = workspaces
    with pytest.raises(HTTPException) as ei:
        _list_workspace_files_payload(ws, "../session_123_other")
    assert ei.value.status_code == 403


def test_read_allows_legitimate_in_workspace_file(workspaces):
    ws, _ = workspaces
    resp = _read_workspace_file_response(ws, "sub/nested.txt")
    assert resp.status_code == 200
    assert resp.body == b"nested-mine"


def test_list_allows_legitimate_in_workspace_dir(workspaces):
    ws, _ = workspaces
    payload = _list_workspace_files_payload(ws, "sub")
    names = {f["name"] for f in payload["files"]}
    assert "nested.txt" in names

"""Unit tests for pure helper functions in services.session_dossier."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from universal_agent.services.session_dossier import (
    _DESCRIPTION_SEPARATOR,
    _SAFETY_MAX_INPUT_CHARS,
    _collect_workspace_data,
    _write_files,
    generate_session_dossier,
)

# ---------------------------------------------------------------------------
# _collect_workspace_data
# ---------------------------------------------------------------------------


class TestCollectWorkspaceData:
    """Tests for the workspace-data assembler (pure function, no LLM)."""

    def test_empty_workspace_with_metadata(self, tmp_path: Path):
        result = _collect_workspace_data(tmp_path, {"source": "heartbeat"})
        assert "## Session Metadata" in result
        assert "- **source**: heartbeat" in result
        assert f"## Workspace\n{tmp_path}" in result

    def test_empty_workspace_without_metadata(self, tmp_path: Path):
        result = _collect_workspace_data(tmp_path, {})
        assert "## Session Metadata" not in result

    def test_run_log_included(self, tmp_path: Path):
        (tmp_path / "run.log").write_text("line 1\nline 2\n", encoding="utf-8")
        result = _collect_workspace_data(tmp_path, {})
        assert "## Run Log" in result
        assert "line 1" in result
        assert "line 2" in result

    def test_run_log_truncated_at_safety_limit(self, tmp_path: Path):
        huge_content = "x" * (_SAFETY_MAX_INPUT_CHARS + 10_000)
        (tmp_path / "run.log").write_text(huge_content, encoding="utf-8")
        result = _collect_workspace_data(tmp_path, {})
        assert len(result) < len(huge_content) + 2000
        assert "[... truncated at safety limit ...]" in result

    def test_run_log_unreadable_produces_error_message(self, tmp_path: Path):
        log = tmp_path / "run.log"
        log.write_text("content", encoding="utf-8")
        log.chmod(0o000)
        try:
            result = _collect_workspace_data(tmp_path, {})
            assert "[Error reading run.log:" in result
        finally:
            log.chmod(0o644)

    def test_checkpoint_original_request_extracted(self, tmp_path: Path):
        cp = {"original_request": "build me a dashboard", "status": "completed"}
        (tmp_path / "run_checkpoint.json").write_text(json.dumps(cp), encoding="utf-8")
        result = _collect_workspace_data(tmp_path, {})
        assert "## Original Request (from run_checkpoint.json)" in result
        assert "build me a dashboard" in result

    def test_checkpoint_query_fallback(self, tmp_path: Path):
        cp = {"query": "research AI agents", "status": "completed"}
        (tmp_path / "run_checkpoint.json").write_text(json.dumps(cp), encoding="utf-8")
        result = _collect_workspace_data(tmp_path, {})
        assert "research AI agents" in result

    def test_checkpoint_task_fallback(self, tmp_path: Path):
        cp = {"task": "fix the bug", "status": "completed"}
        (tmp_path / "session_checkpoint.json").write_text(json.dumps(cp), encoding="utf-8")
        result = _collect_workspace_data(tmp_path, {})
        assert "fix the bug" in result

    def test_checkpoint_invalid_json_skipped(self, tmp_path: Path):
        (tmp_path / "run_checkpoint.json").write_text("not json{{{{", encoding="utf-8")
        result = _collect_workspace_data(tmp_path, {})
        assert "## Original Request" not in result

    def test_sync_ready_stats_included(self, tmp_path: Path):
        sr = {"status": "completed", "duration_seconds": 120, "tool_calls": 15}
        (tmp_path / "sync_ready.json").write_text(json.dumps(sr), encoding="utf-8")
        result = _collect_workspace_data(tmp_path, {})
        assert "## Execution Stats (sync_ready.json)" in result
        assert "- **status**: completed" in result
        assert "- **duration_seconds**: 120" in result
        assert "- **tool_calls**: 15" in result

    def test_workspace_files_listing(self, tmp_path: Path):
        (tmp_path / "report.html").write_text("<h1>hi</h1>", encoding="utf-8")
        subdir = tmp_path / "work_products"
        subdir.mkdir()
        (subdir / "data.csv").write_text("a,b\n1,2", encoding="utf-8")
        result = _collect_workspace_data(tmp_path, {})
        assert "## Workspace Files" in result
        assert "report.html" in result
        assert "work_products/" in result

    def test_workspace_files_skips_dotfiles(self, tmp_path: Path):
        (tmp_path / ".hidden").write_text("secret", encoding="utf-8")
        (tmp_path / "visible.txt").write_text("public", encoding="utf-8")
        result = _collect_workspace_data(tmp_path, {})
        assert ".hidden" not in result.split("## Workspace Files")[1].split("\n\n")[0]
        assert "visible.txt" in result

    def test_transcript_included_when_no_run_log(self, tmp_path: Path):
        (tmp_path / "transcript.md").write_text("User said: do stuff\nAgent: done", encoding="utf-8")
        result = _collect_workspace_data(tmp_path, {})
        assert "## Transcript" in result
        assert "do stuff" in result

    def test_transcript_not_included_when_run_log_exists(self, tmp_path: Path):
        (tmp_path / "run.log").write_text("log content", encoding="utf-8")
        (tmp_path / "transcript.md").write_text("transcript content", encoding="utf-8")
        result = _collect_workspace_data(tmp_path, {})
        assert "## Run Log" in result
        assert "## Transcript" not in result

    def test_transcript_truncated_at_safety_limit(self, tmp_path: Path):
        huge = "y" * (_SAFETY_MAX_INPUT_CHARS + 10_000)
        (tmp_path / "transcript.md").write_text(huge, encoding="utf-8")
        result = _collect_workspace_data(tmp_path, {})
        assert "[... truncated ...]" in result


# ---------------------------------------------------------------------------
# _write_files
# ---------------------------------------------------------------------------


class TestWriteFiles:
    """Tests for the file-writer helper (tolerates write failures)."""

    def test_writes_both_files(self, tmp_path: Path):
        _write_files(tmp_path, "# Dossier", "Short description")
        assert (tmp_path / "context_brief.md").read_text() == "# Dossier"
        assert (tmp_path / "description.txt").read_text() == "Short description"

    def test_overwrites_existing_files(self, tmp_path: Path):
        (tmp_path / "context_brief.md").write_text("old", encoding="utf-8")
        _write_files(tmp_path, "new", "new desc")
        assert (tmp_path / "context_brief.md").read_text() == "new"

    def test_handles_unwritable_directory_gracefully(self, tmp_path: Path):
        subdir = tmp_path / "readonly"
        subdir.mkdir()
        subdir.chmod(0o444)
        try:
            _write_files(subdir, "content", "desc")
        finally:
            subdir.chmod(0o755)


# ---------------------------------------------------------------------------
# generate_session_dossier — non-LLM paths
# ---------------------------------------------------------------------------


class TestGenerateSessionDossierEmpty:
    """Test the early-return path when workspace has no execution artifacts."""

    @pytest.mark.asyncio
    async def test_empty_workspace_returns_minimal_dossier(self, tmp_path: Path):
        dossier, description = await generate_session_dossier(tmp_path)
        assert "No execution artifacts found" in dossier
        assert description == "Empty session — no execution data"
        assert (tmp_path / "context_brief.md").exists()
        assert (tmp_path / "description.txt").exists()

    @pytest.mark.asyncio
    async def test_nonexistent_workspace_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError, match="Workspace directory not found"):
            await generate_session_dossier(tmp_path / "nope")

    @pytest.mark.asyncio
    async def test_metadata_defaults_to_empty_dict(self, tmp_path: Path):
        dossier, description = await generate_session_dossier(tmp_path, metadata=None)
        assert "No execution artifacts found" in dossier

    @pytest.mark.asyncio
    async def test_checkpoint_only_triggers_llm_path(self, tmp_path: Path):
        cp = {"original_request": "test task"}
        (tmp_path / "run_checkpoint.json").write_text(json.dumps(cp), encoding="utf-8")

        with patch(
            "universal_agent.services.session_dossier._async_call_llm",
            new_callable=AsyncMock,
            return_value=(
                f"# Dossier\n\nTask completed.\n\n"
                f"{_DESCRIPTION_SEPARATOR}\n"
                f"Completed test task successfully."
            ),
        ), patch("universal_agent.services.session_dossier.resolve_haiku", return_value="test-model"):
            dossier, description = await generate_session_dossier(tmp_path)

        assert "# Dossier" in dossier
        assert "Completed test task successfully" in description


class TestGenerateSessionDossierParsing:
    """Test the response parsing logic (separator splitting, fallback, normalization)."""

    @pytest.mark.asyncio
    async def test_response_with_separator_splits_correctly(self, tmp_path: Path):
        (tmp_path / "run.log").write_text("did some work", encoding="utf-8")
        response = f"# Analysis\n\nDetailed report.\n\n{_DESCRIPTION_SEPARATOR}\nBrief card text."

        with patch(
            "universal_agent.services.session_dossier._async_call_llm",
            new_callable=AsyncMock,
            return_value=response,
        ), patch("universal_agent.services.session_dossier.resolve_haiku", return_value="m"):
            dossier, description = await generate_session_dossier(tmp_path)

        assert dossier == "# Analysis\n\nDetailed report."
        assert description == "Brief card text."

    @pytest.mark.asyncio
    async def test_response_without_separator_uses_first_line_as_description(self, tmp_path: Path):
        (tmp_path / "run.log").write_text("content", encoding="utf-8")
        response = "# Some Title Here\n\nBody of the dossier."

        with patch(
            "universal_agent.services.session_dossier._async_call_llm",
            new_callable=AsyncMock,
            return_value=response,
        ), patch("universal_agent.services.session_dossier.resolve_haiku", return_value="m"):
            dossier, description = await generate_session_dossier(tmp_path)

        assert "Body of the dossier" in dossier
        assert "Some Title Here" in description

    @pytest.mark.asyncio
    async def test_description_normalizes_whitespace(self, tmp_path: Path):
        (tmp_path / "run.log").write_text("x", encoding="utf-8")
        response = f"Dossier text\n\n{_DESCRIPTION_SEPARATOR}\nLine 1\n  Line 2  \n  Line 3"

        with patch(
            "universal_agent.services.session_dossier._async_call_llm",
            new_callable=AsyncMock,
            return_value=response,
        ), patch("universal_agent.services.session_dossier.resolve_haiku", return_value="m"):
            _, description = await generate_session_dossier(tmp_path)

        assert "\n" not in description
        assert "Line 1 Line 2 Line 3" in description

    @pytest.mark.asyncio
    async def test_description_truncated_at_300_chars(self, tmp_path: Path):
        (tmp_path / "run.log").write_text("x", encoding="utf-8")
        long_desc = "A" * 500
        response = f"Dossier\n\n{_DESCRIPTION_SEPARATOR}\n{long_desc}"

        with patch(
            "universal_agent.services.session_dossier._async_call_llm",
            new_callable=AsyncMock,
            return_value=response,
        ), patch("universal_agent.services.session_dossier.resolve_haiku", return_value="m"):
            _, description = await generate_session_dossier(tmp_path)

        assert len(description) <= 300
        assert description.endswith("…")

    @pytest.mark.asyncio
    async def test_empty_response_fallback(self, tmp_path: Path):
        (tmp_path / "run.log").write_text("x", encoding="utf-8")

        with patch(
            "universal_agent.services.session_dossier._async_call_llm",
            new_callable=AsyncMock,
            return_value="",
        ), patch("universal_agent.services.session_dossier.resolve_haiku", return_value="m"):
            dossier, description = await generate_session_dossier(tmp_path)

        assert description == "Session completed"

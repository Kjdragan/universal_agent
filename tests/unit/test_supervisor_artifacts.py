"""Unit tests for supervisors/artifacts.py pure helpers and render_markdown_snapshot."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile

from universal_agent.supervisors.artifacts import (
    _format_json,
    _json_default,
    list_snapshot_runs,
    persist_snapshot,
    render_markdown_snapshot,
)


class TestJsonDefault:
    def test_string_passthrough(self):
        assert _json_default("hello") == "hello"

    def test_int_converted(self):
        assert _json_default(42) == "42"

    def test_complex_type_converted(self):
        result = _json_default({"a": 1})
        assert isinstance(result, str)
        assert "a" in result


class TestFormatJson:
    def test_produces_valid_json(self):
        data = {"key": "value", "num": 42}
        result = _format_json(data)
        parsed = json.loads(result)
        assert parsed == data

    def test_indented(self):
        result = _format_json({"a": 1})
        assert chr(10) in result

    def test_handles_non_serializable(self):
        data = {"obj": object()}
        result = _format_json(data)
        parsed = json.loads(result)
        assert isinstance(parsed["obj"], str)


class TestRenderMarkdownSnapshot:
    def _minimal_snapshot(self, **overrides) -> dict:
        snap = {
            "supervisor_id": "test-supervisor",
            "generated_at": "2026-01-01T00:00:00+00:00",
            "summary": "test summary",
            "severity": "info",
            "kpis": {"metric_a": 42},
            "diagnostics": {"section": {"nested": True}},
            "recommendations": [],
        }
        snap.update(overrides)
        return snap

    def test_contains_supervisor_id_in_header(self):
        md = render_markdown_snapshot(self._minimal_snapshot())
        assert "# Supervisor Brief: test-supervisor" in md

    def test_contains_severity(self):
        md = render_markdown_snapshot(self._minimal_snapshot())
        assert "`info`" in md

    def test_contains_summary(self):
        md = render_markdown_snapshot(self._minimal_snapshot(summary="hello world"))
        assert "hello world" in md

    def test_kpis_rendered(self):
        md = render_markdown_snapshot(self._minimal_snapshot(kpis={"foo": "bar"}))
        assert "`foo`" in md
        assert "`bar`" in md

    def test_empty_kpis_shows_fallback(self):
        md = render_markdown_snapshot(self._minimal_snapshot(kpis={}))
        assert "No KPI values available" in md

    def test_recommendations_rendered(self):
        recs = [
            {
                "action": "Do something",
                "rationale": "Because reasons",
                "endpoint_or_command": "GET /api/test",
                "requires_confirmation": False,
            }
        ]
        md = render_markdown_snapshot(self._minimal_snapshot(recommendations=recs))
        assert "Do something" in md
        assert "Because reasons" in md
        assert "GET /api/test" in md

    def test_no_recommendations_shows_fallback(self):
        md = render_markdown_snapshot(self._minimal_snapshot(recommendations=[]))
        assert "No immediate recommendations" in md

    def test_missing_fields_graceful(self):
        md = render_markdown_snapshot({})
        assert isinstance(md, str)
        assert "# Supervisor Brief:" in md

    def test_diagnostics_json_in_output(self):
        md = render_markdown_snapshot(
            self._minimal_snapshot(diagnostics={"ping": "pong"})
        )
        assert '"ping"' in md
        assert '"pong"' in md

    def test_machine_readable_section(self):
        md = render_markdown_snapshot(self._minimal_snapshot())
        assert "## Machine Readable" in md


class TestPersistAndListSnapshots:
    def test_persist_creates_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            snap = {
                "supervisor_id": "test-sup",
                "generated_at": "2026-01-01T00:00:00Z",
                "severity": "info",
                "summary": "test",
            }
            paths = persist_snapshot(
                supervisor_id="test-sup",
                snapshot=snap,
                artifacts_root=root,
            )
            assert Path(paths["markdown_path"]).exists()
            assert Path(paths["json_path"]).exists()
            md_content = Path(paths["markdown_path"]).read_text()
            assert "test-sup" in md_content
            json_content = json.loads(Path(paths["json_path"]).read_text())
            assert json_content["supervisor_id"] == "test-sup"

    def test_list_returns_empty_for_missing_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runs = list_snapshot_runs(
                supervisor_id="nonexistent",
                artifacts_root=Path(tmpdir),
            )
            assert runs == []

    def test_list_returns_persisted_run(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            snap = {
                "supervisor_id": "list-sup",
                "generated_at": "2026-01-01T12:00:00Z",
                "severity": "warning",
                "summary": "check listing",
            }
            persist_snapshot(
                supervisor_id="list-sup",
                snapshot=snap,
                artifacts_root=root,
            )
            runs = list_snapshot_runs(
                supervisor_id="list-sup",
                artifacts_root=root,
            )
            assert len(runs) == 1
            assert runs[0]["severity"] == "warning"
            assert runs[0]["artifacts"]["json_path"] != ""

    def test_list_respects_limit(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            snap = {
                "supervisor_id": "limit-sup",
                "generated_at": "2026-01-01T12:00:00Z",
                "severity": "info",
                "summary": "test",
            }
            for _ in range(5):
                persist_snapshot(
                    supervisor_id="limit-sup",
                    snapshot=snap,
                    artifacts_root=root,
                )
            runs = list_snapshot_runs(
                supervisor_id="limit-sup",
                artifacts_root=root,
                limit=3,
            )
            assert len(runs) == 3

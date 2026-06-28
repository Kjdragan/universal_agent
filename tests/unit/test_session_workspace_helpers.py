"""Unit tests for utils/session_workspace.py — interim work-product path + JSON helpers.

Covers two production-used helpers that previously had no direct test coverage:
  - build_interim_work_product_paths() -> used by tools/csi_bridge.py, tools/x_trends_bridge.py
  - write_json()                       -> used by tools/csi_bridge.py, tools/x_trends_bridge.py

These are characterization tests: they pin the helpers' current behavior as a
regression net. No behavior is changed in this PR, so classic red-green TDD does
not apply (there is no fix to drive). Tests are written test-first and confirmed
green against the unchanged code; a mutation check (in dev, not committed)
verified they are sensitive to the real behavior rather than vacuous.
"""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import re

import pytest
import pytz

from universal_agent.utils.session_workspace import (
    InterimWorkProduct,
    build_interim_work_product_paths,
    write_json,
)

# run-dir suffix is "__" + YYYYMMDD_HHMMSS (Houston time)
_TS_SUFFIX_RE = re.compile(r"__(\d{8})_(\d{6})$")


# ── build_interim_work_product_paths ─────────────────────────────────────────


class TestBuildInterimWorkProductPaths:
    def test_returns_frozen_dataclass_instance(self):
        wp = build_interim_work_product_paths(
            workspace_dir="/tmp/ws", domain="d", source="s", run_slug="r"
        )
        assert isinstance(wp, InterimWorkProduct)
        # Frozen: re-assigning a field must fail (regression guard for the
        # dataclass(frozen=True) declaration).
        with pytest.raises(AttributeError):
            wp.base_dir = Path("/elsewhere")  # type: ignore[misc]

    def test_directory_hierarchy_layout(self, tmp_path):
        wp = build_interim_work_product_paths(
            workspace_dir=str(tmp_path), domain="Politics", source="X", run_slug="run1"
        )
        rel = wp.base_dir.relative_to(tmp_path).parts
        # <workspace>/work_products/social/<source>/<domain>/<run>__<ts>
        assert rel[0] == "work_products"
        assert rel[1] == "social"
        assert rel[2] == "X"
        assert rel[3] == "Politics"
        assert rel[4].startswith("run1__")

    def test_source_domain_and_run_slug_are_slugged(self, tmp_path):
        wp = build_interim_work_product_paths(
            workspace_dir=str(tmp_path),
            domain="Health & Wellness",  # -> Health_Wellness
            source="X / Trends",  # -> X_Trends
            run_slug="my run!!",  # -> my_run__<ts>
        )
        rel = wp.base_dir.relative_to(tmp_path).parts
        assert rel[2] == "X_Trends"
        assert rel[3] == "Health_Wellness"
        assert rel[4].startswith("my_run__")

    def test_request_result_manifest_filenames_inside_base_dir(self, tmp_path):
        wp = build_interim_work_product_paths(
            workspace_dir=str(tmp_path), domain="d", source="s", run_slug="r"
        )
        assert wp.request_path.name == "request.json"
        assert wp.result_path.name == "result.json"
        assert wp.manifest_path.name == "manifest.json"
        for path in (wp.request_path, wp.result_path, wp.manifest_path):
            assert wp.base_dir in path.parents

    def test_run_dir_suffix_is_houston_timestamp(self, tmp_path):
        wp = build_interim_work_product_paths(
            workspace_dir=str(tmp_path), domain="d", source="s", run_slug="run1"
        )
        run_dir_name = wp.base_dir.name
        match = _TS_SUFFIX_RE.search(run_dir_name)
        assert match is not None, f"missing __YYYYMMDD_HHMMSS suffix: {run_dir_name!r}"
        # The stamped value must parse as a real datetime and be recent in
        # Houston wall-clock time (mirrors the helper's America/Chicago tz).
        parsed = datetime.strptime(
            f"{match.group(1)}_{match.group(2)}", "%Y%m%d_%H%M%S"
        )
        houston_now = datetime.now(pytz.timezone("America/Chicago")).replace(
            tzinfo=None
        )
        delta = abs((houston_now - parsed).total_seconds())
        assert delta < 300, f"timestamp {parsed} far from Houston now {houston_now}"

    def test_absolute_workspace_yields_absolute_paths(self, tmp_path):
        wp = build_interim_work_product_paths(
            workspace_dir=str(tmp_path), domain="d", source="s", run_slug="r"
        )
        assert wp.base_dir.is_absolute()
        assert wp.request_path.is_absolute()
        assert wp.result_path.is_absolute()
        assert wp.manifest_path.is_absolute()


# ── write_json ───────────────────────────────────────────────────────────────


class TestWriteJson:
    def test_roundtrips_nested_dict(self, tmp_path):
        p = tmp_path / "out.json"
        obj = {"a": 1, "b": [2, 3], "c": {"nested": True}}
        write_json(p, obj)
        assert json.loads(p.read_text(encoding="utf-8")) == obj

    def test_roundtrips_list_root(self, tmp_path):
        p = tmp_path / "out.json"
        obj = [1, "two", {"three": 3}]
        write_json(p, obj)
        assert json.loads(p.read_text(encoding="utf-8")) == obj

    def test_creates_missing_parent_dirs(self, tmp_path):
        p = tmp_path / "deep" / "deeper" / "out.json"
        assert not p.parent.exists()
        write_json(p, {"ok": True})
        assert p.exists()
        assert json.loads(p.read_text(encoding="utf-8")) == {"ok": True}

    def test_idempotent_when_parent_already_exists(self, tmp_path):
        # mkdir(parents=True, exist_ok=True) must not raise on re-use.
        p = tmp_path / "nested" / "out.json"
        write_json(p, {"v": 1})
        write_json(p, {"v": 2})
        assert json.loads(p.read_text(encoding="utf-8")) == {"v": 2}

    def test_overwrites_existing_file_content(self, tmp_path):
        p = tmp_path / "out.json"
        write_json(p, {"v": 1})
        write_json(p, {"v": 2, "extra": [1, 2]})
        assert json.loads(p.read_text(encoding="utf-8")) == {"v": 2, "extra": [1, 2]}

    def test_output_ends_with_single_trailing_newline(self, tmp_path):
        p = tmp_path / "out.json"
        write_json(p, {"a": 1})
        text = p.read_text(encoding="utf-8")
        assert text.endswith("\n")
        assert not text.endswith("\n\n")

    def test_indent_is_two_spaces(self, tmp_path):
        p = tmp_path / "out.json"
        write_json(p, {"a": 1})
        text = p.read_text(encoding="utf-8")
        assert '  "a": 1' in text  # exactly two-space indent on a key line

    def test_ensure_ascii_escapes_non_ascii(self, tmp_path):
        # json.dumps(..., ensure_ascii=True) => non-ASCII is \u-escaped in the
        # file, while still parsing back to the original value.
        p = tmp_path / "out.json"
        write_json(p, {"k": "café"})
        text = p.read_text(encoding="utf-8")
        assert "\\u00e9" in text
        assert "café" not in text
        assert json.loads(text) == {"k": "café"}


# ── combined usage (mirrors tools/csi_bridge.py + tools/x_trends_bridge.py) ──


class TestCombinedBridgeUsage:
    def test_request_result_manifest_write_pattern(self, tmp_path):
        wp = build_interim_work_product_paths(
            workspace_dir=str(tmp_path), domain="d", source="s", run_slug="r"
        )
        write_json(wp.request_path, {"query": "hello"})
        write_json(wp.result_path, {"rows": [1, 2, 3]})
        write_json(wp.manifest_path, {"source": "csi", "count": 3})

        assert wp.request_path.exists()
        assert wp.result_path.exists()
        assert wp.manifest_path.exists()
        assert (
            json.loads(wp.request_path.read_text(encoding="utf-8"))["query"] == "hello"
        )
        assert json.loads(wp.result_path.read_text(encoding="utf-8"))["rows"] == [
            1,
            2,
            3,
        ]
        assert json.loads(wp.manifest_path.read_text(encoding="utf-8"))["count"] == 3

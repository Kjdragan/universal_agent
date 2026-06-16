"""Unit tests for the scratchpad artifact index builder (scripts/build_scratch_index.py).

The builder is a stdlib-only script (not an importable package module), so we load it by
path. Everything runs against a temp scratch root — no VPS, no network.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_BUILDER = Path(__file__).resolve().parents[2] / "scripts" / "build_scratch_index.py"


def _load():
    spec = importlib.util.spec_from_file_location("build_scratch_index", _BUILDER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bsi = _load()
TS = "uaonvps.taildcc090.ts.net"


def _slug(root: Path, name: str) -> Path:
    d = root / name
    d.mkdir(parents=True)
    return d


def test_uses_sidecar_metadata(tmp_path):
    d = _slug(tmp_path, "report-aaa111")
    (d / "page.html").write_text("<title>Ignored</title>")
    (d / "_artifact.json").write_text(
        json.dumps(
            {
                "title": "Quarterly Report",
                "description": "Q2 numbers",
                "kind": "markdown",
                "entry": "page.html",
                "created_at": "2026-06-15T12:00:00+00:00",
            }
        )
    )
    html, count = bsi.build_index(tmp_path, TS)
    assert count == 1
    assert "Quarterly Report" in html and "Q2 numbers" in html
    assert f"https://{TS}/scratch/report-aaa111/page.html" in html
    assert ">markdown<" in html  # kind chip


def test_derives_title_and_entry_without_sidecar(tmp_path):
    d = _slug(tmp_path, "raw-bbb222")
    (d / "index.html").write_text("<html><head><title>Derived Title</title></head></html>")
    (d / "other.html").write_text("<title>Nope</title>")
    html, count = bsi.build_index(tmp_path, TS)
    assert count == 1
    assert "Derived Title" in html
    # index.html is preferred as the entry
    assert f"https://{TS}/scratch/raw-bbb222/index.html" in html


def test_sorted_newest_first(tmp_path):
    old = _slug(tmp_path, "old-1")
    (old / "a.html").write_text("<title>Old</title>")
    (old / "_artifact.json").write_text(json.dumps({"title": "Old", "entry": "a.html", "created_at": "2026-01-01T00:00:00+00:00"}))
    new = _slug(tmp_path, "new-1")
    (new / "b.html").write_text("<title>New</title>")
    (new / "_artifact.json").write_text(json.dumps({"title": "New", "entry": "b.html", "created_at": "2026-06-01T00:00:00+00:00"}))
    html, count = bsi.build_index(tmp_path, TS)
    assert count == 2
    assert html.index("New") < html.index("Old"), "newest must appear first"


def test_empty_root_renders_placeholder(tmp_path):
    html, count = bsi.build_index(tmp_path, TS)
    assert count == 0
    assert "No artifacts yet" in html


def test_root_index_and_dotdirs_excluded(tmp_path):
    (tmp_path / "index.html").write_text("<title>RootIndexZZ</title>")  # the index itself
    dot = tmp_path / ".tmpwork"
    dot.mkdir()
    (dot / "x.html").write_text("<title>DotDirArtifactZZ</title>")
    real = _slug(tmp_path, "real-ccc333")
    (real / "r.html").write_text("<title>Real</title>")
    html, count = bsi.build_index(tmp_path, TS)
    assert count == 1 and "Real" in html
    assert "DotDirArtifactZZ" not in html and "RootIndexZZ" not in html


def test_derived_title_entities_not_double_escaped(tmp_path):
    d = _slug(tmp_path, "ent-eee555")
    (d / "index.html").write_text("<title>Results &amp; Suggestions</title>")
    html, _ = bsi.build_index(tmp_path, TS)
    assert "Results &amp; Suggestions" in html
    assert "&amp;amp;" not in html


def test_search_attribute_present_and_lowercased(tmp_path):
    d = _slug(tmp_path, "srch-ddd444")
    (d / "p.html").write_text("<title>Z</title>")
    (d / "_artifact.json").write_text(json.dumps({"title": "MixedCase Thing", "description": "DESC", "entry": "p.html", "created_at": "2026-06-10T00:00:00+00:00"}))
    html, _ = bsi.build_index(tmp_path, TS)
    assert 'id="q"' in html  # the search box
    assert 'data-search="' in html
    assert "mixedcase thing" in html  # haystack is lowercased

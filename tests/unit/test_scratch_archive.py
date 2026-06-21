"""Unit tests for the durable scratchpad artifact archiver (``scripts/scratch_archive.py``).

The archiver is a stdlib-only script (no venv), so we load it by file path and exercise its
pure ``archive_artifact`` entrypoint against temp dirs — no network, no real scratchpad.
"""

from __future__ import annotations

from datetime import datetime
import importlib.util
import json
from pathlib import Path

import pytest

_MODULE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "scratch_archive.py"


def _load_archiver():
    spec = importlib.util.spec_from_file_location("scratch_archive", _MODULE_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def archiver():
    return _load_archiver()


def _write(p: Path, text: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def test_archive_single_html_file(archiver, tmp_path):
    src = _write(tmp_path / "report.html", "<html><head><title>My Triage</title></head><body>x</body></html>")
    root = tmp_path / "archive"
    now = datetime(2026, 6, 21, 9, 30, 15)

    rec = archiver.archive_artifact(
        src=src, slug="triage-abc123", root=root, is_dir=False, url="https://h/scratch/triage-abc123/report.html", now=now
    )

    # Dated, named copy on disk.
    dest = root / "2026-06-21" / "093015__triage-abc123__report.html"
    assert dest.is_file()
    assert dest.read_text(encoding="utf-8").startswith("<html>")

    # Rich title parsed from <title>.
    assert rec["title"] == "My Triage"
    assert rec["kind"] == "file"
    assert rec["rel"] == "2026-06-21/093015__triage-abc123__report.html"

    # Ledger + both indexes written.
    ledger = (root / "index.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(ledger) == 1
    assert json.loads(ledger[0])["slug"] == "triage-abc123"
    md = (root / "INDEX.md").read_text(encoding="utf-8")
    assert "My Triage" in md and "2026-06-21" in md
    html = (root / "index.html").read_text(encoding="utf-8")
    assert "My Triage" in html and "093015__triage-abc123__report.html" in html


def test_title_falls_back_to_slug(archiver, tmp_path):
    src = _write(tmp_path / "data.csv", "a,b\n1,2\n")
    root = tmp_path / "archive"
    rec = archiver.archive_artifact(src=src, slug="export-x", root=root, is_dir=False, url="", now=datetime(2026, 6, 21, 1, 2, 3))
    assert rec["title"] == "export-x"
    assert (root / "2026-06-21" / "010203__export-x__data.csv").is_file()


def test_archive_docset_uses_sidecar_title(archiver, tmp_path):
    src = tmp_path / "docset"
    _write(src / "DESIGN.html", "<title>Design Hub</title>")
    _write(src / "sub" / "page.html", "<title>Sub</title>")
    # Sidecar that scratch_publish.py would have written for a metadata publish.
    _write(src / "_artifact.json", json.dumps({"title": "Big Plan", "entry": "DESIGN.html", "kind": "docset"}))
    root = tmp_path / "archive"

    rec = archiver.archive_artifact(src=src, slug="plan-9", root=root, is_dir=True, url="https://h/scratch/plan-9/", now=datetime(2026, 6, 21, 12, 0, 0))

    dest_dir = root / "2026-06-21" / "120000__plan-9"
    assert (dest_dir / "DESIGN.html").is_file()
    assert (dest_dir / "sub" / "page.html").is_file()
    assert rec["title"] == "Big Plan"
    assert rec["kind"] == "docset"
    assert rec["rel"] == "2026-06-21/120000__plan-9/DESIGN.html"


def test_multiple_artifacts_newest_first(archiver, tmp_path):
    root = tmp_path / "archive"
    a = _write(tmp_path / "a.md", "# First")
    b = _write(tmp_path / "b.md", "# Second")
    archiver.archive_artifact(src=a, slug="first", root=root, is_dir=False, url="", now=datetime(2026, 6, 20, 8, 0, 0))
    archiver.archive_artifact(src=b, slug="second", root=root, is_dir=False, url="", now=datetime(2026, 6, 21, 9, 0, 0))

    assert len((root / "index.jsonl").read_text(encoding="utf-8").splitlines()) == 2
    md = (root / "INDEX.md").read_text(encoding="utf-8")
    # Newer (Second / 2026-06-21) appears before older (First / 2026-06-20).
    assert md.index("Second") < md.index("First")
    # Markdown H1 became the title.
    assert "Second" in md and "First" in md


def test_same_second_collision_gets_suffix(archiver, tmp_path):
    root = tmp_path / "archive"
    a = _write(tmp_path / "x.html", "<title>One</title>")
    b = _write(tmp_path / "x.html", "<title>One</title>")  # same name, same slug, same second
    now = datetime(2026, 6, 21, 7, 7, 7)
    archiver.archive_artifact(src=a, slug="dup", root=root, is_dir=False, url="", now=now)
    archiver.archive_artifact(src=b, slug="dup", root=root, is_dir=False, url="", now=now)
    day = root / "2026-06-21"
    names = sorted(p.name for p in day.iterdir())
    assert names == ["070707__dup__1__x.html", "070707__dup__x.html"]


def test_disabled_via_env(archiver, tmp_path, monkeypatch):
    monkeypatch.setenv("UA_SCRATCH_ARCHIVE_ENABLED", "0")
    src = _write(tmp_path / "r.html", "<title>Z</title>")
    root = tmp_path / "archive"
    rc = archiver.main(["--src", str(src), "--slug", "z", "--root", str(root)])
    assert rc == 0
    assert not (root / "index.jsonl").exists()

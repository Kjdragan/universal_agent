"""Unit tests for the scratchpad markdown / file / doc-set publishing helpers.

The render helpers are pure (no network). The publish helpers are exercised against a
tiny fake ``publish_scratch.sh`` stand-in (the same technique as ``test_scratch_publish``)
so nothing here touches SSH or the VPS.
"""

from __future__ import annotations

from pathlib import Path
import stat

from universal_agent.services import scratch_publish as sp


def _write_fake_script(tmp_path: Path) -> Path:
    """A fake publish script that echoes a scratchpad URL for both single-file and --dir."""
    script = tmp_path / "publish_scratch.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        'if [ "$1" = "--dir" ]; then\n'
        '  echo "https://uaonvps.taildcc090.ts.net/scratch/$3/"\n'
        "else\n"
        '  echo "https://uaonvps.taildcc090.ts.net/scratch/$2/$(basename "$1")"\n'
        "fi\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return script


# --------------------------- pure render helpers ---------------------------


def test_html_page_pins_light_mode_and_no_dark_block():
    page = sp._html_page("Hi & <there>", "<p>body</p>")
    assert '<meta name="color-scheme" content="light">' in page
    assert "color-scheme:light" in page
    assert "@media (prefers-color-scheme: dark)" not in page
    assert "Hi &amp; &lt;there&gt;" in page  # title is escaped


def test_html_page_has_back_to_index_link_and_style():
    page = sp._html_page("Doc", "<p>body</p>")
    # Every scratchpad-rendered page links back to the artifact index, and the styling
    # for that link is present in the page's CSS.
    assert f'href="{sp.SCRATCH_INDEX_HREF}"' in page
    assert sp.SCRATCH_INDEX_HREF == "/scratch/"
    assert 'class="scratch-back"' in page
    assert ".scratch-back" in page  # CSS rule shipped inline
    # The back-link comes before the page body so it reads as a header.
    assert page.index("scratch-back") < page.index("<main>")


def test_scratch_back_link_html_default_and_custom_label():
    default = sp.scratch_back_link_html()
    assert 'href="/scratch/"' in default and "← Scratchpad index" in default
    # Labels are HTML-escaped.
    assert "A &amp; B" in sp.scratch_back_link_html("A & B")


def test_render_markdown_tables_and_headers():
    html = sp._render_markdown("# Title\n\n| a | b |\n|---|---|\n| 1 | 2 |\n")
    assert "<h1" in html and "<table>" in html and "<td>1</td>" in html


def test_markdown_title_extraction_and_fallback():
    assert sp._markdown_title("intro\n\n# Real **Title**\n", "fallback") == "Real Title"
    assert sp._markdown_title("no heading here", "fallback.md") == "fallback.md"


def test_discover_classifies_and_skips_junk(tmp_path):
    (tmp_path / "DESIGN.md").write_text("# d")
    (tmp_path / "app.py").write_text("x=1")
    (tmp_path / "logo.png").write_bytes(b"\x89PNG")
    (tmp_path / "weird.xyz").write_text("?")  # unsupported → skipped
    (tmp_path / ".hidden.md").write_text("# h")  # dotfile → skipped
    cache = tmp_path / "__pycache__"
    cache.mkdir()
    (cache / "x.py").write_text("c")

    specs = {s["rel"]: s for s in sp._discover_docset(tmp_path)}
    assert set(specs) == {"DESIGN.md", "app.py", "logo.png"}
    assert specs["DESIGN.md"]["out"] == "DESIGN.html" and specs["DESIGN.md"]["kind"] == "md"
    assert specs["app.py"]["out"] == "app.py.html" and specs["app.py"]["kind"] == "source"
    assert specs["logo.png"]["out"] == "logo.png" and specs["logo.png"]["kind"] == "raw"


def test_pick_hub_prefers_design_then_readme_toplevel(tmp_path):
    (tmp_path / "README.md").write_text("# r")
    (tmp_path / "DESIGN.md").write_text("# d")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "README.md").write_text("# nested")
    specs = sp._discover_docset(tmp_path)
    assert sp._pick_hub(specs) == "DESIGN.html"


def test_rewrite_links_relative_and_left_alone():
    out_map = {"survey.md": "survey.html", "app.py": "app.py.html", "DESIGN.md": "DESIGN.html"}
    text = (
        "[s](survey.md) [code](app.py) [ext](https://x.com) "
        "[anchor](#sec) [unknown](other.md) [withfrag](survey.md#h2)"
    )
    out = sp._rewrite_links(text, "DESIGN.md", out_map)
    assert "[s](survey.html)" in out
    assert "[code](app.py.html)" in out
    assert "[ext](https://x.com)" in out
    assert "[anchor](#sec)" in out
    assert "[unknown](other.md)" in out
    assert "[withfrag](survey.html#h2)" in out


def test_rewrite_links_resolves_nested_relative():
    out_map = {"DESIGN.md": "DESIGN.html"}
    out = sp._rewrite_links("[up](../DESIGN.md)", "sub/notes.md", out_map)
    assert "[up](../DESIGN.html)" in out


def test_build_nav_marks_current_and_hub_with_relative_hrefs():
    specs = [
        {"out": "DESIGN.html", "kind": "md", "title": "Design"},
        {"out": "sub/notes.html", "kind": "md", "title": "Notes"},
        {"out": "app.py.html", "kind": "source", "title": "app.py"},
    ]
    nav = sp._build_nav(specs, current_out="sub/notes.html", hub_out="DESIGN.html", brand="demo")
    assert 'href="../DESIGN.html"' in nav  # relative from sub/
    assert "Design · hub" in nav
    assert 'class="current"' in nav and "Notes" in nav
    assert 'class="file ' in nav  # source file in the files group


# --------------------------- publish glue ---------------------------


def test_publish_markdown_returns_url(tmp_path, monkeypatch):
    monkeypatch.setattr(sp, "_publish_script", lambda: _write_fake_script(tmp_path))
    url = sp.publish_markdown_to_scratch("# Hello\n\nbody", slug="note", filename="page.html")
    # markdown always carries a title → metadata (dir) path → base + filename
    assert url is not None
    assert url.startswith("https://uaonvps.taildcc090.ts.net/scratch/note-")
    assert url.endswith("/page.html")


def test_publish_html_without_metadata_uses_single_file(tmp_path, monkeypatch):
    monkeypatch.setattr(sp, "_publish_script", lambda: _write_fake_script(tmp_path))
    url = sp.publish_html_to_scratch("<p>x</p>", slug="s", filename="r.html")
    assert url is not None and url.endswith("/r.html")


def test_publish_file_with_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(sp, "_publish_script", lambda: _write_fake_script(tmp_path))
    f = tmp_path / "chart.png"
    f.write_bytes(b"\x89PNG\r\n")
    url = sp.publish_file_to_scratch(f, slug="img", title="Chart", description="a chart")
    assert url is not None and url.endswith("/chart.png")


def test_publish_file_missing_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(sp, "_publish_script", lambda: _write_fake_script(tmp_path))
    assert sp.publish_file_to_scratch(tmp_path / "nope.png") is None


def test_publish_docset_renders_and_returns_hub_url(tmp_path, monkeypatch):
    monkeypatch.setattr(sp, "_publish_script", lambda: _write_fake_script(tmp_path))
    src = tmp_path / "docs"
    src.mkdir()
    (src / "DESIGN.md").write_text("# Design\n\n[s](survey.md)\n")
    (src / "survey.md").write_text("# Survey\n")
    url = sp.publish_docset_to_scratch(src, slug="ds", title="My Docs", description="d")
    assert url is not None
    assert url.startswith("https://uaonvps.taildcc090.ts.net/scratch/ds-")
    assert url.endswith("/DESIGN.html")


def test_publish_docset_empty_dir_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(sp, "_publish_script", lambda: _write_fake_script(tmp_path))
    empty = tmp_path / "empty"
    empty.mkdir()
    assert sp.publish_docset_to_scratch(empty) is None


# --------------------------- stable id (replace-in-place) ---------------------------


def test_resolve_slug_stable_vs_random():
    # artifact_id -> exact, sanitized, no random suffix (overwrites the same URL).
    assert sp._resolve_slug(None, "qloop-handoff") == "qloop-handoff"
    assert sp._resolve_slug("ignored", "My Doc!!") == "My-Doc"
    # no artifact_id -> random suffix, never colliding.
    a, b = sp._resolve_slug("x", None), sp._resolve_slug("x", None)
    assert a != b and a.startswith("x-")


def test_artifact_id_stable_slug_markdown(tmp_path, monkeypatch):
    monkeypatch.setattr(sp, "_publish_script", lambda: _write_fake_script(tmp_path))
    url = sp.publish_markdown_to_scratch("# Hi\n\nx", artifact_id="qloop-handoff", filename="report.html")
    assert url == "https://uaonvps.taildcc090.ts.net/scratch/qloop-handoff/report.html"


def test_artifact_id_stable_slug_html_single_file(tmp_path, monkeypatch):
    monkeypatch.setattr(sp, "_publish_script", lambda: _write_fake_script(tmp_path))
    url = sp.publish_html_to_scratch("<p>x</p>", artifact_id="living-doc", filename="r.html")
    assert url == "https://uaonvps.taildcc090.ts.net/scratch/living-doc/r.html"


def test_artifact_id_stable_slug_docset(tmp_path, monkeypatch):
    monkeypatch.setattr(sp, "_publish_script", lambda: _write_fake_script(tmp_path))
    src = tmp_path / "docs"
    src.mkdir()
    (src / "DESIGN.md").write_text("# Design\n")
    url = sp.publish_docset_to_scratch(src, artifact_id="vr-design")
    assert url == "https://uaonvps.taildcc090.ts.net/scratch/vr-design/DESIGN.html"


# --------------------------- two-way review toolbar ---------------------------


def test_review_toolbar_baked_into_rendered_page():
    p = sp._html_page("Demo", "<p>hi</p>")
    assert ".sr-fab{" in p  # toolbar CSS
    assert "[scratch-review " in p  # the paste-prompt header it builds
    assert "scratch-comments-" in p  # the downloaded JSON name
    assert "navigator.clipboard" in p  # copy-to-clipboard path
    # light mode stays mandatory and no dark block sneaks in via the toolbar
    assert '<meta name="color-scheme" content="light">' in p
    assert "@media (prefers-color-scheme: dark)" not in p


def test_review_toolbar_can_be_disabled():
    off = sp._html_page("Demo", "<p>hi</p>", review=False)
    assert ".sr-fab{" not in off and "navigator.clipboard" not in off

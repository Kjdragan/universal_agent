"""Regression tests for the defuddle-cli subprocess invocation.

The CLI silently fails with ``error: unknown command`` when the ``parse``
subcommand is omitted. That regression went unnoticed for months because
the function returns ``{"ok": False}`` on any subprocess failure and the
fetcher falls through to the raw-HTML httpx fallback — every URL still
"worked" from the caller's perspective.

These tests pin the canonical argv so future refactors can't silently
break it again, and exercise both the JSON-output and markdown-output
code paths.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from universal_agent.services import csi_url_judge as cuj


class _FakeCompletedProcess:
    def __init__(self, *, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _patch_sp_run(monkeypatch: pytest.MonkeyPatch, *, stdout: str, returncode: int = 0):
    """Patch subprocess.run inside csi_url_judge and capture the argv."""
    captured: dict[str, Any] = {}

    def fake_run(argv, **kwargs):  # type: ignore[no-untyped-def]
        captured["argv"] = list(argv)
        captured["kwargs"] = kwargs
        return _FakeCompletedProcess(stdout=stdout, returncode=returncode)

    monkeypatch.setattr(cuj.sp, "run", fake_run)
    return captured


def test_defuddle_argv_includes_parse_subcommand(monkeypatch, tmp_path):
    """Regression: defuddle-cli requires the ``parse`` subcommand.

    Without it, the CLI fails with ``error: unknown command``. The legacy
    invocation passed the URL straight as the first positional and silently
    failed for every URL.
    """
    captured = _patch_sp_run(monkeypatch, stdout="# Real Markdown\n\nsome prose\n")
    out_path = tmp_path / "out.md"

    result = cuj._fetch_with_defuddle(
        "https://example.com/article", out_path, timeout=15
    )
    assert result["ok"] is True

    argv = captured["argv"]
    assert argv[:3] == ["npx", "-y", "defuddle-cli@latest"], (
        f"npx invocation drifted: argv={argv!r}"
    )
    # The CRITICAL assertion: 'parse' must be present before the URL.
    assert "parse" in argv, f"missing 'parse' subcommand: argv={argv!r}"
    parse_index = argv.index("parse")
    url_index = argv.index("https://example.com/article")
    assert parse_index < url_index, (
        f"'parse' must precede the URL positional: argv={argv!r}"
    )


def test_defuddle_argv_requests_markdown_output(monkeypatch, tmp_path):
    """The CLI defaults to HTML output; we need explicit --markdown."""
    captured = _patch_sp_run(monkeypatch, stdout="# Markdown\n\nbody\n")
    cuj._fetch_with_defuddle(
        "https://example.com/x", tmp_path / "x.md", timeout=15
    )
    argv = captured["argv"]
    assert "--markdown" in argv or "--md" in argv, (
        f"defuddle invocation must request markdown output: argv={argv!r}"
    )


def test_defuddle_argv_constant_matches_invocation(monkeypatch, tmp_path):
    """The module-level _DEFUDDLE_CLI_ARGV constant must be what we ship."""
    captured = _patch_sp_run(monkeypatch, stdout="# x\n")
    cuj._fetch_with_defuddle(
        "https://example.com/y", tmp_path / "y.md", timeout=15
    )
    expected_prefix = list(cuj._DEFUDDLE_CLI_ARGV)
    assert captured["argv"][: len(expected_prefix)] == expected_prefix


def test_defuddle_markdown_stdout_is_saved_verbatim(monkeypatch, tmp_path):
    """When the CLI returns markdown, the saved file should contain it."""
    md = "# Title\n\nReal prose paragraph with sentences.\n"
    _patch_sp_run(monkeypatch, stdout=md)
    out_path = tmp_path / "article.md"

    result = cuj._fetch_with_defuddle(
        "https://example.com/article", out_path, timeout=15
    )
    assert result["ok"] is True
    body = out_path.read_text(encoding="utf-8")
    assert body.startswith("# Source: https://example.com/article")
    assert "Real prose paragraph with sentences." in body


def test_defuddle_json_stdout_unwraps_content(monkeypatch, tmp_path):
    """Defensive: if the CLI ever flips back to JSON-by-default, unwrap it."""
    payload = json.dumps(
        {"content": "# Wrapped\n\nprose", "title": "Article", "domain": "example.com"}
    )
    _patch_sp_run(monkeypatch, stdout=payload)
    out_path = tmp_path / "json.md"

    result = cuj._fetch_with_defuddle(
        "https://example.com/wrapped", out_path, timeout=15
    )
    assert result["ok"] is True
    body = out_path.read_text(encoding="utf-8")
    assert "# Wrapped" in body
    assert "prose" in body
    # The raw JSON envelope must not leak into the saved file.
    assert '"title"' not in body


def test_defuddle_nonzero_returncode_returns_not_ok(monkeypatch, tmp_path):
    """A failing CLI invocation must surface as ok=False, not save garbage."""
    _patch_sp_run(monkeypatch, stdout="", returncode=1)
    out_path = tmp_path / "fail.md"
    result = cuj._fetch_with_defuddle(
        "https://example.com/", out_path, timeout=15
    )
    assert result == {"ok": False}
    assert not out_path.exists()


def test_defuddle_missing_npx_returns_not_ok(monkeypatch, tmp_path):
    """If npx is missing on the host, function returns ok=False cleanly."""

    def raise_filenotfound(argv, **kwargs):  # type: ignore[no-untyped-def]
        raise FileNotFoundError("npx not on PATH")

    monkeypatch.setattr(cuj.sp, "run", raise_filenotfound)
    result = cuj._fetch_with_defuddle(
        "https://example.com/", tmp_path / "x.md", timeout=15
    )
    assert result == {"ok": False}

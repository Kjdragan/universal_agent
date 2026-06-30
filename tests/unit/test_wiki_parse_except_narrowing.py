"""Regression tests for narrowing over-broad ``except Exception:`` in the wiki
vault parsing helpers (``wiki.core._load_json``, ``wiki.core._frontmatter_and_body``,
``wiki.explorer._read_manifest``).

Two kinds of tests per helper:
  * behavior-preserving fallback tests (corrupt/missing input -> default) — these
    document the intended "resilient parse" contract and pass both before and after
    the narrowing;
  * a "propagates non-parse exception" test that is the actual red->green proof of
    the narrowing: with the old ``except Exception:`` an arbitrary error was
    swallowed and the default returned; with the narrowed catch it must propagate.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import universal_agent.wiki.core as core
from universal_agent.wiki.core import _frontmatter_and_body, _load_json
import universal_agent.wiki.explorer as explorer


def _boom(*_args, **_kwargs):
    raise RuntimeError("non-parse failure must not be swallowed")


# --------------------------------------------------------------------------- #
# wiki.core._load_json
# --------------------------------------------------------------------------- #
def test_load_json_returns_default_on_corrupt_json(tmp_path: Path):
    p = tmp_path / "bad.json"
    p.write_text("{ not valid json ", encoding="utf-8")
    sentinel = {"ok": False}
    assert _load_json(p, sentinel) is sentinel


def test_load_json_returns_default_on_non_utf8_bytes(tmp_path: Path):
    p = tmp_path / "binary.json"
    p.write_bytes(b"\xff\xfe\x00bad")  # UnicodeDecodeError -> ValueError subclass
    sentinel = {"ok": False}
    assert _load_json(p, sentinel) is sentinel


def test_load_json_missing_file_returns_default(tmp_path: Path):
    # exists() guard short-circuits before the try block.
    assert _load_json(tmp_path / "absent.json", 42) == 42


def test_load_json_propagates_non_parse_exception(tmp_path: Path, monkeypatch):
    """RED before narrowing (broad except swallowed it), GREEN after."""
    p = tmp_path / "x.json"
    p.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(core.json, "loads", _boom)
    with pytest.raises(RuntimeError, match="non-parse failure"):
        _load_json(p, object())


# --------------------------------------------------------------------------- #
# wiki.core._frontmatter_and_body  (yaml.safe_load)
# --------------------------------------------------------------------------- #
def test_frontmatter_malformed_yaml_returns_empty_meta(tmp_path: Path):
    # Unclosed flow sequence -> yaml.YAMLError (ScannerError); meta falls back to {}.
    page = tmp_path / "page.md"
    page.write_text("---\nkey: [unclosed\n---\n\nbody text\n", encoding="utf-8")
    meta, body = _frontmatter_and_body(page)
    assert meta == {}
    assert "body text" in body


def test_frontmatter_valid_yaml_returns_meta(tmp_path: Path):
    page = tmp_path / "page.md"
    page.write_text("---\ntitle: Hi\nkind: source\n---\n\nhello\n", encoding="utf-8")
    meta, body = _frontmatter_and_body(page)
    assert meta.get("title") == "Hi"
    assert "hello" in body


def test_frontmatter_propagates_non_parse_exception(tmp_path: Path, monkeypatch):
    """RED before narrowing (broad except swallowed it), GREEN after."""
    page = tmp_path / "page.md"
    page.write_text("---\ntitle: Hi\n---\n\nhello\n", encoding="utf-8")
    monkeypatch.setattr(core.yaml, "safe_load", _boom)
    with pytest.raises(RuntimeError, match="non-parse failure"):
        _frontmatter_and_body(page)


# --------------------------------------------------------------------------- #
# wiki.explorer._read_manifest
# --------------------------------------------------------------------------- #
def test_read_manifest_returns_empty_on_corrupt_json(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / explorer.VAULT_MANIFEST).write_text("{ broken json ", encoding="utf-8")
    assert explorer._read_manifest(vault) == {}


def test_read_manifest_returns_empty_on_non_dict_json(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / explorer.VAULT_MANIFEST).write_text(json.dumps(["not", "a", "dict"]), encoding="utf-8")
    assert explorer._read_manifest(vault) == {}


def test_read_manifest_returns_payload_when_valid(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    payload = {"vault_slug": "s", "title": "T"}
    (vault / explorer.VAULT_MANIFEST).write_text(json.dumps(payload), encoding="utf-8")
    assert explorer._read_manifest(vault) == payload


def test_read_manifest_propagates_non_parse_exception(tmp_path: Path, monkeypatch):
    """RED before narrowing (broad except swallowed it), GREEN after."""
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / explorer.VAULT_MANIFEST).write_text("{}", encoding="utf-8")
    monkeypatch.setattr(explorer.json, "loads", _boom)
    with pytest.raises(RuntimeError, match="non-parse failure"):
        explorer._read_manifest(vault)

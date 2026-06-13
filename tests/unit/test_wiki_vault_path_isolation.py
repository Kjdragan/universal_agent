"""Per-topic vault isolation for `resolve_vault_path`.

Each KB/topic must resolve to its OWN vault directory so distinct ingests never
share one `sources/` tree or overwrite `vault_manifest.json`. The resolver nests
under `<root>/<slug>` — idempotently, so the CSI replay pattern (root_override
already pointing AT the slug dir) does not double-nest and orphan its pages.
"""

from __future__ import annotations

from pathlib import Path

from universal_agent.wiki import core as wiki_core
from universal_agent.wiki.core import _slugify, resolve_vault_path


def test_root_override_parent_nests_under_slug(tmp_path: Path) -> None:
    """The nightly path passes the PARENT dir → vault nests under <root>/<slug>."""
    got = resolve_vault_path("external", "topic-alpha", root_override=str(tmp_path))
    assert got == (tmp_path / "topic-alpha").resolve()


def test_distinct_slugs_get_distinct_dirs(tmp_path: Path) -> None:
    a = resolve_vault_path("external", "topic-alpha", root_override=str(tmp_path))
    b = resolve_vault_path("external", "topic-beta", root_override=str(tmp_path))
    assert a != b
    assert a.parent == b.parent == tmp_path.resolve()


def test_root_override_already_slug_does_not_double_nest(tmp_path: Path) -> None:
    """CSI pattern: root_override already ends with the slug → returned as-is."""
    csi_root = tmp_path / "knowledge-vaults" / "claude-code-intelligence"
    got = resolve_vault_path(
        "external", "claude-code-intelligence", root_override=str(csi_root)
    )
    assert got == csi_root.resolve()
    # And explicitly NOT nested a second level deep.
    assert got.name == "claude-code-intelligence"
    assert got.parent.name == "knowledge-vaults"


def test_no_override_uses_slug_under_shared_workspace(tmp_path: Path, monkeypatch) -> None:
    """No root_override → per-slug dir under the shared memory wiki root."""
    monkeypatch.setattr(
        wiki_core, "resolve_shared_memory_workspace", lambda *a, **k: str(tmp_path)
    )
    a = resolve_vault_path("external", "topic-alpha")
    b = resolve_vault_path("external", "topic-beta")
    assert a == tmp_path / "memory" / "wiki" / "topic-alpha"
    assert b == tmp_path / "memory" / "wiki" / "topic-beta"
    assert a != b


def test_vault_dir_name_is_the_slug(tmp_path: Path) -> None:
    """The nested dir name is exactly the slugified vault_slug."""
    got = resolve_vault_path("external", "Topic Alpha!", root_override=str(tmp_path))
    assert got.name == _slugify("Topic Alpha!", fallback="default")
    assert got.parent == tmp_path.resolve()

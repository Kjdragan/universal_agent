"""Read-only Wiki Vault Explorer backend (Spec B). Hermetic: a fixture vault is
built on disk and `_vault_roots` is monkeypatched to point at it — no LLM, no
real shared-memory/artifacts roots."""
from __future__ import annotations

import json
from pathlib import Path

import universal_agent.wiki.explorer as explorer


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _build_fixture_vault(root: Path, slug: str = "demo") -> Path:
    vault = root / slug
    _write(
        vault / "vault_manifest.json",
        json.dumps({"vault_kind": "external", "vault_slug": slug, "title": "Demo Vault",
                    "created_at": "2026-06-15T00:00:00+00:00", "updated_at": "2026-06-15T08:00:00+00:00"}),
    )
    _write(
        vault / "sources" / "report.md",
        "---\ntitle: The Report\nkind: source\nsummary: A report.\ntags: []\n"
        "source_ids: [s1]\nprovenance_kind: external_ingest\nprovenance_refs: []\n"
        "confidence: medium\nstatus: active\n---\n\n"
        "Body.\n\n## Entities\n\n- [[entities/glm-5.md|GLM-5]]\n",
    )
    _write(
        vault / "entities" / "glm-5.md",
        "---\ntitle: GLM-5\nkind: entity\nsummary: A model.\ntags: [entity]\n"
        "source_ids: [s1]\nprovenance_kind: memex_create\nprovenance_refs: [s1]\n"
        "confidence: medium\nstatus: active\n---\n\n"
        "# GLM-5\n\n## Sources\n\n- [[sources/report.md|The Report]]\n",
    )
    return vault


def test_list_and_detail_and_page(tmp_path, monkeypatch):
    root = tmp_path / "wiki"
    _build_fixture_vault(root)
    monkeypatch.setattr(explorer, "_vault_roots", lambda: [root])

    vaults = explorer.list_vaults()
    assert len(vaults) == 1
    v = vaults[0]
    assert v["slug"] == "demo"
    assert v["source_count"] == 1
    assert v["entity_count"] == 1

    detail = explorer.vault_detail("demo")
    assert detail is not None
    ids = {n["id"] for n in detail["graph"]["nodes"]}
    assert {"sources/report.md", "entities/glm-5.md"} <= ids
    # both-direction wikilink edges resolved
    edges = {(e["source"], e["target"]) for e in detail["graph"]["edges"]}
    assert ("sources/report.md", "entities/glm-5.md") in edges
    assert ("entities/glm-5.md", "sources/report.md") in edges

    page = explorer.read_vault_page("demo", "entities/glm-5.md")
    assert page is not None
    assert page["title"] == "GLM-5"
    assert page["kind"] == "entity"
    # the source page backlinks the entity page
    assert any(b["path"] == "sources/report.md" for b in page["backlinks"])


def test_unknown_vault_and_path_traversal(tmp_path, monkeypatch):
    root = tmp_path / "wiki"
    _build_fixture_vault(root)
    monkeypatch.setattr(explorer, "_vault_roots", lambda: [root])

    assert explorer.vault_detail("nope") is None
    assert explorer.read_vault_page("demo", "../../etc/passwd") is None
    assert explorer.read_vault_page("demo", "does/not/exist.md") is None

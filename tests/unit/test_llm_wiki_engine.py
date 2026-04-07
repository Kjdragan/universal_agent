from __future__ import annotations

import json
from pathlib import Path

from universal_agent.wiki import core


def test_external_wiki_roundtrip(tmp_path):
    source = tmp_path / "source.md"
    source.write_text(
        "# Example\n\nOpenAI Codex helps maintain knowledge bases and project memory.\n",
        encoding="utf-8",
    )

    init_ctx = core.ensure_vault("external", "research-vault", root_override=str(tmp_path / "vaults"))
    result = core.ingest_external_source(
        vault_slug="research-vault",
        source_path=str(source),
        title="Example Source",
        root_override=str(tmp_path / "vaults"),
    )

    assert init_ctx.path.exists()
    assert result["source_page"].startswith("sources/")
    assert (init_ctx.path / "index.md").exists()
    assert (init_ctx.path / "log.md").exists()

    query = core.query_vault(
        vault_kind="external",
        vault_slug="research-vault",
        query="Codex knowledge memory",
        save_answer=True,
        answer_title="Codex Analysis",
        root_override=str(tmp_path / "vaults"),
    )
    assert query["matches"]
    assert query["saved_analysis_path"].startswith("analyses/")
    source_page = init_ctx.path / result["source_page"]
    source_text = source_page.read_text(encoding="utf-8")
    assert "## Related Analyses" in source_text
    assert "Codex Analysis" in source_text


def test_internal_memory_sync_creates_projection_without_mutating_sources(tmp_path, monkeypatch):
    shared = tmp_path / "shared"
    (shared / "memory" / "sessions").mkdir(parents=True)
    (shared / "MEMORY.md").write_text("# Agent Memory\n\nPersistent context.\n", encoding="utf-8")
    daily = shared / "memory" / "2026-04-06.md"
    daily.write_text("## 2026-04-06T00:00:00Z — memory\n- summary: decided to use the wiki\n", encoding="utf-8")
    session_file = shared / "memory" / "sessions" / "sess_2026-04-06.md"
    session_file.write_text("## 2026-04-06T00:00:00Z — session\n- tags: prefer, memory\n", encoding="utf-8")

    repo_root = tmp_path / "repo"
    checkpoint_dir = repo_root / "AGENT_RUN_WORKSPACES" / "run_example"
    checkpoint_dir.mkdir(parents=True)
    checkpoint_path = checkpoint_dir / "run_checkpoint.json"
    checkpoint_path.write_text(
        json.dumps(
            {
                "original_request": "Build the wiki system",
                "key_decisions": ["Decided to keep checkpoints canonical"],
                "failed_approaches": ["Failed to treat wiki as runtime state"],
            }
        ),
        encoding="utf-8",
    )

    original_memory = daily.read_text(encoding="utf-8")
    original_checkpoint = checkpoint_path.read_text(encoding="utf-8")

    monkeypatch.setattr(core, "resolve_shared_memory_workspace", lambda: str(shared))
    monkeypatch.setattr(core, "_repo_root", lambda: repo_root)

    result = core.sync_internal_memory_vault(
        vault_slug="internal-memory",
        trigger="test",
        root_override=str(tmp_path / "internal_vault"),
    )

    assert result["generated_pages"]
    assert (tmp_path / "internal_vault" / "decisions" / "decision-ledger.md").exists()
    assert "timings_ms" in result
    assert "total_duration_ms" in result
    assert "copied_counts" in result
    assert "skipped_counts" in result
    assert (tmp_path / "internal_vault" / "sync_state.json").exists()
    assert (tmp_path / "internal_vault" / "sync_progress.json").exists()
    assert daily.read_text(encoding="utf-8") == original_memory
    assert checkpoint_path.read_text(encoding="utf-8") == original_checkpoint


def test_wiki_lint_reports_broken_wikilinks(tmp_path):
    context = core.ensure_vault("external", "lint-vault", root_override=str(tmp_path / "vaults"))
    page = context.path / "analyses" / "broken.md"
    page.write_text(
        "---\n"
        "title: Broken Page\n"
        "kind: analysis\n"
        "updated_at: 2026-04-06T00:00:00Z\n"
        "tags: [analysis]\n"
        "source_ids: [src1]\n"
        "provenance_kind: query_result\n"
        "provenance_refs: [sources/example.md]\n"
        "confidence: medium\n"
        "status: active\n"
        "---\n\n"
        "# Broken Page\n\n"
        "See [[Missing Page]].\n",
        encoding="utf-8",
    )
    core.update_index(context.path)

    result = core.lint_vault(vault_kind="external", vault_slug="lint-vault", root_override=str(tmp_path / "vaults"))

    assert result["finding_count"] >= 1
    assert any(finding["kind"] == "broken_wikilink" for finding in result["findings"])

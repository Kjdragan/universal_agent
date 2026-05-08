"""Tests for the LLM-driven Memex pass wired into ClaudeDevs replay (Phase F).

Replaces the prior PR 15 regex-extractor tests. The new ``apply_memex_pass``
runs ``services.csi_intelligence_pass.analyze_action`` (LLM call, mocked
here) per tier-2+ action, then ``services.csi_intelligence_persistence
.apply_vault_delta_to_vault`` (deterministic). These tests mock the LLM
call to return fixture VaultDeltas and verify the orchestration:

  - Tier filter: actions below ``min_tier`` are skipped (no LLM call).
  - Linked sources are read from disk per ``source_path`` and passed to
    the LLM as text.
  - Existing vault entity slugs are read once and refreshed in-batch as
    new pages are created (so subsequent LLM calls see live state).
  - The packet_id is propagated to the persistence layer.
  - Per-action LLM failures are surfaced as ``action="ERROR"`` records
    without raising or sinking sibling actions.
  - ``ingest_packet_into_external_vault`` honors the
    ``UA_CSI_MEMEX_WIRING_ENABLED=0`` kill switch.

The granular Phase A schema + Phase E persistence behaviors have their own
test files (``test_csi_intelligence_pass.py`` /
``test_csi_intelligence_persistence.py``) — this file is purely about the
replay-level wiring.
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from universal_agent.services import claude_code_intel_replay
from universal_agent.services.claude_code_intel_replay import (
    apply_memex_pass,
    ingest_packet_into_external_vault,
)
from universal_agent.services.csi_intelligence_pass import (
    VaultAction,
    VaultDelta,
    VaultRelation,
)
from universal_agent.wiki.core import ensure_vault

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    """A clean external vault under tmp_path."""
    ctx = ensure_vault(
        "external",
        "csi-replay-test",
        title="CSI replay test vault",
        root_override=str(tmp_path),
    )
    return ctx.path


def _action(post_id: str, text: str, tier: int = 2, **extra) -> dict:
    out = {
        "post_id": post_id,
        "text": text,
        "tier": tier,
        "action_type": "release_announcement",
        "url": f"https://x.com/ClaudeDevs/status/{post_id}",
    }
    out.update(extra)
    return out


def _linked_entry(
    post_id: str,
    *,
    source_path: str,
    fetch_status: str = "fetched",
    title: str = "Doc",
    url: str = "https://platform.claude.com/docs",
) -> dict:
    return {
        "post_id": post_id,
        "url": url,
        "source_path": source_path,
        "fetch_status": fetch_status,
        "title": title,
    }


def _delta(*actions: VaultAction, post_summary: str = "", relations=None) -> VaultDelta:
    return VaultDelta(
        vault_actions=list(actions),
        relations=list(relations or []),
        post_summary=post_summary,
    )


# ---------------------------------------------------------------------------
# Tier filtering
# ---------------------------------------------------------------------------


class TestTierFilter:
    def test_tier_1_actions_are_skipped(self, vault: Path):
        actions = [
            _action("100", "Tier-1 promotional. Live now.", tier=1),
            _action("200", "Tier-2 substantive content.", tier=2),
        ]
        analyzed_post_ids: list[str] = []

        def fake_analyze(action, **_):
            analyzed_post_ids.append(action["post_id"])
            return _delta(VaultAction(
                op="create", kind="product", name=f"Entity for {action['post_id']}",
                summary=".", confidence="medium",
            ))

        with mock.patch(
            "universal_agent.services.csi_intelligence_pass.analyze_action",
            side_effect=fake_analyze
        ):
            results = apply_memex_pass(
                vault_path=vault,
                handle="ClaudeDevs",
                actions=actions,
                linked_source_entries=[],
            )

        # Only tier-2 action got an LLM call
        assert analyzed_post_ids == ["200"]
        # The result list contains only the tier-2 entity record
        post_ids_in_results = {r.get("post_id") for r in results}
        assert post_ids_in_results == {"200"}

    def test_min_tier_override(self, vault: Path):
        actions = [
            _action("a", "tier-2", tier=2),
            _action("b", "tier-3", tier=3),
        ]
        called_with: list[dict] = []

        def fake_analyze(action, **_):
            called_with.append(action)
            return _delta()  # empty — no vault writes

        with mock.patch(
            "universal_agent.services.csi_intelligence_pass.analyze_action",
            side_effect=fake_analyze
        ):
            apply_memex_pass(
                vault_path=vault, handle="ClaudeDevs",
                actions=actions, linked_source_entries=[],
                min_tier=3,
            )
        assert [a["post_id"] for a in called_with] == ["b"]


# ---------------------------------------------------------------------------
# Linked-source plumbing (read from disk, dedupe, pass to LLM as text)
# ---------------------------------------------------------------------------


class TestLinkedSourceWiring:
    def test_linked_sources_read_from_disk_and_passed_per_post(
        self, vault: Path, tmp_path: Path
    ):
        # Create two source files for two different posts
        src_a = tmp_path / "source_a.md"
        src_a.write_text("Body A — Anthropic docs about prompt caching.")
        src_b = tmp_path / "source_b.md"
        src_b.write_text("Body B — different Anthropic doc.")

        actions = [
            _action("aaa", "Post A about caching.", tier=2),
            _action("bbb", "Post B about something else.", tier=2),
        ]
        linked = [
            _linked_entry("aaa", source_path=str(src_a)),
            _linked_entry("bbb", source_path=str(src_b)),
            _linked_entry("aaa", source_path=str(src_b)),  # extra cross-post
            # An entry that didn't actually fetch successfully — should be skipped
            _linked_entry("aaa", source_path="/nonexistent/file.md", fetch_status="failed"),
        ]

        captured: dict[str, list[str]] = {}

        def fake_analyze(action, *, linked_sources, **_):
            captured[action["post_id"]] = list(linked_sources)
            return _delta()

        with mock.patch(
            "universal_agent.services.csi_intelligence_pass.analyze_action",
            side_effect=fake_analyze
        ):
            apply_memex_pass(
                vault_path=vault, handle="ClaudeDevs",
                actions=actions, linked_source_entries=linked,
            )

        # Post aaa got Body A first; the cross-post entry pointing at source_b
        # also gets attached because they're indexed by post_id.
        assert any("Body A" in t for t in captured["aaa"])
        assert any("Body B" in t for t in captured["aaa"])
        # Post bbb got only Body B
        assert any("Body B" in t for t in captured["bbb"])
        # The failed-fetch entry never made it into either list
        assert all("/nonexistent" not in t for t in captured["aaa"])

    def test_dedup_by_source_path(self, vault: Path, tmp_path: Path):
        """Same source file referenced twice for one post should appear once."""
        src = tmp_path / "src.md"
        src.write_text("the body text")
        actions = [_action("p1", "post text", tier=2)]
        linked = [
            _linked_entry("p1", source_path=str(src)),
            _linked_entry("p1", source_path=str(src)),  # duplicate
        ]

        captured: list[list[str]] = []

        def fake_analyze(action, *, linked_sources, **_):
            captured.append(list(linked_sources))
            return _delta()

        with mock.patch(
            "universal_agent.services.csi_intelligence_pass.analyze_action",
            side_effect=fake_analyze
        ):
            apply_memex_pass(
                vault_path=vault, handle="ClaudeDevs",
                actions=actions, linked_source_entries=linked,
            )
        assert len(captured) == 1
        assert len(captured[0]) == 1  # deduplicated


# ---------------------------------------------------------------------------
# Existing-entities refresh — newly-created slugs visible to next LLM call
# ---------------------------------------------------------------------------


class TestExistingEntitiesRefresh:
    def test_action2_sees_action1_created_slug(self, vault: Path):
        """When action #1 creates an entity, action #2's LLM call should
        receive that new slug in existing_vault_entities. (Otherwise the
        LLM might emit a duplicate CREATE that the persistence layer has
        to auto-downgrade.)"""
        actions = [
            _action("p1", "First post.", tier=2),
            _action("p2", "Second post.", tier=2),
        ]
        seen_slugs: list[set[str]] = []

        def fake_analyze(action, *, existing_vault_entities, **_):
            seen_slugs.append(set(existing_vault_entities))
            return _delta(VaultAction(
                op="create", kind="product",
                name=f"Entity {action['post_id']}",
                summary=".", confidence="medium",
            ))

        with mock.patch(
            "universal_agent.services.csi_intelligence_pass.analyze_action",
            side_effect=fake_analyze
        ):
            apply_memex_pass(
                vault_path=vault, handle="ClaudeDevs",
                actions=actions, linked_source_entries=[],
            )

        # First call sees an empty vault
        assert "entity-p1" not in seen_slugs[0]
        # Second call sees the slug created by the first action
        assert "entity-p1" in seen_slugs[1]


# ---------------------------------------------------------------------------
# Persistence integration — VaultDelta ends up as files on disk
# ---------------------------------------------------------------------------


class TestPersistenceIntegration:
    def test_vault_delta_creates_entity_pages(self, vault: Path):
        actions = [_action("100", "Multi-feature release.", tier=3)]

        def fake_analyze(action, **_):
            return _delta(
                VaultAction(
                    op="create", kind="product", name="Claude Managed Agents",
                    summary="Hosted agent runtime.",
                    source_post_ids=[action["post_id"]],
                    confidence="high",
                ),
                VaultAction(
                    op="create", kind="feature", name="Outcomes Loop",
                    summary="Rubric-driven self-improvement.",
                    source_post_ids=[action["post_id"]],
                    confidence="high",
                ),
                relations=[
                    VaultRelation(
                        from_slug="outcomes-loop",
                        to_slug="claude-managed-agents",
                        kind="feature-of",
                    )
                ],
                post_summary="Announce managed agents capabilities.",
            )

        with mock.patch(
            "universal_agent.services.csi_intelligence_pass.analyze_action",
            side_effect=fake_analyze
        ):
            results = apply_memex_pass(
                vault_path=vault, handle="ClaudeDevs",
                actions=actions, linked_source_entries=[],
                packet_id="2026-05-08/test-packet",
            )

        # Files on disk
        assert (vault / "entities" / "claude-managed-agents.md").is_file()
        assert (vault / "entities" / "outcomes-loop.md").is_file()
        assert (vault / "relations.jsonl").is_file()
        assert (vault / "posts.jsonl").is_file()

        # log.md tracks both creations
        log_md = (vault / "log.md").read_text()
        assert "entities/claude-managed-agents.md CREATE" in log_md
        assert "entities/outcomes-loop.md CREATE" in log_md

        # Result records preserve legacy shape
        ops = [r["action"] for r in results]
        assert ops.count("CREATE") == 2
        names = {r["entity_name"] for r in results}
        assert names == {"Claude Managed Agents", "Outcomes Loop"}
        for r in results:
            assert r["post_id"] == "100"
            assert "page_path" in r
            assert "memex_kind" in r
            assert r["memex_kind"] == "entity"  # product/feature both → entity
            assert "log_note" in r

    def test_packet_id_reaches_frontmatter_tags(self, vault: Path):
        """packet_id should propagate into the page's frontmatter as a tag."""
        def fake_analyze(action, **_):
            return _delta(VaultAction(
                op="create", kind="concept", name="Some Concept",
                summary=".", confidence="medium",
            ))

        with mock.patch(
            "universal_agent.services.csi_intelligence_pass.analyze_action",
            side_effect=fake_analyze
        ):
            apply_memex_pass(
                vault_path=vault, handle="ClaudeDevs",
                actions=[_action("1", "x", tier=2)],
                linked_source_entries=[],
                packet_id="2026-05-08/abc__bcherny",
            )
        page = (vault / "concepts" / "some-concept.md").read_text()
        assert "packet:2026-05-08/abc__bcherny" in page


# ---------------------------------------------------------------------------
# Error handling — per-action failures don't sink the batch
# ---------------------------------------------------------------------------


class TestErrorIsolation:
    def test_analyze_action_failure_surfaces_as_error_record(self, vault: Path):
        actions = [
            _action("ok", "First action — fine.", tier=2),
            _action("bad", "Second action — LLM raises.", tier=2),
            _action("ok2", "Third action — also fine.", tier=2),
        ]
        call_count = {"n": 0}

        def fake_analyze(action, **_):
            call_count["n"] += 1
            if action["post_id"] == "bad":
                raise RuntimeError("simulated LLM failure")
            return _delta(VaultAction(
                op="create", kind="product", name=f"Entity {action['post_id']}",
                summary=".", confidence="medium",
            ))

        with mock.patch(
            "universal_agent.services.csi_intelligence_pass.analyze_action",
            side_effect=fake_analyze
        ):
            results = apply_memex_pass(
                vault_path=vault, handle="ClaudeDevs",
                actions=actions, linked_source_entries=[],
            )

        # All three actions were attempted (no early exit on the failure)
        assert call_count["n"] == 3

        ops = [r["action"] for r in results]
        assert ops.count("ERROR") == 1
        assert ops.count("CREATE") == 2

        err = next(r for r in results if r["action"] == "ERROR")
        assert err["post_id"] == "bad"
        assert err["stage"] == "analyze_action"
        assert "RuntimeError" in err["error_type"]
        assert "simulated LLM failure" in err["error"]


# ---------------------------------------------------------------------------
# UA_CSI_MEMEX_WIRING_ENABLED kill switch
# ---------------------------------------------------------------------------


class TestKillSwitch:
    def test_disabled_via_env_var(
        self, vault: Path, tmp_path: Path, monkeypatch
    ):
        # Build the minimal fixture ingest_packet_into_external_vault expects
        packet_dir = tmp_path / "2026-05-08" / "abc__ClaudeDevs"
        packet_dir.mkdir(parents=True)
        (packet_dir / "manifest.json").write_text("{}")

        analyze_called = {"n": 0}

        def fake_analyze(action, **_):
            analyze_called["n"] += 1
            return _delta()

        monkeypatch.setenv("UA_CSI_MEMEX_WIRING_ENABLED", "0")

        with mock.patch(
            "universal_agent.services.csi_intelligence_pass.analyze_action",
            side_effect=fake_analyze
        ):
            result = ingest_packet_into_external_vault(
                packet_dir=packet_dir,
                handle="ClaudeDevs",
                posts=[],
                actions=[_action("1", "tier-2 content", tier=2)],
                linked_source_entries=[],
                artifacts_root=tmp_path,
                work_product_dir=None,
                enabled=True,
            )

        # Memex pass should be entirely skipped
        assert analyze_called["n"] == 0
        assert result["memex_actions"] == []

    def test_enabled_by_default_runs_memex(
        self, vault: Path, tmp_path: Path, monkeypatch
    ):
        packet_dir = tmp_path / "2026-05-08" / "abc__ClaudeDevs"
        packet_dir.mkdir(parents=True)
        (packet_dir / "manifest.json").write_text("{}")

        analyze_called = {"n": 0}

        def fake_analyze(action, **_):
            analyze_called["n"] += 1
            return _delta(VaultAction(
                op="create", kind="product", name="Test Entity",
                summary=".", confidence="medium",
            ))

        # No env var override — wiring defaults to enabled
        monkeypatch.delenv("UA_CSI_MEMEX_WIRING_ENABLED", raising=False)

        with mock.patch(
            "universal_agent.services.csi_intelligence_pass.analyze_action",
            side_effect=fake_analyze
        ):
            result = ingest_packet_into_external_vault(
                packet_dir=packet_dir,
                handle="ClaudeDevs",
                posts=[],
                actions=[_action("1", "content", tier=2)],
                linked_source_entries=[],
                artifacts_root=tmp_path,
                work_product_dir=None,
                enabled=True,
            )
        assert analyze_called["n"] == 1
        assert len(result["memex_actions"]) == 1
        assert result["memex_actions"][0]["action"] == "CREATE"


# ---------------------------------------------------------------------------
# Module-level smoke — package imports cleanly post-swap
# ---------------------------------------------------------------------------


def test_module_no_longer_has_deleted_regex_helpers():
    """Ensure the deleted regex helpers don't have lingering references."""
    for name in (
        "_MEMEX_TERM_PATTERN",
        "_MEMEX_TERM_STOPWORDS",
        "_memex_candidates_for_action",
        "_memex_body_for_create",
        "_memex_body_for_extend",
    ):
        assert not hasattr(claude_code_intel_replay, name), (
            f"{name!r} should have been deleted in Phase F but still exists"
        )

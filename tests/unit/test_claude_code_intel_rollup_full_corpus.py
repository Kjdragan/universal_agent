"""Tests for the full-corpus capability library (PR 5).

Verifies that:
- Old packets (>28 days) are excluded from the windowed brief but
  included in the full-corpus library.
- The library index.json carries source_mode = full_corpus by default.
- Setting UA_CSI_LIBRARY_FULL_CORPUS=0 reverts to v1 behavior
  (library bundles match brief bundles, source_mode = windowed).
- _load_action_contexts(window_days=None) returns ALL contexts.
"""

from __future__ import annotations

import importlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


def _make_packet_dir(
    artifacts_root: Path,
    *,
    date: str,
    stamp: str,
    post_id: str,
    tier: int = 3,
) -> Path:
    packet_dir = artifacts_root / "proactive" / "claude_code_intel" / "packets" / date / f"{stamp}__ClaudeDevs"
    packet_dir.mkdir(parents=True, exist_ok=True)
    (packet_dir / "manifest.json").write_text(
        json.dumps(
            {
                "generated_at": f"{date}T{stamp[:2]}:{stamp[2:4]}:{stamp[4:]}+00:00",
                "handle": "ClaudeDevs",
                "new_post_count": 1,
                "action_count": 1,
                "ok": True,
            }
        ),
        encoding="utf-8",
    )
    (packet_dir / "actions.json").write_text(
        json.dumps(
            [
                {
                    "post_id": post_id,
                    "tier": tier,
                    "action_type": "demo_task",
                    "url": f"https://x.com/ClaudeDevs/status/{post_id}",
                    "text": f"Tweet {post_id} announcing a Claude Code feature.",
                    "links": [f"https://docs.anthropic.com/feature-{post_id}"],
                    "classifier": {"reasoning": "Reusable for agent systems."},
                }
            ]
        ),
        encoding="utf-8",
    )
    return packet_dir


@pytest.fixture
def fresh_rollup(monkeypatch):
    def _reload(env: dict[str, str] | None = None):
        for key, value in (env or {}).items():
            monkeypatch.setenv(key, value)
        from universal_agent.services import claude_code_intel_rollup

        importlib.reload(claude_code_intel_rollup)
        return claude_code_intel_rollup

    return _reload


def test_load_action_contexts_with_no_window_returns_old_packets(
    fresh_rollup,
    tmp_path: Path,
):
    mod = fresh_rollup()
    artifacts_root = tmp_path / "artifacts"
    # One ancient packet (~120 days old), one recent (~5 days old).
    today = datetime.now(timezone.utc)
    old = today - timedelta(days=120)
    recent = today - timedelta(days=5)
    _make_packet_dir(
        artifacts_root,
        date=old.strftime("%Y-%m-%d"),
        stamp=old.strftime("%H%M%S"),
        post_id="old1",
    )
    _make_packet_dir(
        artifacts_root,
        date=recent.strftime("%Y-%m-%d"),
        stamp=recent.strftime("%H%M%S"),
        post_id="new1",
    )

    windowed = mod._load_action_contexts(artifacts_root=artifacts_root, window_days=28)
    full = mod._load_action_contexts(artifacts_root=artifacts_root, window_days=None)

    windowed_ids = {c["post_id"] for c in windowed}
    full_ids = {c["post_id"] for c in full}

    assert "new1" in windowed_ids
    assert "old1" not in windowed_ids, "120-day-old packet must not appear in 28-day window"
    assert {"new1", "old1"}.issubset(full_ids), "full corpus must include the old packet"


def test_library_index_records_full_corpus_source_mode_by_default(
    fresh_rollup,
    monkeypatch,
    tmp_path: Path,
):
    mod = fresh_rollup()
    artifacts_root = tmp_path / "artifacts"
    today = datetime.now(timezone.utc)
    recent = today - timedelta(days=5)
    _make_packet_dir(
        artifacts_root,
        date=recent.strftime("%Y-%m-%d"),
        stamp=recent.strftime("%H%M%S"),
        post_id="new1",
    )

    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(mod, "_repo_root", lambda: repo_root)
    monkeypatch.setattr(mod, "_has_llm_key", lambda: False)

    mod.build_rolling_assets(artifacts_root=artifacts_root)

    library_index = repo_root / "agent_capability_library" / "claude_code_intel" / "current" / "index.json"
    assert library_index.exists()
    payload = json.loads(library_index.read_text(encoding="utf-8"))
    assert payload.get("source_mode") == "full_corpus"
    assert int(payload.get("source_action_count") or 0) >= 1


def test_library_index_records_windowed_when_full_corpus_disabled(
    fresh_rollup,
    monkeypatch,
    tmp_path: Path,
):
    mod = fresh_rollup({"UA_CSI_LIBRARY_FULL_CORPUS": "0"})
    artifacts_root = tmp_path / "artifacts"
    today = datetime.now(timezone.utc)
    recent = today - timedelta(days=5)
    _make_packet_dir(
        artifacts_root,
        date=recent.strftime("%Y-%m-%d"),
        stamp=recent.strftime("%H%M%S"),
        post_id="new1",
    )

    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(mod, "_repo_root", lambda: repo_root)
    monkeypatch.setattr(mod, "_has_llm_key", lambda: False)

    mod.build_rolling_assets(artifacts_root=artifacts_root)

    library_index = repo_root / "agent_capability_library" / "claude_code_intel" / "current" / "index.json"
    payload = json.loads(library_index.read_text(encoding="utf-8"))
    assert payload.get("source_mode") == "windowed"


def test_full_corpus_picks_up_old_packets_in_library(
    fresh_rollup,
    monkeypatch,
    tmp_path: Path,
):
    """Old packets must show up in library bundles even though the brief excludes them."""
    mod = fresh_rollup()
    artifacts_root = tmp_path / "artifacts"
    today = datetime.now(timezone.utc)
    old = today - timedelta(days=120)
    recent = today - timedelta(days=5)
    _make_packet_dir(
        artifacts_root,
        date=old.strftime("%Y-%m-%d"),
        stamp=old.strftime("%H%M%S"),
        post_id="ancient",
        tier=3,
    )
    _make_packet_dir(
        artifacts_root,
        date=recent.strftime("%Y-%m-%d"),
        stamp=recent.strftime("%H%M%S"),
        post_id="newone",
        tier=3,
    )

    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(mod, "_repo_root", lambda: repo_root)
    monkeypatch.setattr(mod, "_has_llm_key", lambda: False)

    payload = mod.build_rolling_assets(artifacts_root=artifacts_root)

    # source_action_count for the brief reflects windowed loading (1 packet).
    assert payload.get("source_action_count") == 1
    # library_source_action_count reflects full corpus (2 packets).
    assert payload.get("library_source_action_count") == 2
    assert payload.get("library_source_mode") == "full_corpus"

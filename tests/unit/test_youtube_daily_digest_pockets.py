from __future__ import annotations

import json
from pathlib import Path

from universal_agent.scripts import youtube_daily_digest


def test_save_repopulate_pocket_records_processed_videos(monkeypatch, tmp_path):
    monkeypatch.setenv("UA_WORKSPACES_DIR", str(tmp_path))
    artifact_path = tmp_path / "daily_digests" / "2026-04-30_MONDAY_Digest.md"

    path = youtube_daily_digest._save_repopulate_pocket(
        day_name="MONDAY",
        date_str="2026-04-30",
        playlist_id="PLdemo",
        artifact_path=artifact_path,
        dry_run=False,
        items=[
            {"video_id": "abc123", "title": "First", "playlist_item_id": "pli-1"},
            {"video_id": "def456", "title": "Second", "playlist_item_id": "pli-2"},
        ],
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert path == tmp_path / "daily_digests" / "repopulate_pockets" / "MONDAY" / "2026-04-30_MONDAY_playlist_pocket.json"
    assert payload["cleanup_mode"] == "delete_after_digest"
    assert payload["video_count"] == 2
    assert payload["videos"] == [
        {"video_id": "abc123", "title": "First", "original_playlist_item_id": "pli-1"},
        {"video_id": "def456", "title": "Second", "original_playlist_item_id": "pli-2"},
    ]


def test_repopulate_digest_playlist_uses_latest_pocket_and_skips_existing(monkeypatch, tmp_path):
    monkeypatch.setenv("UA_WORKSPACES_DIR", str(tmp_path))
    monkeypatch.setenv("MONDAY_YT_PLAYLIST", "PLdemo")
    monkeypatch.setattr(youtube_daily_digest, "initialize_runtime_secrets", lambda: None)
    monkeypatch.setattr(
        youtube_daily_digest,
        "get_playlist_items",
        lambda playlist_id: [{"video_id": "already-there", "title": "Existing", "playlist_item_id": "pli-old"}],
    )
    added: list[tuple[str, str]] = []
    monkeypatch.setattr(
        youtube_daily_digest,
        "add_playlist_item",
        lambda playlist_id, video_id: added.append((playlist_id, video_id)) or {"id": f"new-{video_id}"},
    )

    day_dir = tmp_path / "daily_digests" / "repopulate_pockets" / "MONDAY"
    day_dir.mkdir(parents=True)
    (day_dir / "2026-04-29_MONDAY_playlist_pocket.json").write_text(
        json.dumps({"date": "2026-04-29", "videos": [{"video_id": "old-video"}]}),
        encoding="utf-8",
    )
    (day_dir / "2026-04-30_MONDAY_playlist_pocket.json").write_text(
        json.dumps(
            {
                "date": "2026-04-30",
                "videos": [
                    {"video_id": "already-there", "title": "Existing"},
                    {"video_id": "new-video", "title": "New"},
                ],
            }
        ),
        encoding="utf-8",
    )

    result = youtube_daily_digest.repopulate_digest_playlist(day_override="MONDAY")

    assert added == [("PLdemo", "new-video")]
    assert result["date"] == "2026-04-30"
    assert result["requested"] == 2
    assert result["added"] == 1
    assert result["skipped_existing"] == 1
    assert result["added_video_ids"] == ["new-video"]
    assert result["skipped_existing_video_ids"] == ["already-there"]


def test_repopulate_digest_playlist_dry_run_does_not_add(monkeypatch, tmp_path):
    monkeypatch.setenv("UA_WORKSPACES_DIR", str(tmp_path))
    monkeypatch.setenv("MONDAY_YT_PLAYLIST", "PLdemo")
    monkeypatch.setattr(youtube_daily_digest, "initialize_runtime_secrets", lambda: None)
    monkeypatch.setattr(youtube_daily_digest, "get_playlist_items", lambda playlist_id: [])

    def fail_add(_playlist_id: str, _video_id: str):
        raise AssertionError("dry-run must not add playlist items")

    monkeypatch.setattr(youtube_daily_digest, "add_playlist_item", fail_add)
    path = youtube_daily_digest._pocket_path(day_name="MONDAY", date_str="2026-04-30")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"date": "2026-04-30", "videos": [{"video_id": "new-video"}]}), encoding="utf-8")

    result = youtube_daily_digest.repopulate_digest_playlist(
        day_override="MONDAY",
        date_override="2026-04-30",
        dry_run=True,
    )

    assert result["dry_run"] is True
    assert result["added"] == 1
    assert result["added_video_ids"] == ["new-video"]


def test_digest_decisions_select_only_code_implementation_candidates():
    digest = """
Digest text.

```youtube_digest_decisions
{
  "ranked_videos": [
    {
      "rank": 1,
      "video_id": "concept",
      "title": "Concept Video",
      "value_score": 99,
      "value_tier": "high",
      "code_implementation_prospect": false,
      "concept_only": true,
      "tutorial_candidate": false,
      "recommended_tutorial_mode": "concept_only",
      "evidence_quality": "transcript",
      "reason": "High value concept, no runnable implementation."
    },
    {
      "rank": 2,
      "video_id": "code-low",
      "title": "Code Low",
      "value_score": 70,
      "value_tier": "medium",
      "code_implementation_prospect": true,
      "concept_only": false,
      "tutorial_candidate": true,
      "recommended_tutorial_mode": "explainer_plus_code",
      "evidence_quality": "transcript",
      "reason": "Shows concrete implementation steps."
    },
    {
      "rank": 3,
      "video_id": "code-high",
      "title": "Code High",
      "value_score": 95,
      "value_tier": "high",
      "code_implementation_prospect": true,
      "concept_only": false,
      "tutorial_candidate": true,
      "recommended_tutorial_mode": "explainer_plus_code",
      "evidence_quality": "transcript",
      "reason": "Builds a runnable project."
    }
  ]
}
```
"""

    decisions = youtube_daily_digest._rank_digest_decisions(
        youtube_daily_digest._extract_decision_json(digest),
        [
            {"video_id": "concept", "title": "Concept Video"},
            {"video_id": "code-low", "title": "Code Low"},
            {"video_id": "code-high", "title": "Code High"},
        ],
    )
    selected = youtube_daily_digest._select_tutorial_dispatch_candidates(decisions, top_n=4)

    assert [row["video_id"] for row in decisions["ranked_videos"]] == ["concept", "code-high", "code-low"]
    assert [row["video_id"] for row in selected] == ["code-high", "code-low"]
    assert all(row["recommended_tutorial_mode"] == "explainer_plus_code" for row in selected)


def test_digest_decisions_dispatch_code_prospects_even_without_secondary_tutorial_flag():
    decisions = youtube_daily_digest._rank_digest_decisions(
        {
            "ranked_videos": [
                {
                    "video_id": "code",
                    "title": "Code Prospect",
                    "value_score": 80,
                    "code_implementation_prospect": True,
                    "concept_only": False,
                    "tutorial_candidate": False,
                    "recommended_tutorial_mode": "none",
                }
            ]
        },
        [{"video_id": "code", "title": "Code Prospect"}],
    )

    selected = youtube_daily_digest._select_tutorial_dispatch_candidates(decisions, top_n=4)

    assert selected[0]["video_id"] == "code"
    assert selected[0]["tutorial_candidate"] is True
    assert selected[0]["recommended_tutorial_mode"] == "explainer_plus_code"


def test_digest_dispatch_dry_run_does_not_call_gateway(tmp_path):
    selected = [{"video_id": "abc123", "title": "Code", "rank": 1, "value_score": 90}]

    results = youtube_daily_digest._dispatch_tutorial_candidates(
        selected=selected,
        day_name="MONDAY",
        date_str="2026-04-30",
        digest_artifact_path=tmp_path / "digest.md",
        candidates_artifact_path=tmp_path / "candidates.json",
        dry_run=True,
    )

    assert results == [{"ok": True, "reason": "dry_run", "video_id": "abc123"}]

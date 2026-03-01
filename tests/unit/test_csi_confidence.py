from __future__ import annotations

from universal_agent.csi_confidence import heuristic_confidence, score_event_confidence


def test_score_event_confidence_falls_back_to_heuristic_when_sparse():
    source_mix = {"rss": 1}
    heuristic = heuristic_confidence(event_type="rss_insight_emerging", events_count=1, source_mix=source_mix)
    result = score_event_confidence(
        event_type="rss_insight_emerging",
        subject={"report_key": "rss:emerging:test"},
        events_count=1,
        source_mix=source_mix,
    )
    assert result["method"] == "heuristic"
    assert result["score"] == heuristic


def test_score_event_confidence_uses_evidence_model_for_opportunity_bundle():
    result = score_event_confidence(
        event_type="opportunity_bundle_ready",
        subject={
            "quality_summary": {"signal_volume": 18, "freshness_minutes": 30, "coverage_score": 0.84},
            "opportunities": [
                {"opportunity_id": "opp-1", "source_mix": {"youtube_channel_rss": 8, "reddit_discovery": 4}},
                {"opportunity_id": "opp-2", "source_mix": {"reddit_discovery": 3}},
            ],
        },
        events_count=3,
        source_mix={"youtube_channel_rss": 2, "reddit_discovery": 1},
    )
    assert result["method"] == "evidence_model"
    assert float(result["score"]) >= 0.8
    evidence = result.get("evidence") or {}
    assert int(evidence.get("signal_volume") or 0) == 18
    assert int(evidence.get("opportunity_count") or 0) == 2

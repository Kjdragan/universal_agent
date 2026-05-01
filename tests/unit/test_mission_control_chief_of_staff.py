import json
from pathlib import Path

import pytest

from universal_agent.services import mission_control_chief_of_staff as cos


def test_extract_json_object_accepts_fenced_json():
    payload = {"headline": "ok", "sections": []}

    parsed = cos._extract_json_object(f"```json\n{json.dumps(payload)}\n```")

    assert parsed == payload


def test_fallback_readout_preserves_counts_without_inventing_signal():
    evidence = {
        "generated_at_utc": "2026-04-30T12:00:00+00:00",
        "source_counts": {"activity_events": 3, "tutorial_pipeline": 1},
    }

    readout = cos.fallback_readout(evidence, error="no key")

    assert readout["synthesis_status"] == "fallback"
    assert readout["source_counts"] == evidence["source_counts"]
    assert "synthesis needs a successful LLM pass" in readout["headline"]


def test_persist_and_read_latest_round_trip(tmp_path: Path):
    evidence = {
        "generated_at_utc": "2026-04-30T12:00:00+00:00",
        "source_counts": {"task_hub_active_or_attention_items": 2},
    }
    readout = {
        "headline": "Two tasks need attention.",
        "generated_at_utc": "2026-04-30T12:00:00+00:00",
        "journal_entry": "Two attention items were surfaced.",
        "source_counts": evidence["source_counts"],
    }
    db_path = tmp_path / "mission_control.db"

    stored = cos.persist_readout(readout, evidence, model="test-model", db_path=db_path)
    latest = cos.get_latest_readout(include_evidence=True, db_path=db_path)
    journal = cos.get_recent_journal(limit=5, db_path=db_path)

    assert stored["id"]
    assert latest is not None
    assert latest["headline"] == "Two tasks need attention."
    assert latest["model"] == "test-model"
    assert latest["evidence_bundle"]["source_counts"] == evidence["source_counts"]
    assert journal[0]["summary"] == "Two attention items were surfaced."


@pytest.mark.asyncio
async def test_synthesize_falls_back_without_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("ZAI_API_KEY", raising=False)

    readout, model = await cos.synthesize_readout({"source_counts": {}})

    assert model is None
    assert readout["synthesis_status"] == "fallback"

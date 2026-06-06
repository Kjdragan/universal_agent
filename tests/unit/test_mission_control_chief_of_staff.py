import json
import sqlite3
from pathlib import Path

import pytest

from universal_agent.services import mission_control_chief_of_staff as cos
from universal_agent.services import proactive_artifacts as pa


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


def _seed_ship_brief(conn: sqlite3.Connection, *, artifact_id: str, title: str, summary: str) -> None:
    """Seed a shipped intel_brief row the readout should be able to cite."""
    pa.ensure_schema(conn)
    pa.upsert_artifact(
        conn,
        artifact_id=artifact_id,
        artifact_type=pa.ARTIFACT_TYPE_INTEL_BRIEF,
        source_kind="proactive_signal",
        title=title,
        summary=summary,
    )
    conn.execute(
        "UPDATE proactive_artifacts SET verdict = 'ship' WHERE artifact_id = ?",
        (artifact_id,),
    )
    conn.commit()


def test_collect_proactive_artifact_evidence_cites_artifact_and_brief_url(monkeypatch):
    monkeypatch.delenv("UA_GATEWAY_BASE_URL", raising=False)
    monkeypatch.delenv("FRONTEND_URL", raising=False)
    monkeypatch.delenv("UA_PUBLIC_BASE_URL", raising=False)

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        _seed_ship_brief(
            conn,
            artifact_id="pa_test123",
            title="Anthropic ships new model",
            summary="A shipped intel brief worth citing.",
        )

        result = cos.collect_proactive_artifact_evidence(conn)
    finally:
        conn.close()

    assert result["counts"]["items"] == 1
    item = result["items"][0]
    assert item["artifact_id"] == "pa_test123"
    assert item["brief_url"] == "https://app.clearspringcg.com/briefs/pa_test123"
    assert "/briefs/pa_test123" in item["evidence_ref"]
    assert "pa_test123" in item["evidence_ref"]


def test_collect_proactive_artifact_evidence_filters_non_ship_and_non_brief(monkeypatch):
    monkeypatch.delenv("UA_GATEWAY_BASE_URL", raising=False)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        # A shipped intel brief — should be returned.
        _seed_ship_brief(conn, artifact_id="pa_ship456", title="Keep me", summary="ship")
        # A skipped intel brief — wrong verdict, excluded.
        pa.upsert_artifact(
            conn,
            artifact_id="pa_skip789",
            artifact_type=pa.ARTIFACT_TYPE_INTEL_BRIEF,
            source_kind="proactive_signal",
            title="Drop me (skip)",
        )
        conn.execute("UPDATE proactive_artifacts SET verdict = 'skip' WHERE artifact_id = 'pa_skip789'")
        # A shipped non-brief artifact — wrong type, excluded.
        pa.upsert_artifact(
            conn,
            artifact_id="pa_other000",
            artifact_type="signal_card",
            source_kind="proactive_signal",
            title="Drop me (not a brief)",
        )
        conn.execute("UPDATE proactive_artifacts SET verdict = 'ship' WHERE artifact_id = 'pa_other000'")
        conn.commit()

        result = cos.collect_proactive_artifact_evidence(conn)
    finally:
        conn.close()

    ids = {item["artifact_id"] for item in result["items"]}
    assert ids == {"pa_ship456"}


def test_collect_proactive_artifact_evidence_missing_table_is_graceful():
    conn = sqlite3.connect(":memory:")  # no proactive_artifacts table created
    try:
        result = cos.collect_proactive_artifact_evidence(conn)
    finally:
        conn.close()

    assert result == {"items": [], "counts": {"items": 0}}


def test_collect_proactive_artifact_evidence_honors_gateway_base_url(monkeypatch):
    monkeypatch.setenv("UA_GATEWAY_BASE_URL", "https://staging.example.com/")
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        _seed_ship_brief(conn, artifact_id="pa_env999", title="Env brief", summary="s")
        result = cos.collect_proactive_artifact_evidence(conn)
    finally:
        conn.close()

    assert result["items"][0]["brief_url"] == "https://staging.example.com/briefs/pa_env999"


@pytest.mark.asyncio
async def test_synthesize_falls_back_without_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("ZAI_API_KEY", raising=False)

    readout, model = await cos.synthesize_readout({"source_counts": {}})

    assert model is None
    assert readout["synthesis_status"] == "fallback"

"""Tests for heartbeat findings JSON repair and schema validation.

Verifies that malformed LLM-generated JSON for heartbeat_findings_latest.json
is correctly repaired and validated via the extract_json_payload + HeartbeatFindings
Pydantic model pipeline.
"""
import json

import pytest

from universal_agent.utils.heartbeat_findings_schema import (
    HeartbeatFinding,
    HeartbeatFindings,
)
from universal_agent.utils.json_utils import extract_json_payload

# ── Schema defaults ──────────────────────────────────────────────────────


def test_heartbeat_findings_defaults():
    """Empty construction should produce a valid object with sensible defaults."""
    f = HeartbeatFindings()
    assert f.version == 1
    assert f.overall_status == "warn"
    assert f.source == "heartbeat"
    assert f.findings == []
    assert f.summary == "Heartbeat investigation required."


def test_heartbeat_finding_defaults():
    f = HeartbeatFinding()
    assert f.severity == "warn"
    assert f.confidence == "low"
    assert f.known_rule_match is False


def test_severity_normalization():
    f = HeartbeatFinding(severity="warning")
    assert f.severity == "warn"

    f2 = HeartbeatFinding(severity="error")
    assert f2.severity == "critical"


def test_confidence_normalization():
    f = HeartbeatFinding(confidence="MEDIUM")
    assert f.confidence == "medium"

    # Unknown values fall back to "low"
    f2 = HeartbeatFinding(confidence="maybe")
    assert f2.confidence == "low"


# ── Repair of LLM-typical malformed JSON ─────────────────────────────────


MALFORMED_MISSING_COMMA = """{
  "version": 1,
  "overall_status": "ok",
  "generated_at_utc": "2026-03-19T06:00:00Z",
  "source": "heartbeat",
  "summary": "All systems nominal"
  "findings": []
}"""

MALFORMED_TRAILING_COMMA = """{
  "version": 1,
  "overall_status": "ok",
  "generated_at_utc": "2026-03-19T06:00:00Z",
  "source": "heartbeat",
  "summary": "All systems nominal",
  "findings": [
    {
      "finding_id": "f1",
      "category": "ssh",
      "severity": "warn",
      "title": "SSH check OK",
    },
  ],
}"""

MALFORMED_PYTHON_LITERALS = """{
  "version": 1,
  "overall_status": "ok",
  "generated_at_utc": "2026-03-19T06:00:00Z",
  "source": "heartbeat",
  "summary": "Python-style booleans used",
  "findings": [
    {
      "finding_id": "f2",
      "category": "gateway",
      "severity": "warn",
      "known_rule_match": True,
      "observed_value": None,
      "title": "Test finding"
    }
  ]
}"""


def test_repair_missing_comma():
    """json_repair should handle a missing comma between keys."""
    result = extract_json_payload(MALFORMED_MISSING_COMMA, model=HeartbeatFindings)
    assert isinstance(result, HeartbeatFindings)
    assert result.overall_status == "ok"
    assert result.findings == []


def test_repair_trailing_commas():
    result = extract_json_payload(MALFORMED_TRAILING_COMMA, model=HeartbeatFindings)
    assert isinstance(result, HeartbeatFindings)
    assert len(result.findings) == 1
    assert result.findings[0].finding_id == "f1"


def test_repair_python_literals():
    result = extract_json_payload(MALFORMED_PYTHON_LITERALS, model=HeartbeatFindings)
    assert isinstance(result, HeartbeatFindings)
    assert result.findings[0].known_rule_match is True
    assert result.findings[0].observed_value is None


# ── Round-trip test ──────────────────────────────────────────────────────


def test_round_trip_malformed_to_valid_json():
    """Malformed input → repair → model_dump → json.dumps → json.loads must succeed."""
    for malformed in [
        MALFORMED_MISSING_COMMA,
        MALFORMED_TRAILING_COMMA,
        MALFORMED_PYTHON_LITERALS,
    ]:
        result = extract_json_payload(malformed, model=HeartbeatFindings)
        assert isinstance(result, HeartbeatFindings)
        dumped = json.dumps(result.model_dump(), indent=2, default=str)
        # Must be valid JSON
        loaded = json.loads(dumped)
        assert isinstance(loaded, dict)
        assert "version" in loaded
        assert "findings" in loaded


# ── Partial findings still validate ──────────────────────────────────────


def test_partial_findings_validate():
    """Agent writes minimal findings — missing fields should get defaults."""
    partial = '{"overall_status": "ok", "findings": [{"title": "Disk check passed"}]}'
    result = extract_json_payload(partial, model=HeartbeatFindings)
    assert isinstance(result, HeartbeatFindings)
    assert result.version == 1  # default
    assert result.source == "heartbeat"  # default
    assert len(result.findings) == 1
    assert result.findings[0].severity == "warn"  # default
    assert result.findings[0].finding_id == "unknown"  # default


# ── Valid JSON passes through unchanged ──────────────────────────────────


def test_valid_json_passes_through():
    valid = json.dumps({
        "version": 1,
        "overall_status": "ok",
        "generated_at_utc": "2026-03-19T06:00:00Z",
        "source": "heartbeat",
        "summary": "All systems nominal.",
        "findings": [],
    })
    result = extract_json_payload(valid, model=HeartbeatFindings)
    assert isinstance(result, HeartbeatFindings)
    assert result.overall_status == "ok"

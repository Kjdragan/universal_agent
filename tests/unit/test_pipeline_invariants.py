"""Unit tests for the pipeline_invariants service module."""

from __future__ import annotations

from typing import Any, Dict

import pytest

from universal_agent.services import pipeline_invariants as pi
from universal_agent.services.pipeline_invariants import (
    CATEGORY,
    Invariant,
    clear_registry_for_tests,
    get_registered_invariants,
    invariant,
    register_invariant,
    run_invariants,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    clear_registry_for_tests()
    yield
    clear_registry_for_tests()


def test_register_invariant_function_form() -> None:
    inv = Invariant(
        id="x_check",
        title="X check",
        description="checks X",
        severity="warn",
        probe=lambda ctx: None,
    )
    register_invariant(inv)
    registered = get_registered_invariants()
    assert len(registered) == 1
    assert registered[0].id == "x_check"


def test_register_invariant_decorator_form() -> None:
    @invariant(
        id="y_check",
        title="Y check",
        description="checks Y",
        severity="critical",
        runbook_command="ls -la",
    )
    def _probe(_ctx: Dict[str, Any]) -> None:
        return None

    registered = get_registered_invariants()
    assert len(registered) == 1
    assert registered[0].id == "y_check"
    assert registered[0].severity == "critical"
    assert registered[0].runbook_command == "ls -la"


def test_invariant_rejects_unknown_severity() -> None:
    with pytest.raises(ValueError, match="severity"):
        Invariant(
            id="bad",
            title="bad",
            description="bad",
            severity="catastrophic",
            probe=lambda ctx: None,
        )


def test_invariant_rejects_empty_id() -> None:
    with pytest.raises(ValueError, match="id"):
        Invariant(
            id="",
            title="t",
            description="d",
            severity="warn",
            probe=lambda ctx: None,
        )


def test_ok_probe_emits_no_findings() -> None:
    @invariant(id="ok_check", title="OK", description="d", severity="warn")
    def _probe(_ctx: Dict[str, Any]) -> None:
        return None

    findings = run_invariants({})
    assert findings == []


def test_anomalous_probe_emits_finding_with_correct_shape() -> None:
    @invariant(
        id="anomalous",
        title="Anomalous title",
        description="detects bad things",
        severity="critical",
        runbook_command="see runbook",
        metadata={"static_tag": "youtube"},
    )
    def _probe(_ctx: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "observed_value": {"ok_pct": 0.0},
            "message": "100% missing transcripts",
            "threshold_text": "ok_pct >= 80",
            "metadata": {"days_window": 7},
        }

    findings = run_invariants({})
    assert len(findings) == 1
    f = findings[0]
    assert f.finding_id == "invariant:anomalous"
    assert f.category == CATEGORY
    assert f.severity == "critical"
    assert f.metric_key == "anomalous"
    assert f.title == "Anomalous title"
    assert f.recommendation == "100% missing transcripts"
    assert f.threshold_text == "ok_pct >= 80"
    assert f.runbook_command == "see runbook"
    assert f.observed_value == {"ok_pct": 0.0}
    assert f.metadata["invariant_id"] == "anomalous"
    assert f.metadata["static_tag"] == "youtube"
    assert f.metadata["days_window"] == 7
    assert f.known_rule_match is True
    assert f.confidence == "high"


def test_raising_probe_emits_probe_error_finding_without_crashing() -> None:
    @invariant(id="boom", title="Boom check", description="d", severity="critical")
    def _probe(_ctx: Dict[str, Any]) -> None:
        raise RuntimeError("kaboom")

    findings = run_invariants({})
    assert len(findings) == 1
    f = findings[0]
    assert f.finding_id == "invariant:boom:probe_error"
    assert f.severity == "warn"  # probe errors are always warn, not the invariant's severity
    assert f.category == CATEGORY
    assert f.metric_key == "boom_probe_error"
    assert "RuntimeError" in (f.observed_value or "")
    assert "kaboom" in (f.observed_value or "")
    assert f.metadata["invariant_id"] == "boom"
    assert f.metadata["exception_type"] == "RuntimeError"
    assert "probe_elapsed_ms" in f.metadata


def test_non_dict_probe_result_is_ignored() -> None:
    @invariant(id="weird", title="Weird", description="d", severity="warn")
    def _probe(_ctx: Dict[str, Any]) -> Any:
        return "not a dict and not None"  # type: ignore[return-value]

    findings = run_invariants({})
    assert findings == []


def test_runner_continues_past_one_failing_probe() -> None:
    @invariant(id="a_boom", title="A", description="d", severity="warn")
    def _a(_ctx: Dict[str, Any]) -> None:
        raise ValueError("a")

    @invariant(id="b_ok", title="B", description="d", severity="warn")
    def _b(_ctx: Dict[str, Any]) -> Dict[str, Any]:
        return {"observed_value": 1, "message": "anomaly B"}

    findings = run_invariants({})
    # registry is id-sorted, so a_boom runs before b_ok
    assert len(findings) == 2
    assert findings[0].finding_id == "invariant:a_boom:probe_error"
    assert findings[1].finding_id == "invariant:b_ok"
    assert findings[1].recommendation == "anomaly B"


def test_context_is_passed_to_probe() -> None:
    seen: Dict[str, Any] = {}

    @invariant(id="ctx_check", title="ctx", description="d", severity="warn")
    def _probe(ctx: Dict[str, Any]) -> None:
        seen.update(ctx)
        return None

    run_invariants({"runtime_conn": "fake", "csi_db_path": "/tmp/x"})
    assert seen == {"runtime_conn": "fake", "csi_db_path": "/tmp/x"}


def test_registering_same_id_twice_replaces() -> None:
    register_invariant(
        Invariant(
            id="dup", title="v1", description="d", severity="warn", probe=lambda c: None
        )
    )
    register_invariant(
        Invariant(
            id="dup", title="v2", description="d", severity="critical", probe=lambda c: None
        )
    )
    registered = get_registered_invariants()
    assert len(registered) == 1
    assert registered[0].title == "v2"
    assert registered[0].severity == "critical"


def test_import_has_no_side_effects() -> None:
    # Re-importing should not populate the registry.
    clear_registry_for_tests()
    import importlib

    importlib.reload(pi)
    # After reload, the registry is empty until something registers.
    assert pi.get_registered_invariants() == []

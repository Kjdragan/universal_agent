"""Phase 7 — smart event titles + hide_by_default filter.

Tests cover:
  - metadata_shape_signature: deterministic, type-aware, value-agnostic
  - apply_template: top-level fields, metadata.* placeholders, missing
    keys render as "?", non-string values stringified
  - get/store cached templates: round-trip, upsert by (kind, shape)
  - _fallback_template: code-only path produces sensible defaults when
    the LLM is unavailable
  - hide_by_default: rules for heartbeat ticks, unchanged cron syncs,
    autonomous_run_completed, severity-warning override, requires_action
    override
  - annotate_event: end-to-end pipeline with cached + uncached cases
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from universal_agent.services.mission_control_db import open_store
from universal_agent.services.mission_control_event_titles import (
    _fallback_template,
    annotate_event,
    apply_template,
    get_cached_template,
    hide_by_default,
    metadata_shape_signature,
    store_template,
)


# ── Signature stability ────────────────────────────────────────────────


def test_signature_deterministic_for_same_shape():
    a = {"job_id": "x", "duration_seconds": 5, "status": "ok"}
    b = {"status": "different", "job_id": "y", "duration_seconds": 999}
    # Same keys + same types → same signature, regardless of values
    assert metadata_shape_signature(a) == metadata_shape_signature(b)


def test_signature_changes_on_key_set_change():
    a = {"job_id": "x"}
    b = {"job_id": "x", "extra": "added"}
    assert metadata_shape_signature(a) != metadata_shape_signature(b)


def test_signature_changes_on_type_change():
    a = {"count": 5}             # int
    b = {"count": "five"}        # str
    assert metadata_shape_signature(a) != metadata_shape_signature(b)


def test_signature_handles_string_metadata():
    """SQLite returns metadata as JSON-encoded string. Signature should
    decode and signature on the parsed shape."""
    raw = json.dumps({"job_id": "x", "status": "ok"})
    sig_from_str = metadata_shape_signature(raw)
    sig_from_dict = metadata_shape_signature({"job_id": "x", "status": "ok"})
    assert sig_from_str == sig_from_dict


def test_signature_handles_non_dict_gracefully():
    assert metadata_shape_signature(None).startswith("shape:")
    assert metadata_shape_signature(["list"]).startswith("shape:")
    assert metadata_shape_signature("garbage").startswith("shape:")


# ── apply_template ─────────────────────────────────────────────────────


def test_apply_template_top_level_fields():
    template = "{kind} · {severity}"
    event = {"kind": "cron_run_failed", "severity": "error", "metadata": {}}
    assert apply_template(template, event) == "cron_run_failed · error"


def test_apply_template_metadata_placeholders():
    template = "Cron · {metadata.job_id} · {metadata.duration_seconds}s"
    event = {"kind": "cron_run_completed",
             "metadata": {"job_id": "csi_sync", "duration_seconds": 42}}
    assert apply_template(template, event) == "Cron · csi_sync · 42s"


def test_apply_template_missing_keys_render_as_question_mark():
    template = "{kind} · {metadata.does_not_exist}"
    event = {"kind": "anything", "metadata": {}}
    assert apply_template(template, event) == "anything · ?"


def test_apply_template_handles_string_metadata():
    template = "{metadata.job_id}"
    event = {"kind": "x", "metadata": json.dumps({"job_id": "encoded"})}
    assert apply_template(template, event) == "encoded"


def test_apply_template_empty_template_returns_empty():
    assert apply_template("", {"kind": "x"}) == ""


# ── Template cache round-trip ──────────────────────────────────────────


def test_template_round_trip(tmp_path: Path):
    conn = open_store(tmp_path / "mc.db")
    try:
        sig = metadata_shape_signature({"job_id": "x", "status": "ok"})
        stored = store_template(
            conn,
            event_kind="cron_run_completed",
            shape_sig=sig,
            title_template="Cron · {metadata.job_id} · {metadata.status}",
            generated_by_model="glm-4.7",
        )
        assert stored["template_id"].startswith("tpl_")

        retrieved = get_cached_template(conn, "cron_run_completed", sig)
        assert retrieved is not None
        assert retrieved["title_template"] == "Cron · {metadata.job_id} · {metadata.status}"
        assert retrieved["generated_by_model"] == "glm-4.7"
    finally:
        conn.close()


def test_template_upsert_overwrites_existing(tmp_path: Path):
    """Re-validation of a template (e.g. weekly) must update in place,
    not insert a duplicate row."""
    conn = open_store(tmp_path / "mc.db")
    try:
        sig = metadata_shape_signature({"job_id": "x"})
        store_template(conn, event_kind="k1", shape_sig=sig,
                       title_template="v1", generated_by_model="m1")
        store_template(conn, event_kind="k1", shape_sig=sig,
                       title_template="v2", generated_by_model="m2")
        rows = conn.execute(
            "SELECT * FROM event_title_templates WHERE event_kind=? AND metadata_shape_signature=?",
            ("k1", sig),
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["title_template"] == "v2"
        assert rows[0]["generated_by_model"] == "m2"
    finally:
        conn.close()


def test_get_cached_template_returns_none_for_unknown():
    """Sanity check: querying for an unknown (kind, shape) returns None
    rather than a stale row."""
    from pathlib import Path as _P
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        conn = open_store(_P(td) / "mc.db")
        try:
            assert get_cached_template(conn, "never_seen", "shape:abc") is None
        finally:
            conn.close()


# ── Fallback template ──────────────────────────────────────────────────


def test_fallback_template_includes_pretty_kind():
    template = _fallback_template({
        "kind": "cron_run_completed",
        "metadata": {"job_id": "x", "duration_seconds": 5},
    })
    assert "Cron Run Completed" in template
    assert "{metadata.job_id}" in template
    assert "{metadata.duration_seconds}" in template


def test_fallback_template_handles_empty_metadata():
    template = _fallback_template({"kind": "weird_kind", "metadata": {}})
    assert template == "Weird Kind"


def test_fallback_template_handles_unknown_kind():
    template = _fallback_template({"kind": "", "metadata": {}})
    assert "?" in template or template


# ── hide_by_default ────────────────────────────────────────────────────


def _ev(**kw):
    base = {"kind": "noop", "source_domain": "system", "severity": "info",
            "status": "new", "requires_action": False, "metadata": {}}
    base.update(kw)
    return base


def test_warning_severity_always_shown():
    assert hide_by_default(_ev(severity="warning")) is False


def test_error_severity_always_shown():
    assert hide_by_default(_ev(severity="error")) is False


def test_critical_severity_always_shown():
    assert hide_by_default(_ev(severity="critical")) is False


def test_requires_action_always_shown():
    assert hide_by_default(_ev(severity="info", requires_action=True)) is False


def test_routine_heartbeat_tick_hidden():
    assert hide_by_default(_ev(
        kind="autonomous_heartbeat_completed",
        source_domain="heartbeat",
        severity="info",
    )) is True


def test_heartbeat_with_finding_shown():
    """Heartbeat ticks that emitted a finding should NOT be hidden —
    those are the operationally meaningful ones."""
    assert hide_by_default(_ev(
        kind="autonomous_heartbeat_completed",
        source_domain="heartbeat",
        severity="info",
        metadata={"finding": "stuck task detected"},
    )) is False


def test_heartbeat_investigation_shown():
    """Investigation events are by definition non-routine."""
    assert hide_by_default(_ev(
        kind="heartbeat_investigation_completed",
        source_domain="heartbeat",
        severity="info",
    )) is False


def test_unchanged_cron_sync_hidden():
    assert hide_by_default(_ev(
        kind="autonomous_run_completed",
        source_domain="cron",
        severity="info",
        metadata={"changed": False},
    )) is True


def test_changed_cron_sync_shown():
    assert hide_by_default(_ev(
        kind="autonomous_run_completed",
        source_domain="cron",
        severity="info",
        metadata={"changed": True, "artifact_count": 1},
    )) is False


def test_autonomous_run_with_artifacts_shown():
    """A successful run that produced artifacts is NOT routine noise."""
    assert hide_by_default(_ev(
        kind="autonomous_run_completed",
        severity="info",
        metadata={"artifact_count": 3},
    )) is False


def test_autonomous_run_no_artifacts_hidden():
    assert hide_by_default(_ev(
        kind="autonomous_run_completed",
        severity="info",
        metadata={},
    )) is True


# ── End-to-end annotation ──────────────────────────────────────────────


def test_annotate_uses_cached_template_when_present(tmp_path: Path):
    conn = open_store(tmp_path / "mc.db")
    try:
        sig = metadata_shape_signature({"job_id": "x", "status": "ok"})
        store_template(conn, event_kind="cron_run_completed", shape_sig=sig,
                       title_template="Cron · {metadata.job_id} · {metadata.status}",
                       generated_by_model="glm-4.7")
        ev = {"id": "ev1", "kind": "cron_run_completed", "severity": "info",
              "metadata": {"job_id": "csi_sync", "status": "ok"}}
        annotated = annotate_event(conn, ev)
        assert annotated["smart_title"] == "Cron · csi_sync · ok"
        assert annotated["title_template_source"] == "glm-4.7"
        assert annotated["hide_by_default"] is False  # success cron with status=ok and no changed=False
    finally:
        conn.close()


def test_annotate_falls_back_when_template_missing(tmp_path: Path):
    conn = open_store(tmp_path / "mc.db")
    try:
        ev = {"id": "ev1", "kind": "novel_event_kind", "severity": "info",
              "metadata": {"task_id": "abc"}}
        annotated = annotate_event(conn, ev)
        assert annotated["title_template_source"] == "fallback"
        assert "Novel Event Kind" in annotated["smart_title"]
        assert "abc" in annotated["smart_title"]
    finally:
        conn.close()


def test_annotate_marks_routine_heartbeat_for_default_hide(tmp_path: Path):
    conn = open_store(tmp_path / "mc.db")
    try:
        ev = {"id": "hb1", "kind": "autonomous_heartbeat_completed",
              "source_domain": "heartbeat", "severity": "info",
              "metadata": {}}
        annotated = annotate_event(conn, ev)
        assert annotated["hide_by_default"] is True
    finally:
        conn.close()


def test_annotate_does_not_mutate_original_event(tmp_path: Path):
    """annotate_event must NOT modify the event in place — we annotate a
    copy so caller-side caches don't leak our annotations."""
    conn = open_store(tmp_path / "mc.db")
    try:
        ev = {"id": "ev1", "kind": "test_kind", "metadata": {}}
        original = dict(ev)
        annotate_event(conn, ev)
        assert ev == original  # unchanged
    finally:
        conn.close()

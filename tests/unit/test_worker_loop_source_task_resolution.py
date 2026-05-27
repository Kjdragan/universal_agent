"""Unit tests for _resolve_source_task_id_from_payload (PR #493).

Resolves which Task Hub ``qa-*`` task triggered a VP mission, by
checking three field locations in priority order. Before PR #493 only
the lowest-priority location was checked — which is the key nobody
writes — so the worker_loop's "close source task on VP terminal"
branch silently no-op'd for every operator-dispatched Cody mission.

This is the "delegated zombie" root cause that left ``qa-*`` rows
stuck in ``status=delegated`` indefinitely. With this PR, terminal
sync correctly propagates the disposition to the parent.
"""

from __future__ import annotations

from universal_agent.vp.worker_loop import _resolve_source_task_id_from_payload


def test_top_level_task_id_wins():
    """PR #490's _build_payload writes here — highest priority."""
    payload = {
        "task_id": "qa-toplevel",
        "metadata": {"linked_task_id": "qa-meta", "task_id": "qa-legacy"},
    }
    assert _resolve_source_task_id_from_payload(payload) == "qa-toplevel"


def test_metadata_linked_task_id_when_no_top_level():
    """PR #491's auto-discovery writes here."""
    payload = {"metadata": {"linked_task_id": "qa-meta", "task_id": "qa-legacy"}}
    assert _resolve_source_task_id_from_payload(payload) == "qa-meta"


def test_metadata_task_id_legacy_fallback():
    """Legacy callers stuffed it directly under metadata."""
    payload = {"metadata": {"task_id": "qa-legacy"}}
    assert _resolve_source_task_id_from_payload(payload) == "qa-legacy"


def test_returns_empty_when_no_linkage_anywhere():
    """Ad-hoc tool-call dispatches without a Task Hub parent."""
    payload = {"objective": "do thing", "metadata": {}}
    assert _resolve_source_task_id_from_payload(payload) == ""


def test_returns_empty_for_non_dict_payload():
    assert _resolve_source_task_id_from_payload(None) == ""
    assert _resolve_source_task_id_from_payload([]) == ""
    assert _resolve_source_task_id_from_payload("not-a-dict") == ""


def test_strips_whitespace_from_field_values():
    payload = {"task_id": "  qa-padded  "}
    assert _resolve_source_task_id_from_payload(payload) == "qa-padded"


def test_empty_strings_at_higher_priority_skip_to_next():
    """Whitespace-only or empty top-level should fall through."""
    payload = {
        "task_id": "   ",
        "metadata": {"linked_task_id": "qa-found"},
    }
    assert _resolve_source_task_id_from_payload(payload) == "qa-found"


def test_metadata_must_be_dict_to_be_consulted():
    """Garbage metadata type is treated as no-linkage rather than crashing."""
    payload = {"metadata": "not-a-dict"}
    assert _resolve_source_task_id_from_payload(payload) == ""
    payload = {"metadata": ["list", "not", "dict"]}
    assert _resolve_source_task_id_from_payload(payload) == ""

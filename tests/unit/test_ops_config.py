"""Unit tests for ``universal_agent.ops_config``.

``ops_config`` is a small stdlib-only helper module that backs the runtime
operator-config layer: ``load_ops_config`` / ``write_ops_config`` /
``resolve_ops_config_path`` read and write the on-disk ops JSON, while
``ops_config_hash`` gives it a stable digest and ``apply_merge_patch`` implements
RFC 7396 JSON Merge Patch.

Despite heavy production use, these helpers had **zero direct behavioral
coverage**: every other reference in the test tree is a ``patch(...)`` that
neutralizes ``load_ops_config`` while testing some other module
(``hooks_service``, ``session_policy``, ``gateway_server``). A regression in
``apply_merge_patch`` in particular would silently corrupt session-policy and
ops-config merges, so these tests pin the documented semantics of each helper.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from universal_agent.ops_config import (
    apply_merge_patch,
    load_ops_config,
    ops_config_hash,
    resolve_ops_config_path,
    write_ops_config,
)


# --------------------------------------------------------------------------- #
# resolve_ops_config_path
# --------------------------------------------------------------------------- #
def test_default_path_when_env_unset(monkeypatch: pytest.MonkeyPatch):
    """With ``UA_OPS_CONFIG_PATH`` unset, resolution falls back to the repo path."""
    monkeypatch.delenv("UA_OPS_CONFIG_PATH", raising=False)

    path = resolve_ops_config_path()

    # The canonical default lives under <repo>/AGENT_RUN_WORKSPACES/ops_config.json.
    assert path.name == "ops_config.json"
    assert path.parent.name == "AGENT_RUN_WORKSPACES"


def test_env_override_is_honored(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """``UA_OPS_CONFIG_PATH`` redirects resolution to the caller-supplied path."""
    override = tmp_path / "custom_ops.json"
    monkeypatch.setenv("UA_OPS_CONFIG_PATH", str(override))

    assert resolve_ops_config_path() == override.resolve()


def test_env_override_expands_user(monkeypatch: pytest.MonkeyPatch):
    """A ``~``-relative override is expanded before resolution."""
    monkeypatch.setenv("UA_OPS_CONFIG_PATH", "~/ops.json")

    resolved = resolve_ops_config_path()

    assert "~" not in str(resolved)
    assert resolved.name == "ops.json"


# --------------------------------------------------------------------------- #
# load_ops_config
# --------------------------------------------------------------------------- #
def test_load_missing_file_returns_empty(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """A missing config file resolves to an empty dict, never an error."""
    monkeypatch.setenv("UA_OPS_CONFIG_PATH", str(tmp_path / "absent.json"))

    assert load_ops_config() == {}


def test_load_reads_valid_json(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """A well-formed JSON file round-trips into a parsed dict."""
    config_path = tmp_path / "ops.json"
    config_path.write_text(json.dumps({"heartbeat_mediation": {"enabled": True}}))
    monkeypatch.setenv("UA_OPS_CONFIG_PATH", str(config_path))

    assert load_ops_config() == {"heartbeat_mediation": {"enabled": True}}


def test_load_corrupt_json_returns_empty(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Corrupt JSON is swallowed into an empty dict rather than propagating."""
    config_path = tmp_path / "ops.json"
    config_path.write_text("{not valid json")
    monkeypatch.setenv("UA_OPS_CONFIG_PATH", str(config_path))

    assert load_ops_config() == {}


# --------------------------------------------------------------------------- #
# write_ops_config
# --------------------------------------------------------------------------- #
def test_write_round_trips_through_load(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Anything written must be readable back as the same dict."""
    monkeypatch.setenv("UA_OPS_CONFIG_PATH", str(tmp_path / "ops.json"))
    payload = {"skills": {"entries": {"foo": True}}, "notifications": {"channels": ["email"]}}

    write_ops_config(payload)

    assert load_ops_config() == payload


def test_write_creates_parent_dirs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """A nested override path whose parent does not exist is created on write."""
    nested = tmp_path / "nested" / "deep" / "ops.json"
    monkeypatch.setenv("UA_OPS_CONFIG_PATH", str(nested))

    returned = write_ops_config({"x": 1})

    assert nested.exists()
    assert returned == nested.resolve()


def test_write_output_is_sorted_and_indented(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Written output is deterministically sorted so diffs and hashes stay stable."""
    config_path = tmp_path / "ops.json"
    monkeypatch.setenv("UA_OPS_CONFIG_PATH", str(config_path))

    write_ops_config({"b": 2, "a": 1, "c": {"z": 1, "y": 2}})

    text = config_path.read_text()
    # Sorted top-level keys put ``a`` before ``b`` regardless of insertion order.
    assert text.index('"a"') < text.index('"b"')
    # Indentation is applied (pretty-printed, not single-line).
    assert "\n" in text


# --------------------------------------------------------------------------- #
# ops_config_hash
# --------------------------------------------------------------------------- #
def test_hash_is_deterministic():
    """The same config digest is stable across repeated calls."""
    config = {"a": 1, "b": [1, 2, 3]}

    assert ops_config_hash(config) == ops_config_hash(dict(config))


def test_hash_invariant_to_key_order():
    """Key insertion order must not change the digest (sorted serialization)."""
    left = ops_config_hash({"a": 1, "b": 2})
    right = ops_config_hash({"b": 2, "a": 1})

    assert left == right


def test_hash_distinguishes_different_content():
    """Different content must produce a different digest."""
    assert ops_config_hash({"a": 1}) != ops_config_hash({"a": 2})


def test_hash_is_sha256_hex():
    """The digest is a 64-character lowercase hex string (SHA-256)."""
    digest = ops_config_hash({})

    assert len(digest) == 64
    assert all(c in "0123456789abcdef" for c in digest)


# --------------------------------------------------------------------------- #
# apply_merge_patch (RFC 7396 JSON Merge Patch)
# --------------------------------------------------------------------------- #
def test_none_patch_returns_target_unchanged():
    """A ``None`` patch is a no-op: the target is returned as-is."""
    target = {"a": 1}

    assert apply_merge_patch(target, None) is target


def test_scalar_patch_replaces_target():
    """A non-dict patch replaces the target wholesale."""
    assert apply_merge_patch({"a": 1}, 5) == 5
    assert apply_merge_patch({"a": 1}, "string") == "string"
    assert apply_merge_patch({"a": 1}, [1, 2]) == [1, 2]


def test_dict_patch_into_non_dict_target_resets_to_empty():
    """Merging a dict into a non-dict target treats the target as ``{}``."""
    assert apply_merge_patch(5, {"a": 1}) == {"a": 1}
    assert apply_merge_patch(None, {"a": 1}) == {"a": 1}


def test_none_value_in_patch_deletes_key():
    """A key set to ``None`` in the patch is removed from the result."""
    assert apply_merge_patch({"a": 1, "b": 2}, {"b": None}) == {"a": 1}


def test_nested_dict_deep_merge_preserves_siblings():
    """A nested merge only touches the patched key; siblings are preserved."""
    target = {"a": {"x": 1, "y": 2}, "b": 3}
    patch = {"a": {"y": 20}}

    assert apply_merge_patch(target, patch) == {"a": {"x": 1, "y": 20}, "b": 3}


def test_nested_delete_via_none():
    """A nested ``None`` deletes the deep key, not the whole subtree."""
    target = {"a": {"x": 1, "y": 2}}

    assert apply_merge_patch(target, {"a": {"y": None}}) == {"a": {"x": 1}}


def test_arrays_are_replaced_not_merged():
    """Per RFC 7396, arrays are replaced atomically, not element-merged."""
    target = {"tags": [1, 2, 3]}

    assert apply_merge_patch(target, {"tags": [4]}) == {"tags": [4]}


def test_apply_merge_patch_does_not_mutate_target():
    """The target dict (including nested dicts) must not be mutated."""
    target = {"a": {"x": 1, "y": 2}, "b": 3}
    target_snapshot = json.loads(json.dumps(target))

    apply_merge_patch(target, {"a": {"y": 20}, "b": None})

    assert target == target_snapshot


def test_empty_patch_dict_returns_copy():
    """An empty patch dict yields an equal-but-distinct copy of the target."""
    target = {"a": 1}
    result = apply_merge_patch(target, {})

    assert result == target
    assert result is not target

"""Unit tests for the pure helpers in :mod:`universal_agent.ops_config`.

These functions are heavily-used live code — ``apply_merge_patch`` drives
session-policy merging (``session_policy.py``) and ops-config updates
(``gateway_server.py``), and the config load/write/hash/path helpers back the
runtime ops-config surface — but had no direct unit coverage (every
``test_hooks_service.py`` reference just mocks ``load_ops_config`` to ``{}``).

The tests focus on the :func:`apply_merge_patch` RFC 7396 (JSON Merge Patch)
contract, which has precise, checkable edge cases, plus determinism and
round-trip behavior for the path/load/write/hash helpers.
"""

from __future__ import annotations

import json
from pathlib import Path

from universal_agent import ops_config

# --------------------------------------------------------------------------- #
# apply_merge_patch — RFC 7396 JSON Merge Patch contract
# --------------------------------------------------------------------------- #


def test_apply_merge_patch_none_patch_returns_target_unchanged() -> None:
    """A ``null`` patch MUST return the target unmodified."""
    target = {"a": 1, "b": 2}
    assert ops_config.apply_merge_patch(target, None) == target
    # And it is the *same object* returned (no copy), per the early return.
    assert ops_config.apply_merge_patch(target, None) is target


def test_apply_merge_patch_non_dict_patch_replaces_whole_target() -> None:
    """A non-dict patch replaces the target entirely (scalar or list)."""
    assert ops_config.apply_merge_patch({"a": 1}, 5) == 5
    assert ops_config.apply_merge_patch({"a": 1}, "hello") == "hello"
    assert ops_config.apply_merge_patch({"a": 1}, [1, 2, 3]) == [1, 2, 3]


def test_apply_merge_patch_non_dict_target_is_treated_as_empty_dict() -> None:
    """When the target is not a dict, it is reset to ``{}`` before merging."""
    assert ops_config.apply_merge_patch(5, {"a": 1}) == {"a": 1}
    assert ops_config.apply_merge_patch("x", {"a": 1, "b": 2}) == {"a": 1, "b": 2}
    assert ops_config.apply_merge_patch(None, {"a": 1}) == {"a": 1}


def test_apply_merge_patch_none_value_deletes_key() -> None:
    """A key set to ``null`` in the patch is removed from the result."""
    assert ops_config.apply_merge_patch({"a": 1, "b": 2}, {"a": None}) == {"b": 2}
    # Deleting a key that doesn't already exist is a no-op (no error).
    assert ops_config.apply_merge_patch({"a": 1}, {"z": None}) == {"a": 1}


def test_apply_merge_patch_nested_dict_merges_recursively() -> None:
    """Nested dicts merge one level at a time, preserving untouched keys."""
    target = {"a": {"x": 1, "y": 2}, "b": 10}
    patch = {"a": {"y": 3, "z": 4}}
    assert ops_config.apply_merge_patch(target, patch) == {
        "a": {"x": 1, "y": 3, "z": 4},
        "b": 10,
    }


def test_apply_merge_patch_nested_null_deletes_nested_key() -> None:
    """A nested ``null`` deletes only that nested key."""
    target = {"a": {"x": 1, "y": 2}}
    patch = {"a": {"y": None}}
    assert ops_config.apply_merge_patch(target, patch) == {"a": {"x": 1}}


def test_apply_merge_patch_lists_are_replaced_not_merged() -> None:
    """Per RFC 7396, a list value is replaced wholesale, not element-merged."""
    target = {"items": [1, 2, 3], "keep": True}
    patch = {"items": [9]}
    assert ops_config.apply_merge_patch(target, patch) == {"items": [9], "keep": True}


def test_apply_merge_patch_does_not_mutate_target() -> None:
    """The original target dict must not be mutated by the merge."""
    target = {"a": {"x": 1}}
    target_copy = json.loads(json.dumps(target))
    ops_config.apply_merge_patch(target, {"a": {"y": 2}})
    assert target == target_copy


def test_apply_merge_patch_empty_patch_is_identity() -> None:
    """An empty dict patch returns an equal dict (no keys added/removed)."""
    target = {"a": 1}
    result = ops_config.apply_merge_patch(target, {})
    assert result == {"a": 1}


def test_apply_merge_patch_adds_new_top_level_keys() -> None:
    """Keys present only in the patch are added to the result."""
    assert ops_config.apply_merge_patch({"a": 1}, {"b": 2}) == {"a": 1, "b": 2}


def test_apply_merge_patch_nested_replaces_scalar_with_dict() -> None:
    """A scalar target value can be promoted to a dict via a dict patch."""
    target = {"a": 1}
    patch = {"a": {"x": 2}}
    assert ops_config.apply_merge_patch(target, patch) == {"a": {"x": 2}}


def test_apply_merge_patch_nested_replaces_dict_with_scalar() -> None:
    """A dict target value can be replaced by a scalar via a scalar patch."""
    target = {"a": {"x": 2}}
    patch = {"a": 5}
    assert ops_config.apply_merge_patch(target, patch) == {"a": 5}


# --------------------------------------------------------------------------- #
# ops_config_hash — deterministic, order-independent SHA-256 fingerprint
# --------------------------------------------------------------------------- #


def test_ops_config_hash_is_deterministic() -> None:
    """Same content produces the same hash across calls."""
    assert ops_config.ops_config_hash({"a": 1, "b": 2}) == ops_config.ops_config_hash(
        {"a": 1, "b": 2}
    )


def test_ops_config_hash_is_key_order_independent() -> None:
    """sort_keys=True means dict key order does not change the hash."""
    assert ops_config.ops_config_hash({"a": 1, "b": 2}) == ops_config.ops_config_hash(
        {"b": 2, "a": 1}
    )


def test_ops_config_hash_differs_for_different_content() -> None:
    """Different content yields a different hash."""
    assert ops_config.ops_config_hash({"a": 1}) != ops_config.ops_config_hash({"a": 2})


def test_ops_config_hash_is_a_hex_sha256() -> None:
    """The hash is a 64-char lowercase hex string (SHA-256)."""
    digest = ops_config.ops_config_hash({})
    assert isinstance(digest, str)
    assert len(digest) == 64
    assert all(c in "0123456789abcdef" for c in digest)


# --------------------------------------------------------------------------- #
# resolve_ops_config_path — env override vs project-root default
# --------------------------------------------------------------------------- #


def test_resolve_ops_config_path_honors_env_override(monkeypatch, tmp_path: Path) -> None:
    """``UA_OPS_CONFIG_PATH`` wins over the project-root default."""
    custom = tmp_path / "custom_ops.json"
    monkeypatch.setenv("UA_OPS_CONFIG_PATH", str(custom))
    assert ops_config.resolve_ops_config_path() == custom.resolve()


def test_resolve_ops_config_path_expands_user(monkeypatch) -> None:
    """A leading ``~`` in the env path is expanded."""
    monkeypatch.setenv("UA_OPS_CONFIG_PATH", "~/ops.json")
    resolved = ops_config.resolve_ops_config_path()
    assert not str(resolved).startswith("~")
    assert resolved.name == "ops.json"


def test_resolve_ops_config_path_default_when_env_unset(monkeypatch) -> None:
    """Without the env var, the path is under the project root AGENT_RUN_WORKSPACES dir."""
    monkeypatch.delenv("UA_OPS_CONFIG_PATH", raising=False)
    resolved = ops_config.resolve_ops_config_path()
    assert resolved.name == "ops_config.json"
    assert resolved.parent.name == "AGENT_RUN_WORKSPACES"


# --------------------------------------------------------------------------- #
# load_ops_config / write_ops_config — missing file, corrupt JSON, round-trip
# --------------------------------------------------------------------------- #


def test_load_ops_config_returns_empty_dict_when_file_missing(
    monkeypatch, tmp_path: Path
) -> None:
    """A missing config file resolves to an empty dict (never raises)."""
    monkeypatch.setenv("UA_OPS_CONFIG_PATH", str(tmp_path / "absent.json"))
    assert ops_config.load_ops_config() == {}


def test_load_ops_config_returns_empty_dict_on_corrupt_json(
    monkeypatch, tmp_path: Path
) -> None:
    """Malformed JSON is swallowed and returns ``{}`` (resilience contract)."""
    bad = tmp_path / "bad.json"
    bad.write_text("{ not valid json")
    monkeypatch.setenv("UA_OPS_CONFIG_PATH", str(bad))
    assert ops_config.load_ops_config() == {}


def test_load_ops_config_reads_valid_json(monkeypatch, tmp_path: Path) -> None:
    """A valid config file is parsed into a dict."""
    cfg_path = tmp_path / "ops.json"
    cfg_path.write_text(json.dumps({"skills": {"entries": {"foo": True}}}))
    monkeypatch.setenv("UA_OPS_CONFIG_PATH", str(cfg_path))
    assert ops_config.load_ops_config() == {"skills": {"entries": {"foo": True}}}


def test_write_then_load_round_trips(monkeypatch, tmp_path: Path) -> None:
    """``write_ops_config`` followed by ``load_ops_config`` reproduces the input."""
    cfg_path = tmp_path / "ops.json"
    monkeypatch.setenv("UA_OPS_CONFIG_PATH", str(cfg_path))

    payload = {"skills": {"entries": {"foo": True}}, "notifications": {"channels": ["email"]}}
    written_path = ops_config.write_ops_config(payload)

    assert written_path == cfg_path.resolve()
    assert cfg_path.exists()
    assert ops_config.load_ops_config() == payload


def test_write_ops_config_creates_parent_dir(monkeypatch, tmp_path: Path) -> None:
    """Writing to a path whose parent does not exist yet creates the parent."""
    nested = tmp_path / "deep" / "nest" / "ops.json"
    monkeypatch.setenv("UA_OPS_CONFIG_PATH", str(nested))
    written = ops_config.write_ops_config({"a": 1})
    assert written.exists()
    assert ops_config.load_ops_config() == {"a": 1}


def test_write_ops_config_is_sorted_and_stable(monkeypatch, tmp_path: Path) -> None:
    """The on-disk JSON is sorted (sort_keys=True) so diffs are stable."""
    cfg_path = tmp_path / "ops.json"
    monkeypatch.setenv("UA_OPS_CONFIG_PATH", str(cfg_path))
    ops_config.write_ops_config({"b": 2, "a": 1, "c": {"z": 1, "y": 2}})
    text = cfg_path.read_text()
    # Sorted keys appear in alphabetical order at the top level.
    assert text.index('"a"') < text.index('"b"') < text.index('"c"')


# --------------------------------------------------------------------------- #
# ops_config_schema — structural sanity
# --------------------------------------------------------------------------- #


def test_ops_config_schema_is_a_json_schema_object() -> None:
    """The schema is a well-formed JSON Schema descriptor."""
    schema = ops_config.ops_config_schema()
    assert schema["$schema"].startswith("https://json-schema.org/")
    assert schema["type"] == "object"
    # Core ops-config sections are represented.
    for section in ("skills", "channels", "notifications", "heartbeat_mediation"):
        assert section in schema["properties"]

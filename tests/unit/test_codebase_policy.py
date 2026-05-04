"""Unit tests for codebase_policy pure helper functions.

Tests cover: _truthy, _dedupe_paths, _dedupe_agents,
agent_can_mutate_codebase, path_is_within_roots, repo_mutation_requested,
build_codebase_access, normalize_codebase_access.
No LLM/DB/network dependencies.
"""

from __future__ import annotations

from pathlib import Path

from universal_agent.codebase_policy import (
    _truthy,
    _dedupe_paths,
    _dedupe_agents,
    agent_can_mutate_codebase,
    path_is_within_roots,
    repo_mutation_requested,
    build_codebase_access,
    normalize_codebase_access,
    resolve_codebase_access,
    validate_codebase_root,
)


# ---------------------------------------------------------------------------
# _truthy
# ---------------------------------------------------------------------------


def test_truthy_returns_true_for_bool_true():
    assert _truthy(True) is True


def test_truthy_returns_false_for_bool_false():
    assert _truthy(False) is False


def test_truthy_truthy_strings():
    for val in ("1", "true", "yes", "on", "TRUE", "Yes", "ON"):
        assert _truthy(val) is True, f"{val!r} should be truthy"


def test_truthy_falsy_strings():
    for val in ("0", "false", "no", "off", "FALSE", "No", "OFF"):
        assert _truthy(val) is False, f"{val!r} should be falsy"


def test_truthy_empty_returns_default_false():
    assert _truthy("") is False
    assert _truthy(None) is False


def test_truthy_empty_with_default_true():
    assert _truthy("", default=True) is True
    assert _truthy(None, default=True) is True


def test_truthy_numbers():
    assert _truthy(1) is True
    assert _truthy(0) is False
    assert _truthy(0.0) is False
    assert _truthy(3.14) is True


def test_truthy_unknown_string_returns_default():
    assert _truthy("maybe", default=False) is False
    assert _truthy("maybe", default=True) is True


# ---------------------------------------------------------------------------
# _dedupe_paths
# ---------------------------------------------------------------------------


def test_dedupe_paths_skips_empty_strings():
    result = _dedupe_paths(["", "", ""])
    assert result == []


def test_dedupe_paths_resolves_and_deduplicates(tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    # Same path expressed two ways
    result = _dedupe_paths([str(sub), str(sub.resolve())])
    assert len(result) == 1


def test_dedupe_paths_preserves_order():
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        p1 = str(Path(td) / "a")
        p2 = str(Path(td) / "b")
        result = _dedupe_paths([p1, p2, p1])
        assert len(result) == 2
        assert Path(result[0]).name == "a"
        assert Path(result[1]).name == "b"


def test_dedupe_paths_expands_tilde():
    result = _dedupe_paths(["~/nonexistent_test_path_xyz"])
    assert len(result) == 1
    # Should NOT start with ~
    assert not result[0].startswith("~")


def test_dedupe_paths_accepts_path_objects():
    result = _dedupe_paths([Path("/tmp")])
    assert len(result) == 1


# ---------------------------------------------------------------------------
# _dedupe_agents
# ---------------------------------------------------------------------------


def test_dedupe_agents_strips_and_lowercases():
    result = _dedupe_agents(["  SimOne  ", "CODE-WRITER"])
    assert result == ["simone", "code-writer"]


def test_dedupe_agents_case_insensitive_dedup():
    result = _dedupe_agents(["Simone", "simone", "SIMONE"])
    assert result == ["simone"]


def test_dedupe_agents_skips_empty():
    result = _dedupe_agents(["", "  ", "simone", ""])
    assert result == ["simone"]


def test_dedupe_agents_preserves_order():
    result = _dedupe_agents(["beta", "alpha", "beta", "gamma"])
    assert result == ["beta", "alpha", "gamma"]


# ---------------------------------------------------------------------------
# agent_can_mutate_codebase
# ---------------------------------------------------------------------------


def test_agent_can_mutate_returns_false_for_none_access():
    assert agent_can_mutate_codebase("simone", None) is False


def test_agent_can_mutate_returns_false_when_not_enabled():
    assert (
        agent_can_mutate_codebase(
            "simone", {"enabled": False, "mutation_agents": ["simone"]}
        )
        is False
    )


def test_agent_can_mutate_returns_false_for_empty_agent_name():
    assert (
        agent_can_mutate_codebase(
            "", {"enabled": True, "mutation_agents": ["simone"]}
        )
        is False
    )


def test_agent_can_mutate_returns_true_for_allowed_agent():
    assert (
        agent_can_mutate_codebase(
            "simone", {"enabled": True, "mutation_agents": ["simone", "code-writer"]}
        )
        is True
    )


def test_agent_can_mutate_is_case_insensitive():
    assert (
        agent_can_mutate_codebase(
            "SIMONE", {"enabled": True, "mutation_agents": ["simone"]}
        )
        is True
    )


def test_agent_can_mutate_returns_false_for_unknown_agent():
    assert (
        agent_can_mutate_codebase(
            "rando", {"enabled": True, "mutation_agents": ["simone"]}
        )
        is False
    )


# ---------------------------------------------------------------------------
# path_is_within_roots
# ---------------------------------------------------------------------------


def test_path_is_within_roots_true_for_subpath(tmp_path):
    sub = tmp_path / "deep" / "file.py"
    sub.parent.mkdir(parents=True)
    sub.touch()
    assert path_is_within_roots(str(sub), [str(tmp_path)]) is True


def test_path_is_within_roots_false_for_unrelated(tmp_path):
    assert path_is_within_roots("/totally/unrelated/path", [str(tmp_path)]) is False


def test_path_is_within_roots_empty_roots():
    assert path_is_within_roots("/tmp/foo", []) is False


def test_path_is_within_roots_invalid_path():
    assert path_is_within_roots("\x00bad", ["/tmp"]) is False


# ---------------------------------------------------------------------------
# repo_mutation_requested
# ---------------------------------------------------------------------------


def test_repo_mutation_requested_true_for_truthy_flag():
    assert repo_mutation_requested({"repo_mutation_allowed": True}) is True
    assert repo_mutation_requested({"repo_mutation_allowed": "yes"}) is True


def test_repo_mutation_requested_true_for_code_change_workflow():
    assert repo_mutation_requested({"workflow_kind": "code_change"}) is True


def test_repo_mutation_requested_true_for_coding_task_mission():
    assert repo_mutation_requested({"mission_type": "coding_task"}) is True


def test_repo_mutation_requested_false_for_none():
    assert repo_mutation_requested(None) is False


def test_repo_mutation_requested_false_for_non_dict():
    assert repo_mutation_requested("not a dict") is False


def test_repo_mutation_requested_false_for_empty_dict():
    assert repo_mutation_requested({}) is False


def test_repo_mutation_requested_false_for_unrelated_workflow():
    assert repo_mutation_requested({"workflow_kind": "analysis"}) is False


# ---------------------------------------------------------------------------
# build_codebase_access
# ---------------------------------------------------------------------------


def test_build_codebase_access_with_explicit_roots():
    result = build_codebase_access(enabled=True, roots=["/tmp"])
    assert result["enabled"] is True
    assert any("tmp" in r for r in result["roots"])


def test_build_codebase_access_disabled_returns_enabled_false():
    result = build_codebase_access(enabled=False, roots=["/tmp"])
    assert result["enabled"] is False


def test_build_codebase_access_no_roots_enabled_is_false():
    result = build_codebase_access(enabled=True, roots=[])
    assert result["enabled"] is False


def test_build_codebase_access_default_mutation_agents():
    result = build_codebase_access(enabled=True, roots=["/tmp"])
    assert "simone" in result["mutation_agents"]


def test_build_codebase_access_custom_mutation_agents():
    result = build_codebase_access(
        enabled=True, roots=["/tmp"], mutation_agents=["custom-agent"]
    )
    assert result["mutation_agents"] == ["custom-agent"]


def test_build_codebase_access_validates_codebase_root(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("UA_APPROVED_CODEBASE_ROOTS", str(repo))
    result = build_codebase_access(
        enabled=True, roots=[str(repo)], codebase_root=str(repo)
    )
    assert result["enabled"] is True
    assert str(repo.resolve()) in result["roots"]


# ---------------------------------------------------------------------------
# normalize_codebase_access
# ---------------------------------------------------------------------------


def test_normalize_codebase_access_with_none_policy():
    result = normalize_codebase_access(None)
    assert isinstance(result, dict)
    assert "enabled" in result
    assert "roots" in result
    assert "mutation_agents" in result


def test_normalize_codebase_access_enabled_flag():
    result = normalize_codebase_access({"enabled": "yes", "roots": ["/tmp"]})
    assert result["enabled"] is True


def test_normalize_codebase_access_disabled_flag():
    result = normalize_codebase_access({"enabled": False, "roots": ["/tmp"]})
    assert result["enabled"] is False


def test_normalize_codebase_access_explicit_roots_deduped():
    result = normalize_codebase_access({"enabled": True, "roots": ["/tmp", "/tmp"]})
    assert len(result["roots"]) == 1


def test_normalize_codebase_access_custom_mutation_agents():
    result = normalize_codebase_access(
        {"enabled": True, "roots": ["/tmp"], "mutation_agents": ["Agent-A"]}
    )
    assert result["mutation_agents"] == ["agent-a"]


# ---------------------------------------------------------------------------
# existing tests (preserved from original file)
# ---------------------------------------------------------------------------


def test_validate_codebase_root_accepts_approved_path(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("UA_APPROVED_CODEBASE_ROOTS", str(repo))
    assert validate_codebase_root(str(repo / "src")) == str((repo / "src").resolve())


def test_resolve_codebase_access_enables_request_override(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("UA_APPROVED_CODEBASE_ROOTS", str(repo))

    access = resolve_codebase_access(
        policy=build_codebase_access(enabled=False),
        request_metadata={
            "codebase_root": str(repo),
            "repo_mutation_allowed": True,
        },
    )

    assert access["enabled"] is True
    assert access["codebase_root"] == str(repo.resolve())
    assert str(repo.resolve()) in access["roots"]

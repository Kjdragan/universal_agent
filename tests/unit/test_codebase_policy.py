from universal_agent.codebase_policy import (
    build_codebase_access,
    resolve_codebase_access,
    validate_codebase_root,
)


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

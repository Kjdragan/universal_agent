"""Phase D — canonical-store hygiene guards.

Two classes of guard:

1. The canonical DB-path resolvers in ``durable/db.py`` are cwd-independent
   (``__file__``-derived) and honor their env overrides. A caller can only ever
   produce an off-canonical / orphan DB by passing a *different* path — never by
   running from a different cwd.

2. Skill-content regression pins for the actual Phase D root cause: the
   ``evaluate-and-author-intel-brief`` skill used a placeholder
   ``sqlite3.connect("/path/to/activity_state.db")``, which made the Atlas LLM
   improvise a cwd-relative path and fork orphan ``task_hub.db`` stores that the
   hourly digest never read (split-brain). The cody skills imported a
   non-existent ``universal_agent.activity_db`` module. These tests fail if any
   skill regresses to a placeholder/relative connect or the wrong import module.
"""

from __future__ import annotations

import os
from pathlib import Path

from universal_agent.durable.db import (
    DEFAULT_ACTIVITY_DB_FILENAME,
    get_activity_db_path,
    get_runtime_db_path,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILLS_DIR = REPO_ROOT / ".claude" / "skills"


# --------------------------------------------------------------------------- #
# 1. Resolver behavior
# --------------------------------------------------------------------------- #
def test_get_activity_db_path_honors_env_override(monkeypatch, tmp_path: Path) -> None:
    override = tmp_path / "custom_activity.db"
    monkeypatch.setenv("UA_ACTIVITY_DB_PATH", str(override))
    # The env branch returns before any makedirs side effect -> fully hermetic.
    assert get_activity_db_path() == str(override)


def test_get_runtime_db_path_honors_env_override(monkeypatch, tmp_path: Path) -> None:
    override = tmp_path / "custom_runtime.db"
    monkeypatch.setenv("UA_RUNTIME_DB_PATH", str(override))
    assert get_runtime_db_path() == str(override)


def test_get_activity_db_path_is_cwd_independent(monkeypatch, tmp_path: Path) -> None:
    """With the env override unset, the path is __file__-derived: identical from
    any cwd, absolute, and anchored at AGENT_RUN_WORKSPACES/activity_state.db —
    so it can never resolve to a cwd-relative orphan like ./task_hub.db."""
    monkeypatch.delenv("UA_ACTIVITY_DB_PATH", raising=False)

    first = get_activity_db_path()
    monkeypatch.chdir(tmp_path)
    second = get_activity_db_path()

    assert first == second, "resolver must not depend on cwd"
    assert os.path.isabs(first), "canonical path must be absolute"
    assert first.endswith(
        os.path.join("AGENT_RUN_WORKSPACES", DEFAULT_ACTIVITY_DB_FILENAME)
    )
    # Regression: must never produce the orphan filename or a bare relative name.
    assert not first.endswith("task_hub.db")
    assert Path(first).name == DEFAULT_ACTIVITY_DB_FILENAME


# --------------------------------------------------------------------------- #
# 2. Skill-content regression pins (the actual Phase D root cause)
# --------------------------------------------------------------------------- #
def _skill_text(name: str) -> str:
    path = SKILLS_DIR / name / "SKILL.md"
    assert path.exists(), f"missing skill: {path}"
    return path.read_text(encoding="utf-8")


def test_intel_brief_skill_uses_canonical_resolver_not_placeholder() -> None:
    text = _skill_text("evaluate-and-author-intel-brief")
    # The exact placeholder that caused the split-brain must be gone.
    assert 'sqlite3.connect("/path/to' not in text
    assert "/path/to/activity_state.db" not in text
    # And it must resolve canonically.
    assert "get_activity_db_path" in text


def test_cody_skills_import_canonical_db_module() -> None:
    for name in ("cody-task-dispatcher", "cody-progress-monitor"):
        text = _skill_text(name)
        # The non-existent module must not be referenced.
        assert "universal_agent.activity_db" not in text, name
        # The real resolver module must be used.
        assert "universal_agent.durable.db" in text, name


def test_no_skill_uses_a_placeholder_db_connect() -> None:
    """Broad guard across every skill: no SKILL.md may instruct a
    placeholder/relative sqlite connect for the canonical stores."""
    offenders: list[str] = []
    for skill_md in SKILLS_DIR.glob("*/SKILL.md"):
        text = skill_md.read_text(encoding="utf-8")
        if 'sqlite3.connect("/path/to' in text:
            offenders.append(str(skill_md.relative_to(REPO_ROOT)))
    assert not offenders, f"placeholder DB connect found in: {offenders}"


# --------------------------------------------------------------------------- #
# 3. Same-class guard: youtube_daily_digest._workspace_dir (Phase D follow-up)
# --------------------------------------------------------------------------- #
def test_youtube_workspace_dir_is_cwd_independent(monkeypatch, tmp_path: Path) -> None:
    """youtube_daily_digest._workspace_dir feeds .csi_digests.db /
    youtube_ingestion_state.db. It MUST anchor on the canonical, __file__-derived
    AGENT_RUN_WORKSPACES (never Path.cwd()), else it forks those DBs into an
    orphan workspace when run from a non-repo-root cwd — the Phase D #756 class,
    one timer-migration away from biting."""
    from universal_agent.scripts.youtube_daily_digest import _workspace_dir

    # Clear BOTH overrides so we exercise the __file__-derived canonical fallback
    # (the conftest redirects UA_ACTIVITY_DB_PATH under tmp_path for isolation;
    # the sibling test_get_activity_db_path_is_cwd_independent clears it the same way).
    monkeypatch.delenv("UA_WORKSPACES_DIR", raising=False)
    monkeypatch.delenv("UA_ACTIVITY_DB_PATH", raising=False)
    first = _workspace_dir()
    monkeypatch.chdir(tmp_path)
    second = _workspace_dir()

    assert first == second, "resolver must not depend on cwd"
    assert first.is_absolute(), "canonical workspaces dir must be absolute"
    assert first.name == "AGENT_RUN_WORKSPACES"
    # Regression: must NOT resolve under the (temporary) cwd — the orphan-fork mode.
    assert tmp_path not in first.parents
    assert first != tmp_path / "AGENT_RUN_WORKSPACES"


def test_youtube_workspace_dir_honors_env_override(monkeypatch, tmp_path: Path) -> None:
    override = tmp_path / "ws"
    monkeypatch.setenv("UA_WORKSPACES_DIR", str(override))
    from universal_agent.scripts.youtube_daily_digest import _workspace_dir

    assert _workspace_dir() == override

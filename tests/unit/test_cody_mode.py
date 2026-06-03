"""Hermes Phase E.1 + E.2 — Cody execution-mode toggle unit tests.

Verifies the resolver, schema persistence, and CLI env-scrub behavior.

* E.1 — `resolve_cody_mode` returns "anthropic" by default (hardcoded
  fallback flipped 2026-05-11 PM per operator decision). DB setting
  takes precedence over env, env over hardcoded.
* E.1 — `task_hub_items.cody_mode` column round-trips through
  `upsert_item` / `get_item`.
* E.2 — `set_default_mode` persists the operator UI choice; resolver
  picks it up via the conn parameter.
* E.2.a — `_build_cli_env(cody_mode="anthropic")` strips every
  ANTHROPIC_* env var and forces CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1.
* E.2.a — `_build_cli_env(cody_mode="zai")` inherits ANTHROPIC_* from
  the parent env (current behavior — no regression).
"""

from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from universal_agent import task_hub
from universal_agent.services.cody_mode import (
    get_default_mode_state,
    resolve_cody_mode,
    resolve_from_payload,
    set_default_mode,
)
from universal_agent.vp.clients.claude_cli_client import _build_cli_env
from universal_agent.vp.profiles import get_vp_profile

# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    task_hub.ensure_schema(c)
    yield c
    c.close()


# ── E.1: resolve_cody_mode (new "anthropic" default) ───────────────────────


def test_resolve_defaults_to_anthropic_with_no_task_no_env_no_db(monkeypatch) -> None:
    """2026-05-11 PM operator decision: hardcoded default is "anthropic"."""
    monkeypatch.delenv("UA_CODY_DEFAULT_MODE", raising=False)
    assert resolve_cody_mode(None) == "anthropic"
    assert resolve_cody_mode({}) == "anthropic"


def test_resolve_honors_env_override(monkeypatch) -> None:
    monkeypatch.setenv("UA_CODY_DEFAULT_MODE", "zai")
    assert resolve_cody_mode(None) == "zai"
    assert resolve_cody_mode({}) == "zai"


def test_resolve_task_override_wins_over_env(monkeypatch) -> None:
    monkeypatch.setenv("UA_CODY_DEFAULT_MODE", "zai")
    assert resolve_cody_mode({"cody_mode": "anthropic"}) == "anthropic"


def test_resolve_invalid_task_value_falls_through(monkeypatch) -> None:
    monkeypatch.setenv("UA_CODY_DEFAULT_MODE", "zai")
    # garbage value should be ignored, env wins
    assert resolve_cody_mode({"cody_mode": "garbage"}) == "zai"


def test_resolve_invalid_env_falls_through_to_default(monkeypatch) -> None:
    monkeypatch.delenv("UA_CODY_DEFAULT_MODE", raising=False)
    monkeypatch.setenv("UA_CODY_DEFAULT_MODE", "not-a-mode")
    assert resolve_cody_mode(None) == "anthropic"


def test_resolve_from_payload_reads_payload(monkeypatch) -> None:
    monkeypatch.delenv("UA_CODY_DEFAULT_MODE", raising=False)
    assert resolve_from_payload({"cody_mode": "anthropic"}) == "anthropic"
    assert resolve_from_payload({"cody_mode": "zai"}) == "zai"
    # None falls through to hardcoded default (now anthropic)
    assert resolve_from_payload(None) == "anthropic"


def test_resolve_from_payload_reads_metadata_nested(monkeypatch) -> None:
    """vp_orchestration plumbs cody_mode under metadata.cody_mode."""
    monkeypatch.delenv("UA_CODY_DEFAULT_MODE", raising=False)
    payload = {"metadata": {"cody_mode": "zai"}}
    assert resolve_from_payload(payload) == "zai"


# ── E.2: DB setting (operator UI toggle) ──────────────────────────────────


def test_set_default_mode_persists_to_settings(conn: sqlite3.Connection, monkeypatch) -> None:
    monkeypatch.delenv("UA_CODY_DEFAULT_MODE", raising=False)
    set_default_mode(conn, "zai", updated_by="operator")
    # Resolver picks up the DB setting with a conn passed in.
    assert resolve_cody_mode(None, conn=conn) == "zai"
    # State endpoint returns the audit fields.
    state = get_default_mode_state(conn)
    assert state["mode"] == "zai"
    assert state["source"] == "db_setting"
    assert state["updated_by"] == "operator"
    assert state["updated_at"]  # non-empty ISO timestamp


def test_set_default_mode_validates_input(conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError) as exc:
        set_default_mode(conn, "garbage")
    assert "must be one of" in str(exc.value)


def test_get_default_mode_state_falls_through_env(conn: sqlite3.Connection, monkeypatch) -> None:
    monkeypatch.setenv("UA_CODY_DEFAULT_MODE", "zai")
    state = get_default_mode_state(conn)
    assert state["mode"] == "zai"
    assert state["source"] == "env_var"


def test_get_default_mode_state_hardcoded_default(conn: sqlite3.Connection, monkeypatch) -> None:
    monkeypatch.delenv("UA_CODY_DEFAULT_MODE", raising=False)
    state = get_default_mode_state(conn)
    assert state["mode"] == "anthropic"
    assert state["source"] == "hardcoded_default"


def test_db_setting_beats_env(conn: sqlite3.Connection, monkeypatch) -> None:
    """DB setting (operator UI) takes precedence over env var (deploy override)."""
    monkeypatch.setenv("UA_CODY_DEFAULT_MODE", "zai")
    set_default_mode(conn, "anthropic", updated_by="operator")
    assert resolve_cody_mode(None, conn=conn) == "anthropic"


def test_task_override_beats_db_setting(conn: sqlite3.Connection) -> None:
    """Per-task `cody_mode` field is the highest-priority override."""
    set_default_mode(conn, "zai", updated_by="operator")
    assert resolve_cody_mode({"cody_mode": "anthropic"}, conn=conn) == "anthropic"


# ── E.1: schema column round-trip ──────────────────────────────────────────


def test_cody_mode_column_persists_through_upsert(conn: sqlite3.Connection) -> None:
    task_hub.upsert_item(
        conn,
        {
            "task_id": "task:cody-anth",
            "source_kind": "internal",
            "title": "anthropic-mode task",
            "cody_mode": "anthropic",
        },
    )
    row = task_hub.get_item(conn, "task:cody-anth")
    assert row is not None
    assert row["cody_mode"] == "anthropic"


def test_cody_mode_null_when_not_set(conn: sqlite3.Connection) -> None:
    task_hub.upsert_item(
        conn,
        {
            "task_id": "task:no-mode",
            "source_kind": "internal",
            "title": "no mode",
        },
    )
    row = task_hub.get_item(conn, "task:no-mode")
    assert row is not None
    assert row.get("cody_mode") in (None, "")


def test_cody_mode_invalid_value_normalized_to_null(conn: sqlite3.Connection) -> None:
    task_hub.upsert_item(
        conn,
        {
            "task_id": "task:bad-mode",
            "source_kind": "internal",
            "title": "bad mode",
            "cody_mode": "junk",
        },
    )
    row = task_hub.get_item(conn, "task:bad-mode")
    assert row is not None
    assert row.get("cody_mode") in (None, "")


def test_cody_mode_preserved_on_re_upsert(conn: sqlite3.Connection) -> None:
    """Per-task override survives an upsert that doesn't mention cody_mode."""
    task_hub.upsert_item(
        conn,
        {
            "task_id": "task:keep-anth",
            "source_kind": "internal",
            "title": "first",
            "cody_mode": "anthropic",
        },
    )
    # Re-upsert without cody_mode key — must NOT clobber the existing value.
    task_hub.upsert_item(
        conn,
        {"task_id": "task:keep-anth", "title": "second"},
    )
    row = task_hub.get_item(conn, "task:keep-anth")
    assert row is not None
    assert row["cody_mode"] == "anthropic"


# ── E.2.a: _build_cli_env env scrubbing ────────────────────────────────────


def test_build_cli_env_zai_inherits_anthropic_vars(tmp_path: Path, monkeypatch) -> None:
    """Default zai mode: parent ANTHROPIC_* env survives into subprocess env."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-zai-routed-token")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://api.z.ai/anthropic")
    env = _build_cli_env(enable_agent_teams=False, workspace_dir=tmp_path, cody_mode="zai")
    assert env.get("ANTHROPIC_API_KEY") == "fake-zai-routed-token"
    assert env.get("ANTHROPIC_BASE_URL") == "https://api.z.ai/anthropic"


def test_build_cli_env_anthropic_strips_all_anthropic_vars(
    tmp_path: Path, monkeypatch
) -> None:
    """Anthropic mode: every ANTHROPIC_* env var is scrubbed before exec."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-zai-routed-token")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://api.z.ai/anthropic")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "leaky")
    env = _build_cli_env(
        enable_agent_teams=False, workspace_dir=tmp_path, cody_mode="anthropic"
    )
    leaked = {k: v for k, v in env.items() if k.startswith("ANTHROPIC_")}
    assert leaked == {}, f"Anthropic vars leaked through: {leaked}"
    # Anthropic mode forces agent teams on (the whole point of Anthropic mode).
    assert env.get("CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS") == "1"


def test_build_cli_env_anthropic_preserves_workspace_signal(
    tmp_path: Path, monkeypatch
) -> None:
    """Anthropic-mode scrub must not nuke workspace context env vars."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-token")
    env = _build_cli_env(
        enable_agent_teams=False, workspace_dir=tmp_path, cody_mode="anthropic"
    )
    assert env["CURRENT_RUN_WORKSPACE"] == str(tmp_path)
    assert env["CURRENT_SESSION_WORKSPACE"] == str(tmp_path)


# ── Per-VP profile inference binding (2026-06-03) ──────────────────────────
#
# The agent defines its own inference backend, not the dispatching function:
# CODIE (vp.coder.primary) → anthropic (Max, for demo/coding features);
# ATLAS (vp.general.primary) and any other VP → zai. This replaced the
# VP-blind hardcoded "anthropic" default that silently forced ATLAS
# research/intel missions onto the Max plan.


def test_profile_carries_inference_mode() -> None:
    """VpProfile binds the inference backend per agent."""
    coder = get_vp_profile("vp.coder.primary")
    general = get_vp_profile("vp.general.primary")
    assert coder is not None and coder.inference_mode == "anthropic"
    assert general is not None and general.inference_mode == "zai"


def test_resolve_atlas_defaults_to_zai(monkeypatch) -> None:
    """ATLAS (generalist) defaults to zai when no override is set."""
    monkeypatch.delenv("UA_CODY_DEFAULT_MODE", raising=False)
    assert resolve_cody_mode(None, vp_id="vp.general.primary") == "zai"
    assert resolve_cody_mode({}, vp_id="vp.general.primary") == "zai"


def test_resolve_codie_defaults_to_anthropic(monkeypatch) -> None:
    """CODIE (coder) defaults to anthropic (Max) for coding/demo work."""
    monkeypatch.delenv("UA_CODY_DEFAULT_MODE", raising=False)
    assert resolve_cody_mode(None, vp_id="vp.coder.primary") == "anthropic"


def test_resolve_unknown_vp_falls_back_to_hardcoded(monkeypatch) -> None:
    """Unknown/disabled VP → legacy hardcoded fallback (anthropic)."""
    monkeypatch.delenv("UA_CODY_DEFAULT_MODE", raising=False)
    assert resolve_cody_mode(None, vp_id="vp.does.not.exist") == "anthropic"


def test_no_vp_id_preserves_legacy_default(monkeypatch) -> None:
    """Omitting vp_id (e.g. demo path) preserves the legacy default."""
    monkeypatch.delenv("UA_CODY_DEFAULT_MODE", raising=False)
    assert resolve_cody_mode(None) == "anthropic"


def test_per_task_override_beats_profile() -> None:
    """Per-task cody_mode wins over the per-VP profile default (both ways)."""
    assert (
        resolve_cody_mode({"cody_mode": "anthropic"}, vp_id="vp.general.primary")
        == "anthropic"
    )
    assert (
        resolve_cody_mode({"cody_mode": "zai"}, vp_id="vp.coder.primary") == "zai"
    )


def test_db_setting_beats_profile_the_switch(conn: sqlite3.Connection, monkeypatch) -> None:
    """The operator DB switch flips CODIE off Max to zai without code change."""
    monkeypatch.delenv("UA_CODY_DEFAULT_MODE", raising=False)
    set_default_mode(conn, "zai", updated_by="operator")
    # CODIE would default to anthropic via profile, but the global switch wins.
    assert resolve_cody_mode(None, conn=conn, vp_id="vp.coder.primary") == "zai"


def test_env_beats_profile(monkeypatch) -> None:
    """UA_CODY_DEFAULT_MODE overrides the per-VP profile default."""
    monkeypatch.setenv("UA_CODY_DEFAULT_MODE", "zai")
    assert resolve_cody_mode(None, vp_id="vp.coder.primary") == "zai"

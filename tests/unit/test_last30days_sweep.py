"""Tests for the headless last30days evidence-sweep wrapper.

The load-bearing properties: the third-party engine never receives our
environment, paid sources cannot be reached by accident, a timeout/crash
degrades to a classified record instead of an exception, and the output
contract stays stable for the showrunner lane that consumes it.
"""

from __future__ import annotations

import json
from pathlib import Path

from universal_agent.scripts.last30days_sweep import (
    FREE_SOURCES,
    GRANTABLE_SECRETS,
    SCHEMA_VERSION,
    build_engine_env,
    main,
    run_sweep,
    summarize_report,
)

ENGINE = Path("/nonexistent/engine.py")  # never spawned — runners are injected


def _report(**overrides):
    base = {
        "topic": "mcp security",
        "items_by_source": {
            "hackernews": [
                {
                    "title": "Show HN: vulnerable MCP servers",
                    "url": "https://news.ycombinator.com/item?id=1",
                    "author": "hnuser",
                    "published_at": "2026-07-30",
                    "engagement": {"points": 220, "comments": 84},
                    "snippet": "OWASP agentic top-10 playground",
                },
            ],
            "reddit": [
                {
                    "title": "MCP auth is a mess",
                    "url": "https://reddit.com/r/x/1",
                    "container": "r/LocalLLaMA",
                    "engagement": {"upvotes": 40, "comments": 12},
                    "body": "long body text",
                },
            ],
            "polymarket": [],
        },
        "errors_by_source": {"x": "no key"},
        "warnings": ["reddit rate limited"],
    }
    base.update(overrides)
    return base


class _Runner:
    """Injectable engine runner: returns a canned (rc, stdout, stderr)."""

    def __init__(self, rc=0, stdout="", stderr="", raises=None):
        self.rc, self.stdout, self.stderr, self.raises = rc, stdout, stderr, raises
        self.calls: list[tuple[list[str], dict, int]] = []

    def __call__(self, cmd, env, timeout):
        self.calls.append((list(cmd), dict(env), timeout))
        if self.raises:
            raise self.raises
        return self.rc, self.stdout, self.stderr


# ── trust boundary ───────────────────────────────────────────────────────


def test_engine_env_excludes_our_secrets(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
    monkeypatch.setenv("ZAI_API_KEY", "zai-secret")
    monkeypatch.setenv("AGENTMAIL_API_KEY", "am-secret")
    monkeypatch.setenv("PATH", "/usr/bin")
    env = build_engine_env([], use_user_config=False)
    assert "ANTHROPIC_API_KEY" not in env
    assert "ZAI_API_KEY" not in env
    assert "AGENTMAIL_API_KEY" not in env
    assert env["PATH"] == "/usr/bin"
    # no-config mode so a stray key in the user's engine .env cannot leak in
    assert env["LAST30DAYS_CONFIG_DIR"] == ""


def test_engine_env_forwards_only_granted_secret(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "xai-secret")
    monkeypatch.setenv("SCRAPECREATORS_API_KEY", "paid-secret")
    env = build_engine_env(["XAI_API_KEY"], use_user_config=False)
    assert env["XAI_API_KEY"] == "xai-secret"
    assert "SCRAPECREATORS_API_KEY" not in env


def test_user_config_opt_in_removes_noconfig_flag(monkeypatch):
    env = build_engine_env([], use_user_config=True)
    assert "LAST30DAYS_CONFIG_DIR" not in env


def test_main_refuses_unknown_grant(capsys):
    rc = main(["topic", "--grant", "ANTHROPIC_API_KEY"])
    assert rc == 2
    assert "refusing unknown grant" in capsys.readouterr().err


def test_paid_sources_are_not_default():
    assert FREE_SOURCES == ("reddit", "hackernews", "polymarket")
    assert "SCRAPECREATORS_API_KEY" in GRANTABLE_SECRETS  # grantable, never default


# ── command construction ─────────────────────────────────────────────────


def test_command_uses_headless_json_contract():
    runner = _Runner(stdout=json.dumps(_report()))
    run_sweep("mcp security", engine_path=ENGINE, runner=runner, lookback_days=14)
    cmd = runner.calls[0][0]
    assert "--emit" in cmd and cmd[cmd.index("--emit") + 1] == "json"
    assert cmd[cmd.index("--lookback-days") + 1] == "14"
    assert cmd[cmd.index("--search") + 1] == "reddit,hackernews,polymarket"
    # No --plan: the engine's documented deterministic headless path (no LLM).
    assert "--plan" not in cmd


def test_depth_flag_forwarded():
    runner = _Runner(stdout=json.dumps(_report()))
    run_sweep("t", engine_path=ENGINE, runner=runner, depth="quick")
    assert "--quick" in runner.calls[0][0]


# ── summarization contract ───────────────────────────────────────────────


def test_summarize_ranks_by_engagement_and_caps():
    summary = summarize_report(_report(), max_items=1)
    assert len(summary["evidence"]) == 1
    top = summary["evidence"][0]
    assert top["source"] == "hackernews"          # 220+84 beats 40+12
    assert top["engagement_total"] == 304
    assert summary["counts_by_source"]["polymarket"] == 0
    assert summary["sources_with_evidence"] == ["hackernews", "reddit"]
    assert summary["engine_errors"] == {"x": "no key"}


def test_summarize_tolerates_shape_drift():
    summary = summarize_report(
        {"items_by_source": {"reddit": "not-a-list"}, "warnings": "not-a-list"},
        max_items=5,
    )
    assert summary["evidence"] == []
    assert summary["warnings"] == []


def test_result_contract_is_stable_and_versioned():
    runner = _Runner(stdout=json.dumps(_report()))
    out = run_sweep("t", engine_path=ENGINE, runner=runner).to_dict()
    assert out["schema_version"] == SCHEMA_VERSION
    assert set(out) == {
        "schema_version", "topic", "ok", "generated_at", "lookback_days",
        "sources_requested", "sources_with_evidence", "counts_by_source",
        "evidence", "engine_errors", "warnings", "worker_exit", "error",
    }
    assert out["ok"] is True
    assert out["worker_exit"]["outcome"] == "clean_exit_zero"


# ── failure modes are classified, never raised ───────────────────────────


def test_timeout_is_classified_not_raised():
    runner = _Runner(raises=TimeoutError("boom"))
    res = run_sweep("t", engine_path=ENGINE, runner=runner, timeout_seconds=5)
    assert res.ok is False
    assert res.worker_exit["outcome"] == "timeout_killed"
    assert "timed out" in res.error


def test_spawn_failure_is_classified():
    runner = _Runner(raises=OSError("no such binary"))
    res = run_sweep("t", engine_path=ENGINE, runner=runner)
    assert res.ok is False
    assert res.worker_exit["outcome"] == "nonzero_exit"
    assert "failed to spawn" in res.error


def test_nonzero_exit_reports_stderr_tail():
    runner = _Runner(rc=2, stderr="usage error blah")
    res = run_sweep("t", engine_path=ENGINE, runner=runner)
    assert res.ok is False
    assert "engine exited 2" in res.error
    assert res.worker_exit["is_failure"] is True


def test_signaled_child_is_classified():
    runner = _Runner(rc=-9)
    res = run_sweep("t", engine_path=ENGINE, runner=runner)
    assert res.worker_exit["outcome"] == "signaled"


def test_unparseable_json_is_reported():
    runner = _Runner(stdout="not json at all")
    res = run_sweep("t", engine_path=ENGINE, runner=runner)
    assert res.ok is False
    assert "unparseable JSON" in res.error


def test_clean_run_with_zero_evidence_is_not_ok():
    """'Nobody is talking about this' must be distinguishable from success."""
    runner = _Runner(stdout=json.dumps({"items_by_source": {"reddit": []}}))
    res = run_sweep("t", engine_path=ENGINE, runner=runner)
    assert res.ok is False
    assert res.error == "engine returned no evidence items"
    assert res.worker_exit["outcome"] == "clean_exit_zero"

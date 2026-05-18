"""Regression tests for the VP worker's post-mission PR hook.

Specifically guards the bug observed 2026-05-17: ``_post_mission_push_pr_merge``
was hard-coding ``"develop"`` for the PR base and the ``git log develop..HEAD``
diff check, causing every doc-maintenance mission's PR to fail with
HTTP 422 ``{"field":"base","code":"invalid"}`` because ``develop`` was
retired on 2026-05-10.
"""

from __future__ import annotations

import importlib


def test_pr_base_branch_constant_defaults_to_main(monkeypatch):
    """Bare module import (no env override) must resolve base to ``main``.

    Guards against silent reintroduction of ``develop`` as a default.
    """
    monkeypatch.delenv("UA_GH_PR_BASE_BRANCH", raising=False)
    from universal_agent.vp import worker_loop
    importlib.reload(worker_loop)
    assert worker_loop._PR_BASE_BRANCH == "main"


def test_pr_base_branch_constant_respects_env_override(monkeypatch):
    """Operator can override the default for staging via env var."""
    monkeypatch.setenv("UA_GH_PR_BASE_BRANCH", "staging")
    from universal_agent.vp import worker_loop
    importlib.reload(worker_loop)
    try:
        assert worker_loop._PR_BASE_BRANCH == "staging"
    finally:
        # Restore default for other tests.
        monkeypatch.delenv("UA_GH_PR_BASE_BRANCH", raising=False)
        importlib.reload(worker_loop)


def test_no_lingering_develop_literals_in_post_mission_hook():
    """Source-level guard: the hook body must not reference ``develop`` as
    an active branch literal anywhere (the only allowed mention is in a
    historical-context comment about the 2026-05-10 retirement)."""
    import inspect

    from universal_agent.vp import worker_loop

    src = inspect.getsource(worker_loop._post_mission_push_pr_merge)
    # No git refs against develop, and no "base": "develop" PR payload.
    assert "develop..HEAD" not in src
    assert '"develop"' not in src
    assert '"base": "develop"' not in src
    # No checkout/pull of develop.
    assert "checkout\", \"develop" not in src.replace("'", '"')
    assert "pull\", \"origin\", \"develop" not in src.replace("'", '"')


def test_post_mission_hook_pr_payload_uses_base_constant(monkeypatch):
    """End-to-end: when the hook reaches the PR-creation step it must
    send ``"base": _PR_BASE_BRANCH`` in the JSON body to the GitHub API.

    We stub subprocess + urllib to capture the request body without
    touching the network or a real git repo.
    """
    import json
    from unittest.mock import MagicMock, patch

    from universal_agent.vp import worker_loop

    captured = {}

    # Fake subprocess.run results in the sequence the hook calls them:
    # 1. git rev-parse --abbrev-ref HEAD          → "docs/test"
    # 2. git log {_PR_BASE_BRANCH}..HEAD --oneline → "abc1234 docs: x"
    # 3. git remote get-url origin                → has x-access-token:TOKEN@
    # 4. git push -u origin <branch>              → rc=0
    # 5. (after merge) checkout
    # 6. (after merge) pull
    call_count = {"n": 0}

    def fake_run(cmd, **_kwargs):
        call_count["n"] += 1
        result = MagicMock()
        result.returncode = 0
        result.stderr = ""
        if cmd[:2] == ["git", "rev-parse"]:
            result.stdout = "docs/test-branch\n"
        elif cmd[:2] == ["git", "log"]:
            captured["log_cmd"] = list(cmd)
            result.stdout = "abc1234 docs: test commit\n"
        elif cmd[:3] == ["git", "remote", "get-url"]:
            result.stdout = "https://x-access-token:FAKE_TOKEN@github.com/x/y.git\n"
        elif cmd[:2] == ["git", "push"]:
            result.stdout = ""
        else:
            # checkout / pull / anything else — record but no-op.
            captured.setdefault("other_cmds", []).append(list(cmd))
            result.stdout = ""
        return result

    class _FakeResp:
        def __init__(self, payload):
            self._payload = payload

        def read(self):
            return json.dumps(self._payload).encode()

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    def fake_urlopen(req, **_kwargs):
        # Method=POST on /pulls is the PR creation call.
        if req.method == "POST" and "/pulls" in req.full_url and "/merge" not in req.full_url:
            captured["pr_body"] = json.loads(req.data.decode())
            return _FakeResp({"number": 42, "html_url": "https://github.com/x/y/pull/42"})
        if req.method == "PUT" and "/merge" in req.full_url:
            return _FakeResp({"merged": True, "message": "ok"})
        raise AssertionError(f"unexpected request: {req.method} {req.full_url}")

    # Patch record_mission_pr so we don't reach into task_hub during the test.
    with patch.object(worker_loop.subprocess, "run", side_effect=fake_run), \
         patch("urllib.request.urlopen", side_effect=fake_urlopen), \
         patch(
             "universal_agent.services.vp_mission_pr_reconciler.record_mission_pr",
         ) as _rec, \
         patch(
             "universal_agent.durable.db.connect_runtime_db",
             return_value=MagicMock(commit=MagicMock(), close=MagicMock()),
         ), \
         patch(
             "universal_agent.durable.db.get_activity_db_path",
             return_value="/tmp/fake.db",
         ):
        worker_loop._post_mission_push_pr_merge(
            workspace_root="/tmp/fake-workspace",
            mission_id="vp-mission-test-001",
        )

    assert captured.get("pr_body"), "PR creation request was not made"
    assert captured["pr_body"]["base"] == worker_loop._PR_BASE_BRANCH
    assert captured["pr_body"]["base"] == "main"  # belt-and-suspenders
    # And the diff command must reference the same base, not "develop".
    assert captured["log_cmd"][2] == f"{worker_loop._PR_BASE_BRANCH}..HEAD"

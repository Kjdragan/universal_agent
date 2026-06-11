"""Unit tests for the deslop auto-remediation dispatcher.

Mirrors ``test_ci_failure_grace_recheck.py``: a ``FakeGh`` that maps gh
subcommands to canned responses, pure-decision parametrize tables (no network),
and monkeypatched executors for the orchestration path.

The never-auto gate (``is_never_auto`` / ``classify_issue`` / ``resolve_delivery``)
is the safety-critical part — it gets an exhaustive table and an explicit
invariant test (observe mode and the never-auto class can NEVER auto-merge).
"""

from __future__ import annotations

import pytest

from universal_agent.scripts import deslop_remediation_dispatch as mod
from universal_agent.scripts.deslop_remediation_dispatch import (
    CLASS_NEEDS_OPERATOR,
    CLASS_NEVER_AUTO,
    CLASS_TIER_A,
    DELIVERY_AUTO_MERGE,
    DELIVERY_DRAFT_EMAIL,
    LABEL_DISPATCHED,
    LABEL_NEEDS_OPERATOR,
    MODE_AUTO,
    MODE_OBSERVE,
    DeslopIssue,
    Finding,
    classify_issue,
    decide_action,
    is_never_auto,
    parse_findings,
    parse_source_pr,
    process_issue,
    resolve_delivery,
    run_dispatch,
)

# --------------------------------------------------------------------------- #
# Fixtures / fakes
# --------------------------------------------------------------------------- #


class FakeGh:
    """Maps a gh subcommand (argv[0], argv[1]) to a canned (rc, stdout)."""

    def __init__(self, responses: dict[tuple[str, str], tuple[int, str]]):
        self.responses = responses
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str]) -> tuple[int, str, str]:
        self.calls.append(args)
        key = (args[0], args[1] if len(args) > 1 else "")
        rc, out = self.responses.get(key, (0, ""))
        return rc, out, ""


# A real #798-shaped body (two findings on the same SAFE file).
BODY_798 = (
    "<!-- deslop-advisory -->\n"
    "## 🧹 Deslop advisory (report-only)\n\n"
    "_Advisory only — this never blocks a PR or auto-merge._\n\n"
    "Found 2 suggestion(s):\n\n"
    "- 🟡 **[medium]** `CSI_Ingester/development/csi_ingester/batch_brief.py` — "
    "Redundant comment block restating the control flow.\n"
    "  - _fix_: Remove the 5-line comment block.\n"
    "- 🟢 **[low]** `CSI_Ingester/development/csi_ingester/batch_brief.py` — "
    "Verbose URL normalization comment.\n"
    "  - _fix_: Reduce to a single-line comment.\n\n"
    "---\n\n"
    "Tracked from PR #797 (https://github.com/Kjdragan/universal_agent/pull/797).\n"
)


def _issue(number: int = 798, body: str = BODY_798, labels: list[str] | None = None) -> DeslopIssue:
    return DeslopIssue(
        number=number,
        title=f"deslop findings: PR #{number}",
        body=body,
        labels=list(labels or []),
        findings=parse_findings(body),
        source_pr=parse_source_pr(body),
    )


# --------------------------------------------------------------------------- #
# parse_findings / parse_source_pr
# --------------------------------------------------------------------------- #


def test_parse_findings_real_body():
    findings = parse_findings(BODY_798)
    assert len(findings) == 2
    assert findings[0].file == "CSI_Ingester/development/csi_ingester/batch_brief.py"
    assert findings[0].severity == "medium"
    assert "Redundant comment block" in findings[0].issue
    assert findings[0].fix.startswith("Remove the 5-line")
    assert findings[1].severity == "low"
    assert findings[1].fix.startswith("Reduce to a single-line")


def test_parse_findings_distinct_paths():
    body = (
        "Found 2 suggestion(s):\n\n"
        "- 🔴 **[high]** `scripts/deploy/remote_deploy.sh` — over-broad except.\n"
        "  - _fix_: remove the wrapper.\n"
        "- 🟢 **[low]** `src/universal_agent/foo.py` — redundant comment.\n"
        "  - _fix_: delete it.\n"
    )
    findings = parse_findings(body)
    assert [f.file for f in findings] == [
        "scripts/deploy/remote_deploy.sh",
        "src/universal_agent/foo.py",
    ]


def test_parse_findings_missing_fix_line():
    body = "- 🟡 **[medium]** `src/x.py` — something\n"
    findings = parse_findings(body)
    assert len(findings) == 1
    assert findings[0].fix == ""


def test_parse_findings_no_slop():
    assert parse_findings("✅ No slop found in this diff.") == []
    assert parse_findings("") == []


def test_parse_findings_hyphen_separator_variant():
    # A plain hyphen instead of an em-dash still parses.
    body = "- 🟡 **[medium]** `src/x.py` - plain hyphen issue\n"
    findings = parse_findings(body)
    assert len(findings) == 1
    assert findings[0].file == "src/x.py"


def test_parse_source_pr():
    assert parse_source_pr(BODY_798) == 797
    assert parse_source_pr("no pr here") is None


# --------------------------------------------------------------------------- #
# is_never_auto — the safety-critical gate (exhaustive)
# --------------------------------------------------------------------------- #

NEVER_AUTO_CASES = [
    # deploy scripts
    "scripts/deploy/remote_deploy.sh",
    "scripts/deploy/install_nginx_app_config.sh",
    "remote_deploy.sh",
    "scripts/deploy_prod.sh",
    "./scripts/deploy/remote_deploy.sh",
    "scripts\\deploy\\remote_deploy.sh",  # windows-y separator
    "  scripts/deploy/remote_deploy.sh  ",  # surrounding whitespace
    "scripts//deploy/install_nginx_app_config.sh",  # double-slash obfuscation
    "scripts//deploy//install_nginx_app_config.sh",
    # CI workflow machinery
    ".github/workflows/deploy.yml",
    ".github/workflows/pr-auto-merge.yml",
    "/.github/workflows/x.yml",
    ".github/actions/foo/action.yml",
    "a/.github/CODEOWNERS",
    # --- deploy/ship/release scripts (adversarial: real tracked files) ---
    "ship_workflow.sh",  # REAL: fast-forwards main + triggers prod deploy
    "scratch/ship.sh",  # REAL duplicate
    "scripts/publish_scratch.sh",  # REAL cross-host VPS write
    "ops/release.sh",
    "bin/deploy",  # extensionless deploy launcher
    "redeploy.sh",
    "scripts/promote_to_prod.sh",
    "rollout.sh",
    # --- systemd-installer / host control-plane shell scripts (2nd adversarial pass) ---
    "scripts/install_vps_systemd_units.sh",  # REAL: writes units to /etc/systemd/system
    "CSI_Ingester/development/scripts/csi_install_systemd_extras.sh",  # REAL: runs on every deploy
    "scripts/install_uv_cache_prune_timer.sh",  # REAL
    "scripts/configure_docs_server.sh",  # REAL
    "scripts/some_unmatched_helper.sh",  # generic shell — over-catch is safe
    "tools/bootstrap_host.bash",
    # --- unicode / zero-width obfuscation of a deploy path ---
    "scripts/​remote_deploy",  # zero-width space stripped -> remote_deploy
    "scripts/ＤＥＰＬＯＹ",  # fullwidth -> NFKC -> deploy
    # secrets / infisical / env (adversarial)
    "src/universal_agent/infisical_loader.py",  # intentional over-catch
    "config/secrets.yaml",
    "deploy/credentials.json",
    ".env",
    ".env.production",
    "config/app.env",
    "CSI_Ingester/development/deployment/systemd/csi-ingester.env.example",  # REAL: .env mid-name
    "service.env.local",
    ".envrc",
    "env.production",
    "id_rsa",
    "id_ed25519",
    "deploy_key",
    "server.pem",
    "private.key",
    "keystore.jks",
    "cert.pfx",
    "tls.p12",
    ".netrc",
    ".npmrc",
    ".pypirc",
    "service-account.json",
    "gcp-service-account.json",
    "config/token.json",
    "auth_token.txt",
    "github_token",
    "vault.json",
    ".vault-token",
    # DB schema / migrations (adversarial: 'migrate' verb, numbered, real files)
    "src/universal_agent/durable/migrations/0012_add_col.py",
    "migrations/0001_init.sql",
    "db/foo_migration.py",
    "schema.sql",
    "src/universal_agent/db_schema.py",  # 'schema' in base
    "scripts/migrate_activity_db.sh",  # REAL: mutates cross-host activity_state.db
    "scripts/memory_hard_cut_migrate.py",  # REAL
    "scripts/migrate_session_memory_dbs.py",  # REAL
    "scripts/migrate_proactive_health_parked_rows.py",  # REAL
    "CSI_Ingester/development/scripts/csi_migrate_from_legacy.sh",  # REAL
    "src/universal_agent/durable/0014_add_index.py",  # numbered, not under migrations/
    "src/universal_agent/durable/V2__add_column.py",  # Flyway-style
    "data/foo.ddl",
    # cross-host / sqlite DBs + sidecars + alt extensions (adversarial)
    "csi.db",
    "AGENT_RUN_WORKSPACES/activity_state.db",
    "data/runtime_state.db",
    "data/whatever.sqlite3",
    "x.db",
    "data/runtime.sqlite-wal",
    "data/runtime.sqlite-shm",
    "data/foo.db-journal",
    "data/foo.db3",
    "data/foo.duckdb",
    "data/foo.sqlitedb",
    # systemd units (adversarial: full unit set, templates, drop-ins, real files)
    "ua-backlog-triage.service",
    "ua-backlog-triage.timer",
    "CSI_Ingester/development/deployment/systemd/csi.target",  # REAL .target
    "deployment/systemd/universal-agent-stack-limit.conf",  # REAL drop-in conf (systemd/ dir)
    "deployment/systemd/templates/universal-agent-api.service.template",  # REAL template
    "deployment/systemd/csi.service.d/override.conf",  # drop-in dir
    "deployment/systemd/foo.path",
    "deployment/systemd/foo.slice",
    "foo.automount",
    "foo.service.j2",
    # unknown / empty -> cannot prove safe
    "",
    "   ",
]

SAFE_CASES = [
    "CSI_Ingester/development/csi_ingester/batch_brief.py",
    "src/universal_agent/foo.py",
    "scripts/deslop_advisory.py",  # under scripts/ but NOT scripts/deploy/ and no 'deploy' in base
    "tests/unit/test_foo.py",
    "README.md",
    "docs/operations/runbook.md",
    "src/universal_agent/services/telegram_send.py",
    # no-over-catch boundaries the adversarial sweep confirmed must stay SAFE:
    "src/universal_agent/services/cody_token_tracking.py",  # 'token' but a code module
    "src/universal_agent/release_notes.py",  # 'release' verb but a src module, not a script
    "tests/unit/test_mission_guardrails_vp_dispatch_credit.py",  # 'credit' != 'credential'
    ".claude/skills/structured-output-schema-compliance/SKILL.md",  # 'schema' only in dir
]


@pytest.mark.parametrize("path", NEVER_AUTO_CASES)
def test_is_never_auto_true(path):
    assert is_never_auto(path) is True, f"expected never-auto for {path!r}"


@pytest.mark.parametrize("path", SAFE_CASES)
def test_is_never_auto_false(path):
    assert is_never_auto(path) is False, f"expected SAFE for {path!r}"


# --------------------------------------------------------------------------- #
# classify_issue
# --------------------------------------------------------------------------- #


def test_classify_tier_a_all_safe():
    cls, _ = classify_issue(_issue())
    assert cls == CLASS_TIER_A


def test_classify_never_auto_when_any_file_tainted():
    body = (
        "- 🟢 **[low]** `src/universal_agent/foo.py` — redundant comment.\n"
        "  - _fix_: delete it.\n"
        "- 🔴 **[high]** `scripts/deploy/remote_deploy.sh` — verbose comment.\n"
        "  - _fix_: trim it.\n"
    )
    cls, reason = classify_issue(_issue(body=body))
    assert cls == CLASS_NEVER_AUTO
    assert "remote_deploy.sh" in reason


def test_classify_needs_operator_no_findings():
    cls, reason = classify_issue(_issue(body="✅ No slop found."))
    assert cls == CLASS_NEEDS_OPERATOR
    assert reason == "no_parseable_findings"


def test_classify_needs_operator_unparseable_path():
    issue = DeslopIssue(number=1, findings=[Finding(file="")])
    cls, reason = classify_issue(issue)
    assert cls == CLASS_NEEDS_OPERATOR
    assert reason == "unparseable_file_path"


# --------------------------------------------------------------------------- #
# Content scan — the robust catch for embedded-DDL modules (task_hub.py class)
# --------------------------------------------------------------------------- #


def test_file_has_ddl_detects_create_table(tmp_path):
    f = tmp_path / "task_hub.py"
    f.write_text("import sqlite3\nDDL = '''CREATE TABLE task_hub_items (id INTEGER)'''\n")
    assert mod.file_has_ddl(str(f)) is True


def test_file_has_ddl_false_for_plain_module(tmp_path):
    f = tmp_path / "batch_brief.py"
    f.write_text("def brief():\n    return 'hello'  # no DDL here\n")
    assert mod.file_has_ddl(str(f)) is False


def test_file_has_ddl_missing_file_is_false():
    assert mod.file_has_ddl("/no/such/file/task_hub.py") is False


def test_classify_content_scan_upgrades_path_safe_ddl_module(tmp_path):
    """A path-SAFE module name that embeds DDL must classify never_auto when a
    codebase root is available (the task_hub.py catastrophe the path gate misses)."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "task_hub.py").write_text(
        "CREATE TABLE task_hub_items (id INTEGER PRIMARY KEY);\n"
    )
    body = "- 🟢 **[low]** `src/task_hub.py` — redundant comment.\n  - _fix_: delete it.\n"
    issue = _issue(body=body)
    # Without a root the path gate sees nothing dangerous -> tier_a.
    assert classify_issue(issue)[0] == CLASS_TIER_A
    # With a root the content scan finds the DDL -> never_auto.
    cls, reason = classify_issue(issue, root=str(tmp_path))
    assert cls == CLASS_NEVER_AUTO
    assert "embedded_ddl" in reason


def test_file_touches_control_plane(tmp_path):
    f = tmp_path / "provision.py"
    f.write_text("import subprocess\nsubprocess.run(['systemctl', 'daemon-reload'])\n")
    assert mod.file_touches_control_plane(str(f)) is True
    g = tmp_path / "plain.py"
    g.write_text("x = 1  # nothing host-y here\n")
    assert mod.file_touches_control_plane(str(g)) is False


def test_classify_content_scan_upgrades_control_plane_py(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "host_setup.py").write_text(
        "import os\nos.system('systemctl enable foo.timer')\n"
    )
    body = "- 🟢 **[low]** `src/host_setup.py` — redundant comment.\n  - _fix_: delete it.\n"
    issue = _issue(body=body)
    assert classify_issue(issue)[0] == CLASS_TIER_A  # path-safe without root
    cls, reason = classify_issue(issue, root=str(tmp_path))
    assert cls == CLASS_NEVER_AUTO
    assert "control_plane_ops" in reason


def test_classify_content_scan_ignores_root_escape(tmp_path):
    # A traversal path must not be resolved outside root (no crash, no upgrade).
    body = "- 🟢 **[low]** `../../etc/passwd` — x.\n  - _fix_: y.\n"
    issue = _issue(body=body)
    cls, _ = classify_issue(issue, root=str(tmp_path))
    # ../../ is path-safe by the gate and the escape is refused -> tier_a (no crash).
    assert cls in (CLASS_TIER_A, CLASS_NEVER_AUTO)  # never raises


# --------------------------------------------------------------------------- #
# resolve_delivery — the INVARIANT
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "classification,mode,exp_action,exp_delivery",
    [
        (CLASS_TIER_A, MODE_OBSERVE, "dispatch", DELIVERY_DRAFT_EMAIL),
        (CLASS_TIER_A, MODE_AUTO, "dispatch", DELIVERY_AUTO_MERGE),
        (CLASS_NEVER_AUTO, MODE_OBSERVE, "dispatch", DELIVERY_DRAFT_EMAIL),
        (CLASS_NEVER_AUTO, MODE_AUTO, "dispatch", DELIVERY_DRAFT_EMAIL),  # key invariant
        (CLASS_NEEDS_OPERATOR, MODE_OBSERVE, "escalate", None),
        (CLASS_NEEDS_OPERATOR, MODE_AUTO, "escalate", None),
    ],
)
def test_resolve_delivery_table(classification, mode, exp_action, exp_delivery):
    action, delivery, _ = resolve_delivery(classification, mode)
    assert (action, delivery) == (exp_action, exp_delivery)


def test_invariant_observe_never_auto_merges():
    for cls in (CLASS_TIER_A, CLASS_NEVER_AUTO, CLASS_NEEDS_OPERATOR):
        _, delivery, _ = resolve_delivery(cls, MODE_OBSERVE)
        assert delivery != DELIVERY_AUTO_MERGE


def test_invariant_never_auto_class_never_auto_merges():
    for mode in (MODE_OBSERVE, MODE_AUTO):
        _, delivery, _ = resolve_delivery(CLASS_NEVER_AUTO, mode)
        assert delivery != DELIVERY_AUTO_MERGE


# --------------------------------------------------------------------------- #
# decide_action — idempotency + the §8 planted never-auto case
# --------------------------------------------------------------------------- #


def test_decide_skip_already_dispatched():
    action, _, reason = decide_action(_issue(labels=[LABEL_DISPATCHED]), MODE_OBSERVE)
    assert (action, reason) == ("skip", "already_dispatched")


def test_decide_skip_already_escalated():
    action, _, reason = decide_action(_issue(labels=[LABEL_NEEDS_OPERATOR]), MODE_AUTO)
    assert action == "skip"
    assert reason == "already_escalated"


def test_decide_tier_a_observe_dispatches_draft():
    action, delivery, _ = decide_action(_issue(), MODE_OBSERVE)
    assert (action, delivery) == ("dispatch", DELIVERY_DRAFT_EMAIL)


def test_planted_never_auto_stays_draft_even_in_auto_mode():
    """§8 acceptance: a finding touching remote_deploy.sh is draft+email, never
    auto-merge, even with mode=auto."""
    body = (
        "- 🔴 **[high]** `scripts/deploy/remote_deploy.sh` — redundant comment.\n"
        "  - _fix_: remove it.\n"
    )
    action, delivery, reason = decide_action(_issue(body=body), MODE_AUTO)
    assert action == "dispatch"
    assert delivery == DELIVERY_DRAFT_EMAIL  # NOT auto_merge
    assert "never_auto" in reason


# --------------------------------------------------------------------------- #
# Orchestration — run_dispatch / process_issue with FakeGh + monkeypatch
# --------------------------------------------------------------------------- #


def _issue_list_json(numbers: list[int]) -> str:
    import json

    return json.dumps([{"number": n} for n in numbers])


def _issue_view_json(number: int, body: str, labels: list[str]) -> str:
    import json

    return json.dumps(
        {
            "number": number,
            "title": f"deslop findings: PR #{number}",
            "body": body,
            "labels": [{"name": n} for n in labels],
        }
    )


def test_run_dispatch_tier_a_dispatches(monkeypatch):
    calls = {"dispatch": 0, "escalate": 0}
    monkeypatch.setattr(
        mod,
        "dispatch_cody_fix",
        lambda issue, delivery, **k: (calls.__setitem__("dispatch", calls["dispatch"] + 1))
        or {"action": "dispatch", "delivery": delivery},
    )
    monkeypatch.setattr(
        mod,
        "escalate_to_operator",
        lambda issue, reason, **k: (calls.__setitem__("escalate", calls["escalate"] + 1))
        or {"action": "escalate"},
    )
    gh = FakeGh(
        {
            ("issue", "list"): (0, _issue_list_json([798])),
            ("issue", "view"): (0, _issue_view_json(798, BODY_798, ["deslop-findings"])),
        }
    )
    result = run_dispatch(mode=MODE_OBSERVE, gh=gh)
    assert result["open_issue_count"] == 1
    assert calls["dispatch"] == 1
    assert calls["escalate"] == 0
    assert result["processed"][0]["action"] == "dispatch"
    assert result["processed"][0]["delivery"] == DELIVERY_DRAFT_EMAIL


def test_run_dispatch_no_findings_escalates(monkeypatch):
    calls = {"dispatch": 0, "escalate": 0}
    monkeypatch.setattr(
        mod, "dispatch_cody_fix", lambda *a, **k: calls.__setitem__("dispatch", 1) or {}
    )
    monkeypatch.setattr(
        mod, "escalate_to_operator", lambda *a, **k: calls.__setitem__("escalate", 1) or {}
    )
    gh = FakeGh(
        {
            ("issue", "list"): (0, _issue_list_json([900])),
            ("issue", "view"): (0, _issue_view_json(900, "✅ No slop found.", ["deslop-findings"])),
        }
    )
    run_dispatch(mode=MODE_OBSERVE, gh=gh)
    assert calls["escalate"] == 1
    assert calls["dispatch"] == 0


def test_run_dispatch_already_claimed_skips(monkeypatch):
    calls = {"dispatch": 0, "escalate": 0}
    monkeypatch.setattr(
        mod, "dispatch_cody_fix", lambda *a, **k: calls.__setitem__("dispatch", 1) or {}
    )
    monkeypatch.setattr(
        mod, "escalate_to_operator", lambda *a, **k: calls.__setitem__("escalate", 1) or {}
    )
    gh = FakeGh(
        {
            ("issue", "list"): (0, _issue_list_json([798])),
            ("issue", "view"): (
                0,
                _issue_view_json(798, BODY_798, ["deslop-findings", "deslop-dispatched"]),
            ),
        }
    )
    result = run_dispatch(mode=MODE_AUTO, gh=gh)
    assert calls["dispatch"] == 0 and calls["escalate"] == 0
    assert result["processed"][0]["action"] == "skip"


def test_process_issue_dry_run_has_no_side_effects(monkeypatch):
    # If anything tried to dispatch/escalate the test would record it.
    fired = {"hit": False}
    monkeypatch.setattr(mod, "dispatch_cody_fix", lambda *a, **k: fired.__setitem__("hit", True))
    monkeypatch.setattr(mod, "escalate_to_operator", lambda *a, **k: fired.__setitem__("hit", True))
    res = process_issue(_issue(), mode=MODE_OBSERVE, dry_run=True)
    assert fired["hit"] is False
    assert res["dry_run"] is True
    assert res["action"] == "dispatch"
    assert res["delivery"] == DELIVERY_DRAFT_EMAIL
    assert "brief_preview" in res
    assert "would_email_subject" in res


# --------------------------------------------------------------------------- #
# dispatch_cody_fix internals — claim label, effect policy, email
# --------------------------------------------------------------------------- #


class _FakeConn:
    def commit(self):  # noqa: D401
        pass

    def close(self):
        pass


def test_dispatch_cody_fix_claims_labels_and_emails(monkeypatch):
    import universal_agent.durable.db as dbmod
    import universal_agent.services.proactive_task_builder as ptb

    monkeypatch.setenv("UA_DESLOP_NOTIFY_OPERATOR", "1")  # opt in to the email path
    recorded: dict = {}
    monkeypatch.setattr(dbmod, "connect_runtime_db", lambda *a, **k: _FakeConn())
    monkeypatch.setattr(dbmod, "get_activity_db_path", lambda: ":memory:")
    monkeypatch.setattr(
        ptb,
        "queue_proactive_task",
        lambda conn, **kw: recorded.update(kw) or {"task_id": kw["task_id"], "status": "open"},
    )

    emails: list[dict] = []

    def fake_emailer(**kw):
        emails.append(kw)
        return {"status": "sent", "to": "kevin"}

    gh = FakeGh({("issue", "edit"): (0, "")})
    out = dispatch = mod.dispatch_cody_fix(
        _issue(),
        DELIVERY_DRAFT_EMAIL,
        gh=gh,
        mode=MODE_OBSERVE,
        emailer=fake_emailer,
    )

    # The claim label is ensured (created if missing) BEFORE it is added —
    # gh issue edit --add-label fails on a nonexistent label, which silently
    # broke claiming and caused re-dispatch/re-email.
    assert any(c[0:2] == ["label", "create"] and "deslop-dispatched" in c for c in gh.calls)
    create_idx = next(i for i, c in enumerate(gh.calls) if c[0:2] == ["label", "create"])
    edit_idx = next(i for i, c in enumerate(gh.calls) if c[0:2] == ["issue", "edit"])
    assert create_idx < edit_idx  # ensured before added
    # Claimed via label edit.
    assert any(c[0:2] == ["issue", "edit"] and "deslop-dispatched" in c for c in gh.calls)
    assert out["claimed_label"] is True
    # Effect policy is locked down — Cody may only open a PR.
    policy = recorded["metadata"]["external_effect_policy"]
    assert policy["allow_pr"] is True
    assert policy["allow_merge"] is False
    assert policy["allow_deploy"] is False
    assert policy["allow_secret_mutation"] is False
    assert recorded["metadata"]["delivery"] == DELIVERY_DRAFT_EMAIL
    # Kevin was emailed.
    assert len(emails) == 1
    assert "#798" in emails[0]["subject"]
    assert dispatch["email"]["status"] == "sent"


def test_escalate_labels_and_telegrams(monkeypatch):
    monkeypatch.setenv("UA_DESLOP_NOTIFY_OPERATOR", "1")  # opt in to the telegram path
    gh = FakeGh({("issue", "edit"): (0, "")})
    sent_msgs: list[str] = []

    def fake_tg(*, chat_id, text, bot_token=None):
        sent_msgs.append(text)
        return True, "ok"

    monkeypatch.setenv("UA_OPERATOR_TELEGRAM_CHAT_ID", "12345")
    out = mod.escalate_to_operator(_issue(), "needs_operator", gh=gh, telegram=fake_tg)
    assert out["labelled_needs_operator"] is True
    assert any(c[0:2] == ["label", "create"] and "needs-operator" in c for c in gh.calls)
    assert any(c[0:2] == ["issue", "edit"] and "needs-operator" in c for c in gh.calls)
    assert out["telegram_sent"] is True
    assert sent_msgs and "#798" in sent_msgs[0]


def test_dispatch_suppresses_email_when_notify_off(monkeypatch):
    """Default (UA_DESLOP_NOTIFY_OPERATOR unset) -> NO operator email, but the
    durable records (claim label + Task Hub row) are still written."""
    import universal_agent.durable.db as dbmod
    import universal_agent.services.proactive_task_builder as ptb

    monkeypatch.delenv("UA_DESLOP_NOTIFY_OPERATOR", raising=False)
    recorded: dict = {}
    monkeypatch.setattr(dbmod, "connect_runtime_db", lambda *a, **k: _FakeConn())
    monkeypatch.setattr(dbmod, "get_activity_db_path", lambda: ":memory:")
    monkeypatch.setattr(
        ptb,
        "queue_proactive_task",
        lambda conn, **kw: recorded.update(kw) or {"task_id": kw["task_id"], "status": "open"},
    )

    emails: list[dict] = []

    def fake_emailer(**kw):
        emails.append(kw)
        return {"status": "sent"}

    gh = FakeGh({("issue", "edit"): (0, "")})
    out = mod.dispatch_cody_fix(
        _issue(), DELIVERY_DRAFT_EMAIL, gh=gh, mode=MODE_OBSERVE, emailer=fake_emailer,
    )
    # No email sent, but the dispatch still happened and was durably recorded.
    assert emails == []
    assert out["email"]["status"] == "suppressed"
    assert out["claimed_label"] is True
    assert recorded  # Task Hub row written despite the silence


def test_escalate_suppresses_telegram_when_notify_off(monkeypatch):
    """Default -> NO operator Telegram ping; the needs-operator label is still set."""
    monkeypatch.delenv("UA_DESLOP_NOTIFY_OPERATOR", raising=False)
    monkeypatch.setenv("UA_OPERATOR_TELEGRAM_CHAT_ID", "12345")
    gh = FakeGh({("issue", "edit"): (0, "")})
    sent_msgs: list[str] = []

    def fake_tg(*, chat_id, text, bot_token=None):
        sent_msgs.append(text)
        return True, "ok"

    out = mod.escalate_to_operator(_issue(), "needs_operator", gh=gh, telegram=fake_tg)
    assert sent_msgs == []
    assert out["telegram_sent"] is False
    assert out["telegram_detail"].startswith("suppressed")
    assert out["labelled_needs_operator"] is True


def test_gh_env_scrubs_bad_token_when_config_login_present(monkeypatch, tmp_path):
    """A gh config login exists -> drop the env GH_TOKEN so gh uses the maintained
    config-auth (fixes the infisical 401 token silently 401-ing every gh call)."""
    cfg = tmp_path / "gh"
    cfg.mkdir()
    (cfg / "hosts.yml").write_text("github.com:\n  oauth_token: x\n", encoding="utf-8")
    monkeypatch.setenv("GH_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("GH_TOKEN", "bad-401-token")
    monkeypatch.setenv("GITHUB_TOKEN", "also-bad")
    env = mod._gh_subprocess_env()
    assert "GH_TOKEN" not in env
    assert "GITHUB_TOKEN" not in env


def test_gh_env_keeps_token_when_no_config_login(monkeypatch, tmp_path):
    """No config login (e.g. CI) -> the env token is the sole credential; keep it."""
    cfg = tmp_path / "gh"
    cfg.mkdir()  # no hosts.yml
    monkeypatch.setenv("GH_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("GH_TOKEN", "the-only-cred")
    env = mod._gh_subprocess_env()
    assert env.get("GH_TOKEN") == "the-only-cred"


# --------------------------------------------------------------------------- #
# Source-PR-state gate (added 2026-06-11)
#
# A deslop finding's slop only reaches `main` once its source PR MERGES. For
# manual-review branches (codie/*, kevin/*, feature/*) the PR can sit OPEN for
# hours — dispatching a fix-off-main mission then is wrong (the slop isn't on
# main; e.g. issue #933 / open PR #932). Re-route by PR state:
#   MERGED / unknown -> proceed (dispatch as before)
#   OPEN             -> comment on the PR once, don't dispatch
#   CLOSED unmerged  -> close the issue as moot
# --------------------------------------------------------------------------- #

import json as _json

from universal_agent.scripts.deslop_remediation_dispatch import (
    decide_source_pr_disposition,
    fetch_pr_state,
)


def _pr_view_json(state: str, merged: bool = False) -> str:
    return _json.dumps({"state": state, "mergedAt": "2026-06-11T00:00:00Z" if merged else None})


@pytest.mark.parametrize(
    "pr_state,exp",
    [
        ({"state": "MERGED", "merged": True}, "proceed"),
        ({"state": "OPEN", "merged": False}, "defer_open_pr"),
        ({"state": "CLOSED", "merged": False}, "close_moot"),
        (None, "proceed"),  # unknown -> safe default = behave as before
        ({"state": "", "merged": False}, "proceed"),
    ],
)
def test_decide_source_pr_disposition(pr_state, exp):
    disp, _reason = decide_source_pr_disposition(pr_state)
    assert disp == exp


def test_fetch_pr_state_parses_open():
    gh = FakeGh({("pr", "view"): (0, _pr_view_json("OPEN"))})
    assert fetch_pr_state("r", 1, gh) == {"state": "OPEN", "merged": False}


def test_fetch_pr_state_merged():
    gh = FakeGh({("pr", "view"): (0, _pr_view_json("MERGED", merged=True))})
    assert fetch_pr_state("r", 1, gh) == {"state": "MERGED", "merged": True}


def test_fetch_pr_state_empty_is_none():
    gh = FakeGh({("pr", "view"): (0, "")})
    assert fetch_pr_state("r", 1, gh) is None


def _gh_for_issue(number: int, body: str, labels: list[str], pr_view: str | None = None,
                  pr_comments: str | None = None) -> FakeGh:
    resp = {
        ("issue", "list"): (0, _issue_list_json([number])),
        ("issue", "view"): (0, _issue_view_json(number, body, labels)),
    }
    if pr_view is not None:
        resp[("pr", "view")] = (0, pr_view)
    return FakeGh(resp)


def test_open_source_pr_defers_instead_of_dispatching(monkeypatch):
    calls = {"dispatch": 0}
    monkeypatch.setattr(
        mod, "dispatch_cody_fix",
        lambda *a, **k: calls.__setitem__("dispatch", calls["dispatch"] + 1) or {"action": "dispatch"},
    )
    # PR 797 is OPEN; pr-view (json comments) returns no prior comment -> should post one.
    gh = FakeGh(
        {
            ("issue", "list"): (0, _issue_list_json([798])),
            ("issue", "view"): (0, _issue_view_json(798, BODY_798, ["deslop-findings"])),
            ("pr", "view"): (0, _pr_view_json("OPEN")),  # used for BOTH state + comments fetch
            ("pr", "comment"): (0, ""),
        }
    )
    res = run_dispatch(mode=MODE_OBSERVE, gh=gh)["processed"][0]
    # Executed action reflects the re-route (result.update from the defer helper).
    assert res["action"] == "defer_open_pr"
    assert res["source_pr_disposition"] == "defer_open_pr"
    assert calls["dispatch"] == 0  # NO fix-off-main mission
    # A pr comment was posted on the source PR.
    assert any(c[:2] == ["pr", "comment"] and "797" in c for c in gh.calls)


def test_open_source_pr_comment_is_idempotent(monkeypatch):
    monkeypatch.setattr(mod, "dispatch_cody_fix", lambda *a, **k: {"action": "dispatch"})
    marker = mod._DEFER_MARKER.format(n=798)
    comments_json = _json.dumps({"state": "OPEN", "mergedAt": None,
                                 "comments": [{"body": f"prior {marker} body"}]})
    gh = FakeGh(
        {
            ("issue", "list"): (0, _issue_list_json([798])),
            ("issue", "view"): (0, _issue_view_json(798, BODY_798, ["deslop-findings"])),
            ("pr", "view"): (0, comments_json),
            ("pr", "comment"): (0, ""),
        }
    )
    run_dispatch(mode=MODE_OBSERVE, gh=gh)
    assert not any(c[:2] == ["pr", "comment"] for c in gh.calls)  # no duplicate comment


def test_closed_unmerged_source_pr_closes_issue_as_moot(monkeypatch):
    calls = {"dispatch": 0}
    monkeypatch.setattr(
        mod, "dispatch_cody_fix",
        lambda *a, **k: calls.__setitem__("dispatch", calls["dispatch"] + 1) or {},
    )
    gh = FakeGh(
        {
            ("issue", "list"): (0, _issue_list_json([798])),
            ("issue", "view"): (0, _issue_view_json(798, BODY_798, ["deslop-findings"])),
            ("pr", "view"): (0, _pr_view_json("CLOSED", merged=False)),
            ("issue", "close"): (0, ""),
        }
    )
    res = run_dispatch(mode=MODE_OBSERVE, gh=gh)["processed"][0]
    assert res["source_pr_disposition"] == "close_moot"
    assert calls["dispatch"] == 0
    assert any(c[:2] == ["issue", "close"] and "798" in c for c in gh.calls)


def test_merged_source_pr_still_dispatches(monkeypatch):
    calls = {"dispatch": 0}
    monkeypatch.setattr(
        mod, "dispatch_cody_fix",
        lambda *a, **k: calls.__setitem__("dispatch", calls["dispatch"] + 1) or {"action": "dispatch"},
    )
    gh = FakeGh(
        {
            ("issue", "list"): (0, _issue_list_json([798])),
            ("issue", "view"): (0, _issue_view_json(798, BODY_798, ["deslop-findings"])),
            ("pr", "view"): (0, _pr_view_json("MERGED", merged=True)),
        }
    )
    res = run_dispatch(mode=MODE_OBSERVE, gh=gh)["processed"][0]
    assert res["source_pr_disposition"] == "proceed"
    assert calls["dispatch"] == 1


def test_source_pr_check_kill_switch(monkeypatch):
    monkeypatch.setenv("UA_DESLOP_CHECK_SOURCE_PR", "0")
    calls = {"dispatch": 0}
    monkeypatch.setattr(
        mod, "dispatch_cody_fix",
        lambda *a, **k: calls.__setitem__("dispatch", calls["dispatch"] + 1) or {"action": "dispatch"},
    )
    # PR is OPEN, but the kill switch disables the check -> dispatch proceeds (old behavior).
    gh = FakeGh(
        {
            ("issue", "list"): (0, _issue_list_json([798])),
            ("issue", "view"): (0, _issue_view_json(798, BODY_798, ["deslop-findings"])),
            ("pr", "view"): (0, _pr_view_json("OPEN")),
        }
    )
    res = run_dispatch(mode=MODE_OBSERVE, gh=gh)["processed"][0]
    assert calls["dispatch"] == 1
    assert res.get("source_pr_disposition") in (None, "proceed", "disabled")

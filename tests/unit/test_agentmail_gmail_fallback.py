"""Tests for the Gmail (gws) CLI fallback when AgentMail returns HTTP 429.

The fallback is gated by ``UA_AGENTMAIL_GMAIL_FALLBACK=1`` and only fires on
status_code==429 (e.g. AgentMail's "Daily send limit exceeded"). Other errors
must keep propagating so real bugs aren't masked.
"""
from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from universal_agent.services import agentmail_service as svc


class _Stub429(Exception):
    """Stand-in for ``agentmail.core.api_error.ApiError`` with status 429."""

    def __init__(self) -> None:
        super().__init__("Daily send limit exceeded")
        self.status_code = 429
        self.body = {"name": "RateLimitError", "message": "Daily send limit exceeded"}


class _Stub500(Exception):
    def __init__(self) -> None:
        super().__init__("Internal Server Error")
        self.status_code = 500
        self.body = "boom"


def _make_service(send_raises: Exception | None = None):
    """Build an AgentMailService with the upstream client mocked."""
    service = svc.AgentMailService()
    service._inbox_id = "inbox_x"
    service._inbox_address = "oddcity216@agentmail.to"

    async def _send(**_kwargs):
        if send_raises is not None:
            raise send_raises
        return SimpleNamespace(message_id="agentmail-msg-001")

    service._client = SimpleNamespace(
        inboxes=SimpleNamespace(
            messages=SimpleNamespace(send=_send),
        ),
    )
    return service


@pytest.mark.asyncio
async def test_send_direct_success_path_unchanged():
    service = _make_service(send_raises=None)
    result = await service._send_direct(
        to="kevin@example.com", subject="hi", text="body",
        html=None, attachments=None, labels=None,
    )
    assert result["status"] == "sent"
    assert result["message_id"] == "agentmail-msg-001"


@pytest.mark.asyncio
async def test_429_without_flag_reraises(monkeypatch):
    monkeypatch.delenv("UA_AGENTMAIL_GMAIL_FALLBACK", raising=False)
    service = _make_service(send_raises=_Stub429())
    with pytest.raises(_Stub429):
        await service._send_direct(
            to="kevin@example.com", subject="hi", text="body",
            html=None, attachments=None, labels=None,
        )


@pytest.mark.asyncio
async def test_500_with_flag_still_reraises(monkeypatch):
    monkeypatch.setenv("UA_AGENTMAIL_GMAIL_FALLBACK", "1")
    service = _make_service(send_raises=_Stub500())
    with pytest.raises(_Stub500):
        await service._send_direct(
            to="kevin@example.com", subject="hi", text="body",
            html=None, attachments=None, labels=None,
        )


@pytest.mark.asyncio
async def test_429_with_flag_falls_back_to_gws_cli(monkeypatch):
    monkeypatch.setenv("UA_AGENTMAIL_GMAIL_FALLBACK", "1")
    # Override the CLI so the test doesn't shell out to npx.
    monkeypatch.setenv("UA_GMAIL_CLI_CMD", "/usr/bin/false")
    # Labeling makes extra subprocess.run calls; disable it here so this test
    # stays focused on the +send argv/return shape (labeling has its own tests).
    monkeypatch.setenv("UA_AGENTMAIL_GMAIL_LABEL", "0")
    service = _make_service(send_raises=_Stub429())

    captured: dict = {}

    def _fake_run(argv, *, capture_output, text, timeout, check, **_):
        captured["argv"] = list(argv)
        captured["timeout"] = timeout
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"id": "gmail-cli-msg-77"}),
            stderr="",
        )

    with patch.object(subprocess, "run", _fake_run):
        result = await service._send_direct(
            to="kevin@example.com",
            subject="Daily YouTube Digest",
            text="plain",
            html="<p>rich</p>",
            attachments=None,
            labels=None,
        )

    assert result == {
        "status": "sent_via_gmail_fallback",
        "message_id": "gmail-cli-msg-77",
        "inbox": "oddcity216@agentmail.to",
        "via": "gmail_cli",
        "label": None,  # labeling disabled in this test
    }
    # Argv shape: CLI override + gmail +send --format json --to ... --subject ... --body ... --html
    argv = captured["argv"]
    assert argv[:2] == ["/usr/bin/false", "gmail"]
    assert argv[2] == "+send"
    assert "--to" in argv and argv[argv.index("--to") + 1] == "kevin@example.com"
    assert "--subject" in argv
    assert "--html" in argv, "html body should pass --html flag"
    # When html is provided, body arg should be the html, not the text
    assert argv[argv.index("--body") + 1] == "<p>rich</p>"


@pytest.mark.asyncio
async def test_429_fallback_writes_attachments_and_passes_them(monkeypatch):
    import base64
    monkeypatch.setenv("UA_AGENTMAIL_GMAIL_FALLBACK", "1")
    monkeypatch.setenv("UA_GMAIL_CLI_CMD", "/usr/bin/false")
    monkeypatch.setenv("UA_AGENTMAIL_GMAIL_LABEL", "0")  # focus on attachment plumbing
    service = _make_service(send_raises=_Stub429())

    payload = b"hello-pdf-bytes"
    attachments = [
        {
            "filename": "report.pdf",
            "content_type": "application/pdf",
            "content": base64.b64encode(payload).decode("ascii"),
        }
    ]

    captured_paths: list[str] = []

    def _fake_run(argv, *, capture_output, text, timeout, check, **_):
        # Capture each -a path and confirm the file is there with the right bytes.
        i = 0
        while i < len(argv):
            if argv[i] == "-a":
                captured_paths.append(argv[i + 1])
                i += 2
                continue
            i += 1
        return SimpleNamespace(returncode=0, stdout="{}", stderr="")

    with patch.object(subprocess, "run", _fake_run):
        await service._send_direct(
            to="kevin@example.com", subject="att test", text="x",
            html=None, attachments=attachments, labels=None,
        )

    assert len(captured_paths) == 1
    # File is cleaned up by the time we get here (tmpdir wiped in finally) —
    # the assertion is on the argv plumbing itself.
    assert captured_paths[0].endswith("report.pdf")


@pytest.mark.asyncio
async def test_429_fallback_propagates_cli_failure(monkeypatch):
    monkeypatch.setenv("UA_AGENTMAIL_GMAIL_FALLBACK", "1")
    monkeypatch.setenv("UA_GMAIL_CLI_CMD", "/usr/bin/false")
    service = _make_service(send_raises=_Stub429())

    def _fake_run(*_args, **_kwargs):
        return SimpleNamespace(returncode=1, stdout="", stderr="auth expired")

    with patch.object(subprocess, "run", _fake_run):
        with pytest.raises(RuntimeError, match="agentmail_gmail_fallback_failed"):
            await service._send_direct(
                to="kevin@example.com", subject="x", text="x",
                html=None, attachments=None, labels=None,
            )


@pytest.mark.asyncio
async def test_429_fallback_strips_empty_gws_env_vars(monkeypatch):
    """Empty GOOGLE_WORKSPACE_CLI_* env vars must not leak into the subprocess.

    Real-world failure: deploy.yml's .env bootstrap on the VPS leaves
    GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE set to "" in the gateway process.
    Without scrubbing, the gws CLI interprets that empty path as
    "no fallback to default ~/.config/gws/credentials.enc" and bombs.
    """
    monkeypatch.setenv("UA_AGENTMAIL_GMAIL_FALLBACK", "1")
    monkeypatch.setenv("UA_GMAIL_CLI_CMD", "/usr/bin/false")
    monkeypatch.setenv("UA_AGENTMAIL_GMAIL_LABEL", "0")  # focus on env scrubbing
    monkeypatch.setenv("GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE", "")
    monkeypatch.setenv("GOOGLE_WORKSPACE_CLI_KEEP_ME", "real-value")
    service = _make_service(send_raises=_Stub429())

    captured_env: dict = {}

    def _fake_run(argv, *, capture_output, text, timeout, check, env=None, **_):
        captured_env["env"] = env or {}
        return SimpleNamespace(returncode=0, stdout="{}", stderr="")

    with patch.object(subprocess, "run", _fake_run):
        await service._send_direct(
            to="kevin@example.com", subject="x", text="x",
            html=None, attachments=None, labels=None,
        )

    env = captured_env["env"]
    assert "GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE" not in env, \
        "Empty GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE must be stripped"
    assert env.get("GOOGLE_WORKSPACE_CLI_KEEP_ME") == "real-value", \
        "Non-empty GOOGLE_WORKSPACE_CLI_* vars must pass through"


@pytest.mark.asyncio
async def test_429_fallback_defaults_keyring_backend_file(monkeypatch):
    """Headless gateway has no unlocked OS keyring, so the subprocess must
    default GOOGLE_WORKSPACE_CLI_KEYRING_BACKEND=file to decrypt creds from
    ~/.config/gws/.encryption_key on disk."""
    monkeypatch.setenv("UA_AGENTMAIL_GMAIL_FALLBACK", "1")
    monkeypatch.setenv("UA_GMAIL_CLI_CMD", "/usr/bin/false")
    monkeypatch.setenv("UA_AGENTMAIL_GMAIL_LABEL", "0")
    monkeypatch.delenv("GOOGLE_WORKSPACE_CLI_KEYRING_BACKEND", raising=False)
    service = _make_service(send_raises=_Stub429())

    captured_env: dict = {}

    def _fake_run(argv, *, capture_output, text, timeout, check, env=None, **_):
        captured_env["env"] = env or {}
        return SimpleNamespace(returncode=0, stdout="{}", stderr="")

    with patch.object(subprocess, "run", _fake_run):
        await service._send_direct(
            to="kevin@example.com", subject="x", text="x",
            html=None, attachments=None, labels=None,
        )

    assert captured_env["env"].get("GOOGLE_WORKSPACE_CLI_KEYRING_BACKEND") == "file"


@pytest.mark.asyncio
async def test_429_fallback_preserves_explicit_keyring_backend(monkeypatch):
    """An operator-pinned backend must not be overridden by the file default."""
    monkeypatch.setenv("UA_AGENTMAIL_GMAIL_FALLBACK", "1")
    monkeypatch.setenv("UA_GMAIL_CLI_CMD", "/usr/bin/false")
    monkeypatch.setenv("UA_AGENTMAIL_GMAIL_LABEL", "0")
    monkeypatch.setenv("GOOGLE_WORKSPACE_CLI_KEYRING_BACKEND", "keyring")
    service = _make_service(send_raises=_Stub429())

    captured_env: dict = {}

    def _fake_run(argv, *, capture_output, text, timeout, check, env=None, **_):
        captured_env["env"] = env or {}
        return SimpleNamespace(returncode=0, stdout="{}", stderr="")

    with patch.object(subprocess, "run", _fake_run):
        await service._send_direct(
            to="kevin@example.com", subject="x", text="x",
            html=None, attachments=None, labels=None,
        )

    assert captured_env["env"].get("GOOGLE_WORKSPACE_CLI_KEYRING_BACKEND") == "keyring"


@pytest.mark.asyncio
async def test_429_fallback_handles_missing_cli(monkeypatch):
    monkeypatch.setenv("UA_AGENTMAIL_GMAIL_FALLBACK", "1")
    monkeypatch.setenv("UA_GMAIL_CLI_CMD", "/usr/bin/false")
    service = _make_service(send_raises=_Stub429())

    def _fake_run(*_args, **_kwargs):
        raise FileNotFoundError("npx")

    with patch.object(subprocess, "run", _fake_run):
        with pytest.raises(RuntimeError, match="agentmail_gmail_fallback_missing_cli"):
            await service._send_direct(
                to="kevin@example.com", subject="x", text="x",
                html=None, attachments=None, labels=None,
            )


# ── Gmail Sent-copy labeling (Phase 1) ────────────────────────────────────


def _route_gws(handlers):
    """Build a subprocess.run fake that dispatches on the gws subcommand.

    ``handlers`` maps a predicate name → SimpleNamespace result. The fake
    inspects argv and routes to: 'send' (`+send`), 'list' (labels list),
    'create' (labels create), 'modify' (messages modify). Each call is also
    recorded in the returned ``calls`` list for assertions.
    """
    calls: list[list[str]] = []

    def _run(argv, *, capture_output=True, text=True, timeout=None, check=False, env=None, **_):
        argv = list(argv)
        calls.append(argv)
        if "+send" in argv:
            kind = "send"
        elif "labels" in argv and "list" in argv:
            kind = "list"
        elif "labels" in argv and "create" in argv:
            kind = "create"
        elif "messages" in argv and "modify" in argv:
            kind = "modify"
        else:
            kind = "unknown"
        return handlers[kind]

    return _run, calls


@pytest.mark.asyncio
async def test_429_fallback_labels_sent_copy_by_default(monkeypatch):
    """With labeling on (default), a fresh label is created and applied."""
    monkeypatch.setenv("UA_AGENTMAIL_GMAIL_FALLBACK", "1")
    monkeypatch.setenv("UA_GMAIL_CLI_CMD", "/usr/bin/false")
    monkeypatch.delenv("UA_AGENTMAIL_GMAIL_LABEL", raising=False)  # default ON
    service = _make_service(send_raises=_Stub429())

    run, calls = _route_gws({
        "send": SimpleNamespace(returncode=0, stdout=json.dumps({"id": "msg-abc"}), stderr=""),
        "list": SimpleNamespace(returncode=0, stdout=json.dumps({"labels": []}), stderr=""),
        "create": SimpleNamespace(returncode=0, stdout=json.dumps({"id": "Label_42", "name": "UA/AgentSent/Simone"}), stderr=""),
        "modify": SimpleNamespace(returncode=0, stdout=json.dumps({"id": "msg-abc", "labelIds": ["Label_42"]}), stderr=""),
    })

    with patch.object(subprocess, "run", run):
        result = await service._send_direct(
            to="kevin@example.com", subject="Digest", text="x",
            html=None, attachments=None, labels=None,
        )

    assert result["status"] == "sent_via_gmail_fallback"
    assert result["message_id"] == "msg-abc"
    assert result["label"] == "UA/AgentSent/Simone"

    # The create call carried the nested label name in the request body.
    create_argv = next(a for a in calls if "create" in a)
    body = json.loads(create_argv[create_argv.index("--json") + 1])
    assert body["name"] == "UA/AgentSent/Simone"

    # The modify call targeted the sent message id and added the created label.
    modify_argv = next(a for a in calls if "modify" in a)
    params = json.loads(modify_argv[modify_argv.index("--params") + 1])
    mbody = json.loads(modify_argv[modify_argv.index("--json") + 1])
    assert params == {"userId": "me", "id": "msg-abc"}
    assert mbody == {"addLabelIds": ["Label_42"]}


@pytest.mark.asyncio
async def test_429_fallback_reuses_existing_label_id(monkeypatch):
    """When the label already exists, no create call is made."""
    monkeypatch.setenv("UA_AGENTMAIL_GMAIL_FALLBACK", "1")
    monkeypatch.setenv("UA_GMAIL_CLI_CMD", "/usr/bin/false")
    monkeypatch.delenv("UA_AGENTMAIL_GMAIL_LABEL", raising=False)
    service = _make_service(send_raises=_Stub429())

    run, calls = _route_gws({
        "send": SimpleNamespace(returncode=0, stdout=json.dumps({"id": "msg-xyz"}), stderr=""),
        "list": SimpleNamespace(returncode=0, stdout=json.dumps({"labels": [
            {"id": "Label_7", "name": "UA/AgentSent/Simone"},
            {"id": "Label_1", "name": "INBOX"},
        ]}), stderr=""),
        "create": SimpleNamespace(returncode=0, stdout="{}", stderr=""),  # should not be hit
        "modify": SimpleNamespace(returncode=0, stdout="{}", stderr=""),
    })

    with patch.object(subprocess, "run", run):
        result = await service._send_direct(
            to="kevin@example.com", subject="Digest", text="x",
            html=None, attachments=None, labels=None,
        )

    assert result["label"] == "UA/AgentSent/Simone"
    assert not any("create" in a for a in calls), "must reuse existing label, not create"
    modify_argv = next(a for a in calls if "modify" in a)
    mbody = json.loads(modify_argv[modify_argv.index("--json") + 1])
    assert mbody == {"addLabelIds": ["Label_7"]}


@pytest.mark.asyncio
async def test_429_fallback_label_failure_is_non_fatal(monkeypatch):
    """A failed label step must NOT break an already-successful send."""
    monkeypatch.setenv("UA_AGENTMAIL_GMAIL_FALLBACK", "1")
    monkeypatch.setenv("UA_GMAIL_CLI_CMD", "/usr/bin/false")
    monkeypatch.delenv("UA_AGENTMAIL_GMAIL_LABEL", raising=False)
    service = _make_service(send_raises=_Stub429())

    run, _calls = _route_gws({
        "send": SimpleNamespace(returncode=0, stdout=json.dumps({"id": "msg-1"}), stderr=""),
        "list": SimpleNamespace(returncode=1, stdout="", stderr="auth expired"),
        "create": SimpleNamespace(returncode=1, stdout="", stderr="auth expired"),
        "modify": SimpleNamespace(returncode=1, stdout="", stderr="auth expired"),
    })

    with patch.object(subprocess, "run", run):
        result = await service._send_direct(
            to="kevin@example.com", subject="Digest", text="x",
            html=None, attachments=None, labels=None,
        )

    # Send still reported success; label couldn't be resolved so it's absent.
    assert result["status"] == "sent_via_gmail_fallback"
    assert result["message_id"] == "msg-1"

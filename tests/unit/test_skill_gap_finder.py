"""Unit tests for universal_agent.skill_gap_finder PURE functions.

No network, no anthropic, no gh. Module import must NOT require anthropic — the
heavy imports are lazy inside functions. We build a tmp dir of synthetic *.jsonl
transcripts and assert the mining/reporting helpers behave, plus that --dry-run
returns 0 without touching secrets.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import time

import pytest

# Importing the module must succeed with only stdlib present (no anthropic).
from universal_agent import skill_gap_finder as sgf


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


def _bash_use(cmd: str) -> dict:
    return {
        "type": "assistant",
        "message": {"content": [{"type": "tool_use", "name": "Bash",
                                 "input": {"command": cmd}}]},
    }


def _error_result(text: str) -> dict:
    return {
        "type": "user",
        "message": {"content": [{"type": "tool_result", "is_error": True,
                                 "content": text}]},
    }


def _human_prompt(text: str) -> dict:
    return {"type": "user", "message": {"content": text}}


@pytest.fixture()
def transcripts_dir(tmp_path: Path) -> Path:
    proj = tmp_path / "-home-kjdragan-lrepos-universal-agent"
    proj.mkdir(parents=True)

    # Transcript 1: recurring uv-sync bash + a repeated error + real prompt + synthetic.
    _write_jsonl(proj / "session-a.jsonl", [
        {"type": "agent-setting", "agentSetting": "claude"},  # non-message line
        _bash_use("uv sync --frozen"),
        _bash_use("uv sync --frozen"),
        _bash_use("git push origin main"),
        _error_result("\x1b[31mError: File has not been read yet\x1b[0m"),
        _error_result("Error: File has not been read yet"),
        _human_prompt("Please fix the failing worktree guard"),
        _human_prompt("<task-notification>synthetic wrapper should be ignored"),
        "this is not valid json and must be skipped",
    ])

    # Transcript 2: more recurrence of the same signals across a distinct session.
    _write_jsonl(proj / "session-b.jsonl", [
        _bash_use("uv sync --frozen"),
        _error_result("<tool_use_error>Error: File has not been read yet</tool_use_error>"),
        _human_prompt("Please fix the failing worktree guard"),
    ])
    return tmp_path


def test_read_jsonl_tolerant(transcripts_dir: Path) -> None:
    recs = sgf._read_jsonl(transcripts_dir / "-home-kjdragan-lrepos-universal-agent" / "session-a.jsonl")
    # 8 valid JSON objects; the invalid line is skipped.
    assert len(recs) == 8
    assert all(isinstance(r, dict) for r in recs)


def test_read_jsonl_missing_file(tmp_path: Path) -> None:
    assert sgf._read_jsonl(tmp_path / "nope.jsonl") == []


def test_recent_transcripts_window(transcripts_dir: Path) -> None:
    found = sgf._recent_transcripts(transcripts_dir, window_days=7)
    assert len(found) == 2
    assert all(p.suffix == ".jsonl" for p in found)

    # A stale file outside the window is excluded.
    proj = transcripts_dir / "-home-kjdragan-lrepos-universal-agent"
    old = proj / "ancient.jsonl"
    _write_jsonl(old, [_bash_use("echo old")])
    old_time = time.time() - 30 * 86_400
    os.utime(old, (old_time, old_time))
    found_recent = sgf._recent_transcripts(transcripts_dir, window_days=7)
    assert old not in found_recent
    assert len(found_recent) == 2


def test_recent_transcripts_missing_dir(tmp_path: Path) -> None:
    assert sgf._recent_transcripts(tmp_path / "missing", window_days=7) == []


def test_mine_collects_signals(transcripts_dir: Path) -> None:
    records = []
    for p in sgf._recent_transcripts(transcripts_dir, 7):
        records.extend(sgf._read_jsonl(p))
    mined = sgf._mine(records)

    # uv sync ran 3x total -> clustered under the "uv sync --frozen" head.
    assert mined["bash"]["uv sync --frozen"] == 3
    assert mined["bash"]["git push origin"] == 1

    # The error signature recurs 3x; ANSI + tool_use_error wrappers stripped to
    # the same cleaned text.
    assert mined["errors"]["Error: File has not been read yet"] == 3

    # Real human prompt counted twice; synthetic wrapper excluded.
    assert mined["prompts"]["Please fix the failing worktree guard"] == 2
    assert not any("synthetic wrapper" in k for k in mined["prompts"])


def test_existing_skill_names(tmp_path: Path) -> None:
    skills = tmp_path / "skills"
    (skills / "alpha").mkdir(parents=True)
    (skills / "alpha" / "SKILL.md").write_text("---\nname: alpha\n---\n", encoding="utf-8")
    (skills / "beta").mkdir()  # no SKILL.md -> excluded
    (skills / "gamma").mkdir()
    (skills / "gamma" / "SKILL.md").write_text("---\nname: gamma\n---\n", encoding="utf-8")
    names = sgf._existing_skill_names(skills)
    assert names == ["alpha", "gamma"]


def test_existing_skill_names_missing_dir(tmp_path: Path) -> None:
    assert sgf._existing_skill_names(tmp_path / "nope") == []


def test_build_corpus_includes_signals_and_dedup_list(transcripts_dir: Path) -> None:
    records = []
    for p in sgf._recent_transcripts(transcripts_dir, 7):
        records.extend(sgf._read_jsonl(p))
    mined = sgf._mine(records)
    corpus = sgf._build_corpus(mined, ["alpha", "beta"], window_days=7, transcript_count=2)
    assert "uv sync --frozen" in corpus
    assert "Error: File has not been read yet" in corpus
    assert "alpha, beta" in corpus
    assert "2 transcripts" in corpus


def test_build_report_empty() -> None:
    report = sgf._build_report([])
    assert "No new skill-gap candidates" in report


def test_build_report_with_candidates() -> None:
    candidates = [
        {"title": "uv-sync-helper", "problem": "uv sync rerun manually",
         "evidence": "ran 3x", "frequency": 3, "skill_fit": "new", "score": 0.9},
        {"title": "read-before-edit", "problem": "File not read yet error",
         "evidence": ["3 errors"], "frequency": 3, "kind": "new", "score": 0.8},
    ]
    report = sgf._build_report(candidates)
    assert "uv-sync-helper" in report
    assert "read-before-edit" in report
    assert "score: 0.9" in report
    assert "human" in report.lower()


# ──────────────────────────────────────────────────────────────────────────
# Redaction: secrets/PII must never reach the corpus, report, or issue body.
# ──────────────────────────────────────────────────────────────────────────
_SECRETS = [
    "sk-ant-api03-abcDEF123456_secret-key-value-7890",
    "AKIAIOSFODNN7EXAMPLE",
    "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",  # 40-char AWS secret
    "hunter2-PRIVATE",
    "123-45-6789",
    "alice@example.com",
]


def _assert_scrubbed(text: str) -> None:
    for raw in _SECRETS:
        assert raw not in text, f"secret leaked: {raw!r}"


def test_redact_strips_known_secrets() -> None:
    blob = (
        "key=sk-ant-api03-abcDEF123456_secret-key-value-7890 "
        "AKIAIOSFODNN7EXAMPLE "
        "aws_secret_access_key=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY "
        "password=hunter2-PRIVATE ssn 123-45-6789 mail alice@example.com"
    )
    out = sgf._redact(blob)
    _assert_scrubbed(out)
    assert "[REDACTED" in out


def test_redact_empty_and_idempotent() -> None:
    assert sgf._redact("") == ""
    assert sgf._redact(None) == ""  # type: ignore[arg-type]
    once = sgf._redact("token=sk-ant-api03-abcDEF123456_secret-key-value-7890")
    assert sgf._redact(once) == once  # idempotent


def test_clean_err_redacts() -> None:
    err = sgf._clean_err("Error contacting API with key sk-ant-api03-abcDEF123456_secret-key-value-7890")
    _assert_scrubbed(err)


def test_corpus_and_report_never_leak_secrets(tmp_path: Path) -> None:
    """End-to-end: an adversarial transcript must not leak into corpus or report."""
    proj = tmp_path / "-adversarial"
    proj.mkdir(parents=True)
    _write_jsonl(proj / "evil.jsonl", [
        _bash_use("curl -H 'Authorization: Bearer sk-ant-api03-abcDEF123456_secret-key-value-7890'"),
        _bash_use("curl -H 'Authorization: Bearer sk-ant-api03-abcDEF123456_secret-key-value-7890'"),
        _error_result("aws_secret_access_key=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY AKIAIOSFODNN7EXAMPLE"),
        _error_result("aws_secret_access_key=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY AKIAIOSFODNN7EXAMPLE"),
        _human_prompt("my password=hunter2-PRIVATE and ssn 123-45-6789 email alice@example.com"),
        _human_prompt("my password=hunter2-PRIVATE and ssn 123-45-6789 email alice@example.com"),
    ])

    records = []
    for p in sgf._recent_transcripts(tmp_path, 7):
        records.extend(sgf._read_jsonl(p))
    mined = sgf._mine(records)
    corpus = sgf._build_corpus(mined, [], window_days=7, transcript_count=1)
    _assert_scrubbed(corpus)

    # A worst-case candidate echoing the corpus into 'evidence' must still scrub.
    report = sgf._build_report([
        {"title": "leak sk-ant-api03-abcDEF123456_secret-key-value-7890",
         "problem": "password=hunter2-PRIVATE",
         "evidence": "AKIAIOSFODNN7EXAMPLE wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY ssn 123-45-6789",
         "frequency": 3, "skill_fit": "new", "score": 0.9},
    ])
    _assert_scrubbed(report)


def test_dry_run_returns_zero(transcripts_dir: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("UA_CLAUDE_PROJECTS_DIR", str(transcripts_dir))
    rc = sgf.main(["--dry-run", "--window-days", "7"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "transcripts=2" in out
    assert "corpus_chars=" in out


def test_dry_run_does_not_import_anthropic(transcripts_dir: Path, monkeypatch) -> None:
    # The dry-run path must not require the anthropic package; force-fail any
    # attempt to import it and assert the run still succeeds.
    import builtins

    real_import = builtins.__import__

    def _guard(name, *args, **kwargs):
        if name == "anthropic" or name.startswith("anthropic."):
            raise AssertionError("dry-run must not import anthropic")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _guard)
    monkeypatch.setenv("UA_CLAUDE_PROJECTS_DIR", str(transcripts_dir))
    assert sgf.main(["--dry-run", "--window-days", "7"]) == 0


# ──────────────────────────────────────────────────────────────────────────
# --email delivery: mirrors --open-issue (best-effort, never raises). The
# AgentMailService is fully mocked so no network/creds are required.
# ──────────────────────────────────────────────────────────────────────────
class _FakeMail:
    """Stand-in for AgentMailService: records the send_email kwargs.

    ``_started`` mirrors the real service's readiness flag; the finder's send
    block log-dumps (and skips send) when it is falsy.
    """

    instances: list["_FakeMail"] = []

    def __init__(self, started: bool = True, raise_on_send: bool = False):
        self._started = started
        self._raise_on_send = raise_on_send
        self.sent: list[dict] = []
        self.shutdown_called = False
        _FakeMail.instances.append(self)

    async def startup(self) -> None:
        return None

    async def send_email(self, **kwargs):
        if self._raise_on_send:
            raise RuntimeError("simulated send failure")
        self.sent.append(kwargs)
        return {"status": "sent"}

    async def shutdown(self) -> None:
        self.shutdown_called = True


def _install_fake_mail(monkeypatch, *, started: bool = True, raise_on_send: bool = False):
    """Patch AgentMailService + email_tags so _send_email needs no real deps."""
    import sys
    import types

    _FakeMail.instances = []

    def _factory():
        return _FakeMail(started=started, raise_on_send=raise_on_send)

    svc_mod = types.ModuleType("universal_agent.services.agentmail_service")
    svc_mod.AgentMailService = _factory  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "universal_agent.services.agentmail_service", svc_mod)

    tags_mod = types.ModuleType("universal_agent.services.email_tags")

    class _ActionTag:
        FYI = "FYI"

    class _KindTag:
        PROACTIVE = "PROACTIVE"

    tags_mod.ActionTag = _ActionTag  # type: ignore[attr-defined]
    tags_mod.KindTag = _KindTag  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "universal_agent.services.email_tags", tags_mod)


def test_send_email_uses_mocked_service_with_redacted_body(monkeypatch, capsys) -> None:
    _install_fake_mail(monkeypatch)
    monkeypatch.delenv("UA_SKILL_GAP_EMAIL_RECIPIENT", raising=False)

    report = "# report\nkey=sk-ant-api03-abcDEF123456_secret-key-value-7890\n"
    sgf._send_email(report, 3)

    assert len(_FakeMail.instances) == 1
    mail = _FakeMail.instances[0]
    assert len(mail.sent) == 1, "send_email should have been called exactly once"
    kw = mail.sent[0]
    # Default recipient is the reliable gmail address, not outlook.
    assert kw["to"] == "kevinjdragan@gmail.com"
    assert kw["subject"] == f"{sgf.ISSUE_TITLE_PREFIX}: 3 candidate(s)"
    # Body is the REDACTED report — the raw secret must never be sent.
    assert "sk-ant-api03-abcDEF123456_secret-key-value-7890" not in kw["text"]
    assert "[REDACTED" in kw["text"]
    assert kw["force_send"] is True
    assert kw["require_approval"] is False
    assert mail.shutdown_called is True


def test_send_email_respects_recipient_env_override(monkeypatch) -> None:
    _install_fake_mail(monkeypatch)
    monkeypatch.setenv("UA_SKILL_GAP_EMAIL_RECIPIENT", "ops@example.com")
    sgf._send_email("# report", 1)
    assert _FakeMail.instances[0].sent[0]["to"] == "ops@example.com"


def test_send_email_failure_does_not_raise(monkeypatch, capsys) -> None:
    _install_fake_mail(monkeypatch, raise_on_send=True)
    # Must NOT raise even though the underlying send blows up.
    sgf._send_email("# report", 2)
    out = capsys.readouterr().out
    assert "::warning::" in out


def test_send_email_log_dump_when_not_started(monkeypatch, capsys) -> None:
    _install_fake_mail(monkeypatch, started=True)
    # Force the not-started fallback path.
    _install_fake_mail(monkeypatch, started=False)
    sgf._send_email("# report body", 1)
    out = capsys.readouterr().out
    assert "::warning::AgentMail failed to start" in out
    # Nothing was actually sent in the fallback path.
    assert all(not m.sent for m in _FakeMail.instances)


def test_main_email_flag_invokes_send_and_returns_zero(monkeypatch) -> None:
    """--email with candidates routes the redacted report to _send_email and main returns 0."""
    monkeypatch.setattr(sgf, "_load_zai_env", lambda: None)
    monkeypatch.setattr(sgf, "_client", lambda: object())
    monkeypatch.setattr(
        sgf, "_synthesize",
        lambda client, model, corpus, top_n: [
            {"title": "t", "problem": "p", "evidence": "e",
             "frequency": 2, "skill_fit": "new", "score": 0.9},
        ],
    )
    calls: list[tuple[str, int]] = []
    monkeypatch.setattr(sgf, "_send_email", lambda report, n: calls.append((report, n)))

    rc = sgf.main(["--email", "--window-days", "7"])
    assert rc == 0
    assert len(calls) == 1
    report, n = calls[0]
    assert n == 1
    assert "skill-gap finder" in report


def test_main_send_failure_still_returns_zero(monkeypatch) -> None:
    """A send blowing up inside the real _send_email must not break main (exit 0)."""
    monkeypatch.setattr(sgf, "_load_zai_env", lambda: None)
    monkeypatch.setattr(sgf, "_client", lambda: object())
    monkeypatch.setattr(
        sgf, "_synthesize",
        lambda client, model, corpus, top_n: [
            {"title": "t", "problem": "p", "evidence": "e",
             "frequency": 2, "skill_fit": "new", "score": 0.9},
        ],
    )
    _install_fake_mail(monkeypatch, raise_on_send=True)
    monkeypatch.delenv("UA_SKILL_GAP_EMAIL_RECIPIENT", raising=False)

    rc = sgf.main(["--email", "--window-days", "7"])
    assert rc == 0


def test_main_open_issue_and_email_are_independent(monkeypatch) -> None:
    """--open-issue --email runs BOTH; neither is gated on the other."""
    monkeypatch.setattr(sgf, "_load_zai_env", lambda: None)
    monkeypatch.setattr(sgf, "_client", lambda: object())
    monkeypatch.setattr(
        sgf, "_synthesize",
        lambda client, model, corpus, top_n: [
            {"title": "t", "problem": "p", "evidence": "e",
             "frequency": 2, "skill_fit": "new", "score": 0.9},
        ],
    )
    issue_calls: list[int] = []
    email_calls: list[int] = []
    monkeypatch.setattr(sgf, "_open_issue", lambda report, n: issue_calls.append(n))
    monkeypatch.setattr(sgf, "_send_email", lambda report, n: email_calls.append(n))

    rc = sgf.main(["--open-issue", "--email", "--window-days", "7"])
    assert rc == 0
    assert issue_calls == [1]
    assert email_calls == [1]

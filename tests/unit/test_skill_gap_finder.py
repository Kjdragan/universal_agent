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


def test_is_automation_transcript() -> None:
    assert sgf._is_automation_transcript(
        Path("/home/u/.claude/projects/-tmp-ua-selfimprove-claude-x/reflect.jsonl"))
    assert sgf._is_automation_transcript(
        Path("/home/u/.claude/projects/-proj/subagents/workflows/wf.jsonl"))
    assert not sgf._is_automation_transcript(
        Path("/home/u/.claude/projects/-home-kjdragan-lrepos-universal-agent/s.jsonl"))


def test_recent_transcripts_excludes_automation(transcripts_dir: Path) -> None:
    # UA automation transcripts must NOT be mined: the self-improve Stop hook's
    # reflection (scratch CWD "ua-selfimprove-claude-*") and Workflow/Task
    # subagent fan-outs ("**/subagents/**") repeat identical preambles that would
    # otherwise surface as phantom "recurring workflow" skill candidates.
    selfimprove = transcripts_dir / "-tmp-claude-1000-ua-selfimprove-claude-abc123"
    selfimprove.mkdir(parents=True)
    _write_jsonl(selfimprove / "reflect.jsonl", [
        _human_prompt("You are reviewing a single Claude Code working session..."),
    ])
    subagents = (transcripts_dir / "-home-kjdragan-lrepos-universal-agent"
                 / "subagents" / "workflows")
    subagents.mkdir(parents=True)
    _write_jsonl(subagents / "wf-1.jsonl", [
        _human_prompt("This task builds Claude Code skills derived from the backlog"),
    ])

    found = sgf._recent_transcripts(transcripts_dir, window_days=7)
    names = {p.name for p in found}
    assert "reflect.jsonl" not in names
    assert "wf-1.jsonl" not in names
    # the two real working-session transcripts still come through
    assert names == {"session-a.jsonl", "session-b.jsonl"}


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

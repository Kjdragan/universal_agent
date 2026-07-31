"""Tests for the VP-coder apply/finalize crash-recovery checkpoint.

Covers the three required cases:
  * happy path: apply + ruff + pytest green -> checkpoint written;
  * CRASH RECOVERY: a validated checkpoint from a prior (crashed) attempt means
    the apply-script is NOT re-run on retry (called at most once across both
    attempts) and the pipeline succeeds;
  * validation failure: when ruff/pytest fail, NO checkpoint is written, so a
    retry correctly re-attempts the apply.

Plus atomic-write, corrupt-read tolerance, and the retry-prompt wiring in
``claude_cli_client._build_retry_prompt``.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

from universal_agent.scripts.vp_apply_and_checkpoint import (
    StepResult,
    _spawn_step,
    run_apply_pipeline,
)
from universal_agent.services.worker_exit_classifier import classify_worker_exit
from universal_agent.vp.apply_checkpoint import (
    apply_discipline_requested,
    checkpoint_path,
    has_validated_apply,
    read_checkpoint,
    write_checkpoint,
)
from universal_agent.vp.clients.claude_cli_client import (
    _build_cli_prompt,
    _build_retry_prompt,
)


class _Spy:
    """Callable that counts calls and returns a fixed StepResult."""

    def __init__(self, rc: int = 0, detail: str = "ok") -> None:
        self.calls = 0
        self.rc = rc
        self.detail = detail

    def __call__(self, *args, **kwargs):  # signature-agnostic
        self.calls += 1
        return StepResult(rc=self.rc, detail=self.detail)


class _SeqSpy:
    """Callable that returns each StepResult in sequence, counting calls."""

    def __init__(self, results: list[StepResult]) -> None:
        self.results = list(results)
        self.calls = 0

    def __call__(self, *args, **kwargs):
        idx = min(self.calls, len(self.results) - 1)
        self.calls += 1
        return self.results[idx]


# --------------------------------------------------------------------------
# substrate: write / read / has_validated_apply
# --------------------------------------------------------------------------


def test_no_checkpoint_means_not_validated(tmp_path: Path):
    assert not has_validated_apply(tmp_path)
    assert read_checkpoint(tmp_path) is None


def test_write_then_read_checkpoint_roundtrip(tmp_path: Path):
    target = write_checkpoint(
        tmp_path,
        script="apply_typehints.py",
        applied_at="2026-07-08T18:44:00+00:00",
        git_head_after="abc123",
    )
    assert target == checkpoint_path(tmp_path)
    assert target.exists()

    cp = read_checkpoint(tmp_path)
    assert cp is not None
    assert cp.script == "apply_typehints.py"
    assert cp.applied is True
    assert cp.ruff_ok is True and cp.pytest_ok is True
    assert cp.git_head_after == "abc123"
    assert cp.validated is True
    assert has_validated_apply(tmp_path) is True


def test_corrupt_checkpoint_treated_as_none(tmp_path: Path):
    # A half-written / tampered checkpoint must never crash the retry path;
    # the safe default is "no checkpoint" -> re-apply.
    checkpoint_path(tmp_path).write_text("{not valid json", encoding="utf-8")
    assert read_checkpoint(tmp_path) is None
    assert has_validated_apply(tmp_path) is False


def test_write_checkpoint_leaves_no_temp_and_valid_json(tmp_path: Path):
    write_checkpoint(
        tmp_path,
        script="apply_x.py",
        applied_at="2026-07-10T00:00:00+00:00",
        git_head_after="deadbeef",
    )
    # No lingering temp files from the atomic write.
    leftovers = [
        p.name for p in tmp_path.iterdir() if p.name.startswith(".apply_checkpoint.")
    ]
    assert leftovers == []
    # The written file is complete, valid JSON.
    data = json.loads(checkpoint_path(tmp_path).read_text(encoding="utf-8"))
    assert data["applied"] is True and data["script"] == "apply_x.py"


# --------------------------------------------------------------------------
# pipeline: the three required cases
# --------------------------------------------------------------------------


def test_pipeline_happy_path_writes_checkpoint(tmp_path: Path):
    apply_spy = _Spy(rc=0)
    ruff_spy = _Spy(rc=0)
    pytest_spy = _Spy(rc=0)
    apply_script = tmp_path / "apply_demo.py"

    rc = run_apply_pipeline(
        workspace=tmp_path,
        apply_script=apply_script,
        repo_root=tmp_path,
        apply_runner=apply_spy,
        ruff_runner=ruff_spy,
        pytest_runner=pytest_spy,
        now_iso="2026-07-10T01:00:00+00:00",
        git_head="cafef00d",
    )

    assert rc == 0
    assert apply_spy.calls == 1 and ruff_spy.calls == 1 and pytest_spy.calls == 1
    assert has_validated_apply(tmp_path) is True
    cp = read_checkpoint(tmp_path)
    assert cp is not None and cp.script == str(apply_script)
    assert cp.git_head_after == "cafef00d"


def test_pipeline_crash_recovery_does_not_re_run_apply(tmp_path: Path):
    """THE PROOF: a crashed-mid-finalize mission does not re-execute its
    apply-script on retry. The apply-runner is called at most once across both
    attempts, and the retry succeeds.
    """
    apply_spy = _Spy(rc=0)
    ruff_spy = _Spy(rc=0)
    pytest_spy = _Spy(rc=0)
    apply_script = tmp_path / "apply_demo.py"

    # Attempt 1: apply + validate all green -> checkpoint written, then the
    # CLI session dies at finalize (simulated by simply continuing).
    rc1 = run_apply_pipeline(
        workspace=tmp_path,
        apply_script=apply_script,
        repo_root=tmp_path,
        apply_runner=apply_spy,
        ruff_runner=ruff_spy,
        pytest_runner=pytest_spy,
        now_iso="2026-07-08T18:44:00+00:00",
        git_head="abc123",
    )
    assert rc1 == 0
    assert apply_spy.calls == 1
    assert has_validated_apply(tmp_path) is True

    # Attempt 2 (the retry after the crash): same workspace, so the checkpoint
    # is present. The apply-runner must NOT be called again, and ruff/pytest
    # are skipped too. Pipeline still reports success.
    rc2 = run_apply_pipeline(
        workspace=tmp_path,
        apply_script=apply_script,
        repo_root=tmp_path,
        apply_runner=apply_spy,
        ruff_runner=ruff_spy,
        pytest_runner=pytest_spy,
        now_iso="2026-07-08T18:50:00+00:00",
        git_head="abc123",
    )

    assert rc2 == 0
    # At most once across BOTH attempts:
    assert apply_spy.calls == 1, "apply-script was re-run on retry (destructive!)"
    assert ruff_spy.calls == 1, "ruff was re-run on retry"
    assert pytest_spy.calls == 1, "pytest was re-run on retry"


def test_pipeline_validation_failure_writes_no_checkpoint_then_re_applies(
    tmp_path: Path,
):
    """If validation fails, no checkpoint is written, so a retry correctly
    re-attempts the apply (the skip-on-checkpoint guard must NOT mask a real
    validation failure).
    """
    apply_spy = _Spy(rc=0)
    # ruff fails on the first pass, then passes on the retry pass.
    ruff_spy = _SeqSpy([StepResult(rc=1, detail="E501"), StepResult(rc=0)])
    pytest_spy = _Spy(rc=0)
    apply_script = tmp_path / "apply_demo.py"

    # Attempt 1: apply ok, ruff FAILS -> no checkpoint.
    rc1 = run_apply_pipeline(
        workspace=tmp_path,
        apply_script=apply_script,
        repo_root=tmp_path,
        apply_runner=apply_spy,
        ruff_runner=ruff_spy,
        pytest_runner=pytest_spy,
        now_iso="2026-07-10T02:00:00+00:00",
        git_head="abc123",
    )
    assert rc1 != 0, "ruff failure must surface a non-zero exit"
    assert has_validated_apply(tmp_path) is False, (
        "no checkpoint after validation failure"
    )
    assert apply_spy.calls == 1

    # Attempt 2: ruff now passes, pytest passes -> apply IS re-attempted
    # (call count goes 1 -> 2) and a checkpoint is written.
    rc2 = run_apply_pipeline(
        workspace=tmp_path,
        apply_script=apply_script,
        repo_root=tmp_path,
        apply_runner=apply_spy,
        ruff_runner=ruff_spy,
        pytest_runner=pytest_spy,
        now_iso="2026-07-10T02:05:00+00:00",
        git_head="abc123",
    )
    assert rc2 == 0
    assert apply_spy.calls == 2, (
        "apply must be re-attempted when prior validation failed"
    )
    assert has_validated_apply(tmp_path) is True


def test_pipeline_apply_script_failure_writes_no_checkpoint(tmp_path: Path):
    apply_spy = _Spy(rc=2, detail="AssertionError: anchor not found")
    ruff_spy = _Spy(rc=0)
    pytest_spy = _Spy(rc=0)

    rc = run_apply_pipeline(
        workspace=tmp_path,
        apply_script=tmp_path / "apply_demo.py",
        repo_root=tmp_path,
        apply_runner=apply_spy,
        ruff_runner=ruff_spy,
        pytest_runner=pytest_spy,
        now_iso="2026-07-10T03:00:00+00:00",
        git_head="abc123",
    )
    assert rc == 2
    assert has_validated_apply(tmp_path) is False
    # ruff/pytest never reached because apply failed first.
    assert ruff_spy.calls == 0 and pytest_spy.calls == 0


# --------------------------------------------------------------------------
# retry-prompt wiring
# --------------------------------------------------------------------------


def test_retry_prompt_resume_directive_only_when_checkpoint_present(tmp_path: Path):
    write_checkpoint(
        tmp_path,
        script="apply_typehints.py",
        applied_at="2026-07-08T18:44:00+00:00",
        git_head_after="abc123",
    )
    out = _build_retry_prompt("ORIG", "Unknown error", 2, workspace_dir=tmp_path)
    assert "CRASH-RECOVERY" in out
    assert "DO NOT re-run" in out
    assert "ORIG" in out  # original prompt still appended


def test_retry_prompt_no_directive_without_checkpoint(tmp_path: Path):
    out = _build_retry_prompt("ORIG", "Unknown error", 2, workspace_dir=tmp_path)
    assert "CRASH-RECOVERY" not in out
    assert "Retry Attempt 2" in out
    assert "ORIG" in out


def test_retry_prompt_backward_compatible_without_workspace_dir():
    # The new workspace_dir kwarg is keyword-only with a default; existing
    # callers that don't pass it must still work.
    out = _build_retry_prompt("ORIG", "boom", 3)
    assert "Retry Attempt 3" in out
    assert "CRASH-RECOVERY" not in out
    assert "ORIG" in out


# --------------------------------------------------------------------------
# observability wiring: worker-exit classification of pipeline children
# --------------------------------------------------------------------------


def test_spawn_step_classifies_clean_exit(tmp_path: Path):
    step = _spawn_step([sys.executable, "-c", "pass"], cwd=tmp_path)
    assert step.rc == 0
    assert step.classification is not None
    assert step.classification.outcome == "clean_exit_zero"
    assert step.classification.is_failure is False


def test_spawn_step_classifies_nonzero_exit(tmp_path: Path):
    step = _spawn_step([sys.executable, "-c", "raise SystemExit(3)"], cwd=tmp_path)
    assert step.rc == 3
    assert step.classification is not None
    assert step.classification.outcome == "nonzero_exit"
    assert step.classification.is_failure is True


def test_spawn_step_classifies_signaled(tmp_path: Path):
    step = _spawn_step(
        [sys.executable, "-c", "import os, signal; os.kill(os.getpid(), signal.SIGKILL)"],
        cwd=tmp_path,
    )
    assert step.rc < 0, "SIGKILL must surface as a negative returncode"
    assert step.classification is not None
    assert step.classification.outcome == "signaled"
    assert step.classification.is_failure is True


def test_spawn_step_classifies_timeout(tmp_path: Path):
    step = _spawn_step(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        cwd=tmp_path,
        timeout_seconds=1,
    )
    assert step.rc == 124  # coreutils timeout convention
    assert step.classification is not None
    assert step.classification.outcome == "timeout_killed"
    assert step.classification.is_failure is True


def test_spawn_step_classifies_unspawnable(tmp_path: Path):
    step = _spawn_step(
        ["definitely-not-a-real-binary-xyz"], cwd=tmp_path, timeout_seconds=5
    )
    assert step.rc == 127
    assert step.classification is not None
    assert step.classification.outcome == "nonzero_exit"


def test_pipeline_records_worker_exit_classifications_in_checkpoint(tmp_path: Path):
    """The checkpoint's extra block durably records the protocol
    classification for every pipeline child — the observability record the
    salvage / finalize-diagnosability lane reads."""

    def classified_ok(*args, **kwargs):
        return StepResult(
            rc=0, detail="ok", classification=classify_worker_exit(return_code=0)
        )

    rc = run_apply_pipeline(
        workspace=tmp_path,
        apply_script=tmp_path / "apply_demo.py",
        repo_root=tmp_path,
        apply_runner=classified_ok,
        ruff_runner=classified_ok,
        pytest_runner=classified_ok,
        now_iso="2026-07-31T00:00:00+00:00",
        git_head="abc123",
    )
    assert rc == 0
    cp = read_checkpoint(tmp_path)
    assert cp is not None
    by_step = cp.extra.get("worker_exit_by_step")
    assert isinstance(by_step, dict)
    assert set(by_step) == {"apply", "ruff", "pytest"}
    for name, record in by_step.items():
        assert record.get("outcome") == "clean_exit_zero", name
        assert record.get("is_failure") is False, name


def test_pipeline_timeout_killed_validator_writes_no_checkpoint(tmp_path: Path):
    """A timeout-killed validator is a validation failure: non-zero exit,
    no checkpoint, so a retry correctly re-applies."""
    apply_spy = _Spy(rc=0)

    def timed_out_ruff(*args, **kwargs):
        return StepResult(
            rc=124,
            detail="[step timed out]",
            classification=classify_worker_exit(
                return_code=None, was_timeout_killed=True
            ),
        )

    rc = run_apply_pipeline(
        workspace=tmp_path,
        apply_script=tmp_path / "apply_demo.py",
        repo_root=tmp_path,
        apply_runner=apply_spy,
        ruff_runner=timed_out_ruff,
        pytest_runner=_Spy(rc=0),
        now_iso="2026-07-31T00:10:00+00:00",
        git_head="abc123",
    )
    assert rc == 124
    assert has_validated_apply(tmp_path) is False


# --------------------------------------------------------------------------
# apply-discipline advertisement (both VP client lanes)
# --------------------------------------------------------------------------


def test_apply_discipline_requested_payload_top_level():
    assert apply_discipline_requested({"repo_mutation_allowed": True}) is True
    assert apply_discipline_requested({"workflow_kind": "code_change"}) is True
    assert apply_discipline_requested({"mission_type": "coding_task"}) is True


def test_apply_discipline_requested_nested_constraints():
    payload = {"constraints": {"repo_mutation_allowed": True}}
    assert apply_discipline_requested(payload) is True
    # explicit constraints param (the SDK client's call shape)
    assert apply_discipline_requested({}, {"repo_mutation_allowed": True}) is True


def test_apply_discipline_requested_negative():
    assert apply_discipline_requested({}) is False
    assert apply_discipline_requested(None) is False
    assert apply_discipline_requested({"constraints": {}}) is False
    assert apply_discipline_requested({"constraints": "not-a-dict"}) is False


def test_build_cli_prompt_includes_apply_discipline_for_repo_mutation(tmp_path: Path):
    out = _build_cli_prompt(
        "Fix the bug",
        {"repo_mutation_allowed": True},
        tmp_path,
        "",
    )
    assert "Apply-script discipline (crash-recovery)" in out
    assert "vp_apply_and_checkpoint" in out


def test_build_cli_prompt_includes_apply_discipline_for_nested_constraints(
    tmp_path: Path,
):
    out = _build_cli_prompt(
        "Fix the bug",
        {"constraints": {"repo_mutation_allowed": True}},
        tmp_path,
        "",
    )
    assert "Apply-script discipline (crash-recovery)" in out


def test_build_cli_prompt_omits_apply_discipline_without_repo_mutation(
    tmp_path: Path,
):
    out = _build_cli_prompt("Summarize the docs", {}, tmp_path, "")
    assert "Apply-script discipline" not in out


# --------------------------------------------------------------------------
# retry-time safety net: leftover apply scripts without a checkpoint
# --------------------------------------------------------------------------


def test_retry_prompt_caution_when_apply_script_without_checkpoint(tmp_path: Path):
    (tmp_path / "apply_fix.py").write_text("# edits\n", encoding="utf-8")
    out = _build_retry_prompt("ORIG", "Unknown error", 2, workspace_dir=tmp_path)
    assert "CAUTION" in out
    assert "apply_fix.py" in out
    assert "CRASH-RECOVERY" not in out  # no validated checkpoint -> no RESUME
    assert "ORIG" in out


def test_retry_prompt_resume_wins_over_caution(tmp_path: Path):
    (tmp_path / "apply_fix.py").write_text("# edits\n", encoding="utf-8")
    write_checkpoint(
        tmp_path,
        script="apply_fix.py",
        applied_at="2026-07-31T00:00:00+00:00",
        git_head_after="abc123",
    )
    out = _build_retry_prompt("ORIG", "Unknown error", 2, workspace_dir=tmp_path)
    assert "CRASH-RECOVERY" in out
    assert "CAUTION" not in out


def test_retry_prompt_no_caution_for_clean_workspace(tmp_path: Path):
    out = _build_retry_prompt("ORIG", "Unknown error", 2, workspace_dir=tmp_path)
    assert "CAUTION" not in out
    assert "CRASH-RECOVERY" not in out


# --------------------------------------------------------------------------
# integration: run_mission threads the real workspace into the retry prompt
# --------------------------------------------------------------------------


async def test_run_mission_retry_prompt_carries_resume_directive(tmp_path: Path):
    """End-to-end wiring proof: with a validated checkpoint in the mission's
    real workspace, the SECOND attempt's prompt (as handed to
    _execute_cli_session) carries the CRASH-RECOVERY RESUME directive."""
    import json as _json
    from unittest.mock import patch

    from universal_agent.vp.clients import claude_cli_client as cli_mod

    mission = {
        "mission_id": "test-resume-1",
        "vp_id": "vp.coder.primary",
        "objective": "Fix the bug",
        "payload_json": _json.dumps({"timeout_seconds": 30, "max_retries": 1}),
    }
    # The workspace run_mission will resolve for this mission.
    workspace = (tmp_path / "test-resume-1").resolve()
    workspace.mkdir(parents=True)
    write_checkpoint(
        workspace,
        script="apply_fix.py",
        applied_at="2026-07-31T01:00:00+00:00",
        git_head_after="abc123",
    )

    prompts: list[str] = []

    async def fake_execute(*, prompt, **kwargs):
        prompts.append(prompt)
        if len(prompts) == 1:
            return cli_mod.MissionOutcome(
                status="failed",
                message="Unknown error",
                payload={"trace_id": None, "final_text": ""},
            )
        return cli_mod.MissionOutcome(status="completed", payload={})

    with patch.object(cli_mod, "_execute_cli_session", side_effect=fake_execute):
        outcome = await cli_mod.ClaudeCodeCLIClient().run_mission(
            mission=mission, workspace_root=tmp_path
        )

    assert outcome.status == "completed"
    assert len(prompts) == 2
    assert "CRASH-RECOVERY" not in prompts[0]
    assert "CRASH-RECOVERY" in prompts[1]
    assert "DO NOT re-run" in prompts[1]

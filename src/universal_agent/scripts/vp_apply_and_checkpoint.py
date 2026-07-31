#!/usr/bin/env python3
"""Blessed apply + validate + checkpoint runner for VP-coder missions.

THIS module is the single owner of the apply -> ruff -> pytest -> checkpoint
ordering. A VP-coder mission runs it (instead of its ``apply_*.py`` script
directly) so that:

1. the apply-script runs at most once per workspace. :func:`run_apply_pipeline`
   is **idempotent**: if :func:`has_validated_apply` is already true for the
   workspace, it NO-OPs and returns 0. That is the structural guarantee that a
   mission which crashed at finalize cannot re-execute its apply-script on
   retry (re-running consumes edit anchors and double-applies);
2. the checkpoint is written ONLY after the apply-script *and* ``ruff`` *and*
   ``pytest`` all succeed — never on a validation failure;
3. the retry-prompt builder can read the checkpoint and direct the retried
   session to skip re-applying and resume from push/PR.

Usage (run from the mission workspace; the venv ``python`` sees the editable
``universal_agent`` install)::

    python -m universal_agent.scripts.vp_apply_and_checkpoint <apply_script.py>
        [--workspace DIR] [--repo-root DIR]
        [--ruff-scope PATH [PATH ...]] [--pytest-args ARG [ARG ...]]
        [--apply-arg ARG [ARG ...]] [--step-timeout-seconds N]

The apply-script runs under the same interpreter (``sys.executable``); ``ruff``
and ``pytest`` run under ``uv run`` against ``--repo-root`` (the repo the
apply-script patches). The checkpoint lands in ``--workspace`` (default: cwd,
which IS the mission workspace for a CLI session).

Task Hub Observability Protocol — how this runner participates
--------------------------------------------------------------

This runner spawns real work (the apply-script, ``ruff``, ``pytest``), but it
runs as a **nested child inside an already-tracked unit of work**: the parent
``claude`` CLI turn spawned by ``vp/clients/claude_cli_client.py`` (protocol
site ``"vp_cli"``). The parent owns the mission task's whole ledger lifecycle —
it records the worker PID after spawn (``task_hub.record_worker_pid``),
classifies the CLI exit (``_classify_and_route_cli_exit``), persists the
classification onto the run row via ``task_hub._close_run``, and routes
protocol violations (``park_task_for_protocol_violation``).

Therefore this runner deliberately does NOT open/close ``task_hub`` run rows or
park tasks: ``_close_run`` closes the assignment's open run, so a nested child
calling it would steal the parent's close mid-turn and double-book the ledger.
Its protocol participation is **worker-exit classification**: every pipeline
step's child (apply / ruff / pytest — the read-only ``git rev-parse`` audit
probe is exempt) is classified with :func:`classify_worker_exit` (nonzero /
signaled / timeout-killed aware), the classification is echoed to stderr (which
lands in the turn's ``run.log``), and on success it is durably recorded in
``apply_checkpoint.json`` under ``extra["worker_exit_by_step"]`` — alongside
the marker the salvage / finalize-diagnosability lane
(``vp/finalize_failure_context.py``) reads. ``task_closed_normally=True`` is
passed deliberately: task disposition belongs to the parent site, and a child
must never manufacture a ``clean_exit_zero_no_disposition`` protocol violation
for a task it doesn't own.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import sys
from typing import Callable, Optional, Sequence

from universal_agent.services.worker_exit_classifier import (
    WorkerExit,
    classify_worker_exit,
)
from universal_agent.vp.apply_checkpoint import (
    has_validated_apply,
    write_checkpoint,
)

DEFAULT_REPO_ROOT = "/opt/universal_agent"

# coreutils `timeout` convention for a timed-out child.
_TIMEOUT_EXIT_CODE = 124
# `command not found` convention for an unspawnable child (e.g. `uv` missing).
_SPAWN_FAILURE_EXIT_CODE = 127

_DEFAULT_STEP_TIMEOUT_SECONDS = 1800


def _step_timeout_seconds() -> int:
    """Per-step wall-clock backstop for apply/ruff/pytest children.

    Generous by design — the parent turn's LivenessWatchdog governs overall
    liveness; this only stops a single wedged child from pinning the turn to
    the lane's absolute wall-clock cap. Floor of 30s so a typo'd env value
    cannot make every step instantly time out.
    """
    raw = os.environ.get("UA_VP_APPLY_STEP_TIMEOUT_SECONDS", "").strip()
    try:
        value = int(raw) if raw else _DEFAULT_STEP_TIMEOUT_SECONDS
    except ValueError:
        value = _DEFAULT_STEP_TIMEOUT_SECONDS
    return max(30, value)


@dataclass
class StepResult:
    """Outcome of one pipeline step (apply / ruff / pytest).

    ``classification`` is the Task Hub worker-exit classification for the
    child's termination (None when a test injects a bare result).
    """

    rc: int
    detail: str = ""
    classification: Optional[WorkerExit] = None


def _tail(*chunks: Optional[str]) -> str:
    """Last 1200 chars of the concatenated child output, None-safe."""
    return "".join(c for c in chunks if c)[-1200:]


def _spawn_step(
    cmd: Sequence[str],
    *,
    cwd: Path,
    timeout_seconds: Optional[int] = None,
) -> StepResult:
    """Spawn one pipeline child and classify its termination.

    The classification maps the three real-world failure shapes onto the
    protocol vocabulary: ``TimeoutExpired`` -> ``timeout_killed`` (rc 124),
    negative returncode -> ``signaled`` (OOM-kill etc.), any other nonzero ->
    ``nonzero_exit``. ``task_closed_normally=True`` on purpose — see the
    module docstring: disposition belongs to the parent ``vp_cli`` site.
    """
    timeout = timeout_seconds if timeout_seconds is not None else _step_timeout_seconds()
    try:
        proc = subprocess.run(
            list(cmd),
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else exc.stdout
        stderr = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else exc.stderr
        return StepResult(
            rc=_TIMEOUT_EXIT_CODE,
            detail=_tail(stdout, stderr, f"\n[step timed out after {timeout}s]"),
            classification=classify_worker_exit(
                return_code=None, was_timeout_killed=True
            ),
        )
    except OSError as exc:
        return StepResult(
            rc=_SPAWN_FAILURE_EXIT_CODE,
            detail=f"failed to spawn {cmd[0]!r}: {exc}",
            classification=classify_worker_exit(return_code=None),
        )
    rc = proc.returncode
    return StepResult(
        rc=rc,
        detail=_tail(proc.stdout, proc.stderr),
        classification=classify_worker_exit(
            return_code=rc,
            was_signaled=rc is not None and rc < 0,
        ),
    )


def _real_apply_runner(
    apply_script: Path,
    repo_root: Path,
    apply_args: Sequence[str],
) -> StepResult:
    """Run the agent's apply-script under the venv interpreter."""
    return _spawn_step(
        [sys.executable, str(apply_script), *apply_args], cwd=repo_root
    )


def _real_ruff_runner(scope: Sequence[str], repo_root: Path) -> StepResult:
    return _spawn_step(["uv", "run", "ruff", "check", *scope], cwd=repo_root)


def _real_pytest_runner(args: Sequence[str], repo_root: Path) -> StepResult:
    return _spawn_step(["uv", "run", "pytest", *args], cwd=repo_root)


def _git_head(repo_root: Path) -> str:
    """Best-effort current HEAD sha for the checkpoint audit trail."""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=5,
        )
        if proc.returncode == 0:
            return proc.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return ""


def _classification_dict(step: StepResult) -> dict[str, object]:
    return step.classification.to_dict() if step.classification else {}


def _fail_step(name: str, step: StepResult) -> int:
    """Report a failed pipeline step to stderr and return its exit code."""
    outcome = step.classification.outcome if step.classification else "unclassified"
    print(
        f"apply_checkpoint: {name} FAILED (rc={step.rc}, worker_exit={outcome}); "
        "no checkpoint written.",
        file=sys.stderr,
    )
    if step.detail:
        print(step.detail, file=sys.stderr)
    return step.rc


def run_apply_pipeline(
    workspace: Path,
    apply_script: Path,
    *,
    repo_root: Path,
    apply_args: Sequence[str] = (),
    ruff_scope: Sequence[str] = ("src",),
    pytest_args: Sequence[str] = ("tests/unit",),
    apply_runner: Callable[..., StepResult] = _real_apply_runner,
    ruff_runner: Callable[..., StepResult] = _real_ruff_runner,
    pytest_runner: Callable[..., StepResult] = _real_pytest_runner,
    now_iso: str | None = None,
    git_head: str | None = None,
) -> int:
    """Execute apply -> ruff -> pytest -> checkpoint, idempotently.

    Returns the process-style exit code (0 = success). The idempotency guard
    reads the checkpoint BEFORE any apply attempt; if a validated checkpoint
    already exists the apply-runner is never called. Validators are injectable
    so the ordering and crash-recovery guarantees are unit-testable without
    spawning real ruff/pytest subprocesses.
    """
    workspace = Path(workspace)
    apply_script = Path(apply_script)
    repo_root = Path(repo_root)

    # BEFORE any apply attempt: if a prior attempt already applied + validated,
    # skip re-applying entirely. This is the crash-recovery guarantee.
    if has_validated_apply(workspace):
        print(
            "apply_checkpoint: a validated apply is already recorded for this "
            "workspace; skipping re-apply (crash-recovery no-op).",
            file=sys.stderr,
        )
        return 0

    worker_exit_by_step: dict[str, dict[str, object]] = {}

    step = apply_runner(apply_script, repo_root, apply_args)
    worker_exit_by_step["apply"] = _classification_dict(step)
    if step.rc != 0:
        return _fail_step("apply-script", step)

    step = ruff_runner(ruff_scope, repo_root)
    worker_exit_by_step["ruff"] = _classification_dict(step)
    if step.rc != 0:
        return _fail_step("ruff", step)

    step = pytest_runner(pytest_args, repo_root)
    worker_exit_by_step["pytest"] = _classification_dict(step)
    if step.rc != 0:
        return _fail_step("pytest", step)

    head = git_head if git_head is not None else _git_head(repo_root)
    stamp = now_iso if now_iso is not None else datetime.now(timezone.utc).isoformat()
    target = write_checkpoint(
        workspace,
        script=str(apply_script),
        applied_at=stamp,
        git_head_after=head,
        ruff_ok=True,
        pytest_ok=True,
        extra={"worker_exit_by_step": worker_exit_by_step},
    )
    print(f"apply_checkpoint: validated apply recorded at {target}", file=sys.stderr)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Apply a VP-coder apply-script, validate (ruff+pytest), "
        "and write a crash-recovery checkpoint. Idempotent.",
    )
    p.add_argument("apply_script", help="Path to the agent-authored apply_*.py script.")
    p.add_argument(
        "--workspace",
        default=None,
        help="Mission workspace to write apply_checkpoint.json into "
        "(default: current directory).",
    )
    p.add_argument(
        "--repo-root",
        default=os.environ.get("UA_REPO_ROOT", DEFAULT_REPO_ROOT),
        help=f"Repo root for ruff/pytest (default: {DEFAULT_REPO_ROOT}).",
    )
    p.add_argument(
        "--ruff-scope",
        nargs="+",
        default=["src"],
        help="Paths to pass to `ruff check` (default: src).",
    )
    p.add_argument(
        "--pytest-args",
        nargs="+",
        default=["tests/unit"],
        help="Args to pass to `pytest` (default: tests/unit).",
    )
    p.add_argument(
        "--apply-arg",
        dest="apply_args",
        nargs="+",
        default=[],
        help="Extra args to pass through to the apply-script.",
    )
    p.add_argument(
        "--step-timeout-seconds",
        type=int,
        default=None,
        help="Per-step wall-clock backstop for apply/ruff/pytest children "
        f"(default: UA_VP_APPLY_STEP_TIMEOUT_SECONDS or {_DEFAULT_STEP_TIMEOUT_SECONDS}).",
    )
    args = p.parse_args(argv)

    if args.step_timeout_seconds is not None:
        os.environ["UA_VP_APPLY_STEP_TIMEOUT_SECONDS"] = str(args.step_timeout_seconds)

    workspace = (
        Path(args.workspace).resolve() if args.workspace else Path.cwd().resolve()
    )
    repo_root = Path(args.repo_root).resolve()
    apply_script = Path(args.apply_script).resolve()

    return run_apply_pipeline(
        workspace=workspace,
        apply_script=apply_script,
        repo_root=repo_root,
        apply_args=tuple(args.apply_args),
        ruff_scope=tuple(args.ruff_scope),
        pytest_args=tuple(args.pytest_args),
    )


if __name__ == "__main__":
    raise SystemExit(main())

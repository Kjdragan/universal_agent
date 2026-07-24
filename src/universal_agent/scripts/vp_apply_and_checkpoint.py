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
        [--apply-arg ARG [ARG ...]]

The apply-script runs under the same interpreter (``sys.executable``); ``ruff``
and ``pytest`` run under ``uv run`` against ``--repo-root`` (the repo the
apply-script patches). The checkpoint lands in ``--workspace`` (default: cwd,
which IS the mission workspace for a CLI session).
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import sys
from typing import Callable, Sequence

from universal_agent.vp.apply_checkpoint import (
    has_validated_apply,
    write_checkpoint,
)

DEFAULT_REPO_ROOT = "/opt/universal_agent"


@dataclass
class StepResult:
    """Outcome of one pipeline step (apply / ruff / pytest)."""

    rc: int
    detail: str = ""


def _real_apply_runner(
    apply_script: Path,
    repo_root: Path,
    apply_args: Sequence[str],
) -> StepResult:
    """Run the agent's apply-script under the venv interpreter."""
    cmd = [sys.executable, str(apply_script), *apply_args]
    proc = subprocess.run(cmd, cwd=str(repo_root), capture_output=True, text=True)
    tail = (proc.stdout + proc.stderr)[-1200:]
    return StepResult(rc=proc.returncode, detail=tail)


def _uv_step(cmd_extra: Sequence[str], repo_root: Path) -> StepResult:
    """Run a ``uv run`` step (ruff / pytest) against ``repo_root``."""
    cmd = ["uv", "run", *cmd_extra]
    proc = subprocess.run(cmd, cwd=str(repo_root), capture_output=True, text=True)
    tail = (proc.stdout + proc.stderr)[-1200:]
    return StepResult(rc=proc.returncode, detail=tail)


def _real_ruff_runner(scope: Sequence[str], repo_root: Path) -> StepResult:
    return _uv_step(["ruff", "check", *scope], repo_root)


def _real_pytest_runner(args: Sequence[str], repo_root: Path) -> StepResult:
    return _uv_step(["pytest", *args], repo_root)


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

    step = apply_runner(apply_script, repo_root, apply_args)
    if step.rc != 0:
        print(
            f"apply_checkpoint: apply-script FAILED (rc={step.rc}); no checkpoint written.",
            file=sys.stderr,
        )
        if step.detail:
            print(step.detail, file=sys.stderr)
        return step.rc

    step = ruff_runner(ruff_scope, repo_root)
    if step.rc != 0:
        print(
            f"apply_checkpoint: ruff FAILED (rc={step.rc}); no checkpoint written.",
            file=sys.stderr,
        )
        if step.detail:
            print(step.detail, file=sys.stderr)
        return step.rc

    step = pytest_runner(pytest_args, repo_root)
    if step.rc != 0:
        print(
            f"apply_checkpoint: pytest FAILED (rc={step.rc}); no checkpoint written.",
            file=sys.stderr,
        )
        if step.detail:
            print(step.detail, file=sys.stderr)
        return step.rc

    head = git_head if git_head is not None else _git_head(repo_root)
    stamp = now_iso if now_iso is not None else datetime.now(timezone.utc).isoformat()
    target = write_checkpoint(
        workspace,
        script=str(apply_script),
        applied_at=stamp,
        git_head_after=head,
        ruff_ok=True,
        pytest_ok=True,
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
    args = p.parse_args(argv)

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

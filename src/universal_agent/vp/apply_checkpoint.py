"""Durable apply-script checkpoint for VP-coder crash recovery.

When a VP-coder mission applies repo edits via an agent-authored
``apply_*.py`` script (the Edit tool is workspace-confined, so the agent
writes a script that patches absolute repo paths), the script runs *during*
the Claude CLI turn. If the CLI session then dies at finalize — the opaque
``"Unknown error"`` / empty ``final_text`` signature — the mission's
internal retry loop (``vp.clients.claude_cli_client.ClaudeCodeCLIClient.run_mission``)
re-enters the **same** workspace (the workspace is resolved once by
``mission_id`` and reused on every attempt). A naive retry would re-run the
apply-script, which is destructive: anchor-validated apply-scripts consume
their edit anchors on first run (a second run aborts with ``n == 0``),
wasting the retry and misrouting it as an apply failure.

This module is the durable checkpoint substrate that makes finalize
crash-recoverable. A blessed runner (``scripts/vp_apply_and_checkpoint.py``)
writes the checkpoint **only after** the apply-script *and* validation
(``ruff`` + ``pytest``) succeed; the retry-prompt builder reads it **before**
any apply attempt and, if a validated checkpoint is present, directs the
session to skip re-applying and resume from the post-apply steps.

The checkpoint is a single JSON file at the workspace root
(``apply_checkpoint.json``), written atomically (temp file + ``os.replace``
+ ``fsync``) so a crash mid-write can never leave a half-marker that
falsely reports a validated apply.

Correctness contract (the property this module guarantees):

* the checkpoint write happens AFTER apply + validate succeed;
* the retry read happens BEFORE any apply attempt;
* re-running the blessed runner with a validated checkpoint present is a
  safe NO-OP (it never re-applies).

Coordination: ``scripts/vp_coder_workspace_pruner.py`` is intentionally left
mtime-only. The downstream "salvage clean apply-scripts" task can key off
``has_validated_apply`` so salvage targets workspaces whose apply landed but
whose mission never recovered — this module stops that being needed in the
common case by making the retry resume correctly.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Optional

CHECKPOINT_FILENAME = "apply_checkpoint.json"
_CURRENT_VERSION = 1


@dataclass(frozen=True)
class ApplyCheckpoint:
    """Persisted record that an apply-script ran and was validated.

    ``applied`` is the headline flag the retry path reads. ``ruff_ok`` and
    ``pytest_ok`` are recorded separately so a reader can distinguish "apply
    ran but validation failed" (no checkpoint is written at all in that case)
    from "apply ran and validation passed" (checkpoint present, all green).
    """

    script: str
    applied: bool
    ruff_ok: bool
    pytest_ok: bool
    applied_at: str
    git_head_after: str
    validator: str = "vp_apply_and_checkpoint"
    version: int = _CURRENT_VERSION
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def validated(self) -> bool:
        """True only when apply ran AND both validators passed."""
        return bool(self.applied and self.ruff_ok and self.pytest_ok)


def checkpoint_path(workspace: Path) -> Path:
    """Resolve the checkpoint file location for a workspace root."""
    return Path(workspace) / CHECKPOINT_FILENAME


def read_checkpoint(workspace: Path) -> Optional[ApplyCheckpoint]:
    """Load the checkpoint, or ``None`` if absent / unreadable.

    A corrupt or partially-written checkpoint is treated as "no checkpoint"
    rather than raising: the atomic write in :func:`write_checkpoint` makes a
    true half-write impossible, but a file copied/tampered with by hand must
    never crash the retry path. The caller then re-applies, which is the safe
    default.
    """
    path = checkpoint_path(workspace)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    # Tolerate missing optional fields so a forward-compatible reader does
    # not reject a future writer that added a field.
    try:
        return ApplyCheckpoint(
            script=str(raw.get("script") or ""),
            applied=bool(raw.get("applied")),
            ruff_ok=bool(raw.get("ruff_ok")),
            pytest_ok=bool(raw.get("pytest_ok")),
            applied_at=str(raw.get("applied_at") or ""),
            git_head_after=str(raw.get("git_head_after") or ""),
            validator=str(raw.get("validator") or "vp_apply_and_checkpoint"),
            version=int(raw.get("version") or _CURRENT_VERSION),
            extra=dict(raw.get("extra") or {})
            if isinstance(raw.get("extra"), dict)
            else {},
        )
    except (TypeError, ValueError):
        return None


def has_validated_apply(workspace: Path) -> bool:
    """True iff a validated (apply + ruff + pytest green) checkpoint exists.

    This is the single read-side predicate the retry path and the blessed
    runner consult. Reading it is the "before any apply attempt" half of the
    correctness contract.
    """
    cp = read_checkpoint(workspace)
    return cp is not None and cp.validated


def write_checkpoint(
    workspace: Path,
    *,
    script: str,
    applied_at: str,
    git_head_after: str,
    ruff_ok: bool = True,
    pytest_ok: bool = True,
    validator: str = "vp_apply_and_checkpoint",
    extra: Optional[dict[str, Any]] = None,
) -> Path:
    """Atomically persist a validated-apply checkpoint.

    The caller (the blessed runner) MUST only call this after the apply-script
    *and* ``ruff`` *and* ``pytest`` have all succeeded — this function does not
    re-check, it records. The write is atomic via ``tempfile`` + ``os.replace``
    so a process crash during the write either leaves the previous file intact
    or the complete new file, never a half-written marker.
    """
    workspace = Path(workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    checkpoint = ApplyCheckpoint(
        script=str(script),
        applied=True,
        ruff_ok=bool(ruff_ok),
        pytest_ok=bool(pytest_ok),
        applied_at=str(applied_at),
        git_head_after=str(git_head_after),
        validator=str(validator),
        version=_CURRENT_VERSION,
        extra=dict(extra or {}),
    )
    payload = json.dumps(asdict(checkpoint), sort_keys=True) + "\n"
    target = checkpoint_path(workspace)

    # Atomic replace pattern: write+fsync a temp file in the SAME directory
    # (so os.replace is a single rename on the same filesystem), then rename.
    fd, tmp_name = tempfile.mkstemp(
        prefix=".apply_checkpoint.", suffix=".tmp", dir=str(workspace)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, target)
        # fsync the directory so the rename survives a crash, not just the data.
        _fsync_dir(workspace)
    except BaseException:
        # Best-effort cleanup of the temp file on any failure; never shadow
        # the original exception.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return target


def _fsync_dir(path: Path) -> None:
    """fsync the directory holding the checkpoint so the rename is durable."""
    try:
        dir_fd = os.open(str(path), os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError:
        # Not every filesystem supports directory fsync (e.g. tmpfs in tests).
        # Durability is best-effort; the atomic rename is the real guarantee.
        pass

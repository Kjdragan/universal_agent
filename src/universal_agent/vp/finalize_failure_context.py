"""Diagnosability substrate for VP-coder finalize-step crashes.

Complement to ``vp.apply_checkpoint`` (the sibling crash-*recovery* lane). This
module is the crash-*diagnosability* lane: when a VP mission's finalize/finish
step throws AFTER the real work (apply-edits) has run, the mission record used
to collapse to an opaque ``outcome.message="Unknown error"`` with
``final_text=""`` and ``trace_id=null``, silently losing the fact that the work
completed. That forced destructive re-runs (apply-edits anchor corruption) and
blocked the dispatch sweep from recognizing done work.

This module captures a structured, recoverable record instead:

* :data:`DISPOSITION_WORK_DONE_FINALIZE_FAILED` — a new disposition, distinct
  from generic ``failed``/``vp_self_reported``, that the dispatch sweep and the
  rescue hook (``worker_loop._classify_outcome_failure_mode``) key off to
  recognize done-but-unfinalized work.
* :class:`WorkSnapshot` — a durable, pre-crash record that the apply ran and/or
  workspace artifacts are present, captured BEFORE the finalize exception can
  lose that fact.
* :func:`capture_work_snapshot` / :func:`work_was_done` — detection.
* :func:`build_work_done_finalize_failed_payload` — assembles the structured
  ``MissionOutcome.payload`` (disposition, recoverable flag, salvaged
  ``final_text``, propagated ``trace_id``, error context, work-snapshot).

Sibling compatibility: ``capture_work_snapshot`` reads the recovery lane's
checkpoint via a lazy ``from universal_agent.vp.apply_checkpoint import
has_validated_apply``. When that checkpoint module is present (sibling's PR
landed), ``WorkSnapshot.apply_checkpoint_validated`` is the strongest work-done
signal and this module consumes it directly — the two lanes compose. Until then
the import degrades to ``False`` and detection falls back to the apply-script /
work-product / ``fail_with_edits.txt`` marker signals, so this fix is useful on
its own. The two modules never write the same files and never overlap.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import logging
from pathlib import Path
import subprocess
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

# The recoverable disposition. Distinct from generic "failed"; the dispatch
# sweep / rescue hook read this to avoid a destructive re-run.
DISPOSITION_WORK_DONE_FINALIZE_FAILED = "work_done_finalize_failed"

# Marker file the VP-coder agent drops when it knows it applied edits but is
# about to fail. Agent-authored (no framework code writes it), so it is a
# *signal*, not a contract — never the sole basis for work_was_done.
FAIL_WITH_EDITS_MARKER = "fail_with_edits.txt"

# Artifacts whose presence is strong evidence the mission got deep into its
# turn (a workspace that never started work has none of these).
_NOTABLE_ARTIFACT_NAMES = (
    "manifest.json",
    "sync_ready.json",
    "run.log",
    "COMPLETION.md",
)

# How many work-product / apply-script paths to embed in the snapshot. The full
# lists can be large; we cap to keep the persisted payload bounded.
_MAX_LISTED_PATHS = 25


@dataclass(frozen=True)
class WorkSnapshot:
    """Pre-crash record that a mission's real work ran.

    ``apply_checkpoint_validated`` is the authoritative signal when the sibling
    recovery lane's checkpoint module is present. The remaining fields are
    independent forensic signals so detection still works without it.
    """

    workspace_dir: str
    captured_at_epoch: float
    apply_checkpoint_validated: bool = False
    apply_scripts: list[str] = field(default_factory=list)
    work_product_files: list[str] = field(default_factory=list)
    fail_with_edits_marker: bool = False
    notable_artifacts: dict[str, bool] = field(default_factory=dict)
    git_head: Optional[str] = None
    # git rev-parse/diff can confirm the agent mutated the repo before the
    # crash. None when the repo dir is unknown / git is unavailable.
    git_has_uncommitted_changes: Optional[bool] = None


def _has_validated_apply_safe(workspace_dir: Any) -> bool:
    """Read the sibling recovery lane's checkpoint, or ``False`` if absent.

    Lazy + best-effort: the sibling's ``apply_checkpoint`` module is not yet on
    ``origin/main`` at the time this diagnosability lane lands. When it lands,
    this starts returning ``True`` for recovered workspaces automatically —
    zero coupling, graceful degradation.
    """
    try:
        from universal_agent.vp.apply_checkpoint import has_validated_apply

        return bool(has_validated_apply(Path(workspace_dir)))
    except Exception:
        return False


def _run_git(args: list[str], cwd: str) -> Optional[str]:
    try:
        out = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if out.returncode == 0:
            return out.stdout
    except Exception:
        pass
    return None


def capture_work_snapshot(
    workspace_dir: Any,
    *,
    repo_dir: Any = None,
) -> WorkSnapshot:
    """Capture a structured snapshot of completed work in ``workspace_dir``.

    Pure read + bounded subprocess (``git``); never raises — every field
    degrades to its empty/False default on any error so a snapshot failure can
    never mask the original finalize crash. ``repo_dir`` (when provided) lets
    the caller point at the actual repo the apply-script mutated, since the
    workspace and the repo are often different directories.
    """
    workspace = Path(workspace_dir)
    snapshot_workspace = str(workspace)

    apply_scripts: list[str] = []
    work_product_files: list[str] = []
    notable: dict[str, bool] = {}
    fail_marker = False

    try:
        if workspace.is_dir():
            # apply_*.py at the workspace root are the agent-authored edit
            # scripts (Edit tool is workspace-confined, so the agent writes a
            # script that patches absolute repo paths). Their presence is
            # strong evidence the turn reached the apply step.
            apply_scripts = sorted(
                p.name for p in workspace.glob("apply*.py") if p.is_file()
            )
            wp_dir = workspace / "work_products"
            if wp_dir.is_dir():
                work_product_files = sorted(
                    str(p.relative_to(workspace))
                    for p in wp_dir.rglob("*")
                    if p.is_file()
                )
            fail_marker = (workspace / FAIL_WITH_EDITS_MARKER).is_file()
            for name in _NOTABLE_ARTIFACT_NAMES:
                notable[name] = (workspace / name).is_file()
    except Exception:
        logger.debug("work-snapshot: workspace scan failed", exc_info=True)

    git_head: Optional[str] = None
    git_dirty: Optional[bool] = None
    if repo_dir is not None:
        repo_path = Path(repo_dir)
        head = _run_git(["rev-parse", "HEAD"], cwd=str(repo_path))
        git_head = head.strip() or None if head is not None else None
        status = _run_git(["status", "--porcelain"], cwd=str(repo_path))
        if status is not None:
            git_dirty = bool(status.strip())

    return WorkSnapshot(
        workspace_dir=snapshot_workspace,
        captured_at_epoch=time.time(),
        apply_checkpoint_validated=_has_validated_apply_safe(workspace),
        apply_scripts=apply_scripts[:_MAX_LISTED_PATHS],
        work_product_files=work_product_files[:_MAX_LISTED_PATHS],
        fail_with_edits_marker=fail_marker,
        notable_artifacts=notable,
        git_head=git_head,
        git_has_uncommitted_changes=git_dirty,
    )


def work_was_done(snapshot: Optional[WorkSnapshot]) -> bool:
    """True when the snapshot shows real completed work, not a bare workspace.

    Conservative on purpose: an empty workspace (startup crash before any work)
    must NOT be marked recoverable, or the disposition loses its meaning. The
    sibling's validated checkpoint is authoritative; otherwise we require
    concrete forensic evidence (apply-scripts that ran to a fail-with-edits
    state, validated apply, real work products, or a repo the agent mutated).
    """
    if snapshot is None:
        return False
    if snapshot.apply_checkpoint_validated:
        return True
    # apply-scripts present AND either the agent self-marked fail-with-edits OR
    # the repo actually changed → the apply ran and reached a work state.
    if snapshot.apply_scripts and (
        snapshot.fail_with_edits_marker
        or snapshot.git_has_uncommitted_changes is True
    ):
        return True
    # Real work products written (not just a scratch dir).
    if snapshot.work_product_files:
        return True
    return False


def snapshot_to_dict(snapshot: Optional[WorkSnapshot]) -> dict[str, Any]:
    """JSON-serialize a snapshot for embedding in ``MissionOutcome.payload``."""
    if snapshot is None:
        return {}
    return dict(asdict(snapshot))


def build_work_done_finalize_failed_payload(
    *,
    workspace_dir: Any,
    final_text: str,
    trace_id: Optional[str],
    error_message: Optional[str],
    error_detail: Optional[str] = None,
    log_tail: Optional[str] = None,
    repo_dir: Any = None,
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Assemble the structured, recoverable failure payload.

    Always sets ``disposition``/``recoverable`` and embeds the salvaged
    ``final_text``, propagated ``trace_id``, a bounded ``error_context``, and
    the ``work_snapshot``. ``status``/``result_ref``/``message`` are left to
    the caller — this is just the payload dict.
    """
    snapshot = capture_work_snapshot(workspace_dir, repo_dir=repo_dir)
    payload: dict[str, Any] = {
        "disposition": DISPOSITION_WORK_DONE_FINALIZE_FAILED,
        "recoverable": True,
        # Salvage the finish path's final text that the bare-error path lost.
        "final_text": (final_text or "")[:4000],
        # Propagate the trace id so the failure record links to trace.json.
        "trace_id": trace_id,
        "error_context": {
            "message": (error_message or "")[:2000] or None,
            "detail": (error_detail or "")[:4000] or None,
            "log_tail": (log_tail or "")[:4000] or None,
        },
        "work_snapshot": snapshot_to_dict(snapshot),
    }
    if extra:
        payload.update(extra)
    return payload


def maybe_work_done_finalize_failed_payload(
    *,
    workspace_dir: Any,
    final_text: str,
    trace_id: Optional[str],
    error_message: Optional[str],
    error_detail: Optional[str] = None,
    log_tail: Optional[str] = None,
    prior_payload: Optional[dict[str, Any]] = None,
) -> Optional[dict[str, Any]]:
    """Return the recoverable payload when work ran before the finalize crash.

    The shared gate both SDK clients (``claude_code_client``,
    ``claude_generalist_client``) call on their failure path: if a
    :func:`capture_work_snapshot` shows real completed work, build the
    structured ``work_done_finalize_failed`` payload (preserving any caller
    bookkeeping like ``sdk_consecutive_timeouts`` from ``prior_payload``);
    otherwise return ``None`` so the caller keeps its original failure payload
    unchanged (the negative / no-work case).
    """
    snapshot = capture_work_snapshot(workspace_dir)
    if not work_was_done(snapshot):
        return None
    extra: dict[str, Any] = {}
    if prior_payload:
        for key in ("sdk_consecutive_timeouts", "sdk_parked_for_review"):
            if prior_payload.get(key):
                extra[key] = prior_payload[key]
    return build_work_done_finalize_failed_payload(
        workspace_dir=workspace_dir,
        final_text=final_text,
        trace_id=trace_id,
        error_message=error_message,
        error_detail=error_detail,
        log_tail=log_tail,
        extra=extra or None,
    )


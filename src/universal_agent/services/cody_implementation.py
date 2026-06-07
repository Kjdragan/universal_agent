"""Cody Phase 3 implementation helpers.

The mechanical helpers Cody invokes when she picks up a `cody_demo_task`
from Task Hub (queued by PR 8's `cody-task-dispatcher`). These do the
deterministic parts:

  - Verify the workspace is ready (BRIEF/ACCEPTANCE/business_relevance present,
    .claude/settings.json vanilla)
  - Load the briefing artifacts as structured data
  - Run a command from inside the workspace (so `uv run` finds the
    workspace's `pyproject.toml`); env is inherited by default
  - Capture stdout/stderr/timing
  - Detect which endpoint actually served the request
  - Write the result manifest, BUILD_NOTES, run_output

The actual "build a Claude Code demo" creative work is conversational —
Cody reads her own SKILL.md and these helpers do the boring parts that
need to be deterministic so Simone (Phase 4) can verify reproducibility.

Cody invokes `claude` from inside the workspace dir so project-local
`.claude/settings.json` is in scope. As of 2026-06-07 demos route through
the ZAI proxy like the rest of the daemon: `run_in_workspace` no longer
scrubs ANTHROPIC_* by default, so the subprocess inherits the daemon's
ZAI routing env (Anthropic-now-API-bills the Max SDK path). Pass
`scrub_env=True` explicitly only for a demo that must hit real Anthropic.

See docs/proactive_signals/claudedevs_intel_v2_design.md §8.
See docs/06_Deployment_And_Environments/09_Demo_Execution_Environments.md
for the dual-environment context.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess as sp
from typing import Any, Optional

logger = logging.getLogger(__name__)


# Prefix of every ANTHROPIC_* env var. `_scrubbed_env()` strips this whole
# namespace when `run_in_workspace(scrub_env=True)` is requested.
#
# History: scrubbing used to be the DEFAULT, to keep a demo's `claude` off
# the daemon's env and on the Max-plan OAuth session (real Anthropic). It
# also dodged a failure mode where a stale ANTHROPIC_API_KEY from a
# different Max account reached the subprocess and yielded
# `Invalid API key · Fix external API key`. As of 2026-06-07 the default is
# OFF: demos route through ZAI (the daemon's ANTHROPIC_BASE_URL points at the
# ZAI proxy and ANTHROPIC_AUTH_TOKEN holds the ZAI key — coherent, no
# external-key error). Scrubbing remains available for the rare demo that
# must hit real Anthropic. Canonical reference:
# docs/06_Deployment_And_Environments/10_Interactive_Coding_Environment.md.
LEAKY_ANTHROPIC_ENV_PREFIX = "ANTHROPIC_"

# Backwards-compat alias kept for any external caller that imported the old
# constant. New code should reference LEAKY_ANTHROPIC_ENV_PREFIX. The tuple
# is now derived dynamically from the prefix at module init so it correctly
# describes the *current* env's leaky vars rather than a stale 5-key list.
LEAKY_ANTHROPIC_ENV_VARS: tuple[str, ...] = tuple(
    sorted(k for k in os.environ if k.startswith(LEAKY_ANTHROPIC_ENV_PREFIX))
)


# ── Workspace shape ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class WorkspaceArtifacts:
    """Canonical paths inside a demo workspace."""

    workspace_dir: Path

    @property
    def brief_path(self) -> Path:
        """Return path to BRIEF.md."""
        return self.workspace_dir / "BRIEF.md"

    @property
    def acceptance_path(self) -> Path:
        """Return path to ACCEPTANCE.md."""
        return self.workspace_dir / "ACCEPTANCE.md"

    @property
    def business_relevance_path(self) -> Path:
        """Return path to business_relevance.md."""
        return self.workspace_dir / "business_relevance.md"

    @property
    def sources_dir(self) -> Path:
        """Return path to SOURCES/ directory."""
        return self.workspace_dir / "SOURCES"

    @property
    def src_dir(self) -> Path:
        """Return path to src/ directory."""
        return self.workspace_dir / "src"

    @property
    def build_notes_path(self) -> Path:
        """Return path to BUILD_NOTES.md."""
        return self.workspace_dir / "BUILD_NOTES.md"

    @property
    def run_output_path(self) -> Path:
        """Return path to run_output.txt."""
        return self.workspace_dir / "run_output.txt"

    @property
    def manifest_path(self) -> Path:
        """Return path to manifest.json."""
        return self.workspace_dir / "manifest.json"

    @property
    def feedback_path(self) -> Path:
        """Return path to FEEDBACK.md."""
        return self.workspace_dir / "FEEDBACK.md"

    @property
    def settings_path(self) -> Path:
        """Return path to .claude/settings.json."""
        return self.workspace_dir / ".claude" / "settings.json"

    def to_dict(self) -> dict[str, str]:
        """Serialize all canonical paths to a string dict."""
        return {
            "workspace_dir": str(self.workspace_dir),
            "brief_path": str(self.brief_path),
            "acceptance_path": str(self.acceptance_path),
            "business_relevance_path": str(self.business_relevance_path),
            "sources_dir": str(self.sources_dir),
            "src_dir": str(self.src_dir),
            "build_notes_path": str(self.build_notes_path),
            "run_output_path": str(self.run_output_path),
            "manifest_path": str(self.manifest_path),
            "feedback_path": str(self.feedback_path),
            "settings_path": str(self.settings_path),
        }


def workspace_for(workspace_dir: Path) -> WorkspaceArtifacts:
    """Resolve workspace_dir and return a WorkspaceArtifacts for it."""
    return WorkspaceArtifacts(workspace_dir=workspace_dir.resolve())


def resolve_demo_artifacts_dir(workspace_dir: Path) -> Path:
    """Return the directory that actually holds a demo's build artifacts.

    Direct demo builds write `manifest.json` / `run_output.txt` / `BUILD_NOTES.md`
    at the workspace root. Curated demos built via a VP mission (the VP worker
    terminal handler that routes them to `pending_review`) write those build
    artifacts into a per-mission subdir `<workspace_dir>/vp-mission-<id>/`, while
    the Phase-2 scaffold files (BRIEF/ACCEPTANCE/business_relevance) stay at root.

    This resolver prefers the workspace root when it carries a manifest, and
    otherwise falls back to the newest `vp-mission-*/` subdir that contains one.
    The fallback only fires when the root manifest is absent, so existing
    root-layout behavior is never altered. Returns the workspace root when no
    manifest exists anywhere (caller treats that as "no manifest").
    """
    root = workspace_dir.resolve()
    if (root / "manifest.json").exists():
        return root
    try:
        mission_manifests = sorted(
            root.glob("vp-mission-*/manifest.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        mission_manifests = []
    if mission_manifests:
        return mission_manifests[0].parent
    return root


# ── Pre-build verification ──────────────────────────────────────────────────


@dataclass(frozen=True)
class WorkspaceReadiness:
    """Result of verify_workspace_ready."""

    ok: bool
    reasons: tuple[str, ...] = field(default=tuple())
    iteration: int = 1

    def to_dict(self) -> dict[str, Any]:
        """Serialize readiness result to a plain dict."""
        return {"ok": self.ok, "reasons": list(self.reasons), "iteration": self.iteration}


_PLACEHOLDER_PATTERN = re.compile(r"_\(Simone:[^)]*\)_", re.DOTALL)


def verify_workspace_ready(workspace_dir: Path) -> WorkspaceReadiness:
    """Confirm the workspace is fully populated by Simone before Cody touches code.

    Checks:
      - All three Simone-authored files exist (BRIEF, ACCEPTANCE, business_relevance).
      - None of them still carry `_(Simone: ...)_` placeholders (Simone forgot to refine).
      - `.claude/settings.json` exists and is vanilla (no env/hooks/plugins pollution).
      - SOURCES/ exists (may be empty; Cody can still build without sources but it's a smell).
      - If FEEDBACK.md exists, this is iteration > 1 — flagged in the result.
    """
    artifacts = workspace_for(workspace_dir)
    reasons: list[str] = []
    iteration = 1

    if not artifacts.workspace_dir.exists():
        return WorkspaceReadiness(ok=False, reasons=(f"workspace_dir does not exist: {artifacts.workspace_dir}",))

    for label, path in (
        ("BRIEF.md", artifacts.brief_path),
        ("ACCEPTANCE.md", artifacts.acceptance_path),
        ("business_relevance.md", artifacts.business_relevance_path),
    ):
        if not path.exists():
            reasons.append(f"{label} missing — Simone hasn't scaffolded yet")
            continue
        text = path.read_text(encoding="utf-8")
        if _PLACEHOLDER_PATTERN.search(text):
            reasons.append(f"{label} still has '_(Simone: ...)_' placeholders — refinement incomplete")

    if not artifacts.sources_dir.exists():
        reasons.append("SOURCES/ dir missing — workspace incomplete")

    if not artifacts.settings_path.exists():
        reasons.append(".claude/settings.json missing — workspace not provisioned via demo_workspace")
    else:
        # Reuse PR 7's pollution-marker safety net.
        try:
            from universal_agent.services.demo_workspace import verify_vanilla_settings

            verify_vanilla_settings(artifacts.settings_path)
        except Exception as exc:
            reasons.append(f"settings.json carries pollution markers: {exc}")

    if artifacts.feedback_path.exists():
        # Iteration > 1 — Cody is on a re-attempt after Simone's feedback.
        # Look for explicit iteration count in manifest if it exists.
        if artifacts.manifest_path.exists():
            try:
                manifest = json.loads(artifacts.manifest_path.read_text(encoding="utf-8"))
                iteration = max(int(manifest.get("iteration") or 1), 1) + 1
            except Exception:
                iteration = 2
        else:
            iteration = 2

    return WorkspaceReadiness(
        ok=not reasons,
        reasons=tuple(reasons),
        iteration=iteration,
    )


# ── Briefing artifact loading ───────────────────────────────────────────────


@dataclass(frozen=True)
class BriefingBundle:
    """The three Simone-authored files loaded as text."""

    brief: str
    acceptance: str
    business_relevance: str
    feedback: str = ""

    def to_dict(self) -> dict[str, str]:
        """Serialize briefing text fields to a plain dict."""
        return {
            "brief": self.brief,
            "acceptance": self.acceptance,
            "business_relevance": self.business_relevance,
            "feedback": self.feedback,
        }


def load_briefing(workspace_dir: Path) -> BriefingBundle:
    """Load BRIEF.md / ACCEPTANCE.md / business_relevance.md (+ FEEDBACK.md if present)."""
    artifacts = workspace_for(workspace_dir)
    brief = artifacts.brief_path.read_text(encoding="utf-8") if artifacts.brief_path.exists() else ""
    acceptance = artifacts.acceptance_path.read_text(encoding="utf-8") if artifacts.acceptance_path.exists() else ""
    business_relevance = (
        artifacts.business_relevance_path.read_text(encoding="utf-8")
        if artifacts.business_relevance_path.exists()
        else ""
    )
    feedback = artifacts.feedback_path.read_text(encoding="utf-8") if artifacts.feedback_path.exists() else ""
    return BriefingBundle(
        brief=brief,
        acceptance=acceptance,
        business_relevance=business_relevance,
        feedback=feedback,
    )


def list_sources(workspace_dir: Path) -> list[Path]:
    """Return all files under SOURCES/ (Cody must read at least the primary doc)."""
    artifacts = workspace_for(workspace_dir)
    if not artifacts.sources_dir.exists():
        return []
    return sorted(p for p in artifacts.sources_dir.iterdir() if p.is_file())


# ── Workspace-scoped command runner ─────────────────────────────────────────


def _scrubbed_env() -> dict[str, str]:
    """Return os.environ minus every ANTHROPIC_* var.

    Removes the entire ANTHROPIC_* namespace, not a fixed list, so any new
    Anthropic env var added to Infisical (e.g. ANTHROPIC_VERTEX_PROJECT_ID
    in the future) is automatically scrubbed without needing a code change
    here. Same pattern as `scripts/_claude_launcher.py`'s strip helper.
    """
    return {
        k: v for k, v in os.environ.items()
        if not k.startswith(LEAKY_ANTHROPIC_ENV_PREFIX)
    }


@dataclass(frozen=True)
class RunResult:
    """Result of running a command inside a demo workspace.

    Hermes Phase F.1 site-wiring — ``exit_classification`` carries the
    :class:`WorkerExit` classification from
    :func:`universal_agent.services.worker_exit_classifier.classify_worker_exit`.
    The caller (Cody\u2019s demo task dispatcher) inspects it to decide
    whether the run succeeded, timed out, signaled, or was a protocol
    violation.  F.3 (parking into needs_review) is the caller\u2019s
    responsibility for the demo path because the demo workspace does
    NOT have a direct ``task_hub_assignments`` linkage at the
    ``subprocess.run`` level.

    Hermes Phase F.1 follow-up (Popen migration) — ``worker_pid`` carries
    the spawned subprocess PID when the call provided an
    ``assignment_id`` AND PID recording succeeded.  ``None`` otherwise
    (no linkage requested, recording skipped, or in-process tests where
    Popen was mocked).  PID is captured at spawn time via Popen.pid,
    matching the observability shape of the cron and VP CLI sites that
    already wire Phase F.1.
    """

    return_code: int
    stdout: str
    stderr: str
    wall_time_seconds: float
    command: tuple[str, ...]
    cwd: str
    env_scrubbed: bool
    exit_classification: Any = None  # WorkerExit | None — quoted to keep import optional
    worker_pid: Optional[int] = None  # F.1 follow-up: captured from Popen.pid

    @property
    def ok(self) -> bool:
        """Return True when the subprocess exited with code 0."""
        return self.return_code == 0

    def to_dict(self) -> dict[str, Any]:
        """Serialize run result to a plain dict, truncating stdout/stderr to 2000 chars."""
        payload: dict[str, Any] = {
            "return_code": self.return_code,
            "stdout_excerpt": self.stdout[:2000],
            "stderr_excerpt": self.stderr[:2000],
            "wall_time_seconds": self.wall_time_seconds,
            "command": list(self.command),
            "cwd": self.cwd,
            "env_scrubbed": self.env_scrubbed,
            "ok": self.ok,
        }
        if self.exit_classification is not None:
            try:
                payload["exit_classification"] = self.exit_classification.to_dict()
            except AttributeError:
                # Best-effort: if a plain dict was stashed instead of a
                # WorkerExit, propagate as-is.
                payload["exit_classification"] = self.exit_classification
        if self.worker_pid is not None:
            payload["worker_pid"] = self.worker_pid
        return payload


def _record_demo_worker_pid(assignment_id: str, worker_pid: int) -> bool:
    """Best-effort F.1 demo-site PID record.

    Opens its own runtime DB connection (mirrors the cron/VP CLI site
    pattern), writes the PID against ``assignment_id`` via
    :func:`task_hub.record_worker_pid`, commits, and closes.  Never
    raises — failures are logged at WARNING but do not block the spawn
    happy path.

    Returns ``True`` on a successful write, ``False`` on any error (or
    no-op skip for empty assignment_id / non-positive PID).
    """
    aid = str(assignment_id or "").strip()
    if not aid or worker_pid <= 0:
        return False
    try:
        from universal_agent import task_hub
        from universal_agent.durable.db import connect_runtime_db, get_activity_db_path

        conn = connect_runtime_db(get_activity_db_path())
        try:
            task_hub.record_worker_pid(
                conn,
                assignment_id=aid,
                worker_pid=int(worker_pid),
            )
            try:
                conn.commit()
            except Exception:
                pass
        finally:
            conn.close()
        return True
    except Exception as exc:
        logger.warning(
            "Phase F.1 demo workspace PID record failed (assignment_id=%s): %s",
            aid, exc,
        )
        return False


def run_in_workspace(
    workspace_dir: Path,
    command: list[str] | tuple[str, ...],
    *,
    timeout: int = 300,
    scrub_env: bool = False,
    assignment_id: Optional[str] = None,
    task_id: Optional[str] = None,
) -> RunResult:
    """Run a command from inside the workspace dir with optional env scrubbing.

    Cody invokes this whenever she needs to run `claude` OR her demo Python
    script (so `uv run` finds the workspace's `pyproject.toml`).

    `scrub_env=True` removes any ANTHROPIC_* env var from the subprocess.

    Default is now OFF (2026-06-07). Historically it was ON so that a demo's
    `claude` fell through to the Max-plan OAuth session (real Anthropic).
    Anthropic now API-bills that SDK path, so demos route through ZAI/GLM
    like the rest of the daemon: NOT scrubbing lets the subprocess inherit
    the daemon's Infisical-injected `ANTHROPIC_BASE_URL` (ZAI proxy) +
    `ANTHROPIC_AUTH_TOKEN` (ZAI key) + model-map vars, which take precedence
    over any settings file and route `claude` to ZAI. Pass `scrub_env=True`
    explicitly only for a demo that genuinely must hit real Anthropic.

    Hermes Phase F.1 site-wiring — the returned ``RunResult`` carries an
    ``exit_classification`` (a :class:`WorkerExit`) so the caller can
    distinguish clean exit / timeout / signaled / nonzero / protocol
    violation.

    Hermes Phase F.1 follow-up (Popen migration) — the spawn now uses
    :class:`subprocess.Popen` so the PID is observable immediately after
    spawn rather than only after completion.  When the caller passes
    ``assignment_id`` (the Task Hub assignment that owns this demo run),
    the spawned subprocess PID is stamped onto
    ``task_hub_assignments.worker_pid`` via
    :func:`task_hub.record_worker_pid`.  Backward-compatible: existing
    callers that don't pass ``assignment_id`` see identical behavior to
    the pre-Popen path.

    The ``task_id`` kwarg is accepted (and surfaced on the
    ``RunResult.command`` audit trail via logger context only) for
    future F.3 protocol-violation routing from this site.  Today the
    demo site's caller (cody-task-dispatcher / cody-implements-from-brief)
    owns disposition; this signature keeps the door open for moving the
    F.3 park decision into ``run_in_workspace`` itself once a real
    Task Hub-linked demo path lands.
    """
    cwd = workspace_dir.resolve()
    if not cwd.exists():
        raise FileNotFoundError(f"workspace does not exist: {cwd}")
    env = _scrubbed_env() if scrub_env else dict(os.environ)
    cwd_str = str(cwd)
    command_list = list(command)

    start = datetime.now(timezone.utc)
    was_timeout_killed = False
    worker_pid: Optional[int] = None
    rc: int
    stdout: str
    stderr: str

    try:
        proc = sp.Popen(
            command_list,
            cwd=cwd_str,
            stdout=sp.PIPE,
            stderr=sp.PIPE,
            text=True,
            env=env,
        )
    except FileNotFoundError as exc:
        rc, stdout, stderr = 127, "", f"binary_not_found: {exc}"
    except Exception as exc:
        rc, stdout, stderr = 1, "", f"unexpected_error: {exc}"
    else:
        # Capture PID immediately — that's the whole point of switching
        # to Popen.  Record onto the assignment row before we block on
        # communicate() so the observability metadata lands even if the
        # subprocess is long-running.
        try:
            worker_pid = int(proc.pid) if proc.pid else None
        except (TypeError, ValueError):
            worker_pid = None
        if assignment_id and worker_pid:
            _record_demo_worker_pid(assignment_id, worker_pid)

        try:
            stdout_raw, stderr_raw = proc.communicate(timeout=timeout)
            rc = proc.returncode if proc.returncode is not None else 1
            stdout = stdout_raw or ""
            stderr = stderr_raw or ""
        except sp.TimeoutExpired:
            # Preserve existing semantics: kill the process, drain its
            # pipes, set rc=124, and flag the timeout-killed signal for
            # the classifier.  communicate() after kill() drains the
            # remaining buffered output.
            was_timeout_killed = True
            try:
                proc.kill()
            except Exception:
                pass
            try:
                drain_stdout, drain_stderr = proc.communicate()
            except Exception:
                drain_stdout, drain_stderr = "", ""
            rc = 124
            stdout = drain_stdout or ""
            stderr = (drain_stderr or "") + f"\ntimeout after {timeout}s"
        except Exception as exc:
            # Best-effort cleanup of any straggling process.
            try:
                proc.kill()
            except Exception:
                pass
            try:
                proc.communicate()
            except Exception:
                pass
            rc, stdout, stderr = 1, "", f"unexpected_error: {exc}"
    end = datetime.now(timezone.utc)

    # Phase F.1 — classify the exit so the caller can route protocol
    # violations / failures appropriately.  Best-effort; classifier is
    # pure so failures should never happen, but defensive try just in
    # case.
    classification = None
    try:
        from universal_agent.services.worker_exit_classifier import (
            classify_worker_exit as _f_classify,
        )
        # Negative rc on POSIX = signaled. Skip when we already classified
        # as timeout-killed (timeout takes priority).
        was_signaled = bool(
            isinstance(rc, int) and rc < 0 and not was_timeout_killed
        )
        classification = _f_classify(
            return_code=rc,
            was_signaled=was_signaled,
            was_timeout_killed=was_timeout_killed,
            # No linked Task Hub assignment at this level — caller owns
            # disposition; treat rc=0 as the success signal.
            task_closed_normally=(rc == 0 and not was_timeout_killed),
        )
    except Exception as exc:
        logger.debug("Phase F.1 demo classification skipped: %s", exc)

    if task_id:
        # Quiet breadcrumb so a future F.3 routing decision can be
        # back-correlated through logs without changing this function's
        # return shape.
        logger.debug(
            "run_in_workspace task_id=%s assignment_id=%s rc=%s pid=%s",
            task_id, assignment_id, rc, worker_pid,
        )

    return RunResult(
        return_code=rc,
        stdout=stdout,
        stderr=stderr,
        wall_time_seconds=(end - start).total_seconds(),
        command=tuple(command),
        cwd=cwd_str,
        env_scrubbed=scrub_env,
        exit_classification=classification,
        worker_pid=worker_pid,
    )


# ── Endpoint detection ──────────────────────────────────────────────────────
#
# After running `claude` inside the workspace, Cody captures stdout. This
# helper looks for telltale strings in the response that indicate which
# endpoint actually served the request. Best-effort — Simone's evaluator
# (PR 10) will run a more rigorous check using `ss -t` snapshot during
# the run.

_ANTHROPIC_NATIVE_HINTS = (
    "api.anthropic.com",
    "anthropic-version",
    "claude-opus-",
    "claude-sonnet-",
    "claude-haiku-",
    "Claude Max",
)
_ZAI_HINTS = (
    "api.z.ai",
    "glm-",
    "z.ai/api/anthropic",
)


def detect_endpoint_from_text(text: str) -> str:
    """Return 'anthropic_native' | 'zai' | 'unknown' from response text.

    Heuristic. Definitive verification requires network observation
    during the run (Simone's evaluator does this).
    """
    haystack = (text or "").lower()
    if any(hint.lower() in haystack for hint in _ZAI_HINTS):
        return "zai"
    if any(hint.lower() in haystack for hint in _ANTHROPIC_NATIVE_HINTS):
        return "anthropic_native"
    return "unknown"


def canonicalize_endpoint(value: str) -> str:
    """Fold an endpoint label OR an observed host/marker into the canonical enum.

    The single source of truth that bridges what Cody free-hands into
    manifest.json (frequently the raw host ``api.anthropic.com``) and the
    canonical token the evaluator compares against (``anthropic_native``).
    Without this, ``evaluate_demo`` did a raw ``==`` and FALSE-REJECTED demos
    that genuinely ran on Anthropic — ``"api.anthropic.com" != "anthropic_native"``.

    Returns one of ``anthropic_native`` | ``zai`` | ``unknown``, or passes
    through the no-constraint sentinels ``""`` / ``any`` unchanged so the
    "no constraint" branch in ``evaluate_demo`` keeps working. ZAI hints win
    over Anthropic hints (matches ``detect_endpoint_from_text``) so an env-leak
    that name-drops a Claude model is still flagged ``zai``.
    """
    raw = (value or "").strip()
    if not raw:
        return ""
    low = raw.lower()
    if low in ("any", "anthropic_native", "zai", "unknown"):
        return low
    if any(hint.lower() in low for hint in _ZAI_HINTS):
        return "zai"
    if any(hint.lower() in low for hint in _ANTHROPIC_NATIVE_HINTS):
        return "anthropic_native"
    return low


# ── Manifest ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DemoManifest:
    """Schema for `<workspace>/manifest.json`."""

    demo_id: str
    feature: str
    endpoint_required: str
    endpoint_hit: str
    model_used: str = ""
    claude_code_version: str = ""
    claude_agent_sdk_version: str = ""
    wall_time_seconds: float = 0.0
    acceptance_passed: bool = False
    iteration: int = 1
    started_at: str = ""
    finished_at: str = ""
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize manifest fields to a plain dict."""
        return {
            "demo_id": self.demo_id,
            "feature": self.feature,
            "endpoint_required": self.endpoint_required,
            "endpoint_hit": self.endpoint_hit,
            "model_used": self.model_used,
            "claude_code_version": self.claude_code_version,
            "claude_agent_sdk_version": self.claude_agent_sdk_version,
            "wall_time_seconds": self.wall_time_seconds,
            "acceptance_passed": self.acceptance_passed,
            "iteration": self.iteration,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "notes": self.notes,
        }

    @property
    def endpoint_matches_required(self) -> bool:
        """Return True when endpoint_hit satisfies endpoint_required (or required is 'any')."""
        if not self.endpoint_required or self.endpoint_required == "any":
            return True
        return canonicalize_endpoint(self.endpoint_required) == canonicalize_endpoint(self.endpoint_hit)


def write_manifest(workspace_dir: Path, manifest: DemoManifest) -> Path:
    """Write manifest.json to the workspace and return its path."""
    target = workspace_for(workspace_dir).manifest_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(manifest.to_dict(), indent=2, ensure_ascii=True, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return target


def read_manifest(workspace_dir: Path) -> DemoManifest | None:
    """Read and parse manifest.json from the workspace; return None if absent or malformed.

    Resolves the effective artifacts dir first so curated demos whose build
    manifest landed in a `vp-mission-<id>/` subdir are read correctly, not just
    direct demos that write the manifest at the workspace root.
    """
    target = resolve_demo_artifacts_dir(workspace_dir) / "manifest.json"
    if not target.exists():
        return None
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    return DemoManifest(
        demo_id=str(payload.get("demo_id") or ""),
        feature=str(payload.get("feature") or ""),
        # Legacy manifests that omit endpoint_required default to "any" (no
        # constraint) rather than forcing Anthropic — demos run on ZAI as of
        # 2026-06-07, so an old/missing value must not false-reject a ZAI run.
        endpoint_required=str(payload.get("endpoint_required") or "any"),
        endpoint_hit=str(payload.get("endpoint_hit") or ""),
        model_used=str(payload.get("model_used") or ""),
        claude_code_version=str(payload.get("claude_code_version") or ""),
        claude_agent_sdk_version=str(payload.get("claude_agent_sdk_version") or ""),
        wall_time_seconds=float(payload.get("wall_time_seconds") or 0.0),
        acceptance_passed=bool(payload.get("acceptance_passed")),
        iteration=int(payload.get("iteration") or 1),
        started_at=str(payload.get("started_at") or ""),
        finished_at=str(payload.get("finished_at") or ""),
        notes=str(payload.get("notes") or ""),
    )


# ── BUILD_NOTES.md & run_output.txt ─────────────────────────────────────────


def append_build_note(workspace_dir: Path, note: str, *, kind: str = "gap") -> Path:
    """Append a structured entry to BUILD_NOTES.md.

    `kind` distinguishes:
      - "gap" — docs didn't show how to do something (Cody MUST document, not invent)
      - "decision" — Cody made a non-obvious implementation choice
      - "blocker" — something Simone needs to resolve before Cody can continue
    """
    target = workspace_for(workspace_dir).build_notes_path
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_text("# Build Notes\n\nDocumented gaps, decisions, and blockers from Cody.\n", encoding="utf-8")
    iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    entry = f"\n## [{iso}] {kind.upper()}\n\n{note.strip()}\n"
    with target.open("a", encoding="utf-8") as handle:
        handle.write(entry)
    return target


def write_run_output(workspace_dir: Path, output: str) -> Path:
    """Write run_output.txt to the workspace and return its path."""
    target = workspace_for(workspace_dir).run_output_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(output, encoding="utf-8")
    return target


# ── Version probing ─────────────────────────────────────────────────────────


def probe_versions() -> dict[str, str]:
    """Best-effort capture of installed tool versions for the manifest."""
    versions: dict[str, str] = {}

    if shutil.which("claude"):
        try:
            completed = sp.run(["claude", "--version"], capture_output=True, text=True, timeout=10, check=False)
            versions["claude_code"] = (completed.stdout or "").strip().split("\n", 1)[0]
        except Exception:
            pass

    try:
        from importlib.metadata import version as _pkg_version

        for pkg in ("claude-agent-sdk", "anthropic"):
            try:
                versions[pkg.replace("-", "_")] = _pkg_version(pkg)
            except Exception:
                continue
    except Exception:
        pass

    return versions

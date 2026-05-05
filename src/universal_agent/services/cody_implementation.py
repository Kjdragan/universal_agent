"""Cody Phase 3 implementation helpers.

The mechanical helpers Cody invokes when she picks up a `cody_demo_task`
from Task Hub (queued by PR 8's `cody-task-dispatcher`). These do the
deterministic parts:

  - Verify the workspace is ready (BRIEF/ACCEPTANCE/business_relevance present,
    .claude/settings.json vanilla)
  - Load the briefing artifacts as structured data
  - Run a command from inside the workspace with proper env scrubbing
    (no ANTHROPIC_AUTH_TOKEN leak from parent shell)
  - Capture stdout/stderr/timing
  - Detect which Anthropic endpoint actually served the request
  - Write the result manifest, BUILD_NOTES, run_output

The actual "build a Claude Code demo" creative work is conversational —
Cody reads her own SKILL.md and these helpers do the boring parts that
need to be deterministic so Simone (Phase 4) can verify reproducibility.

Per the v2 design (§8.4), Cody MUST invoke `claude` from inside the
workspace dir so project-local `.claude/settings.json` (vanilla) takes
precedence over the polluted user-global one. The `run_in_workspace`
helper enforces this by `cd`'ing before subprocess and scrubbing the
problematic env vars.

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
from typing import Any

logger = logging.getLogger(__name__)


# Env vars that MUST be unset before invoking `claude` from a demo workspace.
# Their presence in the parent shell would override the project-local
# settings.json and silently route the request to the ZAI proxy.
LEAKY_ANTHROPIC_ENV_VARS = (
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
)


# ── Workspace shape ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class WorkspaceArtifacts:
    """Canonical paths inside a demo workspace."""

    workspace_dir: Path

    @property
    def brief_path(self) -> Path:
        return self.workspace_dir / "BRIEF.md"

    @property
    def acceptance_path(self) -> Path:
        return self.workspace_dir / "ACCEPTANCE.md"

    @property
    def business_relevance_path(self) -> Path:
        return self.workspace_dir / "business_relevance.md"

    @property
    def sources_dir(self) -> Path:
        return self.workspace_dir / "SOURCES"

    @property
    def src_dir(self) -> Path:
        return self.workspace_dir / "src"

    @property
    def build_notes_path(self) -> Path:
        return self.workspace_dir / "BUILD_NOTES.md"

    @property
    def run_output_path(self) -> Path:
        return self.workspace_dir / "run_output.txt"

    @property
    def manifest_path(self) -> Path:
        return self.workspace_dir / "manifest.json"

    @property
    def feedback_path(self) -> Path:
        return self.workspace_dir / "FEEDBACK.md"

    @property
    def settings_path(self) -> Path:
        return self.workspace_dir / ".claude" / "settings.json"

    def to_dict(self) -> dict[str, str]:
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
    return WorkspaceArtifacts(workspace_dir=workspace_dir.resolve())


# ── Pre-build verification ──────────────────────────────────────────────────


@dataclass(frozen=True)
class WorkspaceReadiness:
    """Result of verify_workspace_ready."""

    ok: bool
    reasons: tuple[str, ...] = field(default=tuple())
    iteration: int = 1

    def to_dict(self) -> dict[str, Any]:
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
    """Return os.environ minus the leaky Anthropic vars."""
    env = dict(os.environ)
    for var in LEAKY_ANTHROPIC_ENV_VARS:
        env.pop(var, None)
    return env


@dataclass(frozen=True)
class RunResult:
    """Result of running a command inside a demo workspace."""

    return_code: int
    stdout: str
    stderr: str
    wall_time_seconds: float
    command: tuple[str, ...]
    cwd: str
    env_scrubbed: bool

    @property
    def ok(self) -> bool:
        return self.return_code == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "return_code": self.return_code,
            "stdout_excerpt": self.stdout[:2000],
            "stderr_excerpt": self.stderr[:2000],
            "wall_time_seconds": self.wall_time_seconds,
            "command": list(self.command),
            "cwd": self.cwd,
            "env_scrubbed": self.env_scrubbed,
            "ok": self.ok,
        }


def run_in_workspace(
    workspace_dir: Path,
    command: list[str] | tuple[str, ...],
    *,
    timeout: int = 300,
    scrub_env: bool = True,
) -> RunResult:
    """Run a command from inside the workspace dir with optional env scrubbing.

    Cody invokes this whenever she needs to run `claude` (so the
    project-local vanilla `.claude/settings.json` overrides the polluted
    user-global one) OR when she needs to run her demo Python script (so
    `uv run` finds the workspace's `pyproject.toml`).

    `scrub_env=True` removes any ANTHROPIC_* env var that would override
    the project-local settings. Default ON because the production VPS
    shell typically has them set.
    """
    cwd = workspace_dir.resolve()
    if not cwd.exists():
        raise FileNotFoundError(f"workspace does not exist: {cwd}")
    env = _scrubbed_env() if scrub_env else dict(os.environ)

    start = datetime.now(timezone.utc)
    try:
        completed = sp.run(
            list(command),
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=env,
        )
        rc = completed.returncode
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
    except FileNotFoundError as exc:
        rc, stdout, stderr = 127, "", f"binary_not_found: {exc}"
    except sp.TimeoutExpired:
        rc, stdout, stderr = 124, "", f"timeout after {timeout}s"
    except Exception as exc:
        rc, stdout, stderr = 1, "", f"unexpected_error: {exc}"
    end = datetime.now(timezone.utc)

    return RunResult(
        return_code=rc,
        stdout=stdout,
        stderr=stderr,
        wall_time_seconds=(end - start).total_seconds(),
        command=tuple(command),
        cwd=str(cwd),
        env_scrubbed=scrub_env,
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
        if not self.endpoint_required or self.endpoint_required == "any":
            return True
        return self.endpoint_required == self.endpoint_hit


def write_manifest(workspace_dir: Path, manifest: DemoManifest) -> Path:
    target = workspace_for(workspace_dir).manifest_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(manifest.to_dict(), indent=2, ensure_ascii=True, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return target


def read_manifest(workspace_dir: Path) -> DemoManifest | None:
    target = workspace_for(workspace_dir).manifest_path
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
        endpoint_required=str(payload.get("endpoint_required") or "anthropic_native"),
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

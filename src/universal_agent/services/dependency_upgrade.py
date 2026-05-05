"""Dependency upgrade actuator (Phase 0 / PR 6b).

Companion to `services/dependency_currency.py` (observation-only). Where
the sweep produces a drift report, this module actually applies an
upgrade to a single Anthropic-adjacent dep:

    1. Read pyproject.toml; back it up; edit the package's version pin.
    2. Run `uv sync` so the new version is installed.
    3. Run BOTH smoke tests:
         - ZAI smoke — verifies UA's normal ZAI-mapped operation still works.
         - Anthropic-native smoke — verifies the Max plan demo path still works.
    4. On either smoke fail: roll back pyproject.toml, restore venv,
       record an upgrade_failures entry, return UpgradeFailed.
    5. On both pass: leave the bumped pyproject.toml + .venv in place,
       return UpgradeApplied.

This module deliberately performs **no git operations**. The change is
left in the working tree for the operator to review, commit, and ship
through the normal `/ship` workflow. The actuator's job is to surface
"this bump is safe to ship" or "this bump broke X, here's the rollback,"
not to push to feature/latest2 itself.

The orchestration script `scripts/dependency_upgrade.py` wires this to
the email path so Kevin gets notified either way.

Per the dual-environment design (see
docs/06_Deployment_And_Environments/09_Demo_Execution_Environments.md),
both smokes must pass — an upgrade that breaks ZAI breaks UA, and an
upgrade that breaks Anthropic-native breaks demos.

See docs/proactive_signals/claudedevs_intel_v2_design.md §5.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import difflib
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess as sp
from typing import Iterable

logger = logging.getLogger(__name__)


# Path defaults — overridable via env so tests can isolate.
def _ua_repo_root() -> Path:
    """Repo root the actuator operates against."""
    raw = str(os.getenv("UA_DEPENDENCY_UPGRADE_REPO_ROOT") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    # Module lives at src/universal_agent/services/dependency_upgrade.py.
    return Path(__file__).resolve().parents[3]


def _smoke_workspace_path() -> Path:
    """Where the Anthropic-native smoke lives. Overridable for tests."""
    raw = str(os.getenv("UA_DEMOS_ROOT") or "").strip()
    base = Path(raw).expanduser().resolve() if raw else Path("/opt/ua_demos")
    return base / "_smoke"


# Wall-time guards. Smokes are bounded so a hanging subprocess can't pin
# the actuator forever.
SYNC_TIMEOUT_SECONDS = 10 * 60   # `uv sync` can take a while on a cold cache
SMOKE_TIMEOUT_SECONDS = 5 * 60


# ── Result types ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SmokeResult:
    """Outcome of one smoke test (ZAI or Anthropic-native)."""

    name: str
    ok: bool
    return_code: int = 0
    stdout_excerpt: str = ""
    stderr_excerpt: str = ""
    skipped_reason: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "ok": self.ok,
            "return_code": self.return_code,
            "stdout_excerpt": self.stdout_excerpt[:600],
            "stderr_excerpt": self.stderr_excerpt[:600],
            "skipped_reason": self.skipped_reason,
        }


@dataclass(frozen=True)
class UpgradeOutcome:
    """End-to-end result of one upgrade attempt."""

    package: str
    from_version: str
    to_version: str
    diff: str
    sync_ok: bool
    sync_stderr_excerpt: str
    zai_smoke: SmokeResult
    anthropic_smoke: SmokeResult
    rolled_back: bool
    rollback_reason: str = ""
    started_at: str = ""
    finished_at: str = ""

    @property
    def overall_ok(self) -> bool:
        return self.sync_ok and self.zai_smoke.ok and self.anthropic_smoke.ok and not self.rolled_back

    def to_dict(self) -> dict[str, object]:
        return {
            "package": self.package,
            "from_version": self.from_version,
            "to_version": self.to_version,
            "diff": self.diff,
            "sync_ok": self.sync_ok,
            "sync_stderr_excerpt": self.sync_stderr_excerpt[:600],
            "zai_smoke": self.zai_smoke.to_dict(),
            "anthropic_smoke": self.anthropic_smoke.to_dict(),
            "rolled_back": self.rolled_back,
            "rollback_reason": self.rollback_reason,
            "overall_ok": self.overall_ok,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


# ── pyproject.toml version surgery ──────────────────────────────────────────


# Match a dependencies line like:
#     "claude-agent-sdk>=0.1.51",
#     "anthropic>=0.75.0",
#     "langsmith[claude-agent-sdk,otel]>=0.7.22",
# Captures: leading whitespace+quote, package name (incl. extras), version spec.
# Comma is allowed inside the name group because PEP 508 extras like
# `[a,b,c]` use it. The spec group starts at the first comparator after the
# package name+extras.
_PYPI_DEP_RE = re.compile(
    r'^(?P<prefix>\s*"\s*)(?P<name>[A-Za-z0-9_.\-\[\],]+)(?P<spec>[<>=!~][^"]*?)(?P<suffix>"\s*,?\s*)$',
    re.MULTILINE,
)


def _normalize_package_name(name: str) -> str:
    """Treat 'claude_agent_sdk' and 'claude-agent-sdk' as the same package per PEP 503."""
    return re.sub(r"[-_.]+", "-", str(name or "").strip().lower())


def find_pyproject_dep(pyproject_text: str, package: str) -> tuple[str, str] | None:
    """Locate a dep line. Returns (current_spec, current_version) or None."""
    target_norm = _normalize_package_name(package)
    for match in _PYPI_DEP_RE.finditer(pyproject_text):
        # Strip extras like 'langsmith[claude-agent-sdk,otel]' for the name comparison
        # but only on the bare package side; matching against extras is a separate concern.
        bare_name = re.sub(r"\[.*?\]", "", match.group("name"))
        if _normalize_package_name(bare_name) != target_norm:
            continue
        spec = match.group("spec")
        # Pull a numeric version off the spec (everything after the comparator).
        version = re.sub(r"^[<>=!~]+\s*", "", spec.split(",", 1)[0]).strip()
        return spec, version
    return None


def bump_pyproject_dep(
    pyproject_path: Path,
    *,
    package: str,
    target_version: str,
    backup_dir: Path | None = None,
) -> tuple[str, str, str]:
    """Edit a single dep's lower bound in-place. Returns (from_version, to_version, diff).

    Preserves the comparator (e.g., `>=`, `~=`) and any trailing `, <X` upper
    bound. Raises if the package isn't found or the target isn't a strictly
    higher version (refuses downgrades silently — caller must pass an actually
    newer version).
    """
    if not pyproject_path.exists():
        raise FileNotFoundError(f"pyproject.toml not found at {pyproject_path}")
    original = pyproject_path.read_text(encoding="utf-8")
    found = find_pyproject_dep(original, package)
    if not found:
        raise KeyError(f"package {package!r} not found in {pyproject_path}")
    current_spec, current_version = found

    # Build the new spec — preserve everything except the lower-bound version.
    # Examples:
    #   ">=0.1.51"            → ">=<target>"
    #   ">=0.10.6,<1.0.0"     → ">=<target>,<1.0.0"
    parts = current_spec.split(",", 1)
    leading_comparator = re.match(r"^[<>=!~]+", parts[0]).group(0)  # type: ignore[union-attr]
    new_first = f"{leading_comparator}{target_version}"
    new_spec = new_first + ("," + parts[1] if len(parts) > 1 else "")

    if new_spec == current_spec:
        return current_version, target_version, ""

    target_norm = _normalize_package_name(package)

    def _replace(match: re.Match[str]) -> str:
        bare_name = re.sub(r"\[.*?\]", "", match.group("name"))
        if _normalize_package_name(bare_name) != target_norm:
            return match.group(0)
        return f'{match.group("prefix")}{match.group("name")}{new_spec}{match.group("suffix")}'

    # Visit every match — only the one whose name normalizes to target_norm
    # actually changes; the rest are no-ops via the early return inside
    # `_replace`. Using count=1 here would cause us to short-circuit on
    # the first dep regardless of whether it's the target.
    updated = _PYPI_DEP_RE.sub(_replace, original)
    if updated == original:
        # Defensive: the find succeeded but the replace was a no-op. Surface loudly.
        raise RuntimeError(f"failed to rewrite pyproject for {package!r}")

    if backup_dir is not None:
        backup_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        backup_dir.joinpath(f"pyproject.{timestamp}.bak").write_text(original, encoding="utf-8")

    pyproject_path.write_text(updated, encoding="utf-8")
    diff = "\n".join(
        difflib.unified_diff(
            original.splitlines(),
            updated.splitlines(),
            fromfile=f"{pyproject_path.name} (before)",
            tofile=f"{pyproject_path.name} (after)",
            lineterm="",
        )
    )
    return current_version, target_version, diff


def restore_pyproject(pyproject_path: Path, backup_dir: Path) -> Path | None:
    """Restore pyproject.toml from the most recent backup file. Returns the restored path or None."""
    if not backup_dir.exists():
        return None
    backups = sorted(backup_dir.glob("pyproject.*.bak"))
    if not backups:
        return None
    latest = backups[-1]
    pyproject_path.write_text(latest.read_text(encoding="utf-8"), encoding="utf-8")
    return latest


# ── Subprocess runners ──────────────────────────────────────────────────────


def _run(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    timeout: int,
    env: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    """Run a subprocess; never raise. Return (rc, stdout, stderr)."""
    try:
        completed = sp.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=env,
        )
        return completed.returncode, completed.stdout or "", completed.stderr or ""
    except FileNotFoundError as exc:
        return 127, "", f"binary_not_found: {exc}"
    except sp.TimeoutExpired:
        return 124, "", f"timeout after {timeout}s"
    except Exception as exc:
        return 1, "", f"unexpected_error: {exc}"


def run_uv_sync(*, repo_root: Path | None = None) -> tuple[bool, str]:
    """Run `uv sync` in the repo. Returns (ok, stderr_excerpt)."""
    cwd = repo_root or _ua_repo_root()
    if shutil.which("uv") is None:
        return False, "uv binary not on PATH"
    rc, _, stderr = _run(["uv", "sync"], cwd=cwd, timeout=SYNC_TIMEOUT_SECONDS)
    return (rc == 0), stderr.strip()[:600]


def run_zai_smoke(*, repo_root: Path | None = None) -> SmokeResult:
    """Verify UA's normal ZAI-mapped Anthropic SDK path still works.

    Runs a tiny `from anthropic import Anthropic; client.messages.create(...)`
    in the configured environment (which has ANTHROPIC_BASE_URL pointed at
    ZAI in production). A failure here means UA itself is broken.
    """
    cwd = repo_root or _ua_repo_root()
    # Inline Python so this doesn't require a stable script file path on disk.
    snippet = (
        "import sys\n"
        "try:\n"
        "    from anthropic import Anthropic\n"
        "    client = Anthropic()\n"
        "    resp = client.messages.create(\n"
        "        model='claude-haiku-4-5-20251001',\n"
        "        max_tokens=32,\n"
        "        messages=[{'role':'user','content':'Reply with exactly: OK'}],\n"
        "    )\n"
        "    blocks = [getattr(b,'text','') for b in (resp.content or []) if hasattr(b,'text')]\n"
        "    body = ''.join(blocks).strip()\n"
        "    print(body)\n"
        "    sys.exit(0 if 'OK' in body.upper() else 1)\n"
        "except Exception as exc:\n"
        "    print(f'{type(exc).__name__}: {exc}', file=sys.stderr)\n"
        "    sys.exit(1)\n"
    )
    rc, stdout, stderr = _run(
        ["uv", "run", "python", "-c", snippet],
        cwd=cwd,
        timeout=SMOKE_TIMEOUT_SECONDS,
    )
    return SmokeResult(
        name="zai_smoke",
        ok=(rc == 0),
        return_code=rc,
        stdout_excerpt=stdout.strip()[:600],
        stderr_excerpt=stderr.strip()[:600],
    )


def run_anthropic_native_smoke(*, smoke_dir: Path | None = None) -> SmokeResult:
    """Run /opt/ua_demos/_smoke/smoke.py to verify the Max plan demo path.

    This invokes the same CLI-driven smoke that the operator runs manually
    after `claude /login`. Bumping `claude-code` or `claude-agent-sdk`
    must not break this path or demos will silently start failing.
    """
    target = smoke_dir or _smoke_workspace_path()
    if not target.exists():
        return SmokeResult(
            name="anthropic_native_smoke",
            ok=False,
            skipped_reason=f"smoke workspace missing at {target}; run provision_smoke_workspace first",
        )
    script = target / "smoke.py"
    if not script.exists():
        return SmokeResult(
            name="anthropic_native_smoke",
            ok=False,
            skipped_reason=f"smoke.py missing at {script}",
        )
    if shutil.which("uv") is None:
        return SmokeResult(
            name="anthropic_native_smoke",
            ok=False,
            skipped_reason="uv binary not on PATH",
        )

    rc, stdout, stderr = _run(
        ["uv", "run", "python", "smoke.py"],
        cwd=target,
        timeout=SMOKE_TIMEOUT_SECONDS,
    )
    return SmokeResult(
        name="anthropic_native_smoke",
        ok=(rc == 0),
        return_code=rc,
        stdout_excerpt=stdout.strip()[:600],
        stderr_excerpt=stderr.strip()[:600],
    )


# ── Orchestration ───────────────────────────────────────────────────────────


def apply_upgrade(
    *,
    package: str,
    target_version: str,
    repo_root: Path | None = None,
    smoke_dir: Path | None = None,
    backup_dir: Path | None = None,
) -> UpgradeOutcome:
    """Edit pyproject → uv sync → run both smokes → rollback on any failure.

    Never raises on subprocess errors — every failure is captured in the
    UpgradeOutcome so the caller (typically the email integration) can
    report it cleanly. Only raises on programmer errors (missing
    pyproject.toml, package not declared, etc.).
    """
    started_at = datetime.now(timezone.utc).isoformat()
    repo = repo_root or _ua_repo_root()
    pyproject = repo / "pyproject.toml"
    backups = backup_dir or (repo / "artifacts" / "dependency_upgrade_backups")

    from_version, to_version, diff = bump_pyproject_dep(
        pyproject,
        package=package,
        target_version=target_version,
        backup_dir=backups,
    )

    sync_ok, sync_stderr = run_uv_sync(repo_root=repo)
    if not sync_ok:
        # Sync failed — roll back immediately so the working tree isn't broken.
        restored = restore_pyproject(pyproject, backups)
        return UpgradeOutcome(
            package=package,
            from_version=from_version,
            to_version=to_version,
            diff=diff,
            sync_ok=False,
            sync_stderr_excerpt=sync_stderr,
            zai_smoke=SmokeResult(name="zai_smoke", ok=False, skipped_reason="uv sync failed"),
            anthropic_smoke=SmokeResult(name="anthropic_native_smoke", ok=False, skipped_reason="uv sync failed"),
            rolled_back=bool(restored),
            rollback_reason="uv_sync_failed",
            started_at=started_at,
            finished_at=datetime.now(timezone.utc).isoformat(),
        )

    zai = run_zai_smoke(repo_root=repo)
    anthropic = run_anthropic_native_smoke(smoke_dir=smoke_dir)

    if not (zai.ok and anthropic.ok):
        rollback_reason = (
            "zai_smoke_failed" if not zai.ok and anthropic.ok
            else "anthropic_smoke_failed" if not anthropic.ok and zai.ok
            else "both_smokes_failed"
        )
        restored = restore_pyproject(pyproject, backups)
        # Re-sync to roll the venv back too. Best-effort; don't hide the smoke
        # failure if rollback sync also fails.
        if restored:
            run_uv_sync(repo_root=repo)
        return UpgradeOutcome(
            package=package,
            from_version=from_version,
            to_version=to_version,
            diff=diff,
            sync_ok=True,
            sync_stderr_excerpt=sync_stderr,
            zai_smoke=zai,
            anthropic_smoke=anthropic,
            rolled_back=bool(restored),
            rollback_reason=rollback_reason,
            started_at=started_at,
            finished_at=datetime.now(timezone.utc).isoformat(),
        )

    return UpgradeOutcome(
        package=package,
        from_version=from_version,
        to_version=to_version,
        diff=diff,
        sync_ok=True,
        sync_stderr_excerpt=sync_stderr,
        zai_smoke=zai,
        anthropic_smoke=anthropic,
        rolled_back=False,
        started_at=started_at,
        finished_at=datetime.now(timezone.utc).isoformat(),
    )


# ── Email body building ─────────────────────────────────────────────────────


def build_upgrade_email(outcome: UpgradeOutcome) -> tuple[str, str, str]:
    """Return (subject, plain_text, html) for the upgrade-result email."""
    status_emoji = "OK" if outcome.overall_ok else "FAIL"
    subject = (
        f"[Phase 0 upgrade] {status_emoji} — {outcome.package} "
        f"{outcome.from_version} → {outcome.to_version}"
    )

    text_parts: list[str] = [
        f"Phase 0 dependency upgrade — {status_emoji}",
        "",
        f"Package: {outcome.package}",
        f"From:    {outcome.from_version}",
        f"To:      {outcome.to_version}",
        f"Started: {outcome.started_at}",
        f"Done:    {outcome.finished_at}",
        "",
        "Smoke results:",
        f"  • ZAI smoke (UA's normal path):       {'PASS' if outcome.zai_smoke.ok else 'FAIL'}",
        f"  • Anthropic-native smoke (demo path): {'PASS' if outcome.anthropic_smoke.ok else 'FAIL'}",
        "",
    ]
    if outcome.rolled_back:
        text_parts.extend([
            f"!! ROLLED BACK ({outcome.rollback_reason}) — pyproject.toml restored from backup. !!",
            "",
            "What broke:",
        ])
        for smoke in (outcome.zai_smoke, outcome.anthropic_smoke):
            if not smoke.ok:
                text_parts.extend([
                    f"  [{smoke.name}]",
                    f"    return_code: {smoke.return_code}",
                    f"    skipped_reason: {smoke.skipped_reason}" if smoke.skipped_reason else "",
                    f"    stderr: {smoke.stderr_excerpt}" if smoke.stderr_excerpt else "",
                    f"    stdout: {smoke.stdout_excerpt}" if smoke.stdout_excerpt else "",
                ])
        text_parts.append("")
    else:
        text_parts.extend([
            "Both smokes passed. The bump is in your working tree on feature/latest2 and",
            "ready for /ship. Diff below for review:",
            "",
            outcome.diff or "(no diff — version already at target)",
            "",
        ])
    text = "\n".join(line for line in text_parts if line is not None)

    html = (
        f"<pre>{text.replace('<', '&lt;').replace('>', '&gt;')}</pre>"
    )
    return subject, text, html


# ── Failure logging into the vault ──────────────────────────────────────────


def write_upgrade_failure_record(
    outcome: UpgradeOutcome,
    *,
    vault_path: Path,
) -> Path | None:
    """Reuse dependency_currency.record_upgrade_failure for vault hygiene."""
    if outcome.overall_ok:
        return None
    from universal_agent.services.dependency_currency import record_upgrade_failure

    error_summary_lines: list[str] = []
    for smoke in (outcome.zai_smoke, outcome.anthropic_smoke):
        if smoke.ok:
            continue
        error_summary_lines.append(f"[{smoke.name}] rc={smoke.return_code} reason={smoke.skipped_reason}")
        if smoke.stderr_excerpt:
            error_summary_lines.append(f"  stderr: {smoke.stderr_excerpt}")
        if smoke.stdout_excerpt:
            error_summary_lines.append(f"  stdout: {smoke.stdout_excerpt}")
    if not outcome.sync_ok:
        error_summary_lines.append(f"[uv_sync] {outcome.sync_stderr_excerpt}")
    return record_upgrade_failure(
        vault_path,
        package=outcome.package,
        from_version=outcome.from_version,
        to_version=outcome.to_version,
        error_summary="\n".join(error_summary_lines) or "(no detail captured)",
        detected_at=outcome.finished_at,
    )

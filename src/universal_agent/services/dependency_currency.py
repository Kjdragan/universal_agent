"""Dependency currency layer (Phase 0).

Parses outdated-package output from `uv pip list --outdated`,
`npm outdated --json`, and the `claude` CLI version probe; classifies
which packages are Anthropic-adjacent (the ones that gate demo
execution); and writes the vault infrastructure pages that record
installed versions, drift, releases, and upgrade failures.

PR 6a ships pure logic + vault writers + a CLI sweep. The actual
upgrade actuator (edits pyproject.toml, runs uv sync, deploys) is
PR 6b — kept separate so the deploy-pipeline-touching code has its own
review gate.

See docs/proactive_signals/claudedevs_intel_v2_design.md §5.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import re
from typing import Any, Iterable

logger = logging.getLogger(__name__)


# ── Anthropic-adjacent allowlist ─────────────────────────────────────────────
#
# These are the packages whose currency directly gates demo execution.
# Lifted from intel_lanes.yaml `tracked_packages` so the source of truth
# stays in one place; this list is the operational fallback when the
# lane config is unavailable.

ANTHROPIC_ADJACENT_PACKAGES: frozenset[str] = frozenset(
    {
        # Python
        "claude-agent-sdk",
        "anthropic",
        # TypeScript / npm
        "@anthropic-ai/claude-agent-sdk",
        "@anthropic-ai/sdk",
        # CLI
        "claude-code",
        "claude",  # the npm package alias for the CLI
    }
)


def is_anthropic_adjacent(package_name: str) -> bool:
    """Whether a package counts as Anthropic-adjacent for the auto-upgrade rule."""
    name = str(package_name or "").strip().lower()
    if not name:
        return False
    if name in {p.lower() for p in ANTHROPIC_ADJACENT_PACKAGES}:
        return True
    # Also catch namespaced variants we haven't pinned literally.
    return name.startswith("@anthropic-ai/")


# ── Version comparison ──────────────────────────────────────────────────────


_VERSION_PART_RE = re.compile(r"^(\d+)([\.\-+].*)?$")


def _parse_version_tuple(version: str) -> tuple[int, ...]:
    """Best-effort numeric tuple for version comparison.

    Drops pre-release suffixes (`-beta`, `+build`) for ordering purposes.
    Non-numeric segments terminate the parse so '1.2.x' becomes (1, 2).
    """
    text = str(version or "").strip().lstrip("vV").strip()
    if not text:
        return ()
    out: list[int] = []
    for raw in text.split("."):
        m = _VERSION_PART_RE.match(raw)
        if not m:
            break
        try:
            out.append(int(m.group(1)))
        except ValueError:
            break
    return tuple(out)


def compare_versions(a: str, b: str) -> int:
    """Return -1 if a < b, 0 if a == b, 1 if a > b. Robust to dirty inputs."""
    ta = _parse_version_tuple(a)
    tb = _parse_version_tuple(b)
    if ta < tb:
        return -1
    if ta > tb:
        return 1
    return 0


# ── Outdated parsers ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class OutdatedPackage:
    """One package observed by the drift sweep."""

    name: str
    ecosystem: str  # 'pypi' | 'npm' | 'cli'
    installed: str
    latest: str
    is_anthropic_adjacent: bool

    @property
    def needs_upgrade(self) -> bool:
        return compare_versions(self.installed, self.latest) < 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ecosystem": self.ecosystem,
            "installed": self.installed,
            "latest": self.latest,
            "is_anthropic_adjacent": self.is_anthropic_adjacent,
            "needs_upgrade": self.needs_upgrade,
        }


def parse_uv_outdated(json_text: str) -> list[OutdatedPackage]:
    """Parse `uv pip list --outdated --format json` output.

    uv emits a list of objects shaped like:
        {"name": "...", "version": "...", "latest_version": "...", "latest_filetype": "..."}
    Older versions of uv use 'latest' rather than 'latest_version'.
    """
    out: list[OutdatedPackage] = []
    if not json_text or not json_text.strip():
        return out
    try:
        records = json.loads(json_text)
    except json.JSONDecodeError as exc:
        logger.warning("parse_uv_outdated: invalid JSON (%s)", exc)
        return out
    if not isinstance(records, list):
        return out
    for rec in records:
        if not isinstance(rec, dict):
            continue
        name = str(rec.get("name") or "").strip()
        installed = str(rec.get("version") or "").strip()
        latest = str(rec.get("latest_version") or rec.get("latest") or "").strip()
        if not (name and installed and latest):
            continue
        out.append(
            OutdatedPackage(
                name=name,
                ecosystem="pypi",
                installed=installed,
                latest=latest,
                is_anthropic_adjacent=is_anthropic_adjacent(name),
            )
        )
    return out


def parse_npm_outdated(json_text: str) -> list[OutdatedPackage]:
    """Parse `npm outdated --json` output.

    npm emits an object keyed by package name:
        {"<pkg>": {"current": "...", "wanted": "...", "latest": "..."}}
    """
    out: list[OutdatedPackage] = []
    if not json_text or not json_text.strip():
        return out
    try:
        records = json.loads(json_text)
    except json.JSONDecodeError as exc:
        logger.warning("parse_npm_outdated: invalid JSON (%s)", exc)
        return out
    if not isinstance(records, dict):
        return out
    for name, rec in records.items():
        if not isinstance(rec, dict):
            continue
        installed = str(rec.get("current") or "").strip()
        latest = str(rec.get("latest") or rec.get("wanted") or "").strip()
        if not (name and installed and latest):
            continue
        out.append(
            OutdatedPackage(
                name=str(name).strip(),
                ecosystem="npm",
                installed=installed,
                latest=latest,
                is_anthropic_adjacent=is_anthropic_adjacent(name),
            )
        )
    return out


_CLAUDE_VERSION_RE = re.compile(r"(\d+\.\d+(?:\.\d+)?(?:[-+][A-Za-z0-9_.-]+)?)")


def parse_claude_version(stdout: str) -> str:
    """Parse the version from `claude --version` stdout.

    Output is typically a single line like "claude 2.1.116".
    """
    text = str(stdout or "").strip()
    if not text:
        return ""
    m = _CLAUDE_VERSION_RE.search(text)
    return m.group(1) if m else ""


# ── Release-announcement extraction ─────────────────────────────────────────
#
# Used by the classifier (claude_code_intel.classify_post) when a tweet is
# announcing a new package release. Extracts (package, version) deterministically
# from text and links so the Phase 0 upgrade worker has a structured payload.

_KNOWN_PACKAGE_LITERALS = (
    "@anthropic-ai/claude-agent-sdk",
    "claude-agent-sdk",
    "@anthropic-ai/sdk",
    "claude-code",
    "anthropic",
    "claude",  # Generic CLI alias — match last so 'claude-code' wins on substring.
)
_VERSION_PATTERN = re.compile(r"\bv?(\d+\.\d+(?:\.\d+)?(?:[-+][A-Za-z0-9_.-]+)?)\b")


def _normalize_for_package_match(text: str) -> str:
    """Normalize text so 'Claude Code', 'claude-code', and 'claude_code' all match equally."""
    lowered = (text or "").lower()
    # Collapse dashes, underscores, and whitespace to a single space.
    return re.sub(r"[\s\-_]+", " ", lowered)


def detect_release_announcement(*, text: str, links: list[str]) -> dict[str, Any] | None:
    """Return a release_info dict if the post looks like a package release.

    Conservative: requires both a recognized package name AND a parseable
    version number. Returns None otherwise so the classifier doesn't promote
    every tier-2 post to release_announcement.

    Matching is whitespace/dash/underscore-insensitive so the literal package
    `claude-code` matches against `Claude Code` in tweet prose.
    """
    blob = (text or "") + " " + " ".join(links or [])
    blob_norm = _normalize_for_package_match(blob)
    package = ""
    for candidate in _KNOWN_PACKAGE_LITERALS:
        if _normalize_for_package_match(candidate) in blob_norm:
            package = candidate
            break
    if not package:
        return None
    m = _VERSION_PATTERN.search(blob)
    if not m:
        return None
    version = m.group(1)
    return {
        "package": package,
        "version": version,
        "is_anthropic_adjacent": is_anthropic_adjacent(package),
    }


# ── Vault infrastructure writers ────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _infrastructure_dir(vault_path: Path) -> Path:
    path = vault_path / "infrastructure"
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_installed_versions_page(
    vault_path: Path,
    *,
    python_packages: dict[str, str] | None = None,
    npm_packages: dict[str, str] | None = None,
    claude_cli_version: str = "",
) -> Path:
    """Write the live record of what's actually installed on the VPS.

    This is the source of truth for the demo-task version gate. Format is
    plain markdown so a human can grep or paste it into a runbook.
    """
    target = _infrastructure_dir(vault_path) / "installed_versions.md"
    lines: list[str] = [
        "# Installed Versions",
        "",
        f"_Updated: {_now_iso()}_",
        "",
    ]
    if claude_cli_version:
        lines.extend(["## Claude Code CLI", "", f"- claude: `{claude_cli_version}`", ""])
    if python_packages:
        lines.extend(["## Python (uv)", ""])
        for name in sorted(python_packages):
            marker = " (Anthropic)" if is_anthropic_adjacent(name) else ""
            lines.append(f"- {name}: `{python_packages[name]}`{marker}")
        lines.append("")
    if npm_packages:
        lines.extend(["## TypeScript / npm", ""])
        for name in sorted(npm_packages):
            marker = " (Anthropic)" if is_anthropic_adjacent(name) else ""
            lines.append(f"- {name}: `{npm_packages[name]}`{marker}")
        lines.append("")
    target.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return target


def write_version_drift_page(
    vault_path: Path,
    *,
    outdated: Iterable[OutdatedPackage],
    sweep_started_at: str = "",
) -> Path:
    """Write the human-readable drift report.

    Anthropic-adjacent packages are listed first (they gate demos);
    everything else falls below.
    """
    target = _infrastructure_dir(vault_path) / "version_drift.md"
    items = list(outdated)
    anthropic = [o for o in items if o.is_anthropic_adjacent]
    rest = [o for o in items if not o.is_anthropic_adjacent]

    started = sweep_started_at or _now_iso()
    lines: list[str] = [
        "# Version Drift Report",
        "",
        f"_Sweep started: {started}_",
        f"_Total drifting packages: {len(items)} ({len(anthropic)} Anthropic-adjacent)_",
        "",
    ]
    if anthropic:
        lines.extend(["## Anthropic-adjacent (auto-upgrade gated by smoke tests)", ""])
        for pkg in sorted(anthropic, key=lambda p: p.name):
            lines.append(
                f"- `{pkg.name}` ({pkg.ecosystem}): {pkg.installed} → **{pkg.latest}**"
            )
        lines.append("")
    if rest:
        lines.extend(["## Other packages (manual review)", ""])
        for pkg in sorted(rest, key=lambda p: p.name):
            lines.append(
                f"- `{pkg.name}` ({pkg.ecosystem}): {pkg.installed} → {pkg.latest}"
            )
        lines.append("")
    if not items:
        lines.extend(["All tracked packages are current.", ""])
    target.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return target


def append_release_timeline_entry(
    vault_path: Path,
    *,
    package: str,
    version: str,
    source_url: str = "",
    notable_features: list[str] | None = None,
    detected_at: str = "",
) -> Path:
    """Append one structured release entry to release_timeline.md."""
    target = _infrastructure_dir(vault_path) / "release_timeline.md"
    if not target.exists():
        target.write_text("# Release Timeline\n\nDetected releases of Anthropic-adjacent packages.\n\n", encoding="utf-8")
    timestamp = detected_at or _now_iso()
    lines = [
        f"\n## [{timestamp}] {package} `{version}`",
    ]
    if source_url:
        lines.append(f"- source: {source_url}")
    if notable_features:
        lines.append("- notable_features:")
        for feature in notable_features:
            lines.append(f"  - {str(feature).strip()}")
    lines.append("")
    with target.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
    return target


def record_upgrade_failure(
    vault_path: Path,
    *,
    package: str,
    from_version: str,
    to_version: str,
    error_summary: str,
    detected_at: str = "",
) -> Path:
    """Write a per-failure file under infrastructure/upgrade_failures/."""
    failures_dir = _infrastructure_dir(vault_path) / "upgrade_failures"
    failures_dir.mkdir(parents=True, exist_ok=True)
    timestamp = detected_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    safe_pkg = re.sub(r"[^A-Za-z0-9_.-]+", "-", package).strip("-") or "unknown"
    target = failures_dir / f"{timestamp}_{safe_pkg}.md"
    body = (
        f"# Upgrade Failure: {package}\n\n"
        f"- detected_at: {detected_at or _now_iso()}\n"
        f"- from_version: `{from_version}`\n"
        f"- to_version: `{to_version}`\n"
        f"- ecosystem: {'pypi' if not package.startswith('@') and '/' not in package else 'npm'}\n"
        f"- is_anthropic_adjacent: {is_anthropic_adjacent(package)}\n\n"
        "## Error Summary\n\n"
        f"```\n{error_summary.strip() or '(no detail captured)'}\n```\n"
    )
    target.write_text(body, encoding="utf-8")
    return target


# ── Sweep orchestration ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class SweepReport:
    sweep_started_at: str
    claude_cli_version: str
    pypi_outdated: tuple[OutdatedPackage, ...]
    npm_outdated: tuple[OutdatedPackage, ...]

    @property
    def all_outdated(self) -> list[OutdatedPackage]:
        return list(self.pypi_outdated) + list(self.npm_outdated)

    @property
    def anthropic_outdated(self) -> list[OutdatedPackage]:
        return [p for p in self.all_outdated if p.is_anthropic_adjacent]

    def to_dict(self) -> dict[str, Any]:
        return {
            "sweep_started_at": self.sweep_started_at,
            "claude_cli_version": self.claude_cli_version,
            "pypi_outdated": [p.to_dict() for p in self.pypi_outdated],
            "npm_outdated": [p.to_dict() for p in self.npm_outdated],
            "summary": {
                "total_outdated": len(self.all_outdated),
                "anthropic_outdated": len(self.anthropic_outdated),
            },
        }


def assemble_sweep_report(
    *,
    uv_outdated_json: str = "",
    npm_outdated_json: str = "",
    claude_version_stdout: str = "",
    sweep_started_at: str = "",
) -> SweepReport:
    """Pure-data assembly of a sweep report from the raw subprocess outputs."""
    return SweepReport(
        sweep_started_at=sweep_started_at or _now_iso(),
        claude_cli_version=parse_claude_version(claude_version_stdout),
        pypi_outdated=tuple(parse_uv_outdated(uv_outdated_json)),
        npm_outdated=tuple(parse_npm_outdated(npm_outdated_json)),
    )


def write_sweep_artifacts(
    report: SweepReport,
    *,
    vault_path: Path,
    installed_pypi: dict[str, str] | None = None,
    installed_npm: dict[str, str] | None = None,
) -> dict[str, str]:
    """Write the three vault pages from a SweepReport."""
    out: dict[str, str] = {}
    out["installed_versions"] = str(
        write_installed_versions_page(
            vault_path,
            python_packages=installed_pypi,
            npm_packages=installed_npm,
            claude_cli_version=report.claude_cli_version,
        )
    )
    out["version_drift"] = str(
        write_version_drift_page(
            vault_path,
            outdated=report.all_outdated,
            sweep_started_at=report.sweep_started_at,
        )
    )
    return out

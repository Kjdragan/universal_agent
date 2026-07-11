"""Guarded reclaim of heavy leaves from vault-attached Cody mission scratch.

Why this exists
---------------
Disk pressure on ``/`` is driven by Cody demo-workspace scratch under
``AGENT_RUN_WORKSPACES/vp_coder_primary_external/`` (hundreds of ``vp-mission-*``
dirs, ~22G, growing daily). The leaf bloat is ``node_modules`` and ``.git``
inside each mission's scratch tree (one Remotion demo alone is 442M of
``node_modules``). The durable demos live *separately* under
``/opt/ua_demos/<id>/`` (registered) and the vault entity pages
(``## Demos`` bullets written by
:func:`universal_agent.services.cody_evaluation.attach_demo_to_vault_entity`).
So once a demo is finalized AND vault-attached, the heavy regenerable leaves
in that mission's scratch are dead weight — safe to reclaim.

What this module does
---------------------
A standalone, **idempotent** reclaim pass over the scratch root. For each
``vp-mission-*`` dir it:

1. Resolves the mission's ``demo_id`` (from ``manifest.json`` / ``capabilities.md``
   in the double-nested workspace).
2. **Hard guard**: reclaims nothing unless the demo is confirmed durably
   present elsewhere — registered under ``/opt/ua_demos/`` OR named in a vault
   ``## Demos`` bullet. This is the guard the unit test pins.
3. Applies an age floor (grace) so brand-new missions are never touched.
4. Strips only the configured heavy leaves (default ``node_modules``, ``.git``),
   preserving audit files (``capabilities.md``, ``run.log``, ``manifest.json``,
   briefs). Never follows symlinks out of the scratch dir.

This is deliberately decoupled from the finalize hot path
(``cody_evaluation.complete_demo_task`` is a thin Task Hub setter that runs
before Phase-4 vault-attach and knows nothing of the scratch layout). The
reclaim is inert until a cron task invokes
:func:`reclaim_coder_mission_workspaces` (or the ``scripts/cody_demo_cleanup.py``
CLI); shipping this module does not delete anything on import or on deploy.

Env flags (feature default-on, destructive op dry-run-by-default)
-----------------------------------------------------------------
- ``UA_CODY_DEMO_CLEANUP_ENABLED``  — default ``1`` (feature on).
- ``UA_CODY_DEMO_CLEANUP_DRY_RUN``  — default ``1`` (scan reports what it WOULD
  strip; operator flips to ``0`` after reviewing a dry pass).
- ``UA_CODY_DEMO_CLEANUP_MIN_AGE_HOURS`` — default ``48`` (grace floor).
- ``UA_CODY_DEMO_CLEANUP_LEAVES`` — default ``node_modules,.git``.
- ``UA_CODY_DEMO_CLEANUP_ROOT`` / ``..._DEMOS_ROOT`` / ``..._VAULT_ROOT`` —
  overrides for the scratch root, demos root, and vault root.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
import os
from pathlib import Path
import re
import shutil
import time
from typing import Any

logger = logging.getLogger(__name__)


# ── Env flag names ───────────────────────────────────────────────────────────

ENV_ENABLED = "UA_CODY_DEMO_CLEANUP_ENABLED"
ENV_DRY_RUN = "UA_CODY_DEMO_CLEANUP_DRY_RUN"
ENV_MIN_AGE_HOURS = "UA_CODY_DEMO_CLEANUP_MIN_AGE_HOURS"
ENV_LEAVES = "UA_CODY_DEMO_CLEANUP_LEAVES"
ENV_ROOT = "UA_CODY_DEMO_CLEANUP_ROOT"
ENV_DEMOS_ROOT = "UA_CODY_DEMO_CLEANUP_DEMOS_ROOT"
ENV_VAULT_ROOT = "UA_CODY_DEMO_CLEANUP_VAULT_ROOT"

DEFAULT_MIN_AGE_HOURS = 48
DEFAULT_LEAVES: tuple[str, ...] = ("node_modules", ".git")
DEFAULT_DEMOS_ROOT = Path("/opt/ua_demos")

MISSION_DIR_PREFIX = "vp-mission-"
DEMOS_SECTION_HEADER = "## Demos"


# ── Defaults / env parsing ───────────────────────────────────────────────────


def _repo_root() -> Path:
    """Repo root inferred from this file's location (``parents[3]`` for services/)."""
    return Path(__file__).resolve().parents[3]


def default_scratch_root() -> Path:
    """Where Cody mission scratch lives (overridable via ``UA_CODY_DEMO_CLEANUP_ROOT``)."""
    raw = os.getenv(ENV_ROOT, "").strip()
    if raw:
        return Path(raw).expanduser()
    return _repo_root() / "AGENT_RUN_WORKSPACES" / "vp_coder_primary_external"


def default_demos_root() -> Path:
    raw = os.getenv(ENV_DEMOS_ROOT, "").strip()
    if raw:
        return Path(raw).expanduser()
    return DEFAULT_DEMOS_ROOT


def default_vault_root() -> Path:
    raw = os.getenv(ENV_VAULT_ROOT, "").strip()
    if raw:
        return Path(raw).expanduser()
    return _repo_root() / "artifacts" / "knowledge-vaults" / "claude-code-intelligence"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("Non-integer %s=%r; using default %d", name, raw, default)
        return default


def parse_leaves(raw: str | None) -> tuple[str, ...]:
    """Parse the comma-separated heavy-leaf names from env (or the default)."""
    if not raw:
        return DEFAULT_LEAVES
    items = tuple(p.strip() for p in raw.split(",") if p.strip())
    return items or DEFAULT_LEAVES


# ── Path helpers ─────────────────────────────────────────────────────────────


def _within(path: Path, root: Path) -> bool:
    """True if ``path`` is contained inside ``root`` (after resolve)."""
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def _tree_size(path: Path) -> int:
    """Sum of file sizes under ``path`` (best-effort; ignores unreadable entries)."""
    total = 0
    for dirpath, _dirs, files in os.walk(path):
        for fname in files:
            try:
                total += Path(dirpath, fname).stat().st_size
            except OSError:
                continue
    return total


# ── Demo linkage + vault-attach guard ────────────────────────────────────────


def _candidate_metadata_files(scratch_dir: Path) -> list[Path]:
    """Bounded search for manifest.json / capabilities.md in the double-nested layout."""
    name = scratch_dir.name
    candidates: list[Path] = [
        scratch_dir / "manifest.json",
        scratch_dir / "capabilities.md",
        scratch_dir / name / "manifest.json",
        scratch_dir / name / "capabilities.md",
    ]
    try:
        for child in scratch_dir.iterdir():
            if child.is_dir() and not child.is_symlink():
                candidates.append(child / "manifest.json")
                candidates.append(child / "capabilities.md")
    except OSError:
        pass
    # Deduplicate, preserve order, keep extant only.
    seen: set[Path] = set()
    out: list[Path] = []
    for cand in candidates:
        if cand in seen:
            continue
        seen.add(cand)
        if cand.exists():
            out.append(cand)
    return out


_DEMO_ID_KV_RE = re.compile(r'["\']?demo_id["\']?\s*[:=]\s*["\']?([A-Za-z0-9_.\-/]+)')


def extract_demo_id(scratch_dir: Path) -> str | None:
    """Best-effort ``demo_id`` for a mission scratch dir.

    Checks ``manifest.json`` (DemoManifest.demo_id) first, then a ``demo_id``
    key/value token in ``capabilities.md``. Returns ``None`` if none is found.
    """
    for meta in _candidate_metadata_files(scratch_dir):
        try:
            text = meta.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if meta.name == "manifest.json":
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                continue
            did = str(data.get("demo_id") or "").strip()
            if did:
                return did
        else:  # capabilities.md / free text
            match = _DEMO_ID_KV_RE.search(text)
            if match:
                return match.group(1).strip().strip("`")
    return None


def is_demo_registered(demo_id: str, demos_root: Path) -> bool:
    """True if ``demo_id`` exists as a dir/symlink directly under ``demos_root``."""
    if not demo_id:
        return False
    try:
        if not demos_root.exists() or not demos_root.is_dir():
            return False
        return (demos_root / demo_id).exists()
    except OSError:
        return False


def is_attached_in_vault(demo_id: str, vault_root: Path) -> bool:
    """True if any ``vault_root/entities/*.md`` has a ``## Demos`` bullet naming ``demo_id``.

    Bullets are written by
    :func:`universal_agent.services.cody_evaluation.attach_demo_to_vault_entity`
    in the form ``- `<demo_id>` — …``.
    """
    if not demo_id:
        return False
    entities = vault_root / "entities"
    try:
        if not entities.exists() or not entities.is_dir():
            return False
    except OSError:
        return False
    needle = f"`{demo_id}`"
    try:
        pages = list(entities.glob("*.md"))
    except OSError:
        return False
    for page in pages:
        try:
            text = page.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if DEMOS_SECTION_HEADER in text and needle in text:
            return True
    return False


def is_vault_attached(demo_id: str, demos_root: Path, vault_root: Path) -> bool:
    """Hard guard: the demo is durably present elsewhere (registered OR vault-attached)."""
    return is_demo_registered(demo_id, demos_root) or is_attached_in_vault(demo_id, vault_root)


# ── Heavy-leaf discovery ─────────────────────────────────────────────────────


def find_heavy_leaves(scratch_dir: Path, leaves: tuple[str, ...]) -> list[Path]:
    """Find configured heavy-leaf dirs (e.g. node_modules, .git) under the scratch.

    Does not descend *into* a matched leaf (we rmtree the whole thing), and does
    not follow symlinks. Any leaf that resolves outside the scratch is skipped.
    """
    if not leaves:
        return []
    scratch_resolved = scratch_dir.resolve()
    found: list[Path] = []
    leaf_set = set(leaves)
    for dirpath, dirnames, _files in os.walk(scratch_dir, followlinks=False):
        keep: list[str] = []
        for dname in dirnames:
            full = Path(dirpath) / dname
            if dname in leaf_set:
                # Matched a leaf. Target it only if it lives inside the scratch
                # (not a symlink pointing elsewhere); never descend.
                try:
                    inside = _within(full, scratch_resolved)
                except OSError:
                    inside = False
                if inside:
                    found.append(full)
                continue
            keep.append(dname)
        dirnames[:] = keep  # prune descent into matched leaves
    return found


# ── Report type ──────────────────────────────────────────────────────────────


@dataclass
class ReclaimReport:
    """Outcome of a single mission-scratch reclaim decision."""

    scratch_dir: Path
    demo_id: str | None = None
    action: str = "skipped"  # "stripped" | "skipped" | "dry_run" | "error"
    reason: str = ""
    stripped: list[str] = field(default_factory=list)  # rel paths of leaves acted on
    bytes_freed: int = 0
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize the cleanup outcome for logging / run records."""
        return {
            "scratch_dir": str(self.scratch_dir),
            "demo_id": self.demo_id,
            "action": self.action,
            "reason": self.reason,
            "stripped": list(self.stripped),
            "bytes_freed": self.bytes_freed,
            "error": self.error,
        }


# ── Per-mission reclaim ──────────────────────────────────────────────────────


def _reason_skipped(report: ReclaimReport, reason: str) -> ReclaimReport:
    report.action = "skipped"
    report.reason = reason
    return report


def reclaim_mission_scratch(
    scratch_dir: Path,
    *,
    demos_root: Path | None = None,
    vault_root: Path | None = None,
    leaves: tuple[str, ...] | None = None,
    min_age_hours: int | None = None,
    dry_run: bool | None = None,
    now: float | None = None,
) -> ReclaimReport:
    """Apply the guarded reclaim to a single mission scratch dir.

    Returns a :class:`ReclaimReport`. Never raises on a skipped/error decision —
    failures are captured in the report so a scan over hundreds of dirs is
    resilient. Defaults are pulled from env when an argument is ``None``.
    """
    report = ReclaimReport(scratch_dir=scratch_dir)

    demos_root = demos_root if demos_root is not None else default_demos_root()
    vault_root = vault_root if vault_root is not None else default_vault_root()
    leaves = leaves if leaves is not None else parse_leaves(os.getenv(ENV_LEAVES))
    min_age_hours = (
        min_age_hours if min_age_hours is not None else _env_int(ENV_MIN_AGE_HOURS, DEFAULT_MIN_AGE_HOURS)
    )
    dry_run = dry_run if dry_run is not None else _env_bool(ENV_DRY_RUN, True)
    now_ts = now if now is not None else time.time()

    try:
        if not scratch_dir.exists() or not scratch_dir.is_dir():
            return _reason_skipped(report, "missing_scratch_dir")
        if not scratch_dir.name.startswith(MISSION_DIR_PREFIX):
            return _reason_skipped(report, "not_a_mission_dir")

        demo_id = extract_demo_id(scratch_dir)
        report.demo_id = demo_id
        if not demo_id:
            return _reason_skipped(report, "no_demo_id")

        # ── HARD GUARD: never strip until the demo is vault-attached/registered.
        if not is_vault_attached(demo_id, demos_root, vault_root):
            return _reason_skipped(report, "not_vault_attached")

        # ── Age floor (grace) so brand-new missions are never touched.
        try:
            mtime = scratch_dir.stat().st_mtime
        except OSError as exc:
            report.action = "error"
            report.error = f"stat_failed: {exc}"
            return report
        age_hours = (now_ts - mtime) / 3600.0
        if age_hours < min_age_hours:
            return _reason_skipped(report, f"too_new:{age_hours:.1f}h<{min_age_hours}h")

        leaves_found = find_heavy_leaves(scratch_dir, leaves)
        if not leaves_found:
            return _reason_skipped(report, "no_heavy_leaves")

        scratch_resolved = scratch_dir.resolve()
        targeted: list[Path] = [
            leaf for leaf in leaves_found if _within(leaf, scratch_resolved)
        ]
        bytes_freed = 0
        for leaf in targeted:
            bytes_freed += _tree_size(leaf)

        report.bytes_freed = bytes_freed
        report.stripped = [str(leaf.relative_to(scratch_dir)) for leaf in targeted]

        if dry_run:
            report.action = "dry_run"
            report.reason = f"would_strip:{len(targeted)}"
            return report

        # Live reclaim.
        stripped_now: list[str] = []
        for leaf in targeted:
            try:
                shutil.rmtree(leaf)
                stripped_now.append(str(leaf.relative_to(scratch_dir)))
            except OSError as exc:
                logger.warning("Failed to strip %s: %s", leaf, exc)
        report.stripped = stripped_now
        report.action = "stripped"
        report.reason = f"stripped:{len(stripped_now)}/{len(targeted)}"
        return report
    except Exception as exc:  # pragma: no cover - defensive; scan must stay resilient
        report.action = "error"
        report.error = f"{type(exc).__name__}: {exc}"
        logger.exception("reclaim_mission_scratch failed for %s", scratch_dir)
        return report


# ── Scan aggregator ──────────────────────────────────────────────────────────


def reclaim_coder_mission_workspaces(
    *,
    root: Path | None = None,
    demos_root: Path | None = None,
    vault_root: Path | None = None,
    leaves: tuple[str, ...] | None = None,
    min_age_hours: int | None = None,
    dry_run: bool | None = None,
    enabled: bool | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Scan the scratch root and apply the guarded reclaim to every ``vp-mission-*`` dir.

    Returns an aggregate summary dict (scanned / per-action counts / bytes freed /
    skipped-by-reason). When ``enabled`` is ``False`` (or
    ``UA_CODY_DEMO_CLEANUP_ENABLED=0``), this is a no-op and returns
    ``{"enabled": False, ...}``.
    """
    if enabled is None:
        enabled = _env_bool(ENV_ENABLED, True)
    if not enabled:
        return {"enabled": False, "scanned": 0, "dry_run": None}

    if dry_run is None:
        dry_run = _env_bool(ENV_DRY_RUN, True)
    root = root if root is not None else default_scratch_root()
    demos_root = demos_root if demos_root is not None else default_demos_root()
    vault_root = vault_root if vault_root is not None else default_vault_root()
    leaves = leaves if leaves is not None else parse_leaves(os.getenv(ENV_LEAVES))
    min_age_hours = (
        min_age_hours if min_age_hours is not None else _env_int(ENV_MIN_AGE_HOURS, DEFAULT_MIN_AGE_HOURS)
    )
    now_ts = now if now is not None else time.time()

    if not root.exists() or not root.is_dir():
        return {
            "enabled": True,
            "dry_run": dry_run,
            "root": str(root),
            "scanned": 0,
            "reason": "scratch_root_missing",
        }

    reports: list[dict[str, Any]] = []
    try:
        children = sorted(root.iterdir(), key=lambda p: p.name)
    except OSError as exc:
        return {
            "enabled": True,
            "dry_run": dry_run,
            "root": str(root),
            "scanned": 0,
            "reason": f"iterdir_failed: {exc}",
        }

    for child in children:
        if child.is_dir() and child.name.startswith(MISSION_DIR_PREFIX):
            reports.append(
                reclaim_mission_scratch(
                    child,
                    demos_root=demos_root,
                    vault_root=vault_root,
                    leaves=leaves,
                    min_age_hours=min_age_hours,
                    dry_run=dry_run,
                    now=now_ts,
                ).to_dict()
            )

    by_action: dict[str, int] = {}
    by_reason: dict[str, int] = {}
    total_bytes = 0
    for rep in reports:
        by_action[rep["action"]] = by_action.get(rep["action"], 0) + 1
        if rep["reason"]:
            # Bucket the too_new reason (which carries live numbers) to its prefix.
            key = rep["reason"].split(":", 1)[0] if ":" in rep["reason"] else rep["reason"]
            by_reason[key] = by_reason.get(key, 0) + 1
        total_bytes += int(rep.get("bytes_freed") or 0)

    return {
        "enabled": True,
        "dry_run": dry_run,
        "root": str(root),
        "demos_root": str(demos_root),
        "vault_root": str(vault_root),
        "leaves": list(leaves),
        "min_age_hours": min_age_hours,
        "scanned": len(reports),
        "by_action": by_action,
        "by_reason": by_reason,
        "bytes_freed": total_bytes,
        "reports": reports,
    }

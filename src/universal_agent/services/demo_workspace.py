"""Demo workspace provisioning for Phase 3 (Cody implementation).

Copies the bundled scaffold templates into a target directory under
`/opt/ua_demos/<demo-id>/` so Cody has a clean, vanilla environment to
implement against. Verifies the resulting `.claude/settings.json` is
free of UA pollution before declaring the workspace ready.

PR 7 ships the provisioner + scaffold templates + smoke demo runtime.
The actual orchestration that picks demos to provision lives in PR 8
(Simone's cody-scaffold-builder skill).

See docs/proactive_signals/claudedevs_intel_v2_design.md §8.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
import json
import logging
import os
from pathlib import Path
import re
import shutil
from typing import Iterable

logger = logging.getLogger(__name__)


DEFAULT_DEMOS_ROOT = Path("/opt/ua_demos")
SCAFFOLD_TEMPLATE_PACKAGE = "universal_agent.templates"
SCAFFOLD_TEMPLATE_DIR = "ua_demos_scaffold"
SMOKE_TEMPLATE_DIR = "_smoke_demo"
SMOKE_DEMO_DIRNAME = "_smoke"

# Settings keys that, if present, indicate the workspace is NOT vanilla.
# A demo workspace must never carry ZAI mapping, hooks pointing at agent-flow,
# or experimental flags from UA's main settings.
POLLUTION_INDICATORS = (
    "env",
    "hooks",
    "enabledPlugins",
    "extraKnownMarketplaces",
    "plansDirectory",
    "skipDangerousModePermissionPrompt",
    "statusLine",
    "model",  # demo workspace should let CLI choose; UA pins opus[1m] which leaks
)


@dataclass(frozen=True)
class WorkspaceProvisionResult:
    workspace_dir: Path
    settings_path: Path
    files_written: tuple[Path, ...]
    is_smoke: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "workspace_dir": str(self.workspace_dir),
            "settings_path": str(self.settings_path),
            "files_written": [str(p) for p in self.files_written],
            "is_smoke": self.is_smoke,
        }


def demos_root() -> Path:
    """Configured demo root. Override via UA_DEMOS_ROOT for testing."""
    raw = str(os.getenv("UA_DEMOS_ROOT") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return DEFAULT_DEMOS_ROOT


def _safe_demo_id(value: str) -> str:
    """Slugify a demo id so it can't escape the demos root.

    Allowed: alphanumerics, underscore, dash, dot. Leading underscore is
    preserved so the special `_smoke` workspace name survives slugification.
    """
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "").strip())
    # Strip leading/trailing dashes and dots, but NOT underscores.
    cleaned = cleaned.strip("-.")
    if not cleaned:
        raise ValueError("demo_id must contain at least one alphanumeric character")
    if cleaned in {".", ".."}:
        raise ValueError(f"demo_id may not be {cleaned!r}")
    return cleaned


def workspace_path(demo_id: str, *, root: Path | None = None) -> Path:
    """Resolve the on-disk path for a demo id."""
    base = (root or demos_root()).resolve()
    safe = _safe_demo_id(demo_id)
    target = (base / safe).resolve()
    if base not in target.parents and target != base:
        raise ValueError(f"refusing to escape demos_root: {target}")
    return target


def _walk_template(root, prefix: str = "") -> Iterable[tuple[str, object]]:
    """Recursively yield (relative_path, traversable) for every file in `root`.

    `relative_path` uses forward-slash separators and is rooted at the template
    directory (e.g. '.claude/settings.json'). Works for any
    importlib.resources.Traversable, including filesystem and zipped
    backends.
    """
    for child in root.iterdir():
        rel = f"{prefix}{child.name}" if not prefix else f"{prefix}/{child.name}"
        if child.is_dir():
            yield from _walk_template(child, prefix=rel)
        elif child.is_file():
            yield rel, child


def _copy_template(template_dir: str, target: Path) -> list[Path]:
    """Copy a template directory's contents into target. Returns written paths."""
    target.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    pkg_root = resources.files(SCAFFOLD_TEMPLATE_PACKAGE).joinpath(template_dir)
    for rel, src in _walk_template(pkg_root):
        dst = target / Path(rel)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(src.read_bytes())
        written.append(dst)
    return written


def provision_demo_workspace(
    demo_id: str,
    *,
    root: Path | None = None,
    overwrite: bool = False,
) -> WorkspaceProvisionResult:
    """Create a new demo workspace under demos_root() / demo_id.

    Refuses to overwrite an existing workspace unless `overwrite=True`.
    Verifies the copied .claude/settings.json is vanilla after copy.
    """
    target = workspace_path(demo_id, root=root)
    if target.exists():
        if not overwrite:
            raise FileExistsError(
                f"demo workspace already exists at {target}. "
                "Pass overwrite=True if intentional."
            )
        shutil.rmtree(target)
    written = _copy_template(SCAFFOLD_TEMPLATE_DIR, target)
    settings_path = target / ".claude" / "settings.json"
    verify_vanilla_settings(settings_path)
    return WorkspaceProvisionResult(
        workspace_dir=target,
        settings_path=settings_path,
        files_written=tuple(written),
        is_smoke=False,
    )


def provision_smoke_workspace(*, root: Path | None = None) -> WorkspaceProvisionResult:
    """Provision the dedicated _smoke workspace used by Phase 0 upgrade gating."""
    target = workspace_path(SMOKE_DEMO_DIRNAME, root=root)
    if target.exists():
        shutil.rmtree(target)
    written = _copy_template(SMOKE_TEMPLATE_DIR, target)
    settings_path = target / ".claude" / "settings.json"
    verify_vanilla_settings(settings_path)
    return WorkspaceProvisionResult(
        workspace_dir=target,
        settings_path=settings_path,
        files_written=tuple(written),
        is_smoke=True,
    )


def verify_vanilla_settings(settings_path: Path) -> None:
    """Raise ValueError if a settings.json carries any UA pollution markers.

    This is the safety net that catches a future scaffold edit accidentally
    re-introducing the polluted env block, hooks chain, etc.
    """
    if not settings_path.exists():
        raise FileNotFoundError(f"settings.json missing at {settings_path}")
    raw = settings_path.read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"settings.json at {settings_path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"settings.json at {settings_path} must be a JSON object")
    found = [key for key in POLLUTION_INDICATORS if key in data]
    if found:
        raise ValueError(
            f"demo workspace settings.json carries pollution markers {found!r} "
            f"at {settings_path}; demo would not exercise real Anthropic endpoints. "
            "Remove these keys from the scaffold template."
        )

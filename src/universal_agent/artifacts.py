from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional


def repo_root() -> Path:
    """
    Resolve the repo root for this checkout.

    This module lives at: src/universal_agent/artifacts.py
    Repo root is:        ../../..
    """
    return Path(__file__).resolve().parent.parent.parent


def resolve_artifacts_dir() -> Path:
    """
    Return the configured artifacts root directory.

    Config: UA_ARTIFACTS_DIR
    Default: <repo-root>/artifacts
    """
    raw = (os.getenv("UA_ARTIFACTS_DIR") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()

    # Default durable root.
    root = repo_root()
    default_root = root / "artifacts"
    if default_root.exists() and default_root.is_dir():
        return default_root.resolve()

    # Backward-compat fallback only when default root does not exist.
    legacy = root / "UA_ARTIFACTS_DIR"
    if legacy.exists() and legacy.is_dir():
        return legacy.resolve()

    return default_root.resolve()


def ensure_artifacts_dir() -> Path:
    root = resolve_artifacts_dir()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _safe_slug_component(value: str) -> str:
    """
    Make a directory-friendly slug component (lossy, but predictable).
    """
    value = value.strip().lower()
    out = []
    last_was_sep = False
    for ch in value:
        is_alnum = ("a" <= ch <= "z") or ("0" <= ch <= "9")
        if is_alnum:
            out.append(ch)
            last_was_sep = False
            continue
        if not last_was_sep:
            out.append("-")
            last_was_sep = True
    slug = "".join(out).strip("-")
    return slug or "artifact"


@dataclass(frozen=True)
class ArtifactRun:
    skill_name: str
    run_dir: Path
    date: str  # YYYY-MM-DD
    hhmmss: str  # HHMMSS
    slug: str


def build_artifact_run_dir(
    *,
    skill_name: str,
    slug: str,
    now: Optional[datetime] = None,
    artifacts_root: Optional[Path] = None,
) -> ArtifactRun:
    """
    Build a new run directory path:
      <artifacts_root>/<skill_name>/<YYYY-MM-DD>/<slug>__<HHMMSS>/
    """
    now = now or datetime.now()
    date = now.strftime("%Y-%m-%d")
    hhmmss = now.strftime("%H%M%S")
    safe_skill = _safe_slug_component(skill_name)
    safe_slug = _safe_slug_component(slug)
    root = (artifacts_root or resolve_artifacts_dir()).resolve()
    run_dir = root / safe_skill / date / f"{safe_slug}__{hhmmss}"
    return ArtifactRun(skill_name=safe_skill, run_dir=run_dir, date=date, hhmmss=hhmmss, slug=safe_slug)

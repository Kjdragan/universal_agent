from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


_SAFE_SLUG_RE = re.compile(r"[^a-zA-Z0-9._-]+")
CURRENT_WORKSPACE_MARKER_FILENAMES = (
    ".current_run_workspace",
    ".current_session_workspace",
)


def safe_slug(text: str, *, fallback: str = "run", max_len: int = 80) -> str:
    s = (text or "").strip()
    if not s:
        return fallback
    s = _SAFE_SLUG_RE.sub("_", s).strip("._-")
    if not s:
        s = fallback
    return s[:max_len]


def resolve_current_run_workspace(repo_root: Optional[str] = None) -> Optional[str]:
    """
    Best-effort resolution for the *current* durable workspace directory.

    Priority:
    1. CURRENT_RUN_WORKSPACE / CURRENT_SESSION_WORKSPACE
    2. CURRENT_RUN_WORKSPACE_FILE / CURRENT_SESSION_WORKSPACE_FILE
    3. default marker under AGENT_RUN_WORKSPACES
    """
    candidates: list[str] = []

    for env_name in ("CURRENT_RUN_WORKSPACE", "CURRENT_SESSION_WORKSPACE"):
        ws = (os.getenv(env_name) or "").strip()
        if ws:
            candidates.append(ws)

    marker_candidates: list[str] = []
    for env_name in ("CURRENT_RUN_WORKSPACE_FILE", "CURRENT_SESSION_WORKSPACE_FILE"):
        marker = (os.getenv(env_name) or "").strip()
        if marker:
            marker_candidates.append(marker)
    if not marker_candidates:
        # Default marker paths used by the local toolkit server during migration.
        base = Path(repo_root or Path.cwd()).resolve()
        marker_candidates.extend(
            [
                str((base / "AGENT_RUN_WORKSPACES" / marker_name).resolve())
                for marker_name in CURRENT_WORKSPACE_MARKER_FILENAMES
            ]
        )

    for marker in marker_candidates:
        if marker and os.path.exists(marker):
            try:
                p = Path(marker).read_text(encoding="utf-8").strip()
                if p:
                    candidates.append(p)
            except Exception:
                pass

    for c in candidates:
        try:
            p = Path(c).expanduser()
            if not p.is_absolute():
                # If the harness wrote a relative path, interpret it relative to repo_root/cwd.
                base = Path(repo_root or Path.cwd()).resolve()
                p = (base / p).resolve()
            if p.exists() and p.is_dir():
                return str(p)
        except Exception:
            continue

    return None


def resolve_current_session_workspace(repo_root: Optional[str] = None) -> Optional[str]:
    """Backward-compatible alias for callers still using the legacy helper name."""
    return resolve_current_run_workspace(repo_root=repo_root)


@dataclass(frozen=True)
class InterimWorkProduct:
    base_dir: Path
    request_path: Path
    result_path: Path
    manifest_path: Path


def build_interim_work_product_paths(
    *,
    workspace_dir: str,
    domain: str,
    source: str,
    run_slug: str,
) -> InterimWorkProduct:
    """
    Returns canonical paths for interim work products under a run workspace.

    Layout:
      {workspace}/work_products/social/{source}/{domain}/{run_slug}__{YYYYMMDD_HHMMSS}/
        - request.json
        - result.json
        - manifest.json
    """
    import pytz
    houston_tz = pytz.timezone("America/Chicago")
    ts = datetime.now(houston_tz).strftime("%Y%m%d_%H%M%S")
    root = Path(workspace_dir) / "work_products" / "social" / safe_slug(source) / safe_slug(domain)
    run_dir = root / f"{safe_slug(run_slug)}__{ts}"
    return InterimWorkProduct(
        base_dir=run_dir,
        request_path=run_dir / "request.json",
        result_path=run_dir / "result.json",
        manifest_path=run_dir / "manifest.json",
    )


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")

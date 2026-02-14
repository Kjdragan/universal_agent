from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


_SAFE_SLUG_RE = re.compile(r"[^a-zA-Z0-9._-]+")


def safe_slug(text: str, *, fallback: str = "run", max_len: int = 80) -> str:
    s = (text or "").strip()
    if not s:
        return fallback
    s = _SAFE_SLUG_RE.sub("_", s).strip("._-")
    if not s:
        s = fallback
    return s[:max_len]


def resolve_current_session_workspace(repo_root: Optional[str] = None) -> Optional[str]:
    """
    Best-effort resolution for the *current* session workspace directory.

    Priority:
    1. CURRENT_SESSION_WORKSPACE (set by harness/gateway)
    2. CURRENT_SESSION_WORKSPACE_FILE (or default marker under AGENT_RUN_WORKSPACES)
    """
    candidates: list[str] = []

    ws = (os.getenv("CURRENT_SESSION_WORKSPACE") or "").strip()
    if ws:
        candidates.append(ws)

    marker = (os.getenv("CURRENT_SESSION_WORKSPACE_FILE") or "").strip()
    if not marker:
        # Default marker path used by the local toolkit server.
        base = Path(repo_root or Path.cwd()).resolve()
        marker = str((base / "AGENT_RUN_WORKSPACES" / ".current_session_workspace").resolve())

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
    Returns canonical paths for interim work products under a session workspace.

    Layout:
      {workspace}/work_products/social/{source}/{domain}/{run_slug}__{YYYYMMDD_HHMMSS}/
        - request.json
        - result.json
        - manifest.json
    """
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
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


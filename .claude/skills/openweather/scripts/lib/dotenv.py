"""Minimal .env loader (stdlib only)."""

from __future__ import annotations

import os
from pathlib import Path


def load_dotenv(path: Path) -> None:
    """Load KEY=VALUE pairs into os.environ without overriding existing keys."""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        key = k.strip()
        val = v.strip()
        if not key or key in os.environ:
            continue
        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
            val = val[1:-1]
        os.environ[key] = val


def load_repo_dotenv(start_file: Path) -> None:
    """Best-effort: walk up parents looking for a `.env` and load the first match."""
    for p in [start_file.resolve(), *start_file.resolve().parents]:
        candidate = p if p.name == ".env" else (p / ".env")
        if candidate.is_file():
            load_dotenv(candidate)
            return


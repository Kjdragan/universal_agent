"""Hacker News snapshot service — runs hackernews-pp-cli, builds latest.json."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import subprocess
import time
from typing import Any

import yaml

from universal_agent.artifacts import resolve_artifacts_dir

logger = logging.getLogger(__name__)

# Binary lives in $HOME/.local/bin/ — outside the repo tree, so it survives
# `git clean`, deploys, and other repo-resetting operations. See issue #179
# and docs/operations/2026-05-09_ship_pollution_and_phase1_followups.md for
# the failure mode that drove this relocation. The deploy workflow
# (.github/workflows/deploy.yml) ensures the binary is present on every
# deploy via `bash scripts/install_hackernews_cli.sh`.
CLI_BINARY = Path.home() / ".local" / "bin" / "hackernews-pp-cli"
# hackernews-pp-cli v1.0.0 derives its config + SQLite paths from $HOME and
# does NOT honor XDG_CONFIG_HOME / XDG_DATA_HOME (verified empirically). We
# override $HOME so the CLI's `~/.config` and `~/.local/share` resolve into
# the project tree at /opt/universal_agent/var/hackernews/.
CLI_HOME = Path("/opt/universal_agent/var/hackernews")
WATCHLIST_FILE = Path("/opt/universal_agent/config/hackernews_watchlist.yaml")
SNAPSHOT_RING_DEPTH = 48
DEFAULT_TOPICS = ["claude", "agent", "codex", "llm", "harness", "agentic"]


def _cli_env() -> dict[str, str]:
    env = os.environ.copy()
    env["HOME"] = str(CLI_HOME)
    env["HACKERNEWS_NO_COLOR"] = "1"
    return env


def _run_cli(args: list[str], timeout: int = 60) -> dict[str, Any] | list[Any] | None:
    cmd = [str(CLI_BINARY), *args, "--json", "--agent"]
    try:
        r = subprocess.run(
            cmd,
            env=_cli_env(),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        logger.warning("hackernews CLI timeout: %s", " ".join(args))
        return None
    except FileNotFoundError:
        logger.warning("hackernews CLI binary missing at %s", CLI_BINARY)
        return None
    if r.returncode != 0:
        logger.warning(
            "hackernews CLI exit=%d args=%s stderr=%s",
            r.returncode,
            args,
            r.stderr[:500],
        )
        return None
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError as e:
        logger.warning("hackernews CLI bad JSON: %s", e)
        return None


def _load_watchlist() -> list[str]:
    if not WATCHLIST_FILE.exists():
        return list(DEFAULT_TOPICS)
    try:
        data = yaml.safe_load(WATCHLIST_FILE.read_text())
        topics = data.get("topics") if isinstance(data, dict) else None
        if isinstance(topics, list) and 1 <= len(topics) <= 6:
            cleaned = [str(t).strip() for t in topics if str(t).strip()]
            if cleaned:
                return cleaned
    except (OSError, yaml.YAMLError) as e:
        logger.warning("watchlist load failed: %s — using defaults", e)
    return list(DEFAULT_TOPICS)


def build_snapshot() -> dict[str, Any]:
    started = time.monotonic()
    errors: list[str] = []

    sync_result = _run_cli(["sync", "--resources", "updates"], timeout=180)
    if sync_result is None:
        raise RuntimeError("sync failed — aborting tick, latest.json untouched")

    topics = _load_watchlist()

    def safe(name: str, args: list[str], timeout: int = 60):
        result = _run_cli(args, timeout=timeout)
        if result is None:
            errors.append(name)
        return result

    snapshot: dict[str, Any] = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "schema_version": 1,
            "watchlist": topics,
            "errors": errors,
            "duration_seconds": None,
        },
        "top_stories": safe("top_stories", ["stories", "top", "--limit", "50"]),
        "movers": safe("movers", ["since", "--list", "topstories"]),
        "controversial": safe(
            "controversial",
            ["controversial", "--window", "7d", "--min-comments", "100"],
        ),
        "pulses": {
            t: safe(f"pulse_{t}", ["pulse", t, "--days", "7"])
            for t in topics
        },
        "show_hn": safe("show_hn", ["stories", "show", "--limit", "5"]),
        "ask_hn": safe("ask_hn", ["stories", "ask", "--limit", "5"]),
        "hiring": safe("hiring", ["hiring", "stats", "--months", "3"], timeout=90),
    }

    panel_results = [
        snapshot["top_stories"],
        snapshot["movers"],
        snapshot["controversial"],
        snapshot["show_hn"],
        snapshot["ask_hn"],
        snapshot["hiring"],
    ]
    panel_results.extend(snapshot["pulses"].values())
    if all(r is None for r in panel_results):
        raise RuntimeError("all panels failed — aborting tick, latest.json untouched")

    snapshot["meta"]["errors"] = errors
    snapshot["meta"]["duration_seconds"] = round(time.monotonic() - started, 2)
    return snapshot


def write_snapshot(snapshot: dict[str, Any]) -> Path:
    root = resolve_artifacts_dir() / "hackernews"
    snaps = root / "snapshots"
    root.mkdir(parents=True, exist_ok=True)
    snaps.mkdir(parents=True, exist_ok=True)

    latest = root / "latest.json"
    ts = (
        snapshot["meta"]["generated_at"]
        .replace(":", "")
        .replace("-", "")
    )
    archived = snaps / f"{ts}.json"

    payload = json.dumps(snapshot, indent=2, ensure_ascii=False)
    latest.write_text(payload)
    archived.write_text(payload)

    files = sorted(snaps.glob("*.json"), reverse=True)
    for stale in files[SNAPSHOT_RING_DEPTH:]:
        stale.unlink(missing_ok=True)

    return latest


def read_latest() -> dict[str, Any] | None:
    latest = resolve_artifacts_dir() / "hackernews" / "latest.json"
    if not latest.exists():
        return None
    try:
        return json.loads(latest.read_text())
    except (OSError, json.JSONDecodeError):
        return None

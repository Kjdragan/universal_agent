"""Hacker News snapshot service — runs hackernews-pp-cli, builds latest.json.

Phase 1 frontend (`web-ui/app/dashboard/hackernews/page.tsx`) is typed against a
normalized "consumer" shape that does NOT match the raw `hackernews-pp-cli`
JSON output. The CLI returns ID lists for `stories top|show|ask`, a
diff-flavored `since` payload for movers, a `total_hits`-shaped pulse object,
and a `top_companies/languages/remote_percent` hiring blob. The page expects
hydrated `Story[]`, a flat `{since, changes[]}` movers shape, pulses with
`{count, avg_points, trend, pct_change}`, and hiring as `{companies[]}`. Without
normalization the page calls `.slice` on a `{meta, results}` dict, throws, and
the Next.js error boundary blanks the tab.

`build_snapshot()` therefore runs the CLI, then `_normalize()` reshapes each
panel into the frontend contract before `write_snapshot()` persists it.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
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


def _hydrate_story(item_id: int | str) -> dict[str, Any] | None:
    """Fetch a single story by ID. Returns the bare story dict (id/title/url/...)."""
    result = _run_cli(["items", str(item_id)])
    if not isinstance(result, dict):
        return None
    payload = result.get("results")
    if isinstance(payload, dict) and payload.get("id") is not None:
        return payload
    return None


def _hydrate_stories(item_ids: list[Any], max_workers: int = 8) -> list[dict[str, Any]]:
    """Hydrate a list of story IDs in parallel, preserving input order. Drops failures."""
    if not item_ids:
        return []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        results = list(pool.map(_hydrate_story, item_ids))
    return [r for r in results if r is not None]


def _normalize_top_like(
    raw: dict[str, Any] | list[Any] | None, limit: int
) -> list[dict[str, Any]] | None:
    """Hydrate the first `limit` IDs from a `stories top|show|ask` response."""
    if raw is None:
        return None
    if isinstance(raw, list):
        ids = raw
    elif isinstance(raw, dict):
        ids = raw.get("results") or []
    else:
        return None
    if not isinstance(ids, list):
        return []
    # If the CLI ever returns hydrated dicts (some commands do), pass them through.
    if ids and isinstance(ids[0], dict):
        return ids[:limit]
    return _hydrate_stories(ids[:limit])


def _normalize_movers(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    """Reshape `since` diff into `{since, changes[]}` for the Movers panel."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        return {"since": None, "changes": []}

    since = raw.get("previous_taken_at") or raw.get("current_taken_at")

    added_ids = [i for i in (raw.get("added") or []) if i is not None]
    removed_ids = [i for i in (raw.get("removed") or []) if i is not None]
    moved = [m for m in (raw.get("moved") or []) if isinstance(m, dict)]

    moved_ids = [m.get("id") for m in moved if m.get("id") is not None]
    all_ids = list(added_ids) + list(removed_ids) + list(moved_ids)
    hydrated = {
        str(s.get("id")): s
        for s in _hydrate_stories(all_ids)
        if s.get("id") is not None
    }

    changes: list[dict[str, Any]] = []
    for item_id in added_ids:
        s = hydrated.get(str(item_id), {})
        changes.append(
            {
                "id": s.get("id") or item_id,
                "title": s.get("title") or f"#{item_id}",
                "status": "new",
                "rank": 0,
                "score": s.get("score", 0),
                "delta": 0,
            }
        )
    for m in moved:
        item_id = m.get("id")
        s = hydrated.get(str(item_id), {})
        from_rank = int(m.get("from_rank") or 0)
        to_rank = int(m.get("to_rank") or 0)
        changes.append(
            {
                "id": s.get("id") or item_id,
                "title": s.get("title") or f"#{item_id}",
                "status": "moved",
                "rank": to_rank,
                "score": s.get("score", 0),
                "delta": from_rank - to_rank,  # positive = climbed
            }
        )
    for item_id in removed_ids:
        s = hydrated.get(str(item_id), {})
        changes.append(
            {
                "id": s.get("id") or item_id,
                "title": s.get("title") or f"#{item_id}",
                "status": "dropped",
                "rank": 0,
                "score": s.get("score", 0),
                "delta": 0,
            }
        )

    return {"since": since, "changes": changes}


def _normalize_pulse(raw: dict[str, Any] | None, topic: str) -> dict[str, Any] | None:
    """Map CLI pulse output to the panel's `{count, avg_points, trend, pct_change}` shape."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        return {"topic": topic, "count": 0, "avg_points": 0, "trend": [], "pct_change": 0}
    top_stories = raw.get("top_stories") or []
    points = [s.get("points", 0) for s in top_stories if isinstance(s, dict)]
    avg_points = round(sum(points) / len(points)) if points else 0
    # Phase 1: no historical series yet, so trend stays empty and pct_change is zero.
    # Phase 2 will compute trend from the snapshot ring buffer.
    return {
        "topic": raw.get("topic") or topic,
        "count": int(raw.get("total_hits") or 0),
        "avg_points": avg_points,
        "trend": [],
        "pct_change": 0,
    }


def _normalize_hiring(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    """Reshape hiring stats into `{companies[{name, months}]}` for the panel."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        return {"companies": []}
    top = raw.get("top_companies") or []
    companies = [
        {"name": c.get("name", ""), "months": int(c.get("count", 0))}
        for c in top
        if isinstance(c, dict) and c.get("name")
    ]
    return {"companies": companies[:5]}


def _normalize_controversial(
    raw: list[Any] | dict[str, Any] | None,
) -> list[dict[str, Any]] | None:
    """Pass-through with light coercion — `controversial` already returns Story-like dicts."""
    if raw is None:
        return None
    if isinstance(raw, dict):
        raw = raw.get("results") or []
    if not isinstance(raw, list):
        return []
    return [s for s in raw if isinstance(s, dict)]


def _normalize(raw: dict[str, Any]) -> dict[str, Any]:
    """Reshape raw CLI output into the contract `page.tsx` is typed against.

    The frontend needs:
      top_stories | show_hn | ask_hn  -> Story[]
      controversial                   -> Story[]
      movers                          -> {since, changes: [{id, title, status, rank, score, delta}]}
      pulses[topic]                   -> {topic, count, avg_points, trend, pct_change}
      hiring                          -> {companies: [{name, months}]}
    """
    return {
        "meta": raw["meta"],
        "top_stories": _normalize_top_like(raw.get("top_stories"), limit=10),
        "show_hn": _normalize_top_like(raw.get("show_hn"), limit=5),
        "ask_hn": _normalize_top_like(raw.get("ask_hn"), limit=5),
        "controversial": _normalize_controversial(raw.get("controversial")),
        "movers": _normalize_movers(raw.get("movers")),
        "pulses": {
            topic: _normalize_pulse(raw.get("pulses", {}).get(topic), topic)
            for topic in raw.get("pulses", {})
        },
        "hiring": _normalize_hiring(raw.get("hiring")),
    }


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

    raw: dict[str, Any] = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "schema_version": 2,
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
        raw["top_stories"],
        raw["movers"],
        raw["controversial"],
        raw["show_hn"],
        raw["ask_hn"],
        raw["hiring"],
    ]
    panel_results.extend(raw["pulses"].values())
    if all(r is None for r in panel_results):
        raise RuntimeError("all panels failed — aborting tick, latest.json untouched")

    snapshot = _normalize(raw)
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

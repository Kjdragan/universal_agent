#!/usr/bin/env python3
"""Headless community-evidence sweep over the ``last30days`` skill engine.

A stable, machine-consumable front door to the third-party ``last30days``
research engine (``.claude/skills/last30days``, v3.3.2, MIT/mvanhorn). Give
it a topic, get back a small, versioned JSON evidence record:

    python -m universal_agent.scripts.last30days_sweep "MCP server security" \
        --lookback-days 30 --max-items 12 --out sweep.json

Designed for the episode_ideas showrunner's per-candidate validation sweep
(its seed's D-sources #3) and any other consumer that wants "what is the
community actually saying about X" as data rather than prose. The engine's
own interactive contract (badge + output LAWs) governs *agent-authored*
runs; this wrapper is the opposite path — no LLM, no synthesis, just
evidence.

Three properties the consumers depend on:

1. **Deterministic + free by default.** No ``--plan`` is passed, so the
   engine takes its documented headless/cron path (``planner.py`` falls back
   to the deterministic keyword planner — no LLM call, no key). The default
   source set is the zero-key trio (reddit / hackernews / polymarket); any
   further source must be requested explicitly via ``--sources`` AND have
   its key granted via ``--grant`` (below), so a sweep can never silently
   start costing money.
2. **Least privilege at the trust boundary.** The engine is third-party code
   we did not author, so it is NOT handed our environment (CLAUDE.md's
   subprocess rule). It gets a minimal operational allow-list plus ONLY the
   secrets named in ``--grant`` (default: none). ``LAST30DAYS_CONFIG_DIR=""``
   additionally puts the engine in its documented no-config mode so a
   stray key in ``~/.config/last30days/.env`` cannot leak a paid source
   into an unattended run.
3. **A wall-clock bound.** The engine has per-request timeouts but no global
   one, so this wrapper owns the deadline and reports a timeout as a
   classified, non-crashing result.

Task Hub Observability Protocol — nested-child pattern
------------------------------------------------------
This wrapper spawns real work (the engine) but owns no task row: it is
invoked *inside* an already-tracked unit of work (an agent turn, or a
showrunner lane that owns its own state). Per the nested-child pattern in
``project_docs/02_execution_core/02_task_hub.md``, it therefore classifies
its child's termination with
``services/worker_exit_classifier.py::classify_worker_exit`` and records the
classification in its own output (``worker_exit``) + stderr, but never opens
or closes ``task_hub`` run rows — a nested child doing so would steal the
parent's close and double-book the ledger. ``task_closed_normally=True`` is
passed deliberately: disposition belongs to the caller, and a child must
never manufacture a protocol violation for a task it does not own.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Optional, Sequence

from universal_agent.services.worker_exit_classifier import (
    WorkerExit,
    classify_worker_exit,
)

SCHEMA_VERSION = 1

# Sources that need no key and no external binary (engine's
# pipeline.available_sources: reddit_public / hackernews / polymarket are
# unconditionally available). The safe default for an unattended sweep.
FREE_SOURCES: tuple[str, ...] = ("reddit", "hackernews", "polymarket")

# Secrets a caller may explicitly grant to the engine, mapped to what each
# unlocks. Anything not listed here is never forwarded.
GRANTABLE_SECRETS: dict[str, str] = {
    "XAI_API_KEY": "x",
    "GITHUB_TOKEN": "github",
    "BRAVE_API_KEY": "web-grounding",
    "EXA_API_KEY": "web-grounding",
    "SERPER_API_KEY": "web-grounding",
    "PARALLEL_API_KEY": "web-grounding",
    "SCRAPECREATORS_API_KEY": "tiktok/instagram/threads/pinterest (PAID)",
    "OPENROUTER_API_KEY": "perplexity/deep-research (PAID)",
}

# Non-secret operational vars the child legitimately needs.
_ENV_PASSTHROUGH: tuple[str, ...] = (
    "PATH", "HOME", "LANG", "LANGUAGE", "TZ", "TERM",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
    "TMPDIR",
)

DEFAULT_TIMEOUT_SECONDS = 600
DEFAULT_MAX_ITEMS = 12


@dataclass
class SweepResult:
    """The wrapper's stable output contract (schema_version 1)."""

    topic: str
    ok: bool
    generated_at: str
    schema_version: int = SCHEMA_VERSION
    lookback_days: int = 30
    sources_requested: list[str] = field(default_factory=list)
    sources_with_evidence: list[str] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    counts_by_source: dict[str, int] = field(default_factory=dict)
    engine_errors: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    worker_exit: dict[str, Any] = field(default_factory=dict)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "topic": self.topic,
            "ok": self.ok,
            "generated_at": self.generated_at,
            "lookback_days": self.lookback_days,
            "sources_requested": self.sources_requested,
            "sources_with_evidence": self.sources_with_evidence,
            "counts_by_source": self.counts_by_source,
            "evidence": self.evidence,
            "engine_errors": self.engine_errors,
            "warnings": self.warnings,
            "worker_exit": self.worker_exit,
            "error": self.error,
        }


def default_engine_path() -> Path:
    """The in-repo engine: ``.claude/skills/last30days/scripts/last30days.py``."""
    repo_root = Path(__file__).resolve().parents[3]
    return repo_root / ".claude" / "skills" / "last30days" / "scripts" / "last30days.py"


def build_engine_env(grants: Sequence[str], *, use_user_config: bool) -> dict[str, str]:
    """Minimal environment for the third-party engine subprocess.

    Operational vars only, plus explicitly granted secrets. Unknown grant
    names are rejected by the caller (see :func:`main`) rather than silently
    forwarding an arbitrary variable.
    """
    env = {k: os.environ[k] for k in _ENV_PASSTHROUGH if k in os.environ}
    for name in grants:
        value = os.environ.get(name)
        if value:
            env[name] = value
    # Engine's documented no-config mode: ignore ~/.config/last30days/.env so
    # an unattended sweep cannot pick up a paid-source key we did not grant.
    if not use_user_config:
        env["LAST30DAYS_CONFIG_DIR"] = ""
    env.setdefault("PYTHONUNBUFFERED", "1")
    return env


def _engagement_total(engagement: Any) -> int:
    """Sum the numeric fields of an engine item's engagement dict."""
    if not isinstance(engagement, dict):
        return 0
    total = 0
    for value in engagement.values():
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            total += int(value)
    return total


def _compress_item(item: dict[str, Any], source: str) -> dict[str, Any]:
    """One engine SourceItem -> one compact evidence row."""
    engagement = item.get("engagement") if isinstance(item.get("engagement"), dict) else {}
    snippet = str(item.get("snippet") or item.get("body") or "")[:400]
    return {
        "source": source,
        "title": str(item.get("title") or "")[:300],
        "url": item.get("url") or "",
        "author": item.get("author") or "",
        "container": item.get("container") or "",
        "published_at": item.get("published_at") or "",
        "date_confidence": item.get("date_confidence") or "",
        "engagement": engagement,
        "engagement_total": _engagement_total(engagement),
        "snippet": snippet,
    }


def summarize_report(report: dict[str, Any], *, max_items: int) -> dict[str, Any]:
    """Compress an engine ``--emit json`` Report into the wrapper contract.

    Ranking is by the engine's own engagement signal (its ``engagement_total``
    across sources), NOT by any judgment of this wrapper — consumers do their
    own scoring. Tolerant of shape drift: unknown/missing fields degrade to
    empty rather than raising, because a partial evidence record is more
    useful to a caller than an exception.
    """
    items_by_source = report.get("items_by_source")
    items_by_source = items_by_source if isinstance(items_by_source, dict) else {}

    evidence: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for source, items in items_by_source.items():
        if not isinstance(items, list):
            continue
        counts[source] = len(items)
        for item in items:
            if isinstance(item, dict):
                evidence.append(_compress_item(item, source))

    evidence.sort(key=lambda row: row["engagement_total"], reverse=True)
    trimmed = evidence[: max(1, max_items)]

    errors = report.get("errors_by_source")
    warnings = report.get("warnings")
    return {
        "sources_with_evidence": sorted(s for s, n in counts.items() if n),
        "counts_by_source": counts,
        "evidence": trimmed,
        "engine_errors": errors if isinstance(errors, dict) else {},
        "warnings": [str(w) for w in warnings] if isinstance(warnings, list) else [],
    }


def run_sweep(
    topic: str,
    *,
    engine_path: Path,
    sources: Sequence[str] = FREE_SOURCES,
    lookback_days: int = 30,
    max_items: int = DEFAULT_MAX_ITEMS,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    grants: Sequence[str] = (),
    depth: str = "",
    use_user_config: bool = False,
    runner: Optional[Any] = None,
) -> SweepResult:
    """Run one evidence sweep and return the compressed record.

    ``runner`` is injectable for tests: a callable ``(cmd, env, timeout) ->
    (returncode, stdout, stderr)``. The default spawns the engine.
    """
    now = datetime.now(timezone.utc).isoformat()
    result = SweepResult(
        topic=topic,
        ok=False,
        generated_at=now,
        lookback_days=lookback_days,
        sources_requested=list(sources),
    )

    cmd = [
        sys.executable,
        str(engine_path),
        topic,
        "--emit", "json",
        "--lookback-days", str(lookback_days),
    ]
    if sources:
        cmd += ["--search", ",".join(sources)]
    if depth == "quick":
        cmd.append("--quick")
    elif depth == "deep":
        cmd.append("--deep")

    env = build_engine_env(grants, use_user_config=use_user_config)
    exec_runner = runner or _spawn_engine

    try:
        rc, stdout, stderr = exec_runner(cmd, env, timeout_seconds)
    except TimeoutError:
        result.worker_exit = classify_worker_exit(
            return_code=None, was_timeout_killed=True
        ).to_dict()
        result.error = f"engine timed out after {timeout_seconds}s"
        return result
    except OSError as exc:
        result.worker_exit = classify_worker_exit(return_code=None).to_dict()
        result.error = f"failed to spawn engine: {exc}"
        return result

    classification: WorkerExit = classify_worker_exit(
        return_code=rc, was_signaled=rc is not None and rc < 0
    )
    result.worker_exit = classification.to_dict()

    if rc != 0:
        result.error = f"engine exited {rc}: {(stderr or '')[-500:]}"
        return result

    try:
        report = json.loads(stdout)
    except ValueError as exc:
        result.error = f"engine emitted unparseable JSON: {exc}"
        return result
    if not isinstance(report, dict):
        result.error = "engine JSON was not an object"
        return result

    summary = summarize_report(report, max_items=max_items)
    result.sources_with_evidence = summary["sources_with_evidence"]
    result.counts_by_source = summary["counts_by_source"]
    result.evidence = summary["evidence"]
    result.engine_errors = summary["engine_errors"]
    result.warnings = summary["warnings"]
    # "ok" means the sweep ran AND produced at least one piece of evidence —
    # a clean exit with zero evidence is a real (reportable) outcome, not a
    # success, so a caller can distinguish "nobody is talking about this"
    # from "we looked and here is what they said".
    result.ok = bool(result.evidence)
    if not result.evidence and not result.error:
        result.error = "engine returned no evidence items"
    return result


def _spawn_engine(
    cmd: Sequence[str], env: dict[str, str], timeout_seconds: int
) -> tuple[int, str, str]:
    """Spawn the engine with a wall-clock bound; raise TimeoutError on expiry."""
    try:
        proc = subprocess.run(
            list(cmd),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(str(exc)) from exc
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Headless community-evidence sweep via the last30days engine. "
        "Emits a compact, versioned JSON evidence record on stdout.",
    )
    p.add_argument("topic", help="Topic to sweep (free text).")
    p.add_argument(
        "--sources",
        default=",".join(FREE_SOURCES),
        help=f"Comma-separated engine sources (default: {','.join(FREE_SOURCES)} — all key-free).",
    )
    p.add_argument("--lookback-days", type=int, default=30, help="Window in days (default 30).")
    p.add_argument(
        "--max-items", type=int, default=DEFAULT_MAX_ITEMS,
        help=f"Max evidence rows to emit, ranked by engagement (default {DEFAULT_MAX_ITEMS}).",
    )
    p.add_argument(
        "--timeout-seconds", type=int, default=DEFAULT_TIMEOUT_SECONDS,
        help=f"Wall-clock bound for the engine (default {DEFAULT_TIMEOUT_SECONDS}).",
    )
    p.add_argument(
        "--grant", action="append", default=[], metavar="ENV_VAR",
        help="Forward this secret to the engine (repeatable). Allowed: "
        + ", ".join(sorted(GRANTABLE_SECRETS)),
    )
    p.add_argument("--depth", choices=["quick", "deep"], default="", help="Engine depth flag.")
    p.add_argument(
        "--use-user-config", action="store_true",
        help="Let the engine read ~/.config/last30days/.env (default: no-config mode).",
    )
    p.add_argument("--engine", default="", help="Override the engine script path.")
    p.add_argument("--out", default="", help="Also write the JSON record to this file.")
    args = p.parse_args(argv)

    bad = [g for g in args.grant if g not in GRANTABLE_SECRETS]
    if bad:
        print(
            f"refusing unknown grant(s): {', '.join(bad)}; allowed: "
            + ", ".join(sorted(GRANTABLE_SECRETS)),
            file=sys.stderr,
        )
        return 2

    engine_path = Path(args.engine).resolve() if args.engine else default_engine_path()
    if not engine_path.exists():
        print(f"engine not found at {engine_path}", file=sys.stderr)
        return 2

    sources = [s.strip() for s in (args.sources or "").split(",") if s.strip()]
    result = run_sweep(
        args.topic,
        engine_path=engine_path,
        sources=sources,
        lookback_days=args.lookback_days,
        max_items=args.max_items,
        timeout_seconds=args.timeout_seconds,
        grants=args.grant,
        depth=args.depth,
        use_user_config=args.use_user_config,
    )

    payload = json.dumps(result.to_dict(), indent=2, sort_keys=True)
    print(payload)
    if args.out:
        Path(args.out).write_text(payload + "\n", encoding="utf-8")

    outcome = result.worker_exit.get("outcome", "?")
    print(
        f"last30days_sweep: ok={result.ok} sources={result.sources_with_evidence} "
        f"items={len(result.evidence)} worker_exit={outcome} {result.error}".rstrip(),
        file=sys.stderr,
    )
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

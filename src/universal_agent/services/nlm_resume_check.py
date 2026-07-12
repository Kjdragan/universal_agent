"""Deterministic verdict for the paper-to-podcast `.nlm_resume.json` checkpoint.

The deploy-restart resume decision (adopt vs start fresh) used to live as
prose in the cron prompt and the paper-to-podcast-tf skill, interpreted by
the agent each run — and repeatedly misapplied (skill-gap finder, 2026-07-11:
16 occurrences). This module owns the decision. The cron prompt's first
instruction is to run it and obey the single verdict line it prints:

    RESUME: adopt notebook <id> (topic: '...', status: polling, started 2.4h ago). ...
    FRESH: <reason> — start a new notebook with today's topic.

The agent still WRITES the checkpoint (that part is mechanical and requires
run-time knowledge only the agent has); reading/deciding is deterministic
Python. Checkpoint shape, per the skill's "Deploy-restart resume" section:
{"notebook_id", "topic", "run_started_at" (epoch seconds), "status"} with
status creating -> polling -> done. See
project_docs/06_platform/13_resumable_external_jobs_adr.md.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

CHECKPOINT_NAME = ".nlm_resume.json"
MAX_AGE_SECONDS = 24 * 3600


def verdict(workspace: Path, now: float | None = None) -> str:
    """Return the one-line resume verdict for the given workspace root."""
    now = time.time() if now is None else now
    path = workspace / CHECKPOINT_NAME
    if not path.exists():
        return "FRESH: no .nlm_resume.json checkpoint — start a new notebook with today's topic."
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("checkpoint is not a JSON object")
    except Exception:  # noqa: BLE001 — any unreadable checkpoint means fresh
        return (
            "FRESH: checkpoint exists but is unreadable (corrupt JSON) — delete "
            ".nlm_resume.json and start a new notebook with today's topic."
        )
    status = str(data.get("status") or "")
    if status == "done":
        return (
            "FRESH: checkpoint status=done (prior run completed cleanly) — delete "
            ".nlm_resume.json and start a new notebook with today's topic."
        )
    try:
        age = now - float(data.get("run_started_at") or 0)
    except (TypeError, ValueError):
        age = float("inf")
    if age > MAX_AGE_SECONDS or age < 0:
        return (
            f"FRESH: checkpoint is stale (started {age / 3600:.1f}h ago, limit 24h) — "
            "delete .nlm_resume.json and start a new notebook with today's topic."
        )
    notebook_id = data.get("notebook_id") or "?"
    topic = data.get("topic") or "?"
    return (
        f"RESUME: adopt notebook {notebook_id} (topic: {topic!r}, status: {status or '?'}, "
        f"started {age / 3600:.1f}h ago). Do NOT create a new notebook and do NOT use "
        f"today's rotated topic — finish THIS podcast per the skill's Phase B.0 resume "
        f"steps: verify auth, `nlm studio status {notebook_id} --json`, then poll/download/"
        f"package/email for topic {topic!r}."
    )


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    workspace = Path(args[0]) if args else Path.cwd()
    print(verdict(workspace))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

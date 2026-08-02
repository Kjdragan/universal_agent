#!/usr/bin/env python3
"""Daily health report for ``universal-agent-proactive-demo-nuggets.service``.

WHY THIS EXISTS. The 2026-08-01/02 forensics left three questions that can only
be answered by observing a *future* run, and a closed interactive session cannot
observe anything. This turns those one-off checks into standing monitoring:

  1. Did #1587's process-group reap actually stop the orphan leak?
     -> ``CGPM leftover_procs=`` from the unit's ExecStopPost post-mortem.
  2. Did removing MemoryHigh (#1588) stop the throttling, and does MemoryMax=3G
     hold now that orphaned build trees are reaped?
     -> ``CGPM memory.events high`` and ``CGPM memory.peak``.
  3. THE BIG ONE — the Gemini fidelity gate has rejected 100% of builds since
     2026-07-18. Every build passes the deterministic verifier and is then voted
     down (0/2, 1/3, 0/3, ...). Until that is fixed the job burns 1-2h of
     inference nightly and lands nothing.
     -> ``days_since_last_land`` from demo_factory's build_stats.jsonl.

Severity is chosen so the report is quiet when healthy and loud when not, and so
(3) keeps surfacing every single day until somebody fixes it rather than being
forgotten in a closed session's transcript.

Notification path mirrors ``scripts/watchdog_oom_notifier.py`` exactly: POST to
the gateway ops-notifications endpoint with ``x-ua-ops-token``.

Run manually:  /opt/universal_agent/.venv/bin/python3 scripts/nuggets_health_report.py --dry-run
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any
import urllib.error
import urllib.request

UNIT = "universal-agent-proactive-demo-nuggets.service"
ENDPOINT = (
    os.getenv("UA_NUGGETS_REPORT_ENDPOINT")
    or os.getenv("UA_OOM_ALERT_ENDPOINT")
    or "http://127.0.0.1:8002/api/v1/ops/notifications"
).strip()
BUILD_STATS = Path(
    os.getenv("UA_DEMO_FACTORY_BUILD_STATS", "/home/ua/lrepos/demo_factory/lessons/build_stats.jsonl")
)
# Days with no successful land before we escalate. The gate broke 2026-07-18; a
# healthy factory lands something most nights, so 3 quiet days is already wrong.
STALE_LAND_DAYS = int(os.getenv("UA_NUGGETS_STALE_LAND_DAYS", "3") or "3")


def _run(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=60).stdout
    except Exception:
        return ""


def _bootstrap_secrets() -> None:
    """Load UA_OPS_TOKEN from Infisical — as a standalone oneshot this process
    has no gateway parent to inherit it from (same reasoning as the OOM notifier)."""
    if os.getenv("UA_OPS_TOKEN"):
        return
    try:
        import sys

        sys.path.insert(0, "/opt/universal_agent/src")
        from universal_agent.infisical_loader import initialize_runtime_secrets

        initialize_runtime_secrets(profile="vps")
    except Exception:
        pass


# ── journal parsing ───────────────────────────────────────────────────────────
def _journal(since: str) -> str:
    """Read the nuggets unit journal, escalating to sudo if needed.

    LOAD BEARING: an unprivileged `ua` gets the service's own stdout/stderr
    (the `python[...]` app lines) but NOT the `systemd[1]` lifecycle lines —
    Starting / Deactivated / Failed with result / Consumed. Without those the
    report cannot see whether the run fired or how it ended, and it wrongly
    reports "DID NOT FIRE" on a perfectly good night (caught in live testing
    before this ever shipped). `ua` has passwordless sudo on the VPS; this is a
    read-only escalation. Plain read first so the script still works anywhere
    sudo is unavailable.
    """
    args = ["-u", UNIT, "--since", since, "--no-pager", "-o", "short-iso"]
    plain = _run(["journalctl", *args])
    if "systemd[1]" in plain:
        return plain
    elevated = _run(["sudo", "-n", "journalctl", *args])
    return elevated if "systemd[1]" in elevated else plain


def _parse_run(text: str) -> dict[str, Any]:
    """Extract the last run's outcome, build tallies and CGPM post-mortem."""
    out: dict[str, Any] = {
        "started": None,
        "result": None,
        "consumed": None,
        "builds_attempted": 0,
        "builds_rc3": 0,
        "builds_timeout": 0,
        "builds_ok": 0,
        "swept": None,
        "cgpm": {},
        "leftover": [],
    }
    for line in text.splitlines():
        if "Starting " in line and "systemd[1]" in line:
            out["started"] = line[:19]
        m = re.search(r"Failed with result '([^']+)'", line)
        if m:
            out["result"] = m.group(1)
        if "Deactivated successfully" in line:
            out["result"] = out["result"] or "success"
        m = re.search(r"Consumed ([^,]+(?:,[^,]+)*)\.", line)
        if m and "CPU time" in line:
            out["consumed"] = m.group(1).strip()
        if "nuggets: building" in line:
            out["builds_attempted"] += 1
        if "build TIMEOUT" in line:
            out["builds_timeout"] += 1
        if re.search(r"build FAILED rc=3", line):
            out["builds_rc3"] += 1
        m = re.search(r"nuggets swipe: cancelled (\d+)", line)
        if m:
            out["swept"] = int(m.group(1))
        # CGPM post-mortem lines (added by the unit's ExecStopPost)
        m = re.search(r"CGPM (memory\.events(?:\.local)? \w+) (\d+)", line)
        if m:
            out["cgpm"][m.group(1)] = int(m.group(2))
        m = re.search(r"CGPM (memory\.peak|memory\.swap\.peak|leftover_procs)=(\S+)", line)
        if m:
            out["cgpm"][m.group(1)] = m.group(2)
        m = re.search(r"CGPM leftover pid=(\d+) comm=(\S+)", line)
        if m:
            out["leftover"].append(f"{m.group(2)}({m.group(1)})")
    out["builds_ok"] = max(
        0, out["builds_attempted"] - out["builds_rc3"] - out["builds_timeout"]
    )
    return out


# ── fidelity-gate tracking ────────────────────────────────────────────────────
def _land_history() -> dict[str, Any]:
    """Last successful fidelity-gate pass, and the recent vote record."""
    res: dict[str, Any] = {"last_land": None, "days_since": None, "recent": []}
    if not BUILD_STATS.exists():
        return res
    rows = []
    for line in BUILD_STATS.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    for d in rows:
        ts = str(d.get("ts") or d.get("timestamp") or d.get("date") or "")
        votes = d.get("eval_votes") or d.get("votes")
        if not votes:
            continue
        passed = False
        m = re.match(r"(\d+)\s*/\s*(\d+)", str(votes))
        if m:
            got, total = int(m.group(1)), int(m.group(2))
            passed = total > 0 and got * 2 > total  # strict majority
        res["recent"].append(
            {"ts": ts[:19], "demo": str(d.get("demo_id") or "")[:48], "votes": str(votes), "pass": passed}
        )
        if passed:
            res["last_land"] = ts[:19]
    if res["last_land"]:
        try:
            last = datetime.fromisoformat(res["last_land"]).replace(tzinfo=timezone.utc)
            res["days_since"] = (datetime.now(timezone.utc) - last).days
        except Exception:
            pass
    res["recent"] = res["recent"][-8:]
    return res


# ── report assembly ───────────────────────────────────────────────────────────
def build_report() -> dict[str, Any]:
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    run = _parse_run(_journal(since))
    land = _land_history()

    problems: list[str] = []
    severity = "info"

    if run["started"] is None:
        problems.append("the nuggets run did not fire in the last 24h")
        severity = "error"
    if run["result"] and run["result"] != "success":
        problems.append(f"unit result={run['result']}")
        severity = "error"

    leftover = run["cgpm"].get("leftover_procs")
    if leftover not in (None, "0"):
        problems.append(
            f"leftover_procs={leftover} ({', '.join(run['leftover'][:4]) or 'unnamed'}) "
            "— the #1587 process-group reap did NOT fully close the orphan leak"
        )
        severity = "error"

    high = run["cgpm"].get("memory.events high")
    if isinstance(high, int) and high > 0:
        problems.append(f"memory.events high={high} — something is still throttling on memory")
        severity = "error"
    for k in ("memory.events max", "memory.events oom", "memory.events oom_kill"):
        v = run["cgpm"].get(k)
        if isinstance(v, int) and v > 0:
            problems.append(f"{k}={v}")
            severity = "error"

    if run["builds_timeout"]:
        problems.append(f"{run['builds_timeout']} build(s) hit the 3600s cap")
        severity = "error" if severity == "error" else "warning"

    ds = land["days_since"]
    if ds is None or ds >= STALE_LAND_DAYS:
        votes = ", ".join(f"{r['votes']}" for r in land["recent"][-5:]) or "none"
        problems.append(
            f"FIDELITY GATE: no successful land in {ds if ds is not None else 'ever (in this file)'} days "
            f"(last {land['last_land'] or 'n/a'}); recent votes: {votes}. "
            "Builds pass the deterministic verifier and are then voted down — the job is burning "
            "inference and landing nothing. This needs a decision, not a config change."
        )
        severity = "error" if severity == "error" else "warning"

    return {"run": run, "land": land, "problems": problems, "severity": severity}


def render(rep: dict[str, Any]) -> tuple[str, str]:
    run, land = rep["run"], rep["land"]
    cg = run["cgpm"]
    title = {
        "info": "Nuggets nightly: healthy",
        "warning": "Nuggets nightly: needs attention",
        "error": "Nuggets nightly: FAILING",
    }[rep["severity"]]
    parts = [
        f"run={run['started'] or 'DID NOT FIRE'} result={run['result'] or '?'}",
        f"builds={run['builds_attempted']} (ok={run['builds_ok']} rc3={run['builds_rc3']} timeout={run['builds_timeout']})",
        f"swept={run['swept']}",
        f"peak={cg.get('memory.peak', '?')} swap_peak={cg.get('memory.swap.peak', '?')}",
        f"mem.high_events={cg.get('memory.events high', '?')} leftover_procs={cg.get('leftover_procs', '?')}",
        f"last_land={land['last_land'] or 'never'} ({land['days_since']}d ago)",
    ]
    body = " | ".join(parts)
    if rep["problems"]:
        body += "  ||  " + "; ".join(rep["problems"])
    return title, body


def post(rep: dict[str, Any], title: str, body: str) -> tuple[bool, str]:
    if not ENDPOINT:
        return False, "missing_endpoint"
    _bootstrap_secrets()
    token = (os.getenv("UA_OPS_TOKEN") or "").strip()
    if not token:
        return False, "missing_ops_token"
    payload = {
        "kind": "nuggets_nightly_health",
        "title": title,
        "message": body[:1500],
        "severity": rep["severity"],
        "requires_action": rep["severity"] in ("warning", "error"),
        "metadata": {
            "source": "nuggets_health_report",
            "run": rep["run"],
            "last_land": rep["land"]["last_land"],
            "days_since_land": rep["land"]["days_since"],
            "recent_votes": rep["land"]["recent"],
            "problems": rep["problems"],
        },
    }
    req = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(payload).encode("utf-8"),
        headers={"content-type": "application/json", "x-ua-ops-token": token},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            if not (200 <= resp.status < 300):
                return False, f"status_{resp.status}"
    except urllib.error.HTTPError as exc:
        return False, f"http_{exc.code}"
    except Exception as exc:
        return False, f"{type(exc).__name__}:{exc}"
    return True, "ok"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="print the report, send nothing")
    ap.add_argument("--always-notify", action="store_true", help="notify even when healthy")
    args = ap.parse_args()

    rep = build_report()
    title, body = render(rep)
    print(json.dumps({"title": title, "body": body, "severity": rep["severity"]}, indent=2))

    if args.dry_run:
        return 0
    # Quiet when healthy: a daily "all good" ping trains the operator to ignore it.
    if rep["severity"] == "info" and not args.always_notify:
        print("NUGGETS_REPORT_HEALTHY_NO_NOTIFY")
        return 0
    ok, detail = post(rep, title, body)
    print(f"notify_ok={ok} detail={detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

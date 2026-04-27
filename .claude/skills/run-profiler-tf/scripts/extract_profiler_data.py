#!/usr/bin/env python3
"""
Extract run profiling data from Universal Agent workspaces.
Reads run_checkpoint.json and trace.json files, outputs structured JSON for dashboard generation.

Usage:
    python3 extract_profiler_data.py [--workspaces-root PATH] [--top-n 10] [--output PATH]
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import glob
import json
import os


def load_checkpoints(root, top_n=15):
    """Load run_checkpoint.json from all workspace directories."""
    paths = glob.glob(os.path.join(root, "*/run_checkpoint.json"))
    paths += glob.glob(os.path.join(root, "_daemon_archives/*/run_checkpoint.json"))

    sessions = []
    for cp in paths:
        try:
            d = json.load(open(cp))
            gs = d.get("goal_satisfaction") or {}
            obs = gs.get("observed") or {}
            tool_names = obs.get("tool_names", [])
            sessions.append({
                "session_id": d.get("session_id", os.path.basename(os.path.dirname(cp))),
                "workspace_dir": os.path.basename(os.path.dirname(cp)),
                "timestamp": d.get("timestamp", ""),
                "tool_call_count": d.get("tool_call_count", 0),
                "execution_time_seconds": d.get("execution_time_seconds", 0),
                "passed": gs.get("passed") if gs else None,
                "tool_counts": dict(Counter(tool_names)),
                "missing": gs.get("missing", []) if gs else [],
                "auto_completed": obs.get("auto_completed_after_delivery", False),
                "pressure_score": d.get("tool_call_count", 0) * d.get("execution_time_seconds", 0) / 1000,
            })
        except Exception:
            pass

    sessions.sort(key=lambda x: x["timestamp"] or "", reverse=True)
    return sessions[:top_n]


def load_traces(root):
    """Load trace.json from all workspace directories."""
    paths = glob.glob(os.path.join(root, "*/trace.json"))
    paths += glob.glob(os.path.join(root, "_daemon_archives/*/trace.json"))

    all_calls = []
    session_durations = {}

    for t in paths:
        try:
            d = json.load(open(t))
            session = os.path.basename(os.path.dirname(t))
            duration = d.get("total_duration_seconds", 0)
            session_durations[session] = duration
            tcalls = d.get("tool_calls", [])

            for i, tc in enumerate(tcalls):
                name = tc.get("name", "?")
                offset = tc.get("time_offset_seconds", 0)
                input_sz = tc.get("input_size_bytes", 0)
                next_offset = tcalls[i + 1].get("time_offset_seconds", offset) if i < len(tcalls) - 1 else duration
                est_duration = max(0, next_offset - offset)
                all_calls.append({
                    "session": session,
                    "tool": name,
                    "offset": offset,
                    "est_duration": est_duration,
                    "input_size": input_sz,
                })
        except Exception:
            pass

    return all_calls, session_durations


def compute_tool_stats(all_calls):
    """Compute per-tool aggregate statistics."""
    by_tool = defaultdict(list)
    for tc in all_calls:
        by_tool[tc["tool"]].append(tc["est_duration"])

    stats = []
    for tool, durations in sorted(by_tool.items(), key=lambda x: sum(x[1]) / max(len(x[1]), 1), reverse=True):
        stats.append({
            "tool": tool,
            "count": len(durations),
            "total_time": sum(durations),
            "avg_time": sum(durations) / len(durations),
            "max_time": max(durations),
            "median_time": sorted(durations)[len(durations) // 2],
        })
    return stats


def find_failures(sessions):
    """Identify sessions with failures or lifecycle gaps."""
    failures = []
    for s in sessions:
        flags = []
        if not s.get("passed", True):
            flags.append("FAILED")
        if s.get("auto_completed"):
            flags.append("AUTO_COMPLETED")
        if s.get("missing"):
            flags.append(f"MISSING({len(s['missing'])})")
        if s["pressure_score"] > 50:
            flags.append("HIGH_PRESSURE")
        if flags:
            failures.append({**s, "flags": flags})
    return failures


def main():
    parser = argparse.ArgumentParser(description="Extract UA run profiling data")
    parser.add_argument("--workspaces-root", default="/opt/universal_agent/AGENT_RUN_WORKSPACES/")
    parser.add_argument("--top-n", type=int, default=15, help="Number of recent sessions to profile")
    parser.add_argument("--output", default=None, help="Output JSON path")
    args = parser.parse_args()

    sessions = load_checkpoints(args.workspaces_root, args.top_n)
    all_calls, session_durations = load_traces(args.workspaces_root)
    tool_stats = compute_tool_stats(all_calls)
    failures = find_failures(sessions)

    # Top 15 slowest individual calls
    top_slow = sorted(all_calls, key=lambda x: x["est_duration"], reverse=True)[:15]

    # Trends: sessions over time
    trend_data = []
    for s in sessions:
        trend_data.append({
            "timestamp": s["timestamp"][:16],
            "session": s["workspace_dir"][:40],
            "tools": s["tool_call_count"],
            "duration": round(s["execution_time_seconds"], 1),
            "passed": s["passed"],
        })

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sessions_profiled": len(sessions),
        "total_tool_calls_traced": len(all_calls),
        "sessions": sessions,
        "tool_stats": tool_stats,
        "top_slow_calls": top_slow,
        "failures": failures,
        "trend_data": trend_data,
    }

    out_path = args.output or os.path.join(args.workspaces_root, "profiler_data.json")
    os.makedirs(os.path.dirname(out_path) if os.path.dirname(out_path) else ".", exist_ok=True)
    json.dump(result, open(out_path, "w"), indent=2)
    print(f"Wrote profiler data to {out_path}")
    print(f"Sessions: {len(sessions)}, Tool calls traced: {len(all_calls)}, Tool types: {len(tool_stats)}")


if __name__ == "__main__":
    main()

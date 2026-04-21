#!/usr/bin/env python3
"""Build markdown performance dashboard from profiler_data.json."""
import json
import os
from collections import Counter

WORKSPACE = "/opt/universal_agent/AGENT_RUN_WORKSPACES/session_20260421_123445_747eb265"
DATA_PATH = os.path.join(WORKSPACE, "work_products/profiler_data.json")
OUTPUT_PATH = os.path.join(WORKSPACE, "work_products/run_performance_dashboard.md")

d = json.load(open(DATA_PATH))
md = []

md.append("# Agent Run Performance Dashboard")
md.append("**Generated:** " + d["generated_at"])
md.append("**Sessions Profiled:** " + str(d["sessions_profiled"]) +
          " | **Tool Calls Traced:** " + str(d["total_tool_calls_traced"]) +
          " | **Tool Types:** " + str(len(d["tool_stats"])))
md.append("")
md.append("---")
md.append("")

# 1. Session Summary Table
md.append("## 1. Session Summary (Last 15 Runs)")
md.append("")
md.append("| # | Session | Time | Tools | Duration | Passed | Pressure | Flags |")
md.append("|---|---------|------|-------|----------|--------|----------|-------|")
for i, s in enumerate(d["sessions"], 1):
    ts = s["timestamp"][:16].replace("T", " ")
    sid = s["workspace_dir"][:42]
    tc = s["tool_call_count"]
    dur = "{:.1f}s".format(s["execution_time_seconds"])
    passed = "Yes" if s["passed"] else "**No**"
    pressure = "{:.1f}".format(s["pressure_score"])
    flags = []
    if s["auto_completed"]:
        flags.append("AUTO")
    if not s["passed"]:
        flags.append("FAIL")
    if s["missing"]:
        flags.append("MISS({})".format(len(s["missing"])))
    if s["pressure_score"] > 50:
        flags.append("PRESSURE")
    flag_str = ", ".join(flags) if flags else "-"
    md.append("| {} | `{}` | {} | {} | {} | {} | {} | {} |".format(
        i, sid, ts, tc, dur, passed, pressure, flag_str))

md.append("")

# 2. Aggregate Stats
total_tools = sum(s["tool_call_count"] for s in d["sessions"])
total_time = sum(s["execution_time_seconds"] for s in d["sessions"])
avg_tools = total_tools / len(d["sessions"]) if d["sessions"] else 0
avg_time = total_time / len(d["sessions"]) if d["sessions"] else 0
pass_rate = sum(1 for s in d["sessions"] if s["passed"]) / len(d["sessions"]) * 100

md.append("## 2. Aggregate Statistics")
md.append("")
md.append("| Metric | Value |")
md.append("|--------|-------|")
md.append("| Total tool calls (15 sessions) | {} |".format(total_tools))
md.append("| Total execution time | {:.0f}s ({:.1f} min) |".format(total_time, total_time / 60))
md.append("| Average tools per session | {:.1f} |".format(avg_tools))
md.append("| Average duration per session | {:.1f}s ({:.1f} min) |".format(avg_time, avg_time / 60))
md.append("| Pass rate | {:.0f}% |".format(pass_rate))
md.append("| Sessions with anomalies | {} |".format(len(d["failures"])))
md.append("")

# 3. Tool Performance Table
md.append("## 3. Tool Performance (All Traced Calls)")
md.append("")
md.append("| Tool | Count | Avg (s) | Max (s) | Total (s) |")
md.append("|------|-------|---------|---------|-----------|")
for t in d["tool_stats"][:20]:
    md.append("| `{}` | {} | {:.1f} | {:.1f} | {:.0f} |".format(
        t["tool"], t["count"], t["avg_time"], t["max_time"], t["total_time"]))
md.append("")

# 4. Top 15 Slowest Individual Tool Calls
md.append("## 4. Top 15 Slowest Individual Tool Calls")
md.append("")
md.append("| # | Duration | Tool | Session |")
md.append("|---|----------|------|---------|")
for i, tc in enumerate(d["top_slow_calls"], 1):
    dur_s = tc["est_duration"]
    if dur_s > 600:
        dur = "**{:.0f}s ({:.1f}min)**".format(dur_s, dur_s / 60)
    else:
        dur = "{:.1f}s".format(dur_s)
    tool = tc["tool"]
    sess = tc["session"][:40]
    md.append("| {} | {} | `{}` | `{}` |".format(i, dur, tool, sess))
md.append("")

# 5. Failure Analysis
md.append("## 5. Failure & Lifecycle Gap Analysis")
md.append("")
if d["failures"]:
    md.append("| Session | Time | Tools | Duration | Flags | Details |")
    md.append("|---------|------|-------|----------|-------|---------|")
    for f in d["failures"]:
        ts = f["timestamp"][:16].replace("T", " ")
        sid = f["workspace_dir"][:35]
        flags = ", ".join(f["flags"])
        missing_desc = "; ".join(m.get("message", "")[:60] for m in f.get("missing", []))
        md.append("| `{}` | {} | {} | {:.1f}s | {} | {} |".format(
            sid, ts, f["tool_call_count"], f["execution_time_seconds"], flags, missing_desc))
else:
    md.append("No failures detected in profiled sessions.")
md.append("")

# 6. Trend Analysis
md.append("## 6. Trend Analysis")
md.append("")
sessions = d["sessions"]
if len(sessions) >= 3:
    third = max(1, len(sessions) // 3)
    recent = sessions[:third]
    mid = sessions[third:2 * third]
    older = sessions[2 * third:]

    def avg_metrics(group):
        if not group:
            return (0, 0)
        return (
            sum(s["tool_call_count"] for s in group) / len(group),
            sum(s["execution_time_seconds"] for s in group) / len(group),
        )

    r_tools, r_time = avg_metrics(recent)
    m_tools, m_time = avg_metrics(mid)
    o_tools, o_time = avg_metrics(older)

    md.append("### Session Cohort Comparison")
    md.append("")
    md.append("| Cohort | Sessions | Avg Tools | Avg Duration | Trend |")
    md.append("|--------|----------|-----------|--------------|-------|")
    tool_trend = "Stable" if abs(r_tools - o_tools) < 5 else ("Increasing" if r_tools > o_tools else "Decreasing")
    time_trend = "Stable" if abs(r_time - o_time) < 60 else ("Increasing" if r_time > o_time else "Decreasing")
    md.append("| Most Recent | {} | {:.1f} | {:.1f}s | Tools: {} |".format(len(recent), r_tools, r_time, tool_trend))
    md.append("| Mid | {} | {:.1f} | {:.1f}s | Time: {} |".format(len(mid), m_tools, m_time, time_trend))
    md.append("| Older | {} | {:.1f} | {:.1f}s | - |".format(len(older), o_tools, o_time))
    md.append("")

    recent_tools = Counter()
    older_tools = Counter()
    for s in recent:
        recent_tools.update(s.get("tool_counts", {}))
    for s in older:
        older_tools.update(s.get("tool_counts", {}))

    md.append("### Tool Usage Shift (Recent vs Older)")
    md.append("")
    md.append("| Tool | Recent | Older | Delta |")
    md.append("|------|--------|-------|-------|")
    all_tools = set(recent_tools.keys()) | set(older_tools.keys())
    sorted_tools = sorted(all_tools, key=lambda t: abs(recent_tools.get(t, 0) - older_tools.get(t, 0)), reverse=True)
    for tool in sorted_tools[:10]:
        r = recent_tools.get(tool, 0)
        o = older_tools.get(tool, 0)
        delta = r - o
        delta_str = "+{}".format(delta) if delta > 0 else str(delta)
        md.append("| `{}` | {} | {} | {} |".format(tool, r, o, delta_str))
    md.append("")

# 7. Optimization Recommendations
md.append("## 7. Optimization Recommendations")
md.append("")

recs = []

slow_tools = [t for t in d["tool_stats"] if t["avg_time"] > 30]
if slow_tools:
    names = ", ".join("`{}` ({:.0f}s avg)".format(t["tool"], t["avg_time"]) for t in slow_tools[:5])
    recs.append("**Slow Tools (avg >30s):** " + names + ". These dominate execution time. Consider batching, caching, or parallelizing calls.")

vp_wait = [t for t in d["tool_stats"] if "vp_wait" in t["tool"]]
if vp_wait and vp_wait[0]["avg_time"] > 120:
    recs.append("**VP Wait Bottleneck:** `vp_wait_mission` averages {:.0f}s (max {:.0f}s). Consider reducing poll intervals or using async status checks instead of blocking waits.".format(
        vp_wait[0]["avg_time"], vp_wait[0]["max_time"]))

high_pressure = [f for f in d["failures"] if "HIGH_PRESSURE" in f.get("flags", [])]
if high_pressure:
    recs.append("**Context Pressure:** {} session(s) flagged with high pressure scores. These sessions have high tool counts AND long durations, suggesting context window pressure. Consider breaking large tasks into smaller sub-tasks.".format(len(high_pressure)))

lifecycle_gaps = [f for f in d["failures"] if any("MISSING" in fl for fl in f.get("flags", []))]
if lifecycle_gaps:
    recs.append("**Lifecycle Gaps:** {} session(s) ended without proper Task Hub disposition. This is a recurring pattern suggesting the auto-completion hook sometimes fires before the agent can call `task_hub_task_action`. Consider adding a pre-exit guard.".format(len(lifecycle_gaps)))

bash_stats = [t for t in d["tool_stats"] if t["tool"] == "Bash"]
if bash_stats and bash_stats[0]["count"] > 500:
    recs.append("**Bash Dominance:** Bash accounts for {} of {} traced calls ({:.0f}%). Some of these may be replaceable with dedicated MCP tools for better observability and guardrails.".format(
        bash_stats[0]["count"], d["total_tool_calls_traced"],
        bash_stats[0]["count"] / d["total_tool_calls_traced"] * 100))

failed = [f for f in d["failures"] if "FAIL" in f.get("flags", [])]
if failed:
    recs.append("**Failed Sessions:** {} session(s) with `passed=false`. These warrant investigation — check if they represent real failures or just incomplete lifecycle dispositions.".format(len(failed)))

for i, rec in enumerate(recs, 1):
    md.append("{}. {}".format(i, rec))
    md.append("")

if not recs:
    md.append("No significant optimization opportunities identified. System appears healthy.")
    md.append("")

md.append("---")
md.append("*Generated by `run-profiler` task-skill. Re-run with `python3 scripts/extract_profiler_data.py` to refresh.*")

output = "\n".join(md)
with open(OUTPUT_PATH, "w") as f:
    f.write(output)
print("Dashboard written: {} lines, {} chars".format(len(md), len(output)))
print("Recommendations: {}".format(len(recs)))

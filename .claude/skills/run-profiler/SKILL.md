---
name: run-profiler
description: >
  Profile recent Universal Agent runs — extract tool call counts, execution times, slowest
  tools, failure patterns, and context pressure from run_checkpoint.json and trace.json files
  across session workspaces. Produces a markdown performance dashboard with trend analysis and
  optimization recommendations. USE this skill whenever the user mentions "profile runs",
  "agent performance", "slow tools", "execution stats", "run metrics", "tool call analysis",
  "context pressure", "dashboard", or wants to understand how the agent is performing,
  even if they don't explicitly say "run profiler". Also use for periodic performance reviews,
  heartbeat optimization, and debugging slow agent sessions.
---

# Run Profiler

## Goal
Analyze the last N session workspaces to produce a performance dashboard that reveals
which tools are slowest, which fail most, which sessions hit context pressure, and what
optimization opportunities exist.

## Success Criteria
- A markdown dashboard in `work_products/` with: session summary table, tool performance
  table (count, avg time, max time), top 10+ slowest individual tool calls, failure/missing
  pattern analysis, context pressure flagging, and actionable optimization recommendations
- Dashboard covers at least 10 recent sessions
- Data sourced from `run_checkpoint.json` and `trace.json` files (not run.log parsing)
- Raw extraction data also saved as JSON for downstream consumption

## Constraints
- Read-only: do not modify any workspace files
- Use `run_checkpoint.json` for session-level stats and `trace.json` for tool-level timing
- Session directories: `session_*`, `run_daemon_*`, `run_session_hook_*`, and
  `_daemon_archives/*` all count as valid run workspaces
- Include hook runs and daemon runs alongside interactive sessions for full picture
- Never inline complex Python in bash commands — always write to `.py` files first

## Context
- Workspace root: `/opt/universal_agent/AGENT_RUN_WORKSPACES/`
- Checkpoint schema: see `references/data_schema.md`
- Individual tool call duration = gap between consecutive `time_offset_seconds` values
- Context pressure heuristic: `tool_count * exec_time / 1000 > 50` = HIGH_PRESSURE
- Scripts: `scripts/extract_profiler_data.py` (data extraction) and `scripts/build_dashboard.py` (markdown generation)

## Approach
1. Run `python3 scripts/extract_profiler_data.py --top-n 15 --output work_products/profiler_data.json`
2. Run `python3 scripts/build_dashboard.py` to generate the dashboard from extracted data
3. Review the dashboard, refine recommendations if needed
4. Save final dashboard to `work_products/run_performance_dashboard.md`

### What the scripts do
- **extract_profiler_data.py**: Globs all checkpoint and trace files, extracts structured
  data into JSON. Accepts `--workspaces-root`, `--top-n`, and `--output` args.
- **build_dashboard.py**: Reads the JSON, generates markdown with 7 sections: session
  summary, aggregate stats, tool performance, slowest calls, failure analysis, trend
  analysis, and optimization recommendations.

### Manual fallback (if scripts unavailable)
1. Glob all `run_checkpoint.json` files (including `_daemon_archives/`)
2. Load each with Python json.load, extract: session_id, timestamp, tool_call_count,
   execution_time_seconds, goal_satisfaction.passed, goal_satisfaction.observed.tool_names
3. Sort by timestamp desc, take top N
4. Glob `trace.json` files, compute tool durations from offset gaps
5. Build tables and recommendations from the data

## Anti-Patterns
- Don't parse `run.log` files for timing data; checkpoints and traces are canonical
- Don't assume `python` exists; use `python3` explicitly
- Don't treat `time_offset_seconds` gaps as exact wall-clock time (they're approximate)
- Don't count auto-completed tasks as failures; they're a lifecycle gap, not a bug
- Don't skip `_daemon_archives/` — daemon and cron runs contain critical data

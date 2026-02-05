
# Proactive Agent Capabilities: Brainstorming

## 1. Safety & Maintenance (Start Small)

*These monitor the system's health and prevent rot.*

- **System Health Monitor**: Use the Heartbeat to check `run.log` for recurring errors or exceptions every hour. If >5 errors, alert the user.
- **Disk Usage Watchdog**: Check if the workspace or log directories are growing too large (>1GB) and suggest cleanup or auto-compact logs.
- **Dependency Drift Check**: Once a day (Cron), run `uv pip list --outdated` and draft a consolidated "Dependency Update" report.

## 2. Code Quality & Hygiene (Medium)

*These actively improve the codebase.*

- **Linting Sentinel**: Every night at 3 AM (Cron), run the linter/formatter on the entire `src/` directory. If changes are found, create a branch, commit them, and notify the user: "I polished the code while you slept."
- **TODO Collector**: Scan the codebase for `# TODO` and `# FIXME` comments. Aggregate them into a `tasks/technical_debt.md` file in global memory, ensuring we track loose ends.
- **Test Runner**: Run the unit test suite (`pytest`) every morning at 6 AM. If tests fail, prepare a "Debugging Brief" with the exact failure logs ready for the user's coffee.

## 3. Autonomous Evolution (Big Ideas)

*These require the agent to plan and execute complex workflows.*

- **Auto-Documentation**: Monitor file changes. If a file changes significantly but its associated doc/markdown hasn't been touched, proactively draft an updated documentation snippet and offer it for review.
- **The "Night Watchman"**: Give the agent a "Research Queue". If the user mentions "I wonder how X library works" during the day, add it to the queue. Overnight, the agent creates a sandbox, installs the library, runs examples, and writes a "Research Report" for the morning.
- **PR Reviewer**: If connected to GitHub, poll for open PRs, run a local review (checking against guidelines), and draft comments or approval suggestions.

## Implementation Plan (Draft)

For tonight/tomorrow, we can start with the **"Night Watchman"** combined with **"System Health"**:

1. **Heartbeat**: Check logs every 30m for critical crashes.
2. **Cron (3 AM)**: Run a full project lint/format check.
3. **Cron (4 AM)**: Scan for new TODOs and update the debt list.
4. **Heartbeat (8 AM)**: Morning Briefing (already active) will now include results from the Lint and TODO scan.

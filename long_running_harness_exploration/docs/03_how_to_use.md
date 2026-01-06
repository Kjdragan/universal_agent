# How to Use the Harness

## 1. Slash Command (Interactive)
The easiest way to start a long-running task is using the `/harness` slash command during an interactive session.

### Usage
```text
/harness [Objective]
```

**Examples:**
1. **Direct Objective**:
   ```text
   /harness Research standard models and write a report
   ```
   *Sets Objective, enables Harness, and starts immediate execution.*

2. **Interactive Prompt**:
   ```text
   /harness
   ```
   *The agent will ask you to enter the Objective.*

### What happens?
1. The harness is enabled for the current run.
2. `max_iterations` is set to **10**.
3. `completion_promise` is set to **"TASK_COMPLETE"**.
4. The agent is strictly instructed to loop/restart until it creates the final artifact and outputs "TASK_COMPLETE".

---

## 2. CLI Arguments (Automation/Jobs)
You can start a harnessed run directly from the command line, useful for cron jobs or automated pipelines.

### Usage
```bash
python -m universal_agent.main \
  --run-id <id> \
  --max-iterations 20 \
  --completion-promise "DONE"
```

**Arguments:**
- `--max-iterations <int>`: (Default: 10) Max number of restarts before force-quitting.
- `--completion-promise <str>`: (Default: None) The string the agent MUST output to finish. If this is not set, **Harness is DISABLED** (normal behavior).

### Example
```bash
python -m universal_agent.main \
  --run-id daily_research \
  --max-iterations 5 \
  --completion-promise "REPORT_UPLOADED" \
  --job-path specs/daily_research.json
```

## 3. Best Practices
- **Explicit Artifacts**: Ensure your objective requires a tangible file (e.g., "Write report.md"). The restart mechanism relies on the agent reading these files to know what it did previously.
- **Clear Promise**: Tell the agent *in the prompt* about the completion promise: "When you are finished, you MUST output 'TASK_COMPLETE'." (The system system prompt does this automatically in Harness Mode, but reinforcing it helps).
- **Handoff Files**: For very complex tasks, ask the agent to maintain a `handoff_notes.md` file where it writes instructions for its future self before stopping.

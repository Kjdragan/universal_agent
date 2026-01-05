# Long-Running Harness Exploration

This directory contains research, experiments, and prototypes for extending Universal Agent with long-running task capabilities (1hr to 24hr+ sessions).

**Created**: 2026-01-05  
**Status**: Research Phase

## Directory Structure

```
long_running_harness_exploration/
├── docs/                    # Design documents and findings
├── research/                # Research notes from external sources
├── experiments/             # Ad-hoc experiments and tests
├── tests/                   # Structured stress tests and evaluations
└── prototypes/              # Prototype implementations
```

## Key Findings (2026-01-05)

### Anthropic Autonomous Coding Harness Architecture

**CONFIRMED**: The harness does NOT use compaction for continuity. Instead:

1. **Fresh Agent Instances**: Each session creates a NEW `ClaudeSDKClient` with completely wiped memory
2. **File-Based State**: Continuity is achieved via files:
   - `feature_list.json` - Structured task list with `passes: true/false` flags
   - `claude-progress.txt` - Human-readable narrative of what was done
   - Git history - Change log and rollback capability
3. **Explicit Orientation**: Agent starts each session with "Get Your Bearings" step:
   - `cat feature_list.json | head -50`
   - `cat claude-progress.txt`
   - `git log --oneline -20`
4. **Completion Detection**: Loop continues until all tasks have `passes: true`

### Implications for Universal Agent

Our current system has:
- ✅ Durability system (runs, steps, ledger, checkpoints)
- ✅ File-based artifacts (search_results, work_products)
- ✅ Tool receipts for idempotency
- ❌ No explicit "Get Your Bearings" protocol for sub-agents
- ❌ No structured task list with pass/fail flags
- ❌ No session boundary management for context exhaustion

### Next Steps

1. **Stress Test**: Identify when current system fails due to context exhaustion
2. **Prototype**: Add progress file + orientation step to report-creation-expert
3. **Evaluate**: Does file-based handoff solve same-task continuation?
4. **Design**: Multi-session orchestrator if needed

## Related Documents

- [030_LONG_RUNNING_TASK_EXTENSIONS_DESIGN.md](../Project_Documentation/030_LONG_RUNNING_TASK_EXTENSIONS_DESIGN.md) - Initial design doc
- [017_LONG_RUNNING_AGENTS_PROGRESS.md](../Project_Documentation/017_LONG_RUNNING_AGENTS_PROGRESS.md) - Durability progress

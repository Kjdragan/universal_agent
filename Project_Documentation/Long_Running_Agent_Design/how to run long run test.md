# How to Run Long-Run Test

This document describes the simplest, repeatable durability test flow.

## Start the Run
Run the CLI with a job JSON file. This prints a Run ID and auto-runs the job prompt.

```
PYTHONPATH=src uv run python -m universal_agent.main --job durable_demo.json
```

The resume command is also saved to:

`Project_Documentation/Long_Running_Agent_Design/KevinRestartWithThis.md`

## Kill the Run
To stop the run at any time, press:

```
Ctrl+C
```

## Resume the Same Run
Use the Run ID from `KevinRestartWithThis.md` to resume exactly where it left off.

```
PYTHONPATH=src uv run python -m universal_agent.main --resume --run-id <RUN_ID>
```

## Notes
- Use the same `durable_demo.json` for a consistent test scenario.
- The resume command always requires the original Run ID.
- The job file should include either `prompt` or `objective`. If only `objective` is present, it is used as the prompt, with inputs/constraints appended.

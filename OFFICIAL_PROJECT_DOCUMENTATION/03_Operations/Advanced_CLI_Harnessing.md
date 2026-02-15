# Advanced CLI Harnessing: Programmatic Agent Control

## Overview

The **Universal Agent** is designed to be driven not just by interactive chat, but by programmatic **Task Injection**. This capability allows you to wrap the agent in Python scripts, Cron jobs, or CI/CD pipelines to execute complex workflows autonomously.

## Key Component: `UniversalAgentAdapter`

The `UniversalAgentAdapter` (located in `src/universal_agent/main.py` or importable as part of the SDK) provides a high-level API to spawn an agent session, inject a `Task`, and retrieve the structured `ExecutionResult`.

### Usage Pattern

```python
import asyncio
from universal_agent.main import UniversalAgentAdapter, AgentRequest, Task

async def run_cron_job():
    # 1. Initialize Adapter
    adapter = UniversalAgentAdapter()
    
    # 2. Define the Task
    # The 'prompt' is the natural language instruction.
    # You can parameterize it dynamically!
    task = Task(
        prompt="Check the disk usage on /var/log and summarize the top 5 largest files.",
        context={"user_id": "cron_user"}
    )
    
    # 3. Execute
    # The agent spins up, runs the task, and returns the result.
    result = await adapter.run_task(task)
    
    # 4. Handle Output
    if result.success:
        print("✅ Job Complete!")
        print(result.final_response) # The agent's text summary
        print(result.artifacts)      # List of files generated (PDFs, CSVs, etc.)
    else:
        print(f"❌ Job Failed: {result.error}")

if __name__ == "__main__":
    asyncio.run(run_cron_job())
```

## "Programmatic Prompt Engineering"

The power of this approach lies in **dynamic prompt construction**. You can inject variables from your environment directly into the agent's instructions:

```python
target_url = "https://example.com/daily-report"
prompt = f"Scrape {target_url}, extract the 'Net Revenue' table, and save it as a CSV."
```

## Use Cases

1. **Cron Jobs**: Daily system health checks, report generation, or data scraping.
2. **Event Handlers**: triggered by a webhook (e.g., "New issue on GitHub" -> "Draft a PR").
3. **Batch Processing**: Loop through a list of items and run the agent on each one in parallel (or sequence).
4. **Testing**: Verify agent behavior deterministically (as seen in `tests/final_integration_test.py`).

## Best Practices

* **Artifacts**: Always check `result.artifacts` to get the paths of generated files.
* **Timeouts**: Set a timeout on the `run_task` call to prevent runaway processes.
* **Error Handling**: Wrap the execution in try/except blocks to handle network or API failures gracefully.

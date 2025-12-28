# Telegram Bot Integration: Architecture & Design Discussion

## A Companion Guide for AI Programmers

**Document Purpose**: This document provides the conceptual framework, design rationale, and mental models needed to implement the Telegram bot integration for a Claude Agent SDK multi-agent system. Read this before diving into the code—understanding *why* the architecture is structured this way will make implementation significantly smoother.

---

## Executive Summary

We are building a **thin integration layer** that exposes an existing multi-agent system through Telegram, enabling remote execution from a mobile phone. The key architectural insight is that this is *not* a rewrite—it's a wrapper that bridges two asynchronous systems (Telegram's webhook model and the agent's execution model) through a task queue pattern.

The system solves three core challenges:
1. **Timeout mismatch**: Telegram requires responses within 3 seconds; agents run for minutes
2. **Execution visibility**: Users need to know what's happening during long operations
3. **Result delivery**: Agent outputs (text, files, logs) must reach the user reliably

---

## Part 1: Understanding the Integration Model

### Why This Isn't a Rewrite

The existing multi-agent system was designed to run as a standalone process—you invoke it, it does work, it returns results. That architecture remains completely valid. What we're adding is a new *entry point* that:

1. Receives requests from Telegram instead of a command line or Streamlit UI
2. Queues those requests for async execution
3. Delivers results back through Telegram when ready

Think of it like adding a REST API to an existing library. The library's internals don't change; you just create endpoints that call into it.

```
BEFORE:
  [CLI/Streamlit] → [Agent System] → [Results to Screen]

AFTER:
  [CLI/Streamlit] → [Agent System] → [Results to Screen]
                         ↑
  [Telegram Bot] → [Adapter] ─┘→ [Results to Telegram]
```

The "Adapter" is literally one file (`agent_adapter.py`) with one function that calls your existing code.

### The Adapter Pattern in Detail

The adapter's job is translation. It takes a standardized input (prompt string, workspace path, progress callback) and produces a standardized output (AgentResult with content, artifacts, and logs). Inside, it calls whatever entry point your agent system already has.

**Why this matters**: Your agent system might return results as a string, a dataclass, a dictionary, or write files to disk. The adapter normalizes all of this into a predictable structure that the Telegram handlers can work with. This decoupling means:

- Changes to the agent system don't break the bot (as long as the adapter is updated)
- Changes to the bot don't affect the agent system
- You can test each layer independently

---

## Part 2: The Async Execution Problem

### Why Telegram Can't Wait

When Telegram sends a webhook to your server, it expects a response within approximately 3 seconds. If your server doesn't respond, Telegram assumes something is wrong and may retry the request multiple times, potentially triggering duplicate task execution.

But Claude Agent SDK tasks—especially multi-agent orchestration with web searches, file processing, and iterative refinement—routinely take 30 seconds to 5 minutes. This creates an fundamental mismatch.

### The Solution: Acknowledge-Queue-Callback

The pattern we use is standard in async systems:

1. **Acknowledge immediately**: When a webhook arrives, respond to Telegram within milliseconds saying "got it"
2. **Queue the work**: Put the actual task into an internal queue for background processing
3. **Process asynchronously**: Worker coroutines pick up tasks and run them without blocking the webhook handler
4. **Callback with results**: When done, proactively send results back to the user via Telegram's API

```
Timeline:
  
  0ms     User sends /agent command
  50ms    Webhook received by server
  100ms   Task queued, acknowledgment sent to Telegram
  150ms   User sees "Task submitted!" message
  200ms   Worker picks up task, starts agent execution
  ...     (agent runs for 2 minutes)
  120s    Agent completes
  120.1s  Results sent to user as new Telegram messages
```

The user's experience is: send message → instant acknowledgment → notification when done. They can close Telegram, do other things, and get a push notification with results.

### Why Not Just Use Polling Mode?

Telegram bots can operate in two modes:

**Polling**: Your bot periodically asks Telegram "any new messages?" This is simpler (no webhooks, no public URL needed) but has drawbacks:
- Higher latency (you're asking every few seconds)
- Wastes resources polling when nothing is happening
- Doesn't scale well
- Less representative of production deployment

**Webhooks**: Telegram pushes updates to your server instantly. More complex to set up (needs HTTPS, public URL) but:
- Instant message delivery
- More efficient (only active when there's work)
- How production bots actually run

We use webhooks because you're building toward production deployment. The ngrok setup handles the "public URL" problem during development.

---

## Part 3: The Task Manager Design

### Concurrency Without Complexity

The task manager uses Python's `asyncio.Queue` pattern, which is simpler than external systems like Celery or Redis Queue but sufficient for single-user/small-team workloads.

**How it works**:

1. A fixed number of "worker" coroutines (default: 3) run continuously
2. Each worker waits for tasks on the queue
3. When a task arrives, a worker claims it and executes
4. Other workers handle other tasks concurrently
5. Results are delivered via callback

```python
# Conceptual model (simplified)
async def worker():
    while True:
        task = await queue.get()      # Wait for work
        result = await run_agent(task) # Do the work
        await notify_user(result)      # Deliver results
```

**Why 3 workers?** This is configurable, but 3 provides a balance:
- Allows concurrent tasks (you can submit multiple requests)
- Doesn't overwhelm the Anthropic API or your server's memory
- Each agent task can use significant resources (API calls, file I/O)

### Task Lifecycle

Every task goes through these states:

```
QUEUED → RUNNING → COMPLETED
                 → FAILED
                 → TIMEOUT
```

The task object stores all context needed to deliver results:
- `chat_id`: Where to send the response
- `user_id`: Who requested it (for access control)
- `prompt`: What they asked for
- `progress`: How far along (0.0 to 1.0)
- `result`: The final output (when complete)

This state is kept in memory. For a small deployment, this is fine—if the container restarts, in-flight tasks are lost, but that's acceptable for personal use. For production resilience, you'd add Redis persistence.

---

## Part 4: The Docker Container Model

### Why Containerize?

Docker provides three benefits for this project:

1. **Reproducibility**: The same container runs identically on your laptop and in the cloud
2. **Isolation**: Dependencies don't conflict with your system
3. **Deployment simplicity**: Cloud platforms (Fly.io, Cloud Run) deploy containers directly

### Volume Mounts for Agent Coordination

Your existing agent system uses file-based coordination—agents write intermediate results to disk, read each other's outputs, etc. This pattern works perfectly in Docker via volume mounts:

```yaml
volumes:
  - agent_workspace:/app/workspace
```

This creates a persistent storage area that:
- Survives container restarts
- Can be inspected for debugging
- Keeps agent artifacts available

Each task gets a subdirectory (`/app/workspace/task_abc123/`) so concurrent tasks don't collide.

### Secret Management

API keys should never be in your code or Docker images. The setup uses Docker secrets:

```yaml
secrets:
  - anthropic_api_key
```

These are mounted as files at `/run/secrets/anthropic_api_key` inside the container. The `config.py` reads them at startup. This pattern:
- Keeps secrets out of `docker inspect` output
- Works with Docker Swarm and Kubernetes
- Falls back to environment variables for development

---

## Part 5: The Telegram Bot Structure

### Bot Commands Design

The bot exposes a minimal command set:

| Command | Purpose | Why It Exists |
|---------|---------|---------------|
| `/start` | Welcome message | Standard Telegram bot convention |
| `/agent <prompt>` | Submit a task | Core functionality |
| `/status [id]` | Check task progress | Visibility into async operations |
| `/myid` | Show user's Telegram ID | Onboarding helper for access control |
| `/help` | Detailed usage | Self-service documentation |

**Why commands instead of free-form messages?**

You could make the bot respond to any message, but commands are explicit and predictable. Users know exactly how to interact, and there's no ambiguity about whether they're chatting or requesting work.

### Access Control

The `ALLOWED_USER_IDS` list restricts who can use the bot. This is simple but effective:

```python
ALLOWED_USER_IDS = {123456789, 987654321}  # You and family members
```

When someone unauthorized tries to use the bot, they see their user ID so they can request access from you. The `/myid` command helps with this onboarding flow.

**Getting user IDs**: 
1. User messages the bot
2. Bot shows "Access denied, your ID is X"
3. User tells you their ID
4. You add it to the config
5. User can now use the bot

### Handling Long Outputs

Telegram messages have a ~4096 character limit. Agent outputs can be much longer. The solution is chunking:

```python
def split_message(text, max_length=3500):
    # Find natural break points (newlines, spaces)
    # Split into digestible chunks
    # Each chunk becomes a separate message
```

For very long outputs, the chunked messages appear in sequence. For structured outputs (reports, code), sending as file attachments is often better.

### File Attachments

When your agent produces files (reports, data, logs), they're sent as Telegram documents:

```python
await bot.send_document(
    chat_id=chat_id,
    document=InputFile(file_bytes, filename="report.md"),
    caption="📄 Generated report"
)
```

This is excellent for mobile—users can preview files, share them, or open in other apps.

---

## Part 6: The Development Workflow

### Local Development Loop

The intended workflow during development:

1. **Start Docker container** with your agent system + bot code
2. **Start ngrok** to expose localhost to the internet
3. **Register webhook** telling Telegram where to send updates
4. **Test from phone** by messaging your bot
5. **Iterate**: Change code → Rebuild container → Test again

The `dev.sh` script automates steps 2-4, reducing friction.

### Why ngrok?

Your laptop doesn't have a public IP address that Telegram can reach. ngrok creates a secure tunnel:

```
Telegram → ngrok servers → your laptop:8000
```

During development, ngrok gives you a URL like `https://abc123.ngrok.io`. You tell Telegram to send webhooks there, and they arrive at your local container.

**The free tier limitation**: ngrok's free plan gives you a random URL each time you restart. You can get a free static subdomain now, which eliminates re-registering the webhook constantly.

### Debugging Async Issues

Async code can be tricky to debug because multiple things happen concurrently. Strategies:

1. **Structured logging**: Every log line includes `task_id` so you can trace a specific task's journey
2. **Execution reports**: Each task generates a detailed log file showing exactly what happened
3. **Container logs**: `docker-compose logs -f` shows real-time output from all workers

When something goes wrong, you can:
- Check container logs for errors
- Download the execution log from Telegram
- Inspect `/app/workspace/task_xxx/` for intermediate files

---

## Part 7: Transitioning to Production

### When to Move Off Local

You're ready to deploy when:
- The adapter correctly calls your agent system
- Tasks complete reliably
- Results arrive as expected in Telegram
- You're tired of restarting ngrok

### Deployment Target Recommendation

For your use case (personal + up to 5 family members), **Fly.io** is the recommended platform:

**Why Fly.io?**
- $5-11/month for small instances
- Supports long-running processes (no timeout limits)
- Easy Docker deployment (`fly deploy`)
- Built-in secrets management
- Auto-stop when idle (cost savings)
- Persistent volumes for workspace

**Deployment is essentially:**
```bash
fly launch                    # One-time setup
fly secrets set ANTHROPIC_API_KEY=sk-...
fly secrets set TELEGRAM_BOT_TOKEN=...
fly deploy                    # Deploy container
```

Then update your Telegram webhook to the permanent Fly.io URL, and you're done.

### What Changes in Production

Very little code changes. The differences are:
- Secrets come from `fly secrets` instead of local files
- Webhook URL is permanent (no more ngrok)
- Logs go to `fly logs` instead of your terminal
- Persistent volume is managed by Fly.io instead of Docker

The same container image runs in both environments.

---

## Part 8: Mental Models for the AI Programmer

### Key Abstractions

When implementing, keep these mental models:

1. **The bot is just a messenger**: It receives requests, queues them, and delivers results. It doesn't know or care what the agent does.

2. **The adapter is the translator**: It converts between the bot's expectations and your agent's interface. All agent-specific logic lives here.

3. **Tasks are independent**: Each task has its own ID, workspace, and lifecycle. They don't know about each other.

4. **Callbacks close the loop**: The async pattern only works because we proactively send results back. The user doesn't poll—we push.

### Common Pitfalls

**Pitfall 1: Blocking the webhook handler**
```python
# WRONG - blocks Telegram, causes timeout
@app.post("/webhook")
async def handle(request):
    result = await run_agent(prompt)  # Takes 2 minutes!
    return result

# RIGHT - immediate response, async execution
@app.post("/webhook")
async def handle(request):
    task_id = await task_manager.submit(prompt)
    return {"status": "queued", "task_id": task_id}
```

**Pitfall 2: Forgetting to send results**
If the agent completes but you don't send a message, the user sees nothing. Always have a callback that fires on task completion.

**Pitfall 3: Not handling errors gracefully**
Agents fail sometimes. Network issues happen. Always wrap agent execution in try/except and send meaningful error messages to the user.

**Pitfall 4: Hardcoding configuration**
API keys, user IDs, URLs—all of these should come from config/environment, never hardcoded. This is essential for the local→production transition.

---

## Part 9: Extension Points

### Adding More Commands

To add a new command (e.g., `/research` for research-specific tasks):

1. Create handler function in `telegram_handlers.py`
2. Register it with the bot application
3. Optionally create a specialized adapter function

### Supporting Multiple Platforms

The architecture cleanly separates concerns. To add Slack support later:

1. Add Slack webhook handler (similar to Telegram's)
2. Add Slack-specific result delivery function
3. Task manager remains unchanged (it's platform-agnostic)

### Scaling Up

For heavier usage:
- Add Redis for task persistence (survives restarts)
- Use Huey or ARQ for distributed task execution
- Run multiple worker containers
- Add rate limiting per user

But for personal/family use, the simple asyncio pattern is sufficient.

---

## Summary: What the AI Programmer Should Do

1. **Start with the adapter** (`agent_adapter.py`): Get the existing agent system callable through the standardized interface

2. **Test locally without Telegram first**: Make sure `run_agent()` works when called directly

3. **Add the bot infrastructure**: FastAPI app, task manager, Telegram handlers

4. **Test with ngrok**: Full end-to-end from phone to agent and back

5. **Iterate on UX**: Improve message formatting, error handling, progress feedback

6. **Deploy**: Push to Fly.io when stable

The code companion document provides the implementation details. This document provides the understanding of *why* each piece exists and how they connect.

---

## Appendix: Quick Reference

### File Responsibilities

| File | Responsibility |
|------|----------------|
| `agent_adapter.py` | Bridge to existing agent system |
| `config.py` | Load secrets and settings |
| `task_manager.py` | Async task queue and execution |
| `telegram_handlers.py` | Bot command handlers |
| `execution_logger.py` | Activity capture for visibility |
| `main.py` | FastAPI app, startup/shutdown |

### Data Flow

```
Phone → Telegram API → Webhook → FastAPI → Task Manager → Queue
                                                            ↓
                                                        Worker
                                                            ↓
                                                        Adapter
                                                            ↓
                                                     Agent System
                                                            ↓
                                                      AgentResult
                                                            ↓
Phone ← Telegram API ← send_message() ← Result Callback ←──┘
```

### Environment Variables

| Variable | Purpose | Required |
|----------|---------|----------|
| `ANTHROPIC_API_KEY` | Claude API access | Yes |
| `TELEGRAM_BOT_TOKEN` | Bot authentication | Yes |
| `WEBHOOK_SECRET` | Verify Telegram requests | Yes |
| `WEBHOOK_URL` | Where Telegram sends updates | Yes |
| `ALLOWED_USER_IDS` | Comma-separated user IDs | Recommended |
| `TASK_TIMEOUT` | Max seconds per task | No (default: 300) |
| `LOG_LEVEL` | Logging verbosity | No (default: INFO) |

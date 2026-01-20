# Commands and Usage Guide

**Last Updated**: 2025-12-30

This is the user-facing guide for interacting with the Universal Agent Telegram bot.

---

## Available Commands

### `/start`
Display welcome message and list of available commands.

### `/help`
Same as `/start`.

### `/agent <your request>`
Submit a task for the agent to execute.

**Examples:**
```
/agent Research the latest AI trends and create a summary
/agent Find information about quantum computing breakthroughs
/agent Create a report on renewable energy
```

### `/status`
Check the status of your recent tasks (last 5).

**Status Icons:**
- ⏳ Pending (waiting in queue)
- 🔄 Running (currently executing)
- ✅ Completed (finished successfully)
- ❌ Error (failed)

---

## How It Works

1. **You send** `/agent <request>`
2. **Bot replies**: "✅ Task Queued: `abc12345`"
3. **Bot notifies you** when task starts running
4. **Bot notifies you** when task completes (with result preview)
5. **Bot sends** the execution log file as attachment

---

## Understanding Responses

### Task Queued
```
✅ Task Queued: `abc12345`

I will notify you when it starts and finishes.
```

### Task Running
```
Task Update: `abc12345`
Status: RUNNING
```

### Task Completed
```
Task Update: `abc12345`
Status: COMPLETED

**Result Preview:**
Here is the summary of AI trends...
```

Plus a log file attachment: `task_abc12345.log`

### Task Failed
```
Task Update: `abc12345`
Status: ERROR

Error: <error message>
```

---

## Tips for Best Results

1. **Be Specific**: "Research AI trends in healthcare 2024" is better than "AI stuff"
2. **One Task at a Time**: Wait for the current task to complete before submitting another
3. **Check Status**: Use `/status` if you're unsure what's happening

---

## Security Note

This bot is secured by user ID whitelist. Only pre-approved Telegram accounts can use it. If you receive "⛔ Unauthorized access", contact the bot administrator.

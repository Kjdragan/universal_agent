---
title: Compaction settings | Letta Docs
description: Configure how Letta agents compact their conversation history to manage context window limits.
---

When an agent’s conversation history grows too long to fit in its context window, Letta automatically **compacts** (summarizes) older messages to make room for new ones. The `compaction_settings` field lets you customize how this compaction works.

## Default behavior

If you don’t specify `compaction_settings`, Letta uses sensible defaults:

- **Mode**: `sliding_window` (keeps recent messages, summarizes older ones)
- **Model**: Same as the agent’s main model
- **Sliding window**: `sliding_window_percentage=0.3` (targets keeping \~70% of the most recent history; increases the summarized portion in \~10% steps if needed to fit)
- **Summary limit**: 2000 characters

For most use cases, the defaults work well and you don’t need to configure compaction.

## When to customize compaction

Customize `compaction_settings` when you want to:

- Use a cheaper/faster model for summarization
- Preserve more or less recent context
- Change the summarization strategy
- Customize the summarization prompt

## Compaction settings schema

If you specify `compaction_settings`, the only required field is:

- `model` (string): the summarizer model handle (e.g. `"openai/gpt-4o-mini"`)

All other fields are optional.

| Field                       | Type        | Required | Description                                                 |
| --------------------------- | ----------- | -------- | ----------------------------------------------------------- |
| `model`                     | string      | Yes      | Summarizer model handle (format: provider/model-name)       |
| `model_settings`            | object      | No       | Optional overrides for the summarizer model defaults        |
| `prompt`                    | string      | No       | Custom system prompt for the summarizer                     |
| `prompt_acknowledgement`    | boolean     | No       | Whether to include an acknowledgement post-prompt           |
| `clip_chars`                | int \| null | No       | Max summary length in characters (default: 2000)            |
| `mode`                      | string      | No       | `"sliding_window"` or `"all"` (default: `"sliding_window"`) |
| `sliding_window_percentage` | float       | No       | How aggressively older history is summarized (default: 0.3) |

In the current implementation, higher `sliding_window_percentage` values summarize more of the oldest history (and keep less recent context).

## Prompt behavior

When Letta generates a summary, it makes an LLM call. The message sequence depends on the `prompt_acknowledgement` setting.

**Default (`prompt_acknowledgement=false`):**

```
System: <prompt>
User: <conversation history to summarize>
```

**With acknowledgement (`prompt_acknowledgement=true`):**

```
System: <prompt>
Assistant: <acknowledgement>
User: <conversation history to summarize>
```

When enabled, the acknowledgement is a **prefilled assistant message** injected between the system prompt and user message. This prompting technique:

- Primes the model to output *only* the summary (no preamble like “Here’s a summary:”)
- Prevents the model from asking follow-up questions
- Uses chat continuation — the model continues from where the acknowledgement left off

If you omit `prompt`, Letta uses the default summarization prompt shown below.

### Default prompt

```
You have been interacting with a human user, and are in the middle of a conversation or a task. Write a summary that will allow you (or another instance of yourself) to resume without distruption, even after the conversation history is replaced with this summary. Your summary should be structured, concise, and actionable (if you are in the middle of a task). Include:


1. Task or conversational overview
The user's core request and success criteria you are currently working on.
Any clarifications or constraints they specified.
Any details about the topic of messages that originated the current conversation or task.


2. Current State
What has been completed or discussed so far
Files created, modified, or analyzed (with paths if relevant)
Resources explored or referenced (with URLs if relevant)
What has been discussed or explored so far with the user


3. Next Steps
The next actions or steps you would have taken, if you were to continue the conversation or task.


Keep your summary less than 100 words, do NOT exceed this word limit. Only output the summary, do NOT include anything else in your output.
```

### Default acknowledgement (when `prompt_acknowledgement=true`)

```
Understood, I will respond with a summary of the message (and only the summary, nothing else) once I receive the conversation history. I'm ready.
```

## Example: Custom compaction settings

You can set `compaction_settings` when creating or updating an agent:

- [Python](#tab-panel-383)
- [TypeScript](#tab-panel-384)

```
from letta_client import Letta
import os


client = Letta(api_key=os.getenv("LETTA_API_KEY"))


# When creating an agent
agent = client.agents.create(
    name="my_agent",
    model="openai/gpt-4o",
    compaction_settings={
        "model": "openai/gpt-4o-mini",
        "mode": "sliding_window",
        "sliding_window_percentage": 0.4,
    }
)


# When updating an existing agent
client.agents.update(
    agent.id,
    compaction_settings={
        "model": "openai/gpt-4o-mini",
        "mode": "sliding_window",
        "sliding_window_percentage": 0.2,  # Preserve more context
    }
)
```

The TypeScript SDK may not yet type `compaction_settings`. The examples below use `as any` to pass it through to the API.

```
import Letta from "@letta-ai/letta-client";


const client = new Letta({ apiKey: process.env.LETTA_API_KEY });


// When creating an agent
const agent = await client.agents.create({
  name: "my_agent",
  model: "openai/gpt-4o",
  compaction_settings: {
    model: "openai/gpt-4o-mini",
    mode: "sliding_window",
    sliding_window_percentage: 0.4,
  },
} as any);


// When updating an existing agent
await client.agents.update(agent.id, {
  compaction_settings: {
    model: "openai/gpt-4o-mini",
    mode: "sliding_window",
    sliding_window_percentage: 0.2, // Preserve more context
  },
} as any);
```

## Compaction modes

### Sliding window (default)

In `sliding_window` mode, Letta preserves recent messages and only summarizes older ones:

```
Before compaction (10 messages):
[msg1, msg2, msg3, msg4, msg5, msg6, msg7, msg8, msg9, msg10]
      |---- oldest ~30% summarized ----|


After compaction:
[summary of msg1-3, msg4, msg5, msg6, msg7, msg8, msg9, msg10]
```

The `sliding_window_percentage` controls how aggressively older history is summarized:

- `0.2` = summarize less (keep more recent context)
- `0.5` = summarize more (keep less recent context)

### All mode

In `all` mode, the entire conversation history is summarized:

```
Before compaction:
[msg1, msg2, msg3, msg4, msg5, msg6, msg7, msg8, msg9, msg10]


After compaction:
[summary of entire conversation]
```

Use `all` mode when you need maximum space reduction.

## Use cases

### Cost optimization

Use a smaller model for summarization while keeping a powerful main model:

```
agent = client.agents.create(
    model="anthropic/claude-sonnet-4-20250514",
    compaction_settings={
        "model": "openai/gpt-4o-mini",
    },
)
```

### Preserve more context

Keep more recent messages by lowering `sliding_window_percentage`:

```
compaction_settings={
    "model": "openai/gpt-4o-mini",
    "sliding_window_percentage": 0.2,
}
```

### Longer summaries

Allow more detailed summaries by increasing `clip_chars`:

```
compaction_settings={
    "model": "openai/gpt-4o-mini",
    "clip_chars": 4000,
}
```

## Related

[Context Engineering ](/guides/agents/context-engineering/index.md)Learn about managing agent context windows

[Memory Overview ](/guides/agents/memory/index.md)Understand how agent memory works in Letta

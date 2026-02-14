---
title: ZA Model Performance Tester
date: 2026-02-14
status: draft
---

# ZA Model Performance Tester

This repo includes a small, standalone benchmark script to sanity-check end-to-end responsiveness for our Anthropic-compatible Z.ai gateway across the three Claude Code model aliases:

- `opus`
- `sonnet`
- `haiku`

It is intended for quick triage when "the models feel slow" or when agentic loops appear to stall.

## What It Measures

The script uses `claude-agent-sdk` to run a single query-response cycle against the configured Claude Code backend and measures:

- `connect_s`: time to start/connect the Claude Code process for that model (startup overhead, once per model).
- `wall_s`: end-to-end wall time per query (client `query()` -> streamed response -> `ResultMessage`).
- `first_text_s`: time to first streamed text token/chunk (first `TextBlock`).

The benchmark explicitly disables tools (`tools=[]`, `mcp_servers={}`) to isolate base inference latency rather than tool/MCP latency.

## Script Location

- `scripts/bench_agent_sdk_model_latency.py`

## Model Selection Behavior

The script supports two modes via `--model-mode`:

1. `alias` (default): passes `--model opus|sonnet|haiku` to Claude Code.
2. `resolved`: resolves the alias to the underlying model ID using `~/.claude/settings.json`:
   - `ANTHROPIC_DEFAULT_OPUS_MODEL`
   - `ANTHROPIC_DEFAULT_SONNET_MODEL`
   - `ANTHROPIC_DEFAULT_HAIKU_MODEL`

Use `resolved` when you suspect alias routing is incorrect, or when validating that a specific Z.ai model name is performant/available.

## Runs And Prompts (Important)

Defaults:

- `--warmup 1`: one warmup query per model (not included in stats).
- `--runs 3`: three measured queries per model.
- `--prompt`: the same fixed prompt for every run and every model:
  - `Compute 1234*5678. Respond with only the integer, no other text.`

The prompt is not generated dynamically. Unless you pass `--prompt ...`, every model gets the exact same prompt for comparability.

## Usage

Basic benchmark (aliases):

```bash
uv run python scripts/bench_agent_sdk_model_latency.py
```

More samples (recommended when debugging transient slowness):

```bash
uv run python scripts/bench_agent_sdk_model_latency.py --warmup 1 --runs 5 --timeout-s 120
```

Resolved model IDs (reads `~/.claude/settings.json`):

```bash
uv run python scripts/bench_agent_sdk_model_latency.py --model-mode resolved --warmup 1 --runs 5 --timeout-s 120
```

Write machine-readable results:

```bash
uv run python scripts/bench_agent_sdk_model_latency.py --json --out tmp/bench.json
```

## Interpreting Results

Operationally useful signals:

- If `connect_s` is high: Claude Code startup or machine resource contention may be the bottleneck.
- If `first_text_s` is high: the model/backend may be queued or slow to produce first tokens.
- If `wall_s` is high but `first_text_s` is low: the model may be streaming slowly after it starts responding.
- Frequent `TimeoutError` entries: the backend/model may be unavailable, rate-limited, or wedged. Compare `alias` vs `resolved`.

Notes:

- Expect variance between runs due to backend queueing, caching, and network jitter. Prefer `--runs 5` or more when validating a change.
- Do not compare `connect_s` across machines; compare within the same host/session.


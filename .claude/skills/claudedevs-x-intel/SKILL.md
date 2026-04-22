---
name: claudedevs-x-intel
description: >
  Run the official ClaudeDevs X intelligence lane in the environment where it actually lives,
  write a durable operator summary artifact with links to the packet outputs, and optionally
  email the result. Use when the user wants to manually run the ClaudeDevs sync, inspect the
  newest packet, deliver a report by email, or schedule a Chron job whose command invokes the
  skill directly.
---

# ClaudeDevs X Intel

## Overview

This skill is the operator surface for the existing `@ClaudeDevs` X intelligence subsystem.
It does not replace the lane. It runs the real sync/replay flow, writes an operator report
into the packet directory, and can email the resulting links to Kevin.

## When To Use It

Use this skill when the user asks to:
- run the ClaudeDevs sync now
- see what the latest production packet produced
- email a summary with links to the artifacts
- verify whether the lane is caught up or whether new posts were actually processed

Default posture:
- Prefer the production/VPS environment when the user wants the real persistent run.
- Local runs are only for development or dry checks.

## Core Workflow

### 1. Run the deterministic report script

Use the dedicated script:

```bash
PYTHONPATH=src uv run python -m universal_agent.scripts.claude_code_intel_run_report \
  --profile vps \
  --email-to kevinjdragan@gmail.com
```

What it does:
- runs `claude_code_intel_sync` logic
- runs replay/post-processing unless disabled
- writes `operator_report.md` and `operator_report.json` into the newest packet
- includes durable links to:
  - `digest.md`
  - `candidate_ledger.json`
  - `linked_sources.json`
  - `implementation_opportunities.md`
  - the Claude Code external vault
- optionally emails the operator report using AgentMailService

### 2. Prefer VPS for real runs

The production lane lives on the VPS under `/opt/universal_agent` with persistent artifacts and DB state.
If the user wants the real system, either:
- run this skill from a Simone/VPS session, or
- if you are working from a local checkout, use the VPS runtime instead of claiming the local run is authoritative

### 3. Explain checkpoint behavior when relevant

This lane is checkpointed. Re-runs do not keep reprocessing old posts.

The durable checkpoint is:

```text
artifacts/proactive/claude_code_intel/state.json
```

That state tracks the last seen X post id. A run with no new posts should still create a packet and report, but it should show:
- `new_post_count: 0`
- `action_count: 0`
- no repeated follow-up work

### 4. Use this as the manual operator surface

This skill is intended for manual/on-demand runs.

Recommended invocation:

```text
$claudedevs-x-intel Run the production ClaudeDevs X intelligence sync, write the operator report summary, and email the results to kevinjdragan@gmail.com.
```

The canonical autonomous scheduler remains the built-in `claude_code_intel_sync` system cron job.
That built-in production cron now runs the report entry point and will email Kevin automatically when the poll yields actionable output (`action_count > 0`).

## Expected Outputs

For each run, expect a packet under:

```text
<UA_ARTIFACTS_DIR>/proactive/claude_code_intel/packets/YYYY-MM-DD/HHMMSS__ClaudeDevs/
```

Key files:
- `digest.md`
- `candidate_ledger.json`
- `linked_sources.json`
- `implementation_opportunities.md`
- `operator_report.md`
- `operator_report.json`

## Anti-Patterns

- Do not treat a local desktop run as the production result.
- Do not claim the lane is broken when the packet simply shows `0` new posts.
- Do not create a second intelligence path. This skill must ride the existing `claude_code_intel_sync` + replay flow.
- Do not invent a separate email summary by hand when the deterministic operator report script can generate it.
- Do not use this skill to create a second daily scheduler path while the built-in `claude_code_intel_sync` system job is enabled.

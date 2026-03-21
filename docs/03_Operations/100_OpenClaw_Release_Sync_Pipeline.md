# OpenClaw Release Sync Pipeline

> **Created:** 2026-03-20
> **Status:** Active
> **Schedule:** Biweekly — Tuesday & Friday at 10:13 UTC

## Purpose

Automatically monitors [OpenClaw](https://github.com/openclaw/openclaw) releases and produces actionable reports about features our Universal Agent project could adopt or emulate.

## Architecture

```
GHA Schedule (Tue+Fri) → Stage 1 (Release Scanner) → Stage 2 (VP Sync Agent) → Openclaw Sync Discoveries/
```

### Stage 1: Release Scanner (deterministic, zero LLM)

- **Script:** `src/universal_agent/scripts/openclaw_release_scanner.py`
- Fetches releases via GitHub REST API
- Detects new releases against a state file (`artifacts/openclaw-sync/last_checked.json`)
- Categorizes changes by component area
- Outputs `release_report.json` + `RELEASE_REPORT.md`
- Exit 0 = no new releases; Exit 1 = new releases found (triggers Stage 2)

### Stage 2: Sync Analysis Agent (LLM-powered VP mission)

- **Script:** `src/universal_agent/scripts/openclaw_sync_agent.py`
- Reads Stage 1 report, dispatches VP coder mission via gateway API
- VP analyzes each feature against our codebase
- Detects "recurring innovation gaps" from prior reports
- Outputs `SYNC_REPORT.md` + `sync_analysis.json` to `Openclaw Sync Discoveries/<date>/`

### GitHub Actions Workflow

- **File:** `.github/workflows/openclaw-release-sync.yml`
- Schedule: `cron: '13 10 * * 2,5'` (Tue + Fri 10:13 UTC)
- Manual trigger via `workflow_dispatch` with `force_rescan` and `max_releases` inputs
- Stage 2 connects to VPS via Tailscale SSH (same pattern as doc-drift pipeline)

## Report Structure

Each feature analysis includes:
- Relevance assessment (HIGH/MEDIUM/LOW/NOT_APPLICABLE)
- Recommendation (ADOPT/WATCH/SKIP/INVESTIGATE)
- Gap analysis and implementation guidance
- Effort estimate (S/M/L/XL)
- References to specific OpenClaw files for deeper study

## Key Files

| File | Purpose |
|------|---------|
| `src/universal_agent/scripts/openclaw_release_scanner.py` | Stage 1 scanner |
| `src/universal_agent/scripts/openclaw_sync_agent.py` | Stage 2 VP dispatcher |
| `.github/workflows/openclaw-release-sync.yml` | GHA workflow |
| `artifacts/openclaw-sync/` | Raw release reports + state |
| `Openclaw Sync Discoveries/` | Final analysis reports |

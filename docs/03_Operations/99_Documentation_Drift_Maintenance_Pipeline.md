# Documentation Drift Maintenance Pipeline

**Canonical reference for the automated documentation maintenance system.**
**Last updated:** 2026-03-19

## Overview

The Documentation Drift Maintenance Pipeline is a two-stage automated system that detects and remediates documentation drift (where code changes but corresponding documentation is not updated).

| Stage | Script | Trigger | Purpose |
|-------|--------|---------|---------|
| Stage 1 | `doc_drift_auditor.py` | GHA scheduled workflow (~3:17 AM CDT) | Analyzes git co-change patterns, produces drift report |
| Stage 2 | `doc_maintenance_agent.py` | GHA workflow (inline via Tailscale SSH) | Consumes drift report, dispatches VP fix missions |

## Architecture

### Stage 1: Drift Auditor

- **Location:** GitHub Actions scheduled workflow
- **Output:** `artifacts/doc-drift-reports/<YYYY-MM-DD>/drift_report.json`
- **Commit:** Report is committed directly to `develop` branch
- **Analysis:** Git co-change heuristics to detect code changes without corresponding doc updates

### Stage 2: Maintenance Agent

- **Location:** `src/universal_agent/scripts/doc_maintenance_agent.py`
- **Trigger:** Invoked inline by GHA workflow via Tailscale SSH to VPS, immediately after Stage 1
- **Execution:** On VPS, dispatches VP missions via gateway HTTP API

#### Gateway Dispatch

The maintenance agent dispatches VP missions through the gateway HTTP API:

```
POST /api/v1/ops/vp/missions/dispatch
```

Gateway URL resolution order:
1. `UA_GATEWAY_URL` environment variable
2. `http://localhost:8002` (ops API on VPS)
3. `http://localhost:8001` (main gateway on VPS)
4. `https://app.clearspringcg.com` (public fallback)

#### VP Mission Parameters

| Parameter | Value |
|-----------|-------|
| `vp_id` | `vp.coder.primary` |
| `mission_type` | `doc-maintenance` |
| `reply_mode` | `async` |
| `priority` | `100` |
| `execution_mode` | `sdk` |

## Issue Batching

The agent batches issues by severity to prevent agent stalling (observed with 30KB+ payloads containing 75+ issues):

- **Maximum issues per batch:** 15
- **Severity order:** P0 → P1 → P2
- **Batch naming:** `docs/<severity>-fix-<date>` (with alpha suffix for multiple batches: `-a`, `-b`, etc.)

### Fix Instructions by Category

| Category | Instruction |
|----------|-------------|
| `index_dead_entry` | Remove stale entry from both index files. If file was moved, update the link. |
| `index_orphan` | Add to both `docs/README.md` and `docs/Documentation_Status.md` in the correct section. |
| `broken_link` | Fix the broken link or remove if target was intentionally deleted. |
| `glossary_candidate` | Add project-specific terms to `docs/Glossary.md`. Skip generic terms. |
| `deploy_cochange_violation` | Update `docs/deployment/` to reflect current deployment behavior. |
| `agentic_drift` | Update `AGENTS.md` or workflow/SKILL.md files to match code changes. |
| `code_doc_drift` | Update docs to accurately reflect current code behavior. |

## Retry Logic

The agent implements retry with exponential backoff for gateway unavailability:

| Attempt | Delay |
|---------|-------|
| 1 | Immediate |
| 2 | 30 seconds |
| 3 | 60 seconds |
| 4 | 120 seconds |

If all retries fail, the agent exits with code 1 and the health check will alert Simone.

## VP Mission Workflow

The dispatched VP agent performs:

1. **Branch Creation:** `docs/<severity>-fix-<date>`
2. **Verification:** For each issue, read current doc + source code to confirm drift
3. **Fix:** Only edit files that are genuinely stale
4. **Commit:** Descriptive message with skipped items noted
5. **No Push:** Build system handles push and PR creation automatically

### Verify Before Fixing (Critical)

The drift report uses heuristics and may flag false positives. The VP agent must:

1. Read the current content of the referenced doc file
2. Read the relevant source code
3. Compare: Does the documentation already accurately describe the current code?
4. If YES → Skip this issue, note in commit body
5. If NO → Fix the documentation

## Artifact Locations

| Artifact | Path |
|----------|------|
| Drift reports | `artifacts/doc-drift-reports/<date>/drift_report.json` |
| VPS staging path | `/opt/universal-agent-staging/artifacts/doc-drift-reports/` |
| VPS production path | `/opt/universal_agent/artifacts/doc-drift-reports/` |

## Authentication

The agent requires an ops/auth token resolved from:

1. `UA_OPS_TOKEN` environment variable
2. `AUTH_TOKEN` environment variable

On the VPS, these are injected via Infisical at runtime.

## Related Documentation

- [Documentation Rules](../README.md) — Index and documentation standards
- [AGENTS.md](../../AGENTS.md) — Repository agent rules
- [Gateway Ops API](../04_API_Reference/Ops_API.md) — Mission dispatch endpoint

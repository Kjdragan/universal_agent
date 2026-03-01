# CSI Rebuild Master Plan

Last updated: 2026-03-01 (America/Chicago)
Owner: Codex execution track

## Goal
Rebuild CSI into a reliable, useful trend-research subsystem that:
- ingests YouTube RSS and Reddit discovery data correctly,
- produces both narrative reports and ranked opportunities,
- drives useful UA follow-up behavior,
- is extensible for future sources (for example Threads).

## Delivery Principles
- Reliability before feature expansion.
- Explicit source routing boundaries.
- Evidence-backed confidence (heuristic fallback only).
- Operational observability and auto-remediation by default.

## Work Phases
1. Branch/worktree/source-control cleanup and baseline docs.
2. Reliability foundation (delivery, cursor integrity, replay, routing hard guards).
3. Output contract upgrade (narrative + structured ranked opportunities).
4. Specialist confidence and orchestration upgrade.
5. Dashboard/operator clarity and runbook finalization.

## Definition of Done
- Undelivered events <= 2% over 24h rolling window.
- DLQ replay success >= 95% for transient failures within 60 minutes.
- CSI emits both narrative + ranked opportunities in active windows.
- Dashboard clearly separates ingestion health, delivery health, and analysis quality.
- Main branch is clean and ready for collaborative commits.


---
name: heartbeat_schema_fix
description: Heartbeat findings JSON field names must match Mission Control frontend SystemResourcesPayload type
type: feedback
---

# Heartbeat Findings JSON Schema Fix (2026-03-16)

## Problem
The HEARTBEAT.md prompt spec uses field names that don't match the Mission Control frontend:
- cpu_ratio_1m should be load_per_core
- ram_used_gib should be ram_used_gb
- ram_pct should be ram_percent
- active_sessions should be active_agent_sessions
- gateway_errors_30min should be gateway_errors_30m

## Impact
System Resources panel shows undefined/null metrics when gateway reads heartbeat findings file directly.

## Fix
All heartbeat sessions must use the frontend field names. Full reference at work_products/heartbeat_schema_reference.md.

## Root Cause
The HEARTBEAT.md prompt embedded in the system prompt has an example JSON schema with wrong field names. The prompt template should be updated.

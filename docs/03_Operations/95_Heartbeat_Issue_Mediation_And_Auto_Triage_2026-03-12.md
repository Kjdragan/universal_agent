# Heartbeat Issue Mediation and Auto-Triage (2026-03-12)

## Summary

Non-OK heartbeats are now treated as operational investigations, not passive informational logs.

The current flow is:

1. heartbeat writes a human report to `work_products/system_health_latest.md`
2. heartbeat should also write a structured findings contract to `work_products/heartbeat_findings_latest.json`
3. gateway classifies the findings and persists an actionable `autonomous_heartbeat_completed` notification
4. gateway automatically dispatches Simone for investigation
5. Simone writes an investigation summary back into the hook run workspace
6. gateway updates the original heartbeat notification with mediation status
7. if operator review is required, Kevin gets both:
   - a dashboard notification
   - an AgentMail summary from Simone

This is deliberately an auto-investigation workflow, not auto-remediation.

## Scope and Safety

Enabled behavior:

- actionable heartbeat notifications for non-OK findings
- automatic Simone routing for non-OK findings
- structured investigation status in the Events dashboard
- operator notification for unknown or out-of-rule findings

Explicitly excluded in v1:

- automatic code edits
- automatic deploys
- automatic shell remediation
- Todoist task creation

## Structured Findings Contract

The machine-readable artifact is:

- `work_products/heartbeat_findings_latest.json`

Expected top-level fields:

- `version`
- `overall_status`
- `generated_at_utc`
- `source`
- `summary`
- `findings`

Each `findings[]` row should include:

- `finding_id`
- `category`
- `severity`
- `metric_key`
- `observed_value`
- `threshold_text`
- `known_rule_match`
- `confidence`
- `title`
- `recommendation`
- `runbook_command`
- `metadata`

If the JSON file is missing or malformed, the gateway falls back to the markdown report and emits a `heartbeat_findings_parse_failed` notification. Auto-triage still proceeds.

## Notification Model

Primary notification:

- kind: `autonomous_heartbeat_completed`

For non-OK findings, the notification is now:

- `requires_action=true`
- `severity=warning` or `severity=error`

Relevant metadata fields:

- `heartbeat_findings_status`
- `heartbeat_findings_count`
- `heartbeat_known_rule_count`
- `heartbeat_unknown_rule_count`
- `heartbeat_findings_artifact_path`
- `heartbeat_findings_artifact_href`
- `heartbeat_mediation_status`
- `heartbeat_mediation_session_key`
- `heartbeat_mediation_dispatched_at`
- `heartbeat_operator_review_required`
- `primary_runbook_command`

Additional mediation notifications:

- `heartbeat_findings_parse_failed`
- `heartbeat_mediation_dispatched`
- `heartbeat_mediation_dispatch_failed`
- `heartbeat_investigation_completed`
- `heartbeat_operator_review_required`
- `heartbeat_operator_review_sent`

## Simone Dispatch Contract

Automatic heartbeat routing uses the hook system with:

- action name: `AutoHeartbeatInvestigation`
- session key shape: `simone_heartbeat_<notification_suffix>`

Simone is instructed to:

- investigate the findings
- correlate with session and artifact context
- determine likely cause
- recommend next step
- write:
  - `work_products/heartbeat_investigation_summary.md`
  - optionally `work_products/heartbeat_investigation_summary.json`

Expected JSON fields:

- `version`
- `source_notification_id`
- `session_key`
- `classification`
- `operator_review_required`
- `recommended_next_step`
- `proposed_changes`
- `email_summary`

## Operator Notification Policy

Default policy:

- all non-OK heartbeat findings auto-dispatch Simone
- known-rule findings do not email Kevin by default
- unknown findings, or investigations marked `operator_review_required=true`, do email Kevin

Delivery path:

- AgentMail from Simone

Dashboard visibility remains the primary operations surface. Email is a secondary escalation channel for review-required cases.

## Cooldown and Deduplication

Equivalent findings are suppressed from repeated auto-dispatch for the configured cooldown window.

Current default:

- `cooldown_minutes = 60`

The notification still appears. Only duplicate Simone dispatch is suppressed.

## UI Behavior

The Events page now exposes mediation state with badges such as:

- `Auto-triage dispatched`
- `Investigation completed`
- `Operator review required`

Heartbeat events can also expose:

- `Open Findings`
- `Open Investigation`
- `Copy Runbook`
- `Send to Simone`

Manual handoff remains available as a fallback.

## Operational Guidance

Use this flow when a heartbeat finds something material but not yet obviously remediable.

Do not expand this into automatic remediation casually. Any future auto-remediation should be restricted to a small whitelist of deterministic, reversible actions and documented separately.

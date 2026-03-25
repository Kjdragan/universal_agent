# 104. Run/Attempt Lifecycle and Nomenclature Migration Plan (2026-03-24)

## Purpose

This document is the canonical migration plan for the Universal Agent lifecycle refactor that replaces overloaded durable `session` concepts with the following model:

- `Run`: the durable logical unit of work
- `Attempt`: one execution try of a run
- `Execution Session`: the temporary live provider/runtime process
- `Run Workspace`: the durable filesystem evidence bundle for a run

This plan is intentionally written as a handoff-ready execution record. It defines the migration scope, terminology, packet sequencing, rollback expectations, CSI integration handling, and documentation obligations for the cutover.

## Why This Migration Exists

The current system uses `session` to mean multiple different things:

- a live gateway/provider session
- a durable workspace on disk
- a logical workflow identity
- a historical artifact bundle

That ambiguity has become operationally expensive. In particular:

- archived workspace directories can be mistaken for runnable inventory
- heartbeat and other automation paths can revive completed work
- retries and interruptions are not represented as first-class lifecycle concepts
- external contributors and coding agents cannot infer the architecture from names alone

The goal of this migration is to make the system legible and correct at the same time.

## Canonical Terminology

### Run

The durable workflow record for one logical unit of work.

Examples:

- one email triage workflow
- one YouTube processing workflow
- one heartbeat investigation
- one CSI-triggered analyst follow-up inside UA

### Attempt

One execution try of a run.

Retries create additional attempts under the same run. Attempts are the retry boundary. Runs are the business boundary.

### Execution Session

The live provider/runtime process used by an active attempt.

This remains the correct place to use the word `session` for runtime execution surfaces such as gateway sessions, WebSocket sessions, provider sessions, and browser sessions.

### Run Workspace

The durable filesystem bundle containing the operator-visible evidence for a run:

- checkpoint
- transcript
- trace
- artifacts
- work products
- per-attempt logs

## Terminology Rule

Use `session` only when it truly means a live session concept.

Do **not** use `session` for:

- durable workflow identity
- durable artifact bundle
- retry history container

Use `run`, `attempt`, and `run workspace` instead.

## Scope

This migration combines three tracks into one coordinated effort:

1. Lifecycle refactor:
   move durable execution authority to run/attempt state
2. Nomenclature refactor:
   remove durable use of overloaded `session` terms
3. CSI boundary cleanup:
   preserve CSI as a separate subsystem while removing UA-side dependence on legacy `session_*` durable naming

This is a **full conversion**. Historical data remains readable, but the target architecture is run-based, not session-based.

## Current-State Constraints

### Durable Execution Already Leans Toward Runs

The existing durable `runs` model already includes:

- provider session attachment
- checkpoint references
- run leases
- cancellation
- final artifact references

That means the migration should extend the current durable model rather than inventing a new parallel authority.

### Durable Evidence Already Lives in Workspaces

The current workspace model already preserves the operator-visible evidence bundle:

- `trace.json`
- transcript
- checkpoint
- work products

That durability model is worth keeping. The migration should rename and formalize it, not discard it.

### Current Inventory Semantics Are Mixed

Today, broad session listings combine:

- active runtime sessions
- archived workspace directories
- historical or derived session summaries

This is one of the root causes of the current memory/resource problems and must be corrected early in the migration.

## CSI Integration and Boundary Rules

CSI remains a separate subsystem in architecture, even though its code currently lives in this checkout and on the VPS alongside UA.

### CSI Stays Authoritative For CSI-Native Analytics State

CSI DB remains authoritative for:

- source health
- delivery health
- reliability SLO
- opportunity bundles
- specialist evidence state
- CSI-native reporting data

UA run state must not attempt to replace CSI DB as the source of truth for those domains.

### UA Owns CSI-Triggered Execution Inside UA

When CSI causes UA to execute work, that work becomes a normal UA run.

Examples:

- CSI-triggered specialist follow-up
- CSI-triggered analyst action
- CSI-triggered operator remediation work
- CSI human-review escalation

Those runs must carry explicit CSI origin metadata.

### CSI Rollback Scope

CSI database rollback is **out of scope** for this migration.

The required rollback guarantees are:

- code/config/env compatibility
- UA runtime compatibility
- UA workspace readability
- UA <-> CSI integration contract compatibility

If CSI data must be regenerated, that is acceptable.

## Target Data Model

### `runs`

Extend the existing `runs` table with:

- `workspace_dir`
- `run_kind`
- `trigger_source`
- `dedup_key`
- `run_policy`
- `interrupt_policy`
- `terminal_reason`
- `attempt_count`
- `latest_attempt_id`
- `last_success_attempt_id`
- `canonical_attempt_id`
- `external_origin`
- `external_origin_id`
- `external_correlation_id`

For CSI-triggered UA work:

- `trigger_source = "csi"`
- `external_origin = "csi_ingester"`
- `external_origin_id` should reference CSI event/report/topic identity
- `external_correlation_id` should reference CSI correlation/batch/request identity where available

### `run_attempts`

Add a new `run_attempts` table with:

- `attempt_id`
- `run_id`
- `attempt_number`
- `status`
- `lease_owner`
- `lease_expires_at`
- `provider_session_id`
- `started_at`
- `ended_at`
- `failure_class`
- `failure_reason`
- `retry_reason`
- `retry_backoff_seconds`
- `workspace_subdir`
- `summary_json`

## Target Filesystem Layout

### Canonical Run Workspace

`AGENT_RUN_WORKSPACES/run_<id>/`

Contents:

- `run_manifest.json`
- `run_checkpoint.json`
- `run_checkpoint.md`
- `trace.json`
- `transcript.md`
- `work_products/`
- `turns/`
- `activity.jsonl`
- `attempts/`

### Attempt Directory

`AGENT_RUN_WORKSPACES/run_<id>/attempts/001/`

Contents:

- `attempt_meta.json`
- `run.log`
- `trace.json`
- `transcript.md`
- `work_products/`

### Canonical Artifact Rule

The root run workspace must always expose the canonical attempt’s durable outputs:

- latest successful attempt if one exists
- otherwise latest terminal attempt

## Inventory Split

### Run Catalog

Used for:

- browsing durable historical work
- ops listing
- storage browsing
- rehydration readiness
- dashboard listing of durable work

### Live Execution Registry

Used for:

- heartbeat targeting
- cron/mail/system-command live targeting
- active lease accounting
- active execution supervision

### Hard Rule

No caller that wants runnable targets may use historical workspace scans or mixed session listings.

## Rollback and Change-Control Protocol

This migration is risky enough to require an explicit change-control pattern.

### Pre-Cutover Baseline

The approved baseline for the migration is:

- commit `9ec404ba`

Before code packets begin:

- create an annotated git tag on the approved baseline
- create a dedicated migration branch from that baseline
- record the baseline tag/SHA in the migration ledger

### Rollback Scope

Rollback must restore:

- source code
- configuration and environment contracts
- UA runtime compatibility
- UA workspace/readability behavior

Rollback does **not** need to restore CSI database contents.

### Packet Gates

Each packet must have:

- explicit success criteria
- explicit verification checklist
- explicit rollback note

Do not start a later packet until the prior packet’s gate passes.

## Packet Plan

### Packet 0: Migration Ledger and Terminology Foundation

Deliverables:

- this document
- index updates
- glossary updates
- nomenclature classification ledger covering all high-risk `session` usages
- baseline tag/SHA record

Exit criteria:

- docs are indexed
- glossary is updated
- high-risk `session` usages are classified

### Packet 1: Inventory Split

Deliverables:

- `RunCatalogService`
- `LiveExecutionRegistry`
- heartbeat/cron/mail/system-command targeting moved to live registry

Exit criteria:

- archived durable work can no longer be revived by broad inventory scans

### Packet 2: Durable Run Authority

Deliverables:

- `runs` extension
- `run_attempts`
- `run_manifest.json`
- `activity.jsonl`

Exit criteria:

- new durable work is run-based

### Packet 3: Run Workspace Naming

Deliverables:

- durable child folders renamed from `session_*` to `run_*`
- helper env vars and marker files renamed to run-workspace terms

Exit criteria:

- durable workspace naming uses `run`

### Packet 4: CSI Fallback Refactor

Deliverables:

- remove UA-side `session_hook_csi_*` fallback dependence
- CSI dashboard/report fallback logic uses:
  1. CSI DB
  2. activity events
  3. UA run metadata

Exit criteria:

- CSI dashboard/report paths no longer depend on legacy durable session naming

### Packet 5: Attemptized Execution

Deliverables:

- retries create `attempts/NNN/` under one run workspace
- canonical root artifact promotion

Exit criteria:

- retries no longer create sibling top-level durable workspaces

### Packet 6: Execution Session Cleanup

Deliverables:

- close non-sticky execution sessions on terminal completion
- preserve sticky interactive policies for chat/Telegram/daemons

Exit criteria:

- completed automation work does not linger live

### Packet 7: API/UI/Docs Cutover

Deliverables:

- durable browsing APIs renamed to run terminology
- UI language updated
- docs and prompts updated

Exit criteria:

- the architecture reads naturally to outside engineers

## Required Documentation Updates

At minimum, the migration must update:

- `docs/01_Architecture/02_Gateway_Sessions_And_Execution.md`
- `docs/03_Operations/90_Artifacts_Workspaces_And_Remote_Sync_Source_Of_Truth_2026-03-06.md`
- `docs/03_Operations/92_CSI_Architecture_And_Operations_Source_Of_Truth_2026-03-06.md`
- `docs/04_CSI/CSI_Master_Architecture.md`
- `docs/Glossary.md`

## Session Usage Classification Rule

Before code changes, every high-risk `session` usage must be classified into one of:

- keep as `session`
- rename to `run`
- rename to `run_workspace`
- rename to `attempt`

Examples of `keep as session`:

- `provider_session_id`
- gateway/WebSocket session
- browser session
- VP session lease

Examples of `rename`:

- durable workflow identity -> `run`
- durable filesystem evidence bundle -> `run workspace`
- retry-specific execution try -> `attempt`

## Acceptance Criteria

The migration is complete when:

- durable work is represented as runs and attempts
- live execution targeting uses only live execution inventory
- durable workspace naming uses run terminology
- CSI DB remains authoritative for CSI-native analytics
- CSI-triggered UA work is linked via run metadata, not workspace naming
- durable docs no longer misuse `session` for durable work

## Implementation Notes

- This migration is intentionally phased to avoid mixing terminology cleanup, lifecycle refactor, and CSI boundary cleanup in a single uncontrolled branch.
- Historical workspaces may remain readable even if their old naming remains present in archived data.
- The migration should prefer clear architectural names over preserving ambiguous legacy names for convenience.

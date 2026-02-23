# 76. Sandbox Permissioning And Exception Profile (2026-02-23)

## Objective
Define a practical operating profile that keeps sandbox safety while eliminating repetitive permission friction during normal development/deploy/debug loops.

## Problem Statement
We repeatedly hit avoidable friction from:
1. command-segmentation edge cases in long `ssh` one-liners,
2. write operations that are expected to require elevated permission (`.git/index.lock`, remote SSH/network ops),
3. ad-hoc command shapes instead of stable, pre-approved command prefixes.

This is an execution hygiene issue, not a reason to remove sandboxing.

## Root Cause Model
1. The execution layer evaluates permissions per command segment (pipes/`&&`/subshells split execution).
2. Complex inline shell increases quoting/splitting errors and triggers avoidable denials.
3. Some operations are expected to require escalation (git write, remote SSH, service control), so trying unprivileged first creates noisy retries.

## Standard Execution Profile (Mandatory)

### A) Remote operations
1. Use `scripts/vpsctl.sh` as the default interface.
2. Prefer:
   1. `scripts/vpsctl.sh push ...`
   2. `scripts/vpsctl.sh restart ...`
   3. `scripts/vpsctl.sh status ...`
   4. `scripts/vpsctl.sh logs ...`
   5. `scripts/vpsctl.sh doctor`
3. For multi-step remote logic, use:
   1. `scripts/vpsctl.sh run-file <script>`
4. Avoid raw multiline `ssh "... && ... | ..."` for routine operations.

### B) Git operations
1. Treat `git commit` as escalation-first in this environment.
2. Use normal non-interactive commands only:
   1. `git add ...`
   2. `git commit -m "..."`
   3. `git push ...`
3. Do not use interactive git flows in automation loops.

### C) Health-check flow (post-change)
1. `scripts/vpsctl.sh status all`
2. `scripts/vpsctl.sh doctor`
3. For CSI stream routes:
   1. check digest services/timers,
   2. verify `*_SENT=1` in journal logs.

## Required Prefix Approval Baseline
The operating baseline should maintain persistent approvals for:
1. `scripts/vpsctl.sh`
2. `./scripts/deploy_vps.sh`
3. `ssh -i ~/.ssh/id_ed25519 ...` (tailnet host)
4. `scp -i ~/.ssh/id_ed25519 ...`
5. `git add`, `git commit`, `git push`
6. `uv run` (project runtime/test scripts)

If an operation falls outside this baseline, first convert it into one of these command families before introducing new ad-hoc command patterns.

## Quality Gates
This profile is considered healthy when:
1. routine deploy/debug work is completed without multiline `ssh` quoting failures,
2. expected escalations happen once (not fail-then-retry),
3. service health checks pass after each change,
4. logs show functional outcomes, not permission churn.

## Current Status (2026-02-23)
1. Telegram stream routing now isolated by dedicated channel IDs:
   1. RSS -> `UA RSS Feed`
   2. Reddit -> `UA Reddit Feed`
   3. Tutorial -> `UA Tutorial Feed`
2. Tutorial digest verified after new playlist event:
   1. `PLAYLIST_TUTORIAL_NEW_COUNT=1`
   2. `PLAYLIST_TUTORIAL_SENT=1`
3. Telegram middleware patched to ignore channel updates without `effective_user` and stop repeated error traces.


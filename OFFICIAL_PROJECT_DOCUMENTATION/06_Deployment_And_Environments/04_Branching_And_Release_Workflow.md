# Branching and Release Workflow

Last updated: March 12, 2026

## Purpose

This document defines the current branch policy for day-to-day coding, staging validation, and production release.

Use this document as the operational source of truth for how code should move through the repository.

## Canonical Rule

There are three practical branch roles:

1. `feature/...` branches for active coding work
2. `develop` for integrated staging validation
3. `main` for production release

Do not use long-lived historical branches as the default development lane.

## Current Deployment Contract

GitHub Actions is the only supported application deployment path.

1. Open a pull request to `develop` to run Codex review on the proposed change.
2. Merge to `develop` to deploy to staging automatically.
3. Promote the exact validated `develop` SHA to `main` to deploy to production automatically.

Supporting references:

- `docs/deployment/ci_cd_pipeline.md`
- `docs/deployment/architecture_overview.md`
- `AGENTS.md`

## Environment Mapping

| Branch | Role | Deployment Target |
|------|------|-------------------|
| `feature/...` | local development and review | no automatic deploy |
| `develop` | staging integration branch | staging VPS |
| `main` | release branch | production VPS |

## Required Working Method

### 1. Start New Work

Create new work from `develop`.

```bash
git checkout develop
git pull --ff-only
git checkout -b feature/my-change
```

### 2. Do Local Development

Work on the `feature/...` branch until the change is locally ready.

For the main product UI and gateway, local development should happen from the repo checkout in **HQ dev mode**, not from the local worker environment.

Local checkout roles:

1. `/home/kjdragan/lrepos/universal_agent` = HQ dev lane
2. `~/universal_agent_factory` = optional local worker lane

If localhost starts returning role-based `403` responses on HQ dashboard pages, the repo checkout is almost certainly pointed at the worker environment instead of `kevins-desktop-hq-dev`.

Typical local loop:

1. code
2. run targeted tests
3. build affected surfaces as needed
4. commit the feature branch

### 3. Promote to Staging

When the change should be tested live on the VPS-backed staging environment:

1. open a pull request from the feature branch into `develop`
2. let Codex review the PR, or let the workflow soft-skip if `OPENAI_API_KEY` is not configured yet
3. merge or fast-forward the feature branch into `develop`
4. wait for the `Deploy Staging` workflow to pass
5. validate the live staging environment

### 4. Promote to Production

Only after staging validation is acceptable:

1. record the exact validated `develop` commit SHA
2. run the manual promotion workflow with that SHA
3. let the workflow fast-forward `main` to that SHA
4. wait for the `Deploy Production` workflow to pass
5. validate production

## What Not To Do

1. Do not do normal coding directly on `main`.
2. Do not treat `dev-parallel` as the active integration branch.
3. Do not use `scripts/deploy_vps.sh`, `scripts/vpsctl.sh`, `ssh`, `scp`, or `rsync` as the default application deployment path.

Those older scripts are legacy or break-glass tooling only.

## Status Snapshot As Of March 12, 2026

This snapshot explains why the current branch policy was chosen.

1. `develop` and `main` were aligned at the same latest commit.
2. `dev-parallel` was behind both `develop` and `main` by 37 commits.
3. Staging deploy from `develop` was green.
4. Production deploy from `main` was green.

That means:

1. `develop` is the correct active integration branch.
2. `main` is a working production branch.
3. `dev-parallel` is a stale historical branch and should not be used as the normal base for future work.

## Practical Usage

### If you want staging only

Tell the agent to:

`Open or update a PR from my current feature branch into develop, run Codex review, merge it if acceptable, and verify staging.`

### If you want full rollout

Tell the agent to:

`Open or update a PR from my current feature branch into develop, run Codex review, merge it if acceptable, verify staging, then promote the validated develop SHA to main and verify production.`

## Summary

The default operating model is:

1. branch from `develop`
2. code on `feature/...`
3. review and merge through a PR into `develop`
4. deploy and validate through `develop`
5. promote the exact validated `develop` SHA to `main`
6. release through `main`

## 1. One-Minute Cheat Sheet

Use this if you just want the shortest correct explanation.

1. Start new work from `develop`.
2. Do your coding on a `feature/...` branch.
3. Open a PR into `develop` when you want Codex review and staging deployment.
4. Promote only the exact validated `develop` SHA to `main`.

Short meanings:

1. `feature/...` = your working branch
2. `develop` = staging branch
3. `main` = production branch

## 2. Exact Git Commands

### Start a new feature

```bash
git checkout develop
git pull --ff-only
git checkout -b feature/my-change
```

### Promote to staging only

```bash
git push -u origin feature/my-change
gh pr create --base develop --head feature/my-change --fill
```

That opens the reviewed path. After the PR is approved and merged, the staging deployment pipeline runs from `develop`.

### Promote to production after staging passes

```bash
gh workflow run "Promote Validated Develop To Main" -f develop_sha=<validated_sha>
```

That fast-forwards `main` to the exact validated `develop` commit and then triggers the production deployment pipeline.

## 3. Branch Flow Diagram

```text
feature/my-change
        |
        v
  PR to develop
 (Codex review)
        |
        v
     develop
   (staging deploy)
        |
        v
 promote exact SHA
        |
        v
       main
 (production deploy)
```

Read it this way:

1. work starts on `feature/...`
2. code review happens on the PR to `develop`
3. live validation happens through `develop`
4. release happens by promoting the validated `develop` SHA to `main`

For local runtime mode details, see:

- `OFFICIAL_PROJECT_DOCUMENTATION/06_Deployment_And_Environments/05_Local_Runtime_Modes.md`

If deployment behavior changes later, update this file together with the GitHub Actions workflow documentation.

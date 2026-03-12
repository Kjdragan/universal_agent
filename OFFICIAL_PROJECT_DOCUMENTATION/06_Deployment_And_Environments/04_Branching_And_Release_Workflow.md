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

1. Push or merge to `develop` to deploy to staging automatically.
2. Push or merge to `main` to deploy to production automatically.

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

Typical local loop:

1. code
2. run targeted tests
3. build affected surfaces as needed
4. commit the feature branch

### 3. Promote to Staging

When the change should be tested live on the VPS-backed staging environment:

1. merge or fast-forward the feature branch into `develop`
2. push `develop`
3. wait for the `Deploy Staging` workflow to pass
4. validate the live staging environment

### 4. Promote to Production

Only after staging validation is acceptable:

1. merge or fast-forward `develop` into `main`
2. push `main`
3. wait for the `Deploy Production` workflow to pass
4. validate production

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

`Promote my current feature branch to develop and verify staging.`

### If you want full rollout

Tell the agent to:

`Promote my current feature branch to develop, verify staging, then promote develop to main and verify production.`

## Summary

The default operating model is:

1. branch from `develop`
2. code on `feature/...`
3. deploy and validate through `develop`
4. release through `main`

If deployment behavior changes later, update this file together with the GitHub Actions workflow documentation.

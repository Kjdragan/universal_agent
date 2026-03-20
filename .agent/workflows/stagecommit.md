---
description: Commit changes and deploy to staging only — does NOT touch production
---
// turbo-all

# Stage Commit

Commit all changes on `feature/latest2`, push to `develop`, and verify staging deployment.
Production remains untouched. Use `/productioncommit` or `/promotecommit` to go further.

## Pre-flight

1. Ensure you are on `feature/latest2`:

   ```bash
   git checkout feature/latest2
   ```

## Commit & Push

2. Run `git status --short` to review pending changes.
3. Run `git add .` to stage all changes.
4. Run `git diff --staged --stat` to confirm what will be committed.
5. Generate a commit message (conventional format: `feat:`, `fix:`, `docs:`, `chore:`).
6. Run `git commit -m "YOUR_GENERATED_MESSAGE"`.
7. Push to the feature branch AND to develop:

   ```bash
   git push origin feature/latest2
   git push origin HEAD:develop
   ```

## Verify Staging (MANDATORY GATE)

8. Verify staging was triggered:

   ```bash
   gh run list --workflow="deploy-staging.yml" --limit 1 --json status,conclusion,headSha,databaseId
   ```

9. Wait for staging to complete:

   ```bash
   gh run watch <RUN_ID> --exit-status
   ```

10. **GATE**: Confirm `conclusion: success`. If failed: `gh run view <RUN_ID> --log-failed` and report.
11. Report: "✅ Staging deployed (run #NNN). Production unchanged. Use `/promotecommit` to promote."

## Post-flight

12. Confirm you are still on `feature/latest2` (do NOT leave on `develop` or `main`).

## Rules

- ❌ Never claim "deployed" until CI/CD shows success.
- ❌ Do NOT promote to production — use `/promotecommit` or `/productioncommit`.
- ❌ Do NOT commit directly on `develop` — always work on `feature/latest2`.
- ✅ Always end on `feature/latest2`.

---
description: Commit changes and deploy all the way through staging AND production
---
// turbo-all

# Production Commit

Full end-to-end pipeline: commit on `feature/latest2`, push to `develop`,
verify staging, promote to `main` via GitHub Actions, and verify production.

## Pre-flight

1. Ensure you are on `feature/latest2`:

   ```bash
   git checkout feature/latest2
   ```

## Phase 1: Commit & Push

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

## Phase 2: Verify Staging (MANDATORY GATE)

8. Verify staging was triggered:

   ```bash
   gh run list --workflow="deploy-staging.yml" --limit 1 --json status,conclusion,headSha,databaseId
   ```

9. Wait for staging:

   ```bash
   gh run watch <RUN_ID> --exit-status
   ```

10. **GATE**: Confirm `conclusion: success`. If failed, investigate and STOP.

## Phase 3: Promote to Main via GitHub Actions

11. Get the validated develop SHA:

    ```bash
    DEVELOP_SHA=$(git rev-parse origin/develop)
    ```

12. Trigger the CI-based promotion:

    ```bash
    gh workflow run "Promote Validated Develop To Main" -f develop_sha=$DEVELOP_SHA
    ```

13. Wait for promotion:

    ```bash
    sleep 10
    gh run list --workflow="promote-develop-to-main.yml" --limit 1 --json status,conclusion,databaseId
    gh run watch <RUN_ID> --exit-status
    ```

## Phase 4: Verify Production (MANDATORY GATE)

14. Wait for production deploy (triggered automatically by the promotion):

    ```bash
    sleep 10
    gh run list --workflow="deploy-prod.yml" --limit 1 --json status,conclusion,databaseId
    gh run watch <RUN_ID> --exit-status
    ```

15. **GATE**: Confirm `conclusion: success`.
    - If failed: `gh run view <RUN_ID> --log-failed`
    - Common fix for `.venv` lock: `gh run rerun <RUN_ID>`, then wait again.

## Post-flight

16. Confirm you are on `feature/latest2` (do NOT leave on `develop` or `main`).

## Final Report

```text
## Deployment Summary
- Commit: <SHA> — <message>
- Staging:    ✅ Deploy #NNN (Xs)
- Promotion:  ✅ Promote #NNN
- Production: ✅ Deploy #NNN (Xs)
```

## Rules

- ❌ **Never claim "deployed" until CI/CD shows success.**
- ❌ Never skip staging verification before promoting.
- ❌ Do NOT commit directly on `develop` — always work on `feature/latest2`.
- ✅ Always end on `feature/latest2`.
- ✅ Use `promote-develop-to-main.yml` for SHA-validated promotion.

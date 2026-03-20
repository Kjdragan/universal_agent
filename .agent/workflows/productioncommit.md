---
description: Commit changes and deploy all the way through staging AND production
---
// turbo-all

# Production Commit

Full end-to-end pipeline: commit all changes, push to `develop`, verify staging,
promote to `main` via GitHub Actions, and verify production deployment.

## Phase 1: Commit & Push

1. Run `git status --short` to review pending changes.
2. Run `git add .` to stage all changes.
3. Run `git diff --staged --stat` to confirm what will be committed.
4. Generate a commit message (conventional format: `feat:`, `fix:`, `docs:`, `chore:`).
5. Run `git commit -m "YOUR_GENERATED_MESSAGE"`.
6. Push to develop:
   - If on `develop`: `git push origin develop`
   - If on a feature branch: `git push origin HEAD:develop`

## Phase 2: Verify Staging (MANDATORY GATE)

7. Verify staging was triggered:

   ```bash
   gh run list --workflow="deploy-staging.yml" --limit 1 --json status,conclusion,headSha,databaseId
   ```

8. Wait for staging:

   ```bash
   gh run watch <RUN_ID> --exit-status
   ```

9. **GATE**: Confirm `conclusion: success`. If failed, investigate and STOP.

## Phase 3: Promote to Main via GitHub Actions

10. Get the validated develop SHA:

    ```bash
    DEVELOP_SHA=$(git rev-parse origin/develop)
    ```

11. Trigger the CI-based promotion workflow:

    ```bash
    gh workflow run "Promote Validated Develop To Main" -f develop_sha=$DEVELOP_SHA
    ```

12. Wait for the promotion workflow:

    ```bash
    gh run list --workflow="promote-develop-to-main.yml" --limit 1 --json status,conclusion,databaseId
    gh run watch <RUN_ID> --exit-status
    ```

## Phase 4: Verify Production (MANDATORY GATE)

13. Wait for production deploy (triggered automatically by the promotion):

    ```bash
    gh run list --workflow="deploy-prod.yml" --limit 1 --json status,conclusion,databaseId
    gh run watch <RUN_ID> --exit-status
    ```

14. **GATE**: Confirm `conclusion: success`.
    - If failed: `gh run view <RUN_ID> --log-failed`
    - Common fix for `.venv` lock: `gh run rerun <RUN_ID>`, then wait again.

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
- ✅ Always end on `develop` branch.
- ✅ Use `promote-develop-to-main.yml` (not local merge) for SHA-validated promotion.

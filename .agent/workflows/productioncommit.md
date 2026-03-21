---
description: Commit changes and deploy all the way through staging AND production
---
// turbo-all

# Production Commit

Full end-to-end pipeline: commit on `feature/latest2`, push to `develop`,
verify staging, promote to `main` via GitHub Actions, and verify production.

## Pre-flight

1. Ensure you are on `feature/latest2` and working tree is dirty:

   ```bash
   git checkout feature/latest2
   git status --short
   ```

   If `git status` shows nothing → report "Nothing to commit" and STOP.

## Phase 1: Commit & Push

2. Stage and review:

   ```bash
   git add .
   git diff --staged --stat
   ```

3. Generate a commit message (conventional format: `feat:`, `fix:`, `docs:`, `chore:`).

4. Commit with `--no-verify` to avoid pre-commit hook hangs.
   **IMPORTANT**: Set `WaitMsBeforeAsync` to `10000` (10s). If the command
   has not finished in 10s, immediately check its status. Do NOT wait silently.

   ```bash
   git commit --no-verify -m "YOUR_GENERATED_MESSAGE"
   ```

5. Verify the commit landed:

   ```bash
   git log --oneline -1
   ```

   You MUST see your new commit at HEAD. If not, investigate before pushing.

6. Push to the feature branch AND to develop:

   ```bash
   git push origin feature/latest2
   git push origin HEAD:develop
   ```

## Phase 2: Verify Staging (MANDATORY GATE)

7. Check staging was triggered:

   ```bash
   gh run list --workflow="deploy-staging.yml" --limit 1 --json status,conclusion,headSha,databaseId
   ```

   Confirm the `headSha` matches your commit. If not, wait 5s and retry once.

8. Wait for staging:

   ```bash
   gh run watch <RUN_ID> --exit-status
   ```

9. **GATE**: Confirm `conclusion: success`. If failed, investigate and STOP.

## Phase 3: Promote to Main via GitHub Actions

10. Get the validated develop SHA:

    ```bash
    DEVELOP_SHA=$(git rev-parse origin/develop)
    echo "Promoting SHA: $DEVELOP_SHA"
    ```

11. Trigger the CI-based promotion:

    ```bash
    gh workflow run "Promote Validated Develop To Main" -f develop_sha=$DEVELOP_SHA
    ```

12. Wait for promotion (check after 5s, not 10s):

    ```bash
    sleep 5
    gh run list --workflow="promote-develop-to-main.yml" --limit 1 --json status,conclusion,databaseId
    gh run watch <RUN_ID> --exit-status
    ```

## Phase 4: Verify Production (MANDATORY GATE)

13. Wait for production deploy:

    ```bash
    sleep 5
    gh run list --workflow="deploy-prod.yml" --limit 1 --json status,conclusion,databaseId
    gh run watch <RUN_ID> --exit-status
    ```

14. **GATE**: Confirm `conclusion: success`.
    - If failed: `gh run view <RUN_ID> --log-failed`
    - Common fix for `.venv` lock: `gh run rerun <RUN_ID>`, then wait again.

## Post-flight

15. Confirm you are on `feature/latest2`:

    ```bash
    git branch --show-current
    ```

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
- ✅ Always use `--no-verify` on commits to avoid silent pre-commit hangs.
- ✅ Always verify `git log --oneline -1` shows your commit before pushing.
- ✅ Always end on `feature/latest2`.
- ✅ Use `promote-develop-to-main.yml` for SHA-validated promotion.

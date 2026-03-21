---
description: Commit changes and deploy to staging only — does NOT touch production
---
// turbo-all

# Stage Commit

Commit all changes on `feature/latest2`, push to `develop`, and verify staging deployment.
Production remains untouched. Use `/productioncommit` or `/promotecommit` to go further.

## Pre-flight

1. Ensure you are on `feature/latest2` and working tree is dirty:

   ```bash
   git checkout feature/latest2
   git status --short
   ```

   If `git status` shows nothing → report "Nothing to commit" and STOP.

## Commit & Push

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

## Verify Staging (MANDATORY GATE)

7. Check staging was triggered (no `sleep` needed — just query immediately):

   ```bash
   gh run list --workflow="deploy-staging.yml" --limit 1 --json status,conclusion,headSha,databaseId
   ```

   Confirm the `headSha` matches your commit from step 5. If it does not match,
   wait 5 seconds and retry once.

8. Wait for staging to complete:

   ```bash
   gh run watch <RUN_ID> --exit-status
   ```

9. **GATE**: Confirm `conclusion: success`. If failed: `gh run view <RUN_ID> --log-failed` and report.
10. Report: "✅ Staging deployed (run #NNN). Production unchanged. Use `/promotecommit` to promote."

## Post-flight

11. Confirm you are still on `feature/latest2` (do NOT leave on `develop` or `main`):

    ```bash
    git branch --show-current
    ```

## Rules

- ❌ Never claim "deployed" until CI/CD shows success.
- ❌ Do NOT promote to production — use `/promotecommit` or `/productioncommit`.
- ❌ Do NOT commit directly on `develop` — always work on `feature/latest2`.
- ✅ Always use `--no-verify` on commits to avoid silent pre-commit hangs.
- ✅ Always verify `git log --oneline -1` shows your commit before pushing.
- ✅ Always end on `feature/latest2`.

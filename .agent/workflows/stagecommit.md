---
description: Commit changes and deploy to staging only — does NOT touch production
---
// turbo-all

# Stage Commit

Commit all changes, push to `develop`, and verify staging deployment.
Production remains untouched. Use `/productioncommit` or `/promotecommit` to go further.

## Steps

1. Run `git status --short` to review pending changes.
2. Run `git add .` to stage all changes.
3. Run `git diff --staged --stat` to confirm what will be committed.
4. Generate a commit message (conventional format: `feat:`, `fix:`, `docs:`, `chore:`).
5. Run `git commit -m "YOUR_GENERATED_MESSAGE"`.
6. Push to develop:
   - If on `develop`: `git push origin develop`
   - If on a feature branch: `git push origin HEAD:develop`
7. Verify staging was triggered:

   ```bash
   gh run list --workflow="deploy-staging.yml" --limit 1 --json status,conclusion,headSha,databaseId
   ```

8. Wait for staging to complete:

   ```bash
   gh run watch <RUN_ID> --exit-status
   ```

9. **GATE**: Confirm `conclusion: success`. If failed: `gh run view <RUN_ID> --log-failed` and report.
10. Report: "✅ Staging deployed (run #NNN). Production unchanged. Use `/promotecommit` to promote."

## Rules

- ❌ Never claim "deployed" until CI/CD shows success.
- ❌ Do NOT promote to production — use `/promotecommit` or `/productioncommit`.
- ✅ Always end on `develop` branch.

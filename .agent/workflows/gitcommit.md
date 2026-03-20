---
description: Stage all changes, commit with a generated message, and push to remote
---
// turbo-all

# Commit & Push to Staging

This workflow commits and pushes to `develop`, then **verifies staging deployment succeeds**.
It does NOT promote to production. For full production deployment, use `/deploy`.

## Steps

1. Run `git status --short` to review pending changes.
2. Run `git add .` to stage all changes.
3. Run `git diff --staged --stat` to confirm what will be committed.
4. Generate a commit message (conventional commit format: `feat:`, `fix:`, `docs:`, `chore:`).
5. Run `git commit -m "YOUR_GENERATED_MESSAGE"`.
6. Push to develop:
   - If on `develop`: `git push origin develop`
   - If on a feature branch: `git push origin HEAD:develop`
7. Verify staging deployment was triggered:
   ```bash
   gh run list --workflow="deploy-staging.yml" --limit 1 --json status,conclusion,headSha,databaseId
   ```
8. Wait for staging to complete:
   ```bash
   gh run watch <RUN_ID> --exit-status
   ```
9. **GATE CHECK**: Confirm `conclusion: success`.
   - If failed: `gh run view <RUN_ID> --log-failed` and report the error.
10. Report to user: "✅ Staging deployed (run #NNN). Use `/deploy` to promote to production."

## CRITICAL RULES

- ❌ Never claim "deployed" until CI/CD pipeline shows success.
- ❌ Do NOT promote to production — that's the `/deploy` workflow's job.
- ✅ Always end on `develop` branch.

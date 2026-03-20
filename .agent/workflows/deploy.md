---
description: Full deployment pipeline — push to develop, verify staging, promote to main, verify production
---
// turbo-all

# Full Deployment Pipeline

This workflow handles the complete path from committing to verified production deployment.
**CRITICAL**: Never claim deployment success until the CI/CD pipeline run is verified as ✅.

---

## Phase 1: Stage, Commit, and Push to Develop

1. Run `git status --short` to review all pending changes.
2. Run `git add .` to stage all changes.
3. Run `git diff --staged --stat` to confirm what will be committed.
4. Generate a concise, descriptive commit message based on the changes (use conventional commit format: `feat:`, `fix:`, `docs:`, `chore:`, etc.).
5. Run `git commit -m "YOUR_GENERATED_MESSAGE"`.
6. Push to develop:
   - If on `develop` branch: `git push origin develop`
   - If on a feature branch: `git push origin HEAD:develop`

---

## Phase 2: Verify Staging Deployment (MANDATORY)

7. Check that the staging deployment was triggered:
   ```bash
   gh run list --workflow="deploy-staging.yml" --limit 1 --json status,conclusion,headSha,databaseId
   ```
8. Wait for the staging deploy to complete (timeout 5 minutes):
   ```bash
   gh run watch <RUN_ID> --exit-status
   ```
9. **GATE CHECK**: Confirm the staging run shows `conclusion: success`.
   - If it **failed**, investigate the logs: `gh run view <RUN_ID> --log-failed`
   - Do NOT proceed to Phase 3 if staging failed.
   - Report the failure to the user with the error details.
10. Report staging status to the user: "✅ Staging deployed successfully (run #NNN, Xs)."

---

## Phase 3: Promote to Main (Only After Staging Succeeds)

11. Get the current develop SHA:
    ```bash
    git rev-parse origin/develop
    ```
12. Fast-forward merge develop into main:
    ```bash
    git checkout main && git merge --ff-only develop && git push origin main
    ```
    - If the fast-forward fails, **stop and report** — there may be divergence.
13. Switch back to develop:
    ```bash
    git checkout develop
    ```

---

## Phase 4: Verify Production Deployment (MANDATORY)

14. Check that the production deployment was triggered:
    ```bash
    gh run list --workflow="deploy-prod.yml" --limit 1 --json status,conclusion,headSha,databaseId
    ```
15. Wait for the production deploy to complete (timeout 5 minutes):
    ```bash
    gh run watch <RUN_ID> --exit-status
    ```
16. **GATE CHECK**: Confirm the production run shows `conclusion: success`.
    - If it **failed**, investigate: `gh run view <RUN_ID> --log-failed`
    - Common failure: `.venv/lib directory not empty` (process lock). Fix: re-run the job via `gh run rerun <RUN_ID>` and wait again.
    - Report failures to the user with the error details and what was done to remediate.
17. Report final status to the user: "✅ Production deployed successfully (run #NNN, Xs)."

---

## Final Report Template

After ALL phases complete, report this summary to the user:

```
## Deployment Summary
- **Commit**: <SHA> — <commit message>
- **Staging**: ✅ Deploy #NNN succeeded (Xs)
- **Production**: ✅ Deploy #NNN succeeded (Xs)
- **Files changed**: N files
```

---

## Common Mistakes to AVOID

- ❌ **Never claim "deployed" before verifying the CI/CD pipeline succeeded.**
- ❌ Do NOT develop directly on `develop` — use feature branches when possible.
- ❌ Do NOT skip staging verification before promoting to production.
- ❌ Do NOT use short SHAs when the workflow requires full 40-character SHAs.
- ❌ Do NOT leave the local checkout on `main` — always switch back to `develop`.

## Known Failure Modes

| Error | Cause | Fix |
|-------|-------|-----|
| `failed to remove directory '.venv/lib': Directory not empty` | Running process holds file lock | Re-run the job: `gh run rerun <RUN_ID>` |
| `SSH preflight failed` | VPS unreachable or Tailscale auth issue | Check VPS status and Tailscale ACLs |
| `fast-forward merge failed` | develop and main diverged | Investigate manually, do not force push |

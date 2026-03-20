---
description: Full deployment pipeline — push to develop, verify staging, promote to main, verify production
---
// turbo-all

# Full Production Deployment Pipeline

Use this workflow when the user explicitly requests production deployment
(keywords: "deploy", "production", "push to main", "promote to main").

For staging-only (commit + push to develop), use `/gitcommit` instead.

---

## Phase 1: Commit & Push to Develop

1. Run `git status --short` to review pending changes.
2. Run `git add .` to stage all changes.
3. Run `git diff --staged --stat` to confirm what will be committed.
4. Generate a commit message (conventional commit format).
5. Run `git commit -m "YOUR_GENERATED_MESSAGE"`.
6. Push to develop:
   - If on `develop`: `git push origin develop`
   - If on a feature branch: `git push origin HEAD:develop`

---

## Phase 2: Verify Staging (MANDATORY GATE)

7. Check staging was triggered:

   ```bash
   gh run list --workflow="deploy-staging.yml" --limit 1 --json status,conclusion,headSha,databaseId
   ```

8. Wait for completion:

   ```bash
   gh run watch <RUN_ID> --exit-status
   ```

9. **GATE**: Confirm `conclusion: success`. If failed, investigate with `gh run view <RUN_ID> --log-failed` and STOP.

---

## Phase 3: Promote to Main

10. Fast-forward merge:

    ```bash
    git checkout main && git merge --ff-only develop && git push origin main
    ```

    If fast-forward fails, STOP and report divergence.

11. Return to develop:

    ```bash
    git checkout develop
    ```

---

## Phase 4: Verify Production (MANDATORY GATE)

12. Check production was triggered:

    ```bash
    gh run list --workflow="deploy-prod.yml" --limit 1 --json status,conclusion,headSha,databaseId
    ```

13. Wait for completion:

    ```bash
    gh run watch <RUN_ID> --exit-status
    ```

14. **GATE**: Confirm `conclusion: success`.
    - If failed, investigate: `gh run view <RUN_ID> --log-failed`
    - Common fix for `.venv` lock: `gh run rerun <RUN_ID>`, then wait again.

---

## Final Report

```text
## Deployment Summary
- Commit: <SHA> — <message>
- Staging:    ✅ Deploy #NNN succeeded (Xs)
- Production: ✅ Deploy #NNN succeeded (Xs)
```

---

## CRITICAL RULES

- ❌ **Never claim "deployed" until the CI/CD run shows success.**
- ❌ Never skip staging verification before promoting.
- ❌ Never leave local checkout on `main` — always return to `develop`.

## Known Failures

| Error | Cause | Fix |
| ----- | ----- | --- |
| `.venv/lib: Directory not empty` | Process lock | `gh run rerun <RUN_ID>` |
| SSH preflight failed | VPS unreachable | Check VPS + Tailscale ACLs |
| Fast-forward failed | Branch divergence | Investigate manually |

## GitHub Actions Inventory

| Workflow | Trigger | Purpose |
| -------- | ------- | ------- |
| `deploy-staging.yml` | Auto on `develop` push | Deploy to staging VPS |
| `deploy-prod.yml` | Auto on `main` push | Deploy to production VPS |
| `promote-develop-to-main.yml` | Manual dispatch (SHA input) | CI-based promotion with SHA validation |
| `codex-review-develop-pr.yml` | PR to `develop` | Codex code review |
| `nightly-doc-drift-audit.yml` | Scheduled | Documentation freshness check |
| `debug-prod.yml` | Manual dispatch | Break-glass: fetch prod logs |
| `fix-prod-repo.yml` | Manual dispatch | Break-glass: reconstitute prod git repo |
| `run-clear-queue.yml` | Manual dispatch | Break-glass: clear agent task queue |

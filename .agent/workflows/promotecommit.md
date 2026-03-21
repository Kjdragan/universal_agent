---
description: Promote existing staging deployment to production — no new commits
---
// turbo-all

# Promote Commit

Promote what is currently deployed on staging (`develop`) to production (`main`).
No new commits are made — just moves the validated staging code to production.

## Pre-flight

1. Ensure you are on `feature/latest2`:

   ```bash
   git checkout feature/latest2
   ```

## Verify There Is Something to Promote

2. Fetch latest refs and check:

   ```bash
   git fetch origin develop main
   echo "develop: $(git rev-parse origin/develop)"
   echo "main:    $(git rev-parse origin/main)"
   ```

   If SHAs match → report "Nothing to promote — staging and production are already in sync." and STOP.

## Promote via GitHub Actions

3. Trigger the CI-based promotion:

   ```bash
   DEVELOP_SHA=$(git rev-parse origin/develop)
   echo "Promoting SHA: $DEVELOP_SHA"
   gh workflow run "Promote Validated Develop To Main" -f develop_sha=$DEVELOP_SHA
   ```

4. Wait for promotion (check after 5s):

   ```bash
   sleep 5
   gh run list --workflow="promote-develop-to-main.yml" --limit 1 --json status,conclusion,databaseId
   gh run watch <RUN_ID> --exit-status
   ```

## Verify Production (MANDATORY GATE)

5. Wait for production deploy:

   ```bash
   sleep 5
   gh run list --workflow="deploy-prod.yml" --limit 1 --json status,conclusion,databaseId
   gh run watch <RUN_ID> --exit-status
   ```

6. **GATE**: Confirm `conclusion: success`.
   - If failed: `gh run view <RUN_ID> --log-failed`
   - Common fix: `gh run rerun <RUN_ID>`, then wait again.

## Post-flight

7. Confirm you are on `feature/latest2`:

   ```bash
   git branch --show-current
   ```

8. Report: "✅ Production promoted and deployed (run #NNN)."

## Rules

- ❌ **Never claim "deployed" until CI/CD shows success.**
- ❌ Do NOT commit directly on `develop` — always work on `feature/latest2`.
- ✅ Always end on `feature/latest2`.
- ✅ Use `promote-develop-to-main.yml` for SHA-validated promotion.

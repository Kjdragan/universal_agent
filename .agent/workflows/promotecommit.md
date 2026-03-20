---
description: Promote existing staging deployment to production — no new commits
---
// turbo-all

# Promote Commit

Promote what is currently deployed on staging (`develop`) to production (`main`).
No new commits are made — this just moves the validated staging code to production.

Use this after reviewing staging and confirming it looks good.

## Steps

1. Ensure local repo is up to date:

   ```bash
   git fetch origin develop main
   ```

2. Confirm develop is ahead of main (there's something to promote):

   ```bash
   echo "develop: $(git rev-parse origin/develop)"
   echo "main:    $(git rev-parse origin/main)"
   ```

   If they are the same SHA, report "Nothing to promote — staging and production are already in sync."

3. Trigger the CI-based promotion:

   ```bash
   DEVELOP_SHA=$(git rev-parse origin/develop)
   gh workflow run "Promote Validated Develop To Main" -f develop_sha=$DEVELOP_SHA
   ```

4. Wait for promotion:

   ```bash
   gh run list --workflow="promote-develop-to-main.yml" --limit 1 --json status,conclusion,databaseId
   gh run watch <RUN_ID> --exit-status
   ```

5. Wait for production deploy:

   ```bash
   gh run list --workflow="deploy-prod.yml" --limit 1 --json status,conclusion,databaseId
   gh run watch <RUN_ID> --exit-status
   ```

6. **GATE**: Confirm production `conclusion: success`.
   - If failed: `gh run view <RUN_ID> --log-failed`
   - Common fix: `gh run rerun <RUN_ID>`, then wait again.

7. Report: "✅ Production promoted and deployed (run #NNN)."

## Rules

- ❌ **Never claim "deployed" until CI/CD shows success.**
- ✅ Use `promote-develop-to-main.yml` for SHA-validated promotion.

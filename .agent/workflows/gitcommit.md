---
description: Stage all changes, commit with a generated message, and push to remote
---
// turbo-all

1. Run `git add .` to stage all changes.
2. Run `git diff --staged --stat` to see what has changed.
3. Generate a helpful, concise commit message based on the changes.
4. Run `git commit -m "YOUR_GENERATED_MESSAGE"`.
5. Push to the current branch: `git push`.
6. If the current branch is `feature/latest2` (or another feature branch), also push to develop: `git push origin HEAD:develop`.
7. After pushing to develop, staging auto-deploys. Monitor with: `gh run list --workflow="deploy-staging.yml" --limit 1`
8. Wait for staging deploy to complete: `gh run watch <RUN_ID> --exit-status`

**To promote to production after staging is validated:**

9. Get the full develop SHA: `git rev-parse origin/develop`
10. Trigger promotion: `gh workflow run "Promote Validated Develop To Main" -f develop_sha=<FULL_40_CHAR_SHA>`
    - ⚠️ MUST use the **full 40-character SHA** — short SHAs will be resolved but must match `origin/develop` HEAD exactly.
11. Monitor the promote + production deploy workflows.

**Common mistakes to avoid:**
- Do NOT develop directly on `develop` — use `feature/latest2` or another feature branch.
- Do NOT use `git push origin develop` when on a feature branch — use `git push origin HEAD:develop`.
- Always verify staging deploy succeeds before promoting to production.

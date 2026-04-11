// turbo-all
# Ship
Commit, push, deploy. End-to-end from feature/latest2 to live VPS.

## Pre-flight
1. Ensure on feature/latest2 with dirty working tree.

## Phase 1: Commit & Push
2. git add . && git diff --staged --stat
3. Generate conventional commit message
4. git commit --no-verify -m "message"
5. Verify: git log --oneline -1
6. git push origin feature/latest2

## Phase 2: Land on develop
7. Open or update PR: gh pr create --base develop --head feature/latest2 --fill
8. Enable auto-merge: gh pr merge --auto --squash
9. Wait for merge

## Phase 3: Deploy
10. Fast-forward main: git push origin origin/develop:main
11. Wait for deploy: gh run list --workflow=deploy.yml --limit 1
12. gh run watch <RUN_ID> --exit-status

## Report
13. "✅ Deployed (run #NNN). Open app.clearspringcg.com to verify."

## Rules
- ❌ Never claim deployed until CI shows success
- ❌ Never commit directly to develop or main
- ✅ Always end on feature/latest2
- ✅ Always use --no-verify on commits

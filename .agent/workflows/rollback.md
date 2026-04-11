// turbo-all
# Rollback
Reset main to its previous commit. VPS gets the old code.

## Steps
1. git fetch origin main
2. Find previous SHA: git log --oneline origin/main -5 (show to user, confirm which)
3. git push --force-with-lease origin <SHA>:main
4. Wait for deploy workflow
5. Report result

## Rules
- ❌ Never touch develop — work-in-progress is preserved
- ✅ Always show the user the SHA list and confirm before force-pushing
- ✅ If deploy fails, offer to re-run: gh workflow run deploy.yml

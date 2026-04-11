// turbo-all
# Checkpoint
Deploy what's already on develop without making a new commit.

## Steps
1. git fetch origin develop main
2. Compare SHAs — if identical, "nothing to deploy" and STOP
3. git push origin origin/develop:main
4. Wait for deploy workflow
5. Report result

## Rules
- ❌ Do not make any commits
- ✅ Always verify develop != main before pushing

// turbo-all
# Status
Read-only pipeline status check.

## Steps
1. git fetch origin develop main
2. Report: develop SHA, main SHA, whether they match
3. gh run list --workflow=deploy.yml --limit 3
4. Report latest deploy status

## Rules
- ❌ Read-only. Do not modify anything.

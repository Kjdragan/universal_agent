---
description: Stage all changes, commit with a generated message, and push to remote
---
// turbo-all

1. Run `git add .` to stage all changes.
2. Run `git diff --staged --stat` to see what has changed.
3. Generate a helpful, concise commit message based on the changes (conventional commit format: `feat:`, `fix:`, `docs:`, `chore:`).
4. Run `git commit -m "YOUR_GENERATED_MESSAGE"`.
5. Push to the current branch: `git push`.
6. If the current branch is a feature branch (not `develop`), also push to develop: `git push origin HEAD:develop`.

**To deploy (staging + production):** Use the `/deploy` workflow which includes mandatory CI/CD verification gates.


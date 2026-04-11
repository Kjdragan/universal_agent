# Local Dev Slash Commands (Claude Code)

This repo ships four Claude Code slash commands that wrap the `scripts/dev_*.sh` tooling. They are thin shortcuts — the actual logic lives in the shell scripts. The slash commands exist so that an agent running in Claude Code can react to "start local dev" / "stop local dev" without having to rediscover the workflow each time.

**Canonical workflow guide:** `docs/development/LOCAL_DEV.md`

## Commands

| Slash command | Script                                    | What it does                                                     |
|---------------|-------------------------------------------|------------------------------------------------------------------|
| `/devup`      | `scripts/dev_up.sh`                       | Enter State B: pause VPS conflict services, start local stack.   |
| `/devdown`    | `scripts/dev_down.sh`                     | Exit State B: stop local stack, resume VPS services.             |
| `/devstatus`  | `scripts/dev_status.sh`                   | Read-only snapshot (local PIDs, ports, VPS pause stamp, units).  |
| `/devreset`   | `scripts/dev_reset.sh` (with `CONFIRM`)   | Wipe the local data dir and logs. Refuses if stack is up.        |

## Rules every command reinforces

1. **Do not push to `develop` or `main` while in State B.** A deploy would restart the paused VPS services and collide with the local stack. Always run `/devdown` before pushing.
2. **The `local` Infisical env is a copy of `production`.** Anything local dev writes to shared infra (Slack, Discord, Telegram, AgentMail, Redis, Postgres, third-party APIs) hits real endpoints.
3. **Secrets never touch disk.** Every service is launched under `infisical run --env=local --projectId=$INFISICAL_PROJECT_ID --`. The only credentials on disk are the three Infisical bootstrap values in `~/.bashrc`.

## Files

- `.claude/commands/devup.md`
- `.claude/commands/devdown.md`
- `.claude/commands/devstatus.md`
- `.claude/commands/devreset.md`

Slash commands are auto-discovered by Claude Code on session start; no registration step is needed.

## For non-Claude-Code agents

If you are using Antigravity IDE or another agent harness that does not support Claude Code slash commands, read the "Local Development Mode" section in `AGENTS.md`. It contains the same rules in plain prose so any agent reading `AGENTS.md` will see them.

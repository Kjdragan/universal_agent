# universal_agent

Python-based agent runtime and orchestration project.

## Local development

You can run the full stack locally on your workstation against real Infisical secrets, with the VPS services hot-swapped to pause for the duration of your session.

```bash
./scripts/dev_up.sh       # start local stack (State B)
./scripts/dev_status.sh   # read-only health snapshot
./scripts/dev_down.sh     # stop local stack, resume VPS
```

Then open <http://localhost:3000>.

**Critical rule:** do **not** `git push` to `develop` or `main` while local dev mode is running. Always run `./scripts/dev_down.sh` first. See the deploy-while-dev crash-loop explanation in the guide.

**Canonical guide:** [`docs/development/LOCAL_DEV.md`](docs/development/LOCAL_DEV.md) — one-time setup, daily workflow, VPS coordination, security model, troubleshooting.

**Claude Code slash commands:** `/devup`, `/devdown`, `/devstatus`, `/devreset` — see [`docs/development/LOCAL_DEV_SLASH_COMMANDS.md`](docs/development/LOCAL_DEV_SLASH_COMMANDS.md).

**Agent rules** (for Claude Code, Antigravity, etc.): see the "Local Development Mode" section in [`AGENTS.md`](AGENTS.md).

## Deployment

Production deployment is fully automated via GitHub Actions. Do not use ad hoc `ssh`, `rsync`, or manual VPS flows. See [`AGENTS.md`](AGENTS.md) §"Deployment Contract" and `docs/deployment/`.

## Documentation

All documentation lives under [`docs/`](docs/). Start at [`docs/README.md`](docs/README.md) and [`docs/Documentation_Status.md`](docs/Documentation_Status.md).

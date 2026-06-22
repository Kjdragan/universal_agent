---
name: project-scaffolder
description: >
  Scaffold a FULL production web APPLICATION deployed as live services on the VPS:
  FastAPI backend + Vite/React frontend + PostgreSQL + Alembic migrations, each run
  as a systemd service behind Nginx + Let's Encrypt SSL on a dedicated subdomain,
  with GitHub Actions CI/CD and its own auto-created Infisical project. Use this ONLY
  when the user explicitly wants to stand up and DEPLOY a production, database-backed
  web app or public HTTPS service. Do NOT use it for a lightweight repo, experiment,
  lab, sandbox, prototype, script, or a plain "new project / new repo" — those go to
  the new-repo skill (dragan:new-repo), which makes a shared-uv-venv experiment repo
  with no web stack, database, Nginx, or systemd. If there is no explicit need for a
  deployed database-backed web service, prefer new-repo. Within this skill, always use
  its scaffold.py script rather than improvising project setup.
---

# Project Scaffolder

Create a production-ready project on the VPS with a single command. This skill replaces
any manual project setup — always prefer it over improvising directory structures.

## Required Input

- `project_name` — lowercase, hyphenated (e.g., `acme-dashboard`). Used as directory name, GitHub repo, subdomain, systemd service prefix, and Infisical project name.
- `project_description` — one-liner describing the project purpose.

## Prerequisites

Before running the scaffolder, ensure:

1. **GitHub CLI authenticated** — `gh auth status` must succeed on the VPS.
2. **Infisical credentials available** — The UA `.env` must contain `INFISICAL_CLIENT_ID` and `INFISICAL_CLIENT_SECRET`. The Infisical project is **auto-created** by the script.
3. **DNS records** — Can be set up before or after scaffolding. The script will print the exact records needed.

## Execution Procedure

### Step 1: Collect Information

Ask the user for:
- `project_name` (required)
- `project_description` (required)

**Do NOT ask for `infisical_project_id`** — the script auto-creates it via the Infisical REST API.

### Step 2: Run the Scaffold Script

```bash
cd /home/kjdragan/lrepos/universal_agent
uv run .agents/skills/project-scaffolder/scripts/scaffold.py \
  --name <project_name> \
  --description "<project_description>"
```

The script handles:
- **Infisical project auto-creation** via REST API (idempotent — reuses existing)
- **`/opt` permission handling** with `sudo` fallback
- Directory creation at `/opt/<project_name>/`
- Git initialization
- All project files from templates
- Port allocation (scans for next available)
- Bootstrap `.env` generation
- Skills symlinking

### Step 3: Post-Scaffold Manual Steps

After the script completes, perform these steps:

#### 3a. Create GitHub Repository
```bash
cd /opt/<project_name>
gh repo create kjdragan/<project_name> --private --source=. --push
```

#### 3b. Seed Infisical Secrets
Copy shared API keys from UA's Infisical project to the new project:
```bash
# Get keys from UA project
ANTHROPIC_KEY=$(infisical secrets get ANTHROPIC_API_KEY --env production --plain --silent)

# Set in new project (switch to new project context)
infisical secrets set ANTHROPIC_API_KEY="$ANTHROPIC_KEY" --env production
infisical secrets set ANTHROPIC_API_KEY="$ANTHROPIC_KEY" --env development
```

Generate project-specific secrets:
```bash
APP_SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(48))")
infisical secrets set APP_SECRET_KEY="$APP_SECRET" --env production
infisical secrets set APP_SECRET_KEY="$APP_SECRET" --env development
infisical secrets set DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost:<db_port>/<project_name>" --env development
infisical secrets set DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost:<db_port>/<project_name>" --env production
```

#### 3c. Add GitHub Secrets for CI/CD
```bash
gh secret set TAILSCALE_OAUTH_CLIENT_ID --body "..."
gh secret set TAILSCALE_OAUTH_SECRET --body "..."
gh secret set INFISICAL_CLIENT_ID --body "..."
gh secret set INFISICAL_CLIENT_SECRET --body "..."
gh secret set INFISICAL_PROJECT_ID --body "<infisical_project_id>"
```

#### 3d. Setup SSL
```bash
sudo certbot --nginx -d <project_name>.clearspringcg.com -d dev-<project_name>.clearspringcg.com
```

#### 3e. Start Services
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now <project_name>-db
sudo systemctl enable --now <project_name>-api
sudo systemctl enable --now <project_name>-web
sudo systemctl enable --now <project_name>-docs
```

#### 3f. Run Initial Migration
```bash
cd /opt/<project_name>/backend
uv run alembic upgrade head
```

### Step 4: Verify

1. `curl http://localhost:<backend_port>/health` — should return `{"status": "ok"}`
2. `curl http://localhost:<frontend_port>` — should return HTML
3. Open `https://dev-<project_name>.clearspringcg.com` in browser
4. Verify `gh repo view kjdragan/<project_name>` shows the repo

## Generated Project Stack

| Layer | Technology | Purpose |
|-------|-----------|---------|
| Frontend | Vite + React + TypeScript | SPA user interface |
| Backend | FastAPI + Uvicorn | REST API + WebSocket |
| Database | PostgreSQL (Docker) | Persistent storage |
| ORM | SQLAlchemy + Alembic | Models + migrations |
| Agent | Anthropic SDK | AI agent capabilities |
| Secrets | Infisical | Secret management |
| Docs | MkDocs Material | Dynamic documentation |
| CI/CD | GitHub Actions | Automated deployment |
| Reverse Proxy | Nginx + Let's Encrypt | SSL + routing |
| Package Mgmt | uv (Python), npm (JS) | Dependency management |

## Curated Skills Included

The following skills are symlinked from UA's skill library:

clean-code, systematic-debugging, verification-before-completion, git-commit, github,
dependency-management, task-forge, skill-creator, coding-agent, deep-research, defuddle,
deepwiki, image-generation, webapp-testing, pdf, media-processing, json-canvas, grill-me,
ideation, just, obsidian, weather

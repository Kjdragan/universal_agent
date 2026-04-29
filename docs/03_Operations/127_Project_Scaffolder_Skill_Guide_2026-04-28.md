# Project Scaffolder Skill Guide

**Last Updated:** 2026-04-29

The `project-scaffolder` skill provisions a production-oriented FastAPI, Vite/React, PostgreSQL, Infisical, systemd, Nginx, CI/CD, documentation, and agent-skill baseline for new VPS projects. Its mandate is one-command setup with only externally coordinated work left afterward: DNS, SSL, secret values, and service installation.

Do not improvise project setup with ad hoc `uv init`, manual directory creation, or copied templates. Use `.agents/skills/project-scaffolder/scripts/scaffold.py` so generated projects keep the same operational contract.

## Current Contract

| Area | Current behavior | Code reference |
|------|------------------|----------------|
| Project path | Resolves the effective `--base-dir` and uses that real path as `PROJECT_DIR` for generated infrastructure. | [scaffold.py](file:///home/kjdragan/lrepos/universal_agent/.agents/skills/project-scaffolder/scripts/scaffold.py#L968), [scaffold.py](file:///home/kjdragan/lrepos/universal_agent/.agents/skills/project-scaffolder/scripts/scaffold.py#L997) |
| Systemd units | Generates API, DB, web, and docs service units, including the frontend `-web.service`. | [scaffold.py](file:///home/kjdragan/lrepos/universal_agent/.agents/skills/project-scaffolder/scripts/scaffold.py#L345), [web.service.tmpl](file:///home/kjdragan/lrepos/universal_agent/.agents/skills/project-scaffolder/templates/infrastructure/web.service.tmpl#L1) |
| Infisical CLI | Checks that the host has `infisical` on `PATH` before normal Infisical provisioning. | [scaffold.py](file:///home/kjdragan/lrepos/universal_agent/.agents/skills/project-scaffolder/scripts/scaffold.py#L667), [scaffold.py](file:///home/kjdragan/lrepos/universal_agent/.agents/skills/project-scaffolder/scripts/scaffold.py#L695) |
| Host dependencies | Checks `git`, `uv`, `node`, `npm`, Docker Compose, Infisical CLI, and GitHub CLI before normal provisioning. | [scaffold.py](file:///home/kjdragan/lrepos/universal_agent/.agents/skills/project-scaffolder/scripts/scaffold.py#L695), [scaffold.py](file:///home/kjdragan/lrepos/universal_agent/.agents/skills/project-scaffolder/scripts/scaffold.py#L977) |
| Project metadata | Writes `project.scaffold.json` with paths, ports, DNS names, Infisical project ID, and install commands. | [scaffold.py](file:///home/kjdragan/lrepos/universal_agent/.agents/skills/project-scaffolder/scripts/scaffold.py#L396) |
| Generated preflight | Writes `scripts/preflight.sh` so future agents can verify CLIs, Infisical CLI, dependency install directories, and scaffold metadata. | [scaffold.py](file:///home/kjdragan/lrepos/universal_agent/.agents/skills/project-scaffolder/scripts/scaffold.py#L589) |
| Skill links | Creates canonical generated-project skill links under `.agents/skills/`; legacy `.claude/skills/` is lookup-only fallback. | [scaffold.py](file:///home/kjdragan/lrepos/universal_agent/.agents/skills/project-scaffolder/scripts/scaffold.py#L51), [scaffold.py](file:///home/kjdragan/lrepos/universal_agent/.agents/skills/project-scaffolder/scripts/scaffold.py#L615) |
| Dependency install | Runs backend `uv sync --extra dev` and frontend `npm install` unless `--skip-install` is supplied. | [scaffold.py](file:///home/kjdragan/lrepos/universal_agent/.agents/skills/project-scaffolder/scripts/scaffold.py#L706), [scaffold.py](file:///home/kjdragan/lrepos/universal_agent/.agents/skills/project-scaffolder/scripts/scaffold.py#L1050) |
| Git baseline | Initializes `main`, configures a local scaffolder author, stages files, and commits `chore: initial scaffold`. | [scaffold.py](file:///home/kjdragan/lrepos/universal_agent/.agents/skills/project-scaffolder/scripts/scaffold.py#L719) |
| GitHub publish | Creates or reuses `kjdragan/<project_name>` by default and pushes `main` unless `--skip-github` is supplied. | [scaffold.py](file:///home/kjdragan/lrepos/universal_agent/.agents/skills/project-scaffolder/scripts/scaffold.py#L758), [scaffold.py](file:///home/kjdragan/lrepos/universal_agent/.agents/skills/project-scaffolder/scripts/scaffold.py#L1058) |

## Generated Stack

| Layer | Technology | Purpose |
|-------|------------|---------|
| Frontend | Vite + React + TypeScript | SPA UI and development server |
| Backend | FastAPI + Uvicorn | REST API and agent runtime surface |
| Database | PostgreSQL via Docker Compose | Persistent relational storage |
| ORM | SQLAlchemy + Alembic | Models and migrations |
| Secrets | Infisical | Runtime secret source of truth |
| Docs | MkDocs Material | Project documentation site |
| CI/CD | GitHub Actions + Tailscale | Branch-driven VPS deployment |
| Proxy | Nginx + Certbot | Public routing and TLS |
| Services | systemd | API, DB, web, and docs lifecycle |

## Provisioning Flow

```mermaid
sequenceDiagram
    autonumber
    participant User
    participant Skill as project-scaffolder skill
    participant Infisical
    participant FS as Project filesystem
    participant Git
    participant GitHub

    User->>Skill: uv run scaffold.py --name <project> --description <text>
    Skill->>Skill: Verify core CLIs and Infisical CLI unless skipped
    Skill->>Infisical: Create or reuse project unless --skip-infisical
    Skill->>FS: Allocate ports and render templates with PROJECT_DIR
    Skill->>FS: Write backend, frontend, docs, metadata, preflight, systemd, Nginx, CI/CD
    Skill->>FS: Link curated skills into .agents/skills
    Skill->>FS: Run uv sync --extra dev and npm install unless --skip-install
    Skill->>Git: git init -b main, git add ., git commit
    Skill->>GitHub: gh repo create/view + push unless --skip-github
    Skill-->>User: Print DNS, secrets, SSL, and systemd next steps
```

## Routing Logic

```mermaid
flowchart TD
    A[Start scaffold] --> B{--skip-infisical?}
    B -- no --> C[Verify core CLIs plus Infisical CLI]
    C --> D{Infisical project supplied?}
    D -- no --> E[Create/reuse Infisical project]
    D -- yes --> F[Use supplied project ID]
    B -- yes --> G[Use blank project ID]
    E --> H[Resolve actual project_dir]
    F --> H
    G --> H
    H --> I[Render templates using PROJECT_DIR and DNS_NAME]
    I --> J[Symlink curated skills to .agents/skills]
    J --> K{--skip-install?}
    K -- no --> L[uv sync --extra dev and npm install]
    K -- yes --> M[Leave install to scripts/bootstrap.sh]
    L --> N[Initialize main branch and initial commit]
    M --> N
    N --> O{--skip-github?}
    O -- no --> P[Create/reuse GitHub repo and push]
    O -- yes --> Q[Print GitHub command]
    P --> R[Print remaining operator steps]
    Q --> R
```

## Usage

```bash
cd /home/kjdragan/lrepos/universal_agent
uv run .agents/skills/project-scaffolder/scripts/scaffold.py \
  --name <project_name> \
  --description "<project_description>"
```

Optional controls:

| Flag | Effect |
|------|--------|
| `--base-dir <path>` | Override the project parent directory. The generated systemd, Nginx, and CI/CD files use this resolved path. |
| `--skip-infisical` | Skip Infisical CLI preflight and project creation. Core host CLI checks still run. |
| `--skip-install` | Skip backend/frontend dependency installation. |
| `--skip-github` | Skip GitHub repository creation and push. |
| `--github-owner <owner>` | Override the default GitHub owner, currently `kjdragan`. |

## Remaining Operator Steps

These steps intentionally remain outside the script because they require external DNS control, secret values, host-level service installation, or TLS issuance:

1. Add DNS A records:

```bash
<project_name>.clearspringcg.com      A  187.77.16.29
dev-<project_name>.clearspringcg.com  A  187.77.16.29
```

2. Seed Infisical secrets:

```bash
infisical secrets set ANTHROPIC_API_KEY="..." --env production
infisical secrets set APP_SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')" --env production
infisical secrets set DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost:<db_port>/<project_module>" --env production
```

3. Add GitHub Actions secrets for Tailscale and Infisical if the repository was created:

```bash
gh secret set TAILSCALE_OAUTH_CLIENT_ID --body "..."
gh secret set TAILSCALE_OAUTH_SECRET --body "..."
gh secret set INFISICAL_CLIENT_ID --body "..."
gh secret set INFISICAL_CLIENT_SECRET --body "..."
gh secret set INFISICAL_PROJECT_ID --body "<infisical_project_id>"
```

4. Install Nginx and systemd files, then issue certificates:

```bash
sudo cp <project_dir>/_nginx/<project_name>.conf /etc/nginx/sites-enabled/
sudo cp <project_dir>/_systemd/*.service /etc/systemd/system/
sudo certbot --nginx -d <project_name>.clearspringcg.com -d dev-<project_name>.clearspringcg.com
sudo systemctl daemon-reload
sudo systemctl enable --now <project_name>-db <project_name>-api <project_name>-web <project_name>-docs
```

5. Run the first migration after the database is available:

```bash
cd <project_dir>/backend
uv run alembic upgrade head
```

## Evaluation Remediation Status

| Evaluation issue | Status |
|------------------|--------|
| Systemd hardcoded `/opt/<name>` paths | Fixed by rendering `PROJECT_DIR` into service, Nginx, and deploy templates. |
| Staged but uncommitted git repository | Fixed by required initial commit creation. |
| Default `master` branch | Fixed by `git init -b main` with fallback branch rename. |
| Missing frontend systemd unit | Fixed with generated `<project_name>-web.service`. |
| Duplicate `.claude/skills` and `.agents/skills` links | Fixed by writing links only to `.agents/skills`. |
| `deep-research` warning from stale lookup path | Fixed by searching `.agents/skills` first and `.claude/skills` only as fallback. |
| Dependency install left manual | Fixed by default `uv sync --extra dev` and `npm install`. |
| GitHub repo creation left manual | Fixed by default `gh repo create`/reuse and push. |

## Verification Expectations

For local validation that should not create external resources:

```bash
uv run .agents/skills/project-scaffolder/scripts/scaffold.py \
  --name test-project \
  --description "Test run" \
  --base-dir /tmp/project-scaffolder-check \
  --skip-infisical \
  --skip-github
```

Then verify:

```bash
git -C /tmp/project-scaffolder-check/test-project branch --show-current
git -C /tmp/project-scaffolder-check/test-project log -1 --pretty=%s
grep -R "/tmp/project-scaffolder-check/test-project" /tmp/project-scaffolder-check/test-project/_systemd
test -L /tmp/project-scaffolder-check/test-project/.agents/skills/deep-research
cd /tmp/project-scaffolder-check/test-project && bash scripts/preflight.sh
```

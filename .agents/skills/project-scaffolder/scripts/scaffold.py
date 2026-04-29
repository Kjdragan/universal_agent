#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx"]
# ///
"""Project Scaffolder — creates fully-provisioned VPS projects.

Usage:
    uv run .agents/skills/project-scaffolder/scripts/scaffold.py \
        --name my-project --description "My new project"
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import getpass
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys

try:
    import httpx
except ImportError:
    httpx = None  # type: ignore

SKILL_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = SKILL_DIR / "templates"
# Auto-detect: script lives in <ua_root>/.agents/skills/project-scaffolder/scripts/
# So SKILL_DIR.parent = .agents/skills/ (or .claude/skills/)
UA_SKILLS_DIR = SKILL_DIR.parent
# Verify it looks right (contains other skill dirs)
if not (UA_SKILLS_DIR / "clean-code").exists():
    # Fallback: search common locations
    for _candidate in [
        Path("/opt/universal_agent/.agents/skills"),
        Path("/home/kjdragan/lrepos/universal_agent/.agents/skills"),
    ]:
        if _candidate.exists():
            UA_SKILLS_DIR = _candidate
            break

UA_ROOT = SKILL_DIR.parents[2]


def _skill_source_dirs() -> list[Path]:
    """Return canonical and legacy skill source directories, in lookup order."""
    candidates = [
        UA_ROOT / ".agents" / "skills",
        UA_SKILLS_DIR,
        UA_ROOT / ".claude" / "skills",
        Path("/opt/universal_agent/.agents/skills"),
        Path("/opt/universal_agent/.claude/skills"),
        Path("/home/kjdragan/lrepos/universal_agent/.agents/skills"),
        Path("/home/kjdragan/lrepos/universal_agent/.claude/skills"),
    ]
    seen: set[Path] = set()
    existing: list[Path] = []
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen or not candidate.exists():
            continue
        seen.add(resolved)
        existing.append(candidate)
    return existing


UA_SKILL_SOURCE_DIRS = _skill_source_dirs()

# Infisical API config
INFISICAL_API_URL = "https://app.infisical.com"
INFISICAL_ORG_ID = "42e4d09c-9723-48fb-8244-43e5c7b9fae3"
VPS_IP = "187.77.16.29"
CORE_HOST_CHECKS = [
    ("git", ["git", "--version"]),
    ("uv", ["uv", "--version"]),
    ("node", ["node", "--version"]),
    ("npm", ["npm", "--version"]),
    ("docker compose", ["docker", "compose", "version"]),
]

CURATED_SKILLS = [
    "clean-code",
    "systematic-debugging",
    "verification-before-completion",
    "git-commit",
    "github",
    "dependency-management",
    "task-forge",
    "skill-creator",
    "coding-agent",
    "deep-research",
    "defuddle",
    "deepwiki",
    "image-generation",
    "webapp-testing",
    "pdf",
    "media-processing",
    "json-canvas",
    "grill-me",
    "ideation",
    "just",
    "obsidian",
    "weather",
]

# Port ranges for auto-allocation
PORT_RANGE_START = 8010
PORT_RANGE_END = 8099
FRONTEND_PORT_RANGE_START = 3010
FRONTEND_PORT_RANGE_END = 3099
DB_PORT_RANGE_START = 5433
DB_PORT_RANGE_END = 5499
DOCS_PORT_RANGE_START = 8100
DOCS_PORT_RANGE_END = 8199


def slugify(name: str) -> str:
    """Convert project name to a Python module name."""
    return re.sub(r"[^a-z0-9]", "_", name.lower()).strip("_")


def find_free_port(start: int, end: int) -> int:
    """Find the next available port in a range."""
    for port in range(start, end + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"No free port in range {start}-{end}")


# ---------------------------------------------------------------------------
# Infisical helpers
# ---------------------------------------------------------------------------


def _infisical_auth() -> tuple[str, str, str]:
    """Get Infisical access token using UA's machine-identity credentials.

    Returns (access_token, client_id, client_secret).
    Reads from INFISICAL_CLIENT_ID / INFISICAL_CLIENT_SECRET env vars,
    falling back to the UA .env file.
    """
    # Try env first, then load from UA .env
    client_id = os.environ.get("INFISICAL_CLIENT_ID", "").strip()
    client_secret = os.environ.get("INFISICAL_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        ua_env = Path("/home/kjdragan/lrepos/universal_agent/.env")
        if not ua_env.exists():
            ua_env = Path("/opt/universal_agent/.env")
        if ua_env.exists():
            for line in ua_env.read_text().splitlines():
                if "=" in line and not line.strip().startswith("#"):
                    k, _, v = line.partition("=")
                    k, v = k.strip(), v.strip()
                    if k == "INFISICAL_CLIENT_ID" and not client_id:
                        client_id = v
                    elif k == "INFISICAL_CLIENT_SECRET" and not client_secret:
                        client_secret = v

    if not client_id or not client_secret:
        raise RuntimeError("Cannot find INFISICAL_CLIENT_ID / INFISICAL_CLIENT_SECRET")

    if httpx is None:
        raise RuntimeError("httpx is required for Infisical API calls")

    with httpx.Client(timeout=20.0) as client:
        resp = client.post(
            f"{INFISICAL_API_URL}/api/v1/auth/universal-auth/login",
            json={"clientId": client_id, "clientSecret": client_secret},
        )
        resp.raise_for_status()
        token = resp.json()["accessToken"]
    return token, client_id, client_secret


def create_infisical_project(project_name: str) -> str:
    """Create an Infisical project and return its ID.

    If a project with the same name already exists, return its ID instead.
    """
    token, _, _ = _infisical_auth()
    headers = {"Authorization": f"Bearer {token}"}

    if httpx is None:
        raise RuntimeError("httpx is required for Infisical API calls")

    with httpx.Client(timeout=20.0) as client:
        # Check for existing project with same name
        list_resp = client.get(
            f"{INFISICAL_API_URL}/api/v2/organizations/{INFISICAL_ORG_ID}/workspaces",
            headers=headers,
        )
        if list_resp.status_code == 200:
            for w in list_resp.json().get("workspaces", []):
                if w.get("name", "").lower() == project_name.lower():
                    print(
                        f"  ℹ Infisical project '{project_name}' already exists (ID: {w['id']})"
                    )
                    return w["id"]

        # Create new project
        resp = client.post(
            f"{INFISICAL_API_URL}/api/v2/workspace",
            json={"projectName": project_name, "organizationId": INFISICAL_ORG_ID},
            headers=headers,
        )
        resp.raise_for_status()
        project_id = resp.json()["project"]["id"]
        print(f"  ✓ Created Infisical project '{project_name}' (ID: {project_id})")
        return project_id


# ---------------------------------------------------------------------------
# Directory helpers
# ---------------------------------------------------------------------------


def _default_base_dir() -> str:
    """Pick a writable base directory for new projects."""
    if os.access("/opt", os.W_OK):
        return "/opt"
    # VPS: ua user can't write to /opt — use ~/projects
    fallback = Path.home() / "projects"
    fallback.mkdir(exist_ok=True)
    return str(fallback)


def ensure_project_dir(project_dir: Path) -> None:
    """Create the project root directory.

    Tries direct mkdir first. If that fails (e.g. /opt as non-root),
    attempts sudo. If sudo also fails, falls back to ~/projects/.
    """
    if project_dir.exists():
        return
    try:
        project_dir.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        # Try sudo (works on desktop, may fail on VPS)
        try:
            user = getpass.getuser()
            print(f"  ⚠ Permission denied for {project_dir}, trying sudo...")
            subprocess.run(
                ["sudo", "mkdir", "-p", str(project_dir)],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["sudo", "chown", "-R", f"{user}:{user}", str(project_dir)],
                check=True,
                capture_output=True,
            )
            print(f"  ✓ Created {project_dir} (owned by {user})")
        except (subprocess.CalledProcessError, FileNotFoundError):
            # sudo not available — re-raise with helpful message
            raise PermissionError(
                f"Cannot create {project_dir}. "
                f"Run with --base-dir ~/projects or ask an admin to: "
                f"sudo mkdir -p {project_dir} && sudo chown {getpass.getuser()} {project_dir}"
            )


def render_template(template_path: Path, variables: dict[str, str]) -> str:
    """Render a template file with variable substitution."""
    content = template_path.read_text()
    for key, value in variables.items():
        content = content.replace(f"{{{{{key}}}}}", value)
    return content


def create_directory_structure(project_dir: Path, module_name: str) -> None:
    """Create the full project directory tree."""
    dirs = [
        ".claude",
        ".agents/skills",
        ".github/workflows",
        f"backend/src/{module_name}/routers",
        f"backend/src/{module_name}/services",
        f"backend/src/{module_name}/models",
        f"backend/src/{module_name}/agent",
        "backend/tests",
        "backend/alembic/versions",
        "frontend/src/components",
        "frontend/public",
        "docs/01_Architecture",
        "docs/02_API",
        "docs/03_Operations",
        "docs/04_Agents",
        "docs/deployment",
        "scripts",
    ]
    for d in dirs:
        (project_dir / d).mkdir(parents=True, exist_ok=True)


def write_init_files(project_dir: Path, module_name: str) -> None:
    """Create __init__.py files for all Python packages."""
    packages = [
        f"backend/src/{module_name}",
        f"backend/src/{module_name}/routers",
        f"backend/src/{module_name}/services",
        f"backend/src/{module_name}/models",
        f"backend/src/{module_name}/agent",
    ]
    for pkg in packages:
        init = project_dir / pkg / "__init__.py"
        if not init.exists():
            init.write_text("")


def render_and_write_templates(project_dir: Path, variables: dict[str, str]) -> None:
    """Render all template files into the project directory."""
    module = variables["PROJECT_MODULE"]
    mapping = {
        # Backend
        "backend/pyproject.toml.tmpl": "backend/pyproject.toml",
        "backend/config.py.tmpl": f"backend/src/{module}/config.py",
        "backend/main.py.tmpl": f"backend/src/{module}/main.py",
        "backend/database.py.tmpl": f"backend/src/{module}/database.py",
        "backend/health.py.tmpl": f"backend/src/{module}/routers/health.py",
        "backend/agent_core.py.tmpl": f"backend/src/{module}/agent/core.py",
        "backend/agent_tools.py.tmpl": f"backend/src/{module}/agent/tools.py",
        # Docs
        "docs/README.md.tmpl": "docs/README.md",
        "docs/Documentation_Status.md.tmpl": "docs/Documentation_Status.md",
        "docs/02_Residential_Proxy_Architecture.md.tmpl": "docs/03_Operations/02_Residential_Proxy_Architecture.md",
        # Claude
        "claude/AGENTS.md.tmpl": "AGENTS.md",
        # Infrastructure
        "infrastructure/deploy.yml.tmpl": ".github/workflows/deploy.yml",
        "infrastructure/docker-compose.yml.tmpl": "docker-compose.yml",
        "infrastructure/mkdocs.yml.tmpl": "mkdocs.yml",
        "infrastructure/nginx.conf.tmpl": f"_nginx/{variables['PROJECT_NAME']}.conf",
        "infrastructure/api.service.tmpl": f"_systemd/{variables['PROJECT_NAME']}-api.service",
        "infrastructure/db.service.tmpl": f"_systemd/{variables['PROJECT_NAME']}-db.service",
        "infrastructure/web.service.tmpl": f"_systemd/{variables['PROJECT_NAME']}-web.service",
        "infrastructure/docs.service.tmpl": f"_systemd/{variables['PROJECT_NAME']}-docs.service",
    }

    for tmpl_rel, output_rel in mapping.items():
        tmpl_path = TEMPLATES_DIR / tmpl_rel
        if not tmpl_path.exists():
            print(f"  ⚠ Template not found: {tmpl_rel}")
            continue
        content = render_template(tmpl_path, variables)
        output_path = project_dir / output_rel
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content)
        print(f"  ✓ {output_rel}")


def write_static_files(project_dir: Path, variables: dict[str, str]) -> None:
    """Write static files that don't need templates."""
    module = variables["PROJECT_MODULE"]
    name = variables["PROJECT_NAME"]

    # .python-version
    (project_dir / "backend" / ".python-version").write_text("3.12\n")

    # .gitignore
    (project_dir / ".gitignore").write_text(
        "__pycache__/\n*.pyc\n.venv/\nnode_modules/\ndist/\nsite/\n*.tsbuildinfo\n"
        ".env\n*.egg-info/\n.ruff_cache/\n.mypy_cache/\n"
    )

    # .env.sample
    (project_dir / ".env.sample").write_text(
        "# Bootstrap credentials — only these go in .env\n"
        "# All other secrets are loaded from Infisical at runtime\n"
        "INFISICAL_CLIENT_ID=\nINFISICAL_CLIENT_SECRET=\n"
        f"INFISICAL_PROJECT_ID={variables.get('INFISICAL_PROJECT_ID', '')}\n"
        "INFISICAL_ENVIRONMENT=development\n"
    )

    # .infisical.json
    (project_dir / ".infisical.json").write_text(
        json.dumps(
            {
                "workspaceId": variables.get("INFISICAL_PROJECT_ID", ""),
                "defaultEnvironment": "development",
            },
            indent=2,
        )
        + "\n"
    )

    (project_dir / "project.scaffold.json").write_text(
        json.dumps(
            {
                "project_name": name,
                "project_module": module,
                "description": variables["PROJECT_DESCRIPTION"],
                "project_dir": variables["PROJECT_DIR"],
                "dns": {
                    "production": f"{variables['DNS_NAME']}.clearspringcg.com",
                    "development": f"dev-{variables['DNS_NAME']}.clearspringcg.com",
                },
                "ports": {
                    "backend": int(variables["BACKEND_PORT"]),
                    "frontend": int(variables["FRONTEND_PORT"]),
                    "database": int(variables["DB_PORT"]),
                    "docs": int(variables["DOCS_PORT"]),
                },
                "infisical": {
                    "project_id": variables.get("INFISICAL_PROJECT_ID", ""),
                    "default_environment": "development",
                    "site_url": INFISICAL_API_URL,
                },
                "dependencies": {
                    "host_clis": [
                        "git",
                        "uv",
                        "node",
                        "npm",
                        "docker compose",
                        "infisical",
                        "gh",
                    ],
                    "backend_install": "uv sync --extra dev",
                    "frontend_install": "npm install",
                },
            },
            indent=2,
        )
        + "\n"
    )

    # README.md
    (project_dir / "README.md").write_text(
        f"# {name}\n\n{variables['PROJECT_DESCRIPTION']}\n\n"
        f"## Quick Start\n\n```bash\n# Start database\n"
        f"docker compose up -d\n\n# Backend\ncd backend && uv sync --extra dev && "
        f"uv run uvicorn {module}.main:app --reload --port {variables['BACKEND_PORT']}\n\n"
        f"# Frontend\ncd frontend && npm install && npm run dev\n```\n\n"
        f"## Provisioning Check\n\n```bash\nbash scripts/preflight.sh\n```\n\n"
        f"## Documentation\n\nSee [docs/README.md](docs/README.md)\n"
    )
    (project_dir / "backend" / "README.md").write_text(
        f"# {name} Backend\n\nFastAPI backend package for {name}.\n"
    )

    # Alembic
    (project_dir / "backend" / "alembic.ini").write_text(
        "[alembic]\nscript_location = alembic\n"
        f"sqlalchemy.url = postgresql+asyncpg://postgres:postgres@localhost:"
        f"{variables['DB_PORT']}/{module}\n\n"
        "[loggers]\nkeys = root,sqlalchemy,alembic\n"
        "[handlers]\nkeys = console\n[formatters]\nkeys = generic\n"
        "[logger_root]\nlevel = WARN\nhandlers = console\n"
        "[logger_sqlalchemy]\nlevel = WARN\nhandlers =\nqualname = sqlalchemy.engine\n"
        "[logger_alembic]\nlevel = INFO\nhandlers =\nqualname = alembic\n"
        "[handler_console]\nclass = StreamHandler\nargs = (sys.stderr,)\nlevel = NOTSET\n"
        "formatter = generic\n"
        "[formatter_generic]\nformat = %(levelname)-5.5s [%(name)s] %(message)s\n"
    )

    # Alembic env.py
    (project_dir / "backend" / "alembic" / "env.py").write_text(
        "from logging.config import fileConfig\n"
        "from sqlalchemy import pool\nfrom sqlalchemy.engine import Connection\n"
        "from alembic import context\n\n"
        "config = context.config\nif config.config_file_name:\n"
        "    fileConfig(config.config_file_name)\n\n"
        f"from {module}.database import Base\ntarget_metadata = Base.metadata\n\n"
        "def run_migrations_online():\n"
        "    from sqlalchemy import create_engine\n"
        "    connectable = create_engine(config.get_main_option('sqlalchemy.url'))\n"
        "    with connectable.connect() as connection:\n"
        "        context.configure(connection=connection, target_metadata=target_metadata)\n"
        "        with context.begin_transaction():\n"
        "            context.run_migrations()\n\n"
        "run_migrations_online()\n"
    )

    # Test file
    (project_dir / "backend" / "tests" / "test_health.py").write_text(
        "import pytest\nfrom httpx import AsyncClient, ASGITransport\n"
        f"from {module}.main import app\n\n\n"
        "@pytest.mark.asyncio\nasync def test_health():\n"
        "    transport = ASGITransport(app=app)\n"
        "    async with AsyncClient(transport=transport, base_url='http://test') as c:\n"
        "        r = await c.get('/api/health')\n"
        "        assert r.status_code == 200\n"
        "        assert r.json()['status'] == 'ok'\n"
    )

    # Starter docs
    for doc, content in [
        (
            "docs/01_Architecture/01_System_Overview.md",
            f"# System Overview\n\n{name} architecture overview.\n\n## Components\n\n"
            "- **FastAPI Backend** — REST API + agent services\n"
            "- **React Frontend** — Vite-powered SPA\n"
            "- **PostgreSQL** — primary data store\n"
            "- **Claude Agent SDK** — AI agent capabilities\n",
        ),
        (
            "docs/02_API/01_Endpoints.md",
            "# API Endpoints\n\nSee FastAPI auto-generated docs at `/api/docs`.\n\n"
            '## Health\n\n- `GET /api/health` — returns `{{"status": "ok"}}`\n',
        ),
        (
            "docs/03_Operations/01_Deployment.md",
            f"# Deployment\n\n## Production\n\nPush to `main` triggers automated deploy.\n\n"
            f"## Services\n\n- `{name}-api` — FastAPI backend\n"
            f"- `{name}-db` — PostgreSQL container\n"
            f"- `{name}-web` — Vite dev server for dev subdomain\n"
            f"- `{name}-docs` — MkDocs server\n",
        ),
        (
            "docs/04_Agents/01_Agent_Architecture.md",
            f"# Agent Architecture\n\n{name} uses the Anthropic Python SDK for AI agent capabilities.\n\n"
            "## Key Files\n\n- `backend/src/*/agent/core.py` — agent execution loop\n"
            "- `backend/src/*/agent/tools.py` — tool definitions and dispatcher\n",
        ),
        (
            "docs/deployment/secrets_and_environments.md",
            "# Secrets and Environments\n\nAll secrets managed via Infisical.\n\n"
            "## Project Context\n\n"
            f"- Infisical project ID: `{variables.get('INFISICAL_PROJECT_ID', '')}`\n"
            "- Default environment: `development`\n"
            "- Project metadata: `project.scaffold.json`\n\n"
            "## Bootstrap\n\nOnly `.env` contains Infisical credentials.\n"
            "All other secrets loaded at runtime via `config.py`.\n\n"
            "## CLI Checks\n\n"
            "```bash\n"
            "infisical --version\n"
            "infisical secrets list --env development\n"
            'infisical secrets set APP_SECRET_KEY="..." --env development\n'
            "```\n",
        ),
        (
            "docs/deployment/ci_cd_pipeline.md",
            "# CI/CD Pipeline\n\n## Trigger\n\nPush to `main` → deploy via GitHub Actions.\n\n"
            "## Steps\n\n1. Connect Tailscale\n2. Write bootstrap .env\n"
            "3. Pull + sync deps\n4. Run migrations\n5. Build frontend\n6. Restart services\n",
        ),
        ("docs/Glossary.md", "# Glossary\n\nProject terminology reference.\n"),
    ]:
        p = project_dir / doc
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)

    # .claude/settings.json
    (project_dir / ".claude" / "settings.json").write_text(
        json.dumps(
            {
                "permissions": {"allow": ["Read", "Edit", "Write", "Command"]},
            },
            indent=2,
        )
        + "\n"
    )

    # scripts/dev.sh
    dev_script = project_dir / "scripts" / "dev.sh"
    dev_script.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n\n"
        "echo '🐘 Starting PostgreSQL...'\ndocker compose up -d\n\n"
        f"echo '🐍 Starting backend on port {variables['BACKEND_PORT']}...'\n"
        f"cd backend && uv run uvicorn {module}.main:app "
        f"--reload --port {variables['BACKEND_PORT']} &\n\n"
        f"echo '⚛️  Starting frontend on port {variables['FRONTEND_PORT']}...'\n"
        f"cd frontend && npm run dev -- --port {variables['FRONTEND_PORT']} &\n\n"
        "wait\n"
    )
    dev_script.chmod(0o755)

    # scripts/bootstrap.sh
    bootstrap = project_dir / "scripts" / "bootstrap.sh"
    bootstrap.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n\n"
        "echo '📦 Setting up backend...'\ncd backend\nuv sync --extra dev\n"
        "uv run alembic upgrade head\ncd ..\n\n"
        "echo '⚛️  Setting up frontend...'\ncd frontend\nnpm install\ncd ..\n\n"
        "echo '✅ Bootstrap complete. Run scripts/dev.sh to start.'\n"
    )
    bootstrap.chmod(0o755)

    preflight = project_dir / "scripts" / "preflight.sh"
    preflight.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n\n"
        "check() {\n"
        '  local label="$1"\n'
        "  shift\n"
        "  printf 'Checking %s... ' \"$label\"\n"
        '  "$@" >/dev/null\n'
        "  printf 'ok\\n'\n"
        "}\n\n"
        "check git git --version\n"
        "check uv uv --version\n"
        "check node node --version\n"
        "check npm npm --version\n"
        "check 'docker compose' docker compose version\n"
        "check infisical infisical --version\n"
        "check gh gh --version\n\n"
        "test -f .infisical.json\n"
        "test -f project.scaffold.json\n"
        "test -d backend/.venv\n"
        "test -d frontend/node_modules\n\n"
        "echo 'Preflight complete.'\n"
    )
    preflight.chmod(0o755)


def symlink_skills(project_dir: Path) -> None:
    """Symlink curated skills from UA to the new project's canonical skill dir."""
    dest_dir = project_dir / ".agents" / "skills"
    dest_dir.mkdir(parents=True, exist_ok=True)
    for skill in CURATED_SKILLS:
        src = next(
            (
                source_dir / skill
                for source_dir in UA_SKILL_SOURCE_DIRS
                if (source_dir / skill).exists()
            ),
            None,
        )
        dst = dest_dir / skill
        if src is None:
            print(f"  ⚠ Skill not found: {skill}")
            continue
        if dst.exists():
            continue
        dst.symlink_to(src)
        print(f"  ✓ .agents/skills/{skill} → {src}")


def run_command(
    command: list[str], cwd: Path, label: str, *, required: bool = True
) -> bool:
    """Run a command and print a concise status line."""
    executable = shutil.which(command[0])
    if executable is None:
        message = f"  ⚠ {label} skipped: {command[0]} not found"
        if required:
            raise RuntimeError(message)
        print(message)
        return False

    result = subprocess.run(command, cwd=cwd, text=True, capture_output=True)
    if result.returncode == 0:
        print(f"  ✓ {label}")
        return True

    stderr = (
        result.stderr.strip()
        or result.stdout.strip()
        or f"exit code {result.returncode}"
    )
    message = f"  ⚠ {label} failed: {stderr}"
    if required:
        raise RuntimeError(message)
    print(message)
    return False


def check_cli(command: list[str], label: str, *, required: bool = True) -> bool:
    """Verify a host CLI is available and runnable."""
    if shutil.which(command[0]) is None:
        message = f"  ⚠ {label} check failed: {command[0]} not found on PATH"
        if required:
            raise RuntimeError(message)
        print(message)
        return False

    result = subprocess.run(command, text=True, capture_output=True)
    if result.returncode == 0:
        version = (result.stdout.strip() or result.stderr.strip()).splitlines()[0]
        print(f"  ✓ {label} available ({version})")
        return True

    stderr = result.stderr.strip() or result.stdout.strip()
    message = f"  ⚠ {label} check failed: {stderr}"
    if required:
        raise RuntimeError(message)
    print(message)
    return False


def check_infisical_cli(*, required: bool = True) -> bool:
    """Verify the host has the Infisical CLI available for post-scaffold secret work."""
    return check_cli(["infisical", "--version"], "Infisical CLI", required=required)


def check_host_dependencies(*, skip_infisical: bool, skip_github: bool) -> None:
    """Verify required host CLIs before creating a project."""
    print("\n🔎 Checking host dependencies...")
    for label, command in CORE_HOST_CHECKS:
        check_cli(command, label)
    if not skip_infisical:
        check_infisical_cli()
    if not skip_github:
        check_cli(["gh", "--version"], "GitHub CLI")


def install_dependencies(project_dir: Path) -> bool:
    """Install backend and frontend dependencies so the scaffold is runnable."""
    backend_ok = run_command(
        ["uv", "sync", "--extra", "dev"],
        project_dir / "backend",
        "Backend dependencies",
    )
    frontend_ok = run_command(
        ["npm", "install"], project_dir / "frontend", "Frontend dependencies"
    )
    return backend_ok and frontend_ok


def initialize_git(project_dir: Path) -> None:
    """Create a main-branch repository with an initial Lore-compatible commit."""
    try:
        run_command(
            ["git", "init", "-b", "main"], project_dir, "Git repository initialized"
        )
    except RuntimeError:
        run_command(["git", "init"], project_dir, "Git repository initialized")
        run_command(
            ["git", "branch", "-M", "main"], project_dir, "Git branch renamed to main"
        )

    run_command(
        ["git", "config", "user.name", "Universal Agent Scaffolder"],
        project_dir,
        "Git author name configured",
    )
    run_command(
        ["git", "config", "user.email", "ua-scaffolder@clearspringcg.com"],
        project_dir,
        "Git author email configured",
    )
    run_command(["git", "add", "."], project_dir, "Git files staged")
    run_command(
        [
            "git",
            "commit",
            "-m",
            "chore: initial scaffold",
            "-m",
            "Create the generated project baseline from the Universal Agent project-scaffolder skill.",
            "-m",
            "Constraint: Generated project should be immediately reproducible from committed scaffold output\nConfidence: high\nScope-risk: narrow\nTested: scaffold.py generation path",
        ],
        project_dir,
        "Initial git commit created",
    )


def publish_github_repo(
    project_dir: Path, owner: str, name: str, description: str
) -> bool:
    """Create or reuse a GitHub repository and push the main branch."""
    if not run_command(
        ["gh", "auth", "status"], project_dir, "GitHub authentication", required=False
    ):
        return False

    repo = f"{owner}/{name}"
    repo_exists = (
        subprocess.run(
            ["gh", "repo", "view", repo, "--json", "name"],
            cwd=project_dir,
            text=True,
            capture_output=True,
        ).returncode
        == 0
    )

    if repo_exists:
        remote_url = f"git@github.com:{repo}.git"
        remote_exists = (
            subprocess.run(
                ["git", "remote", "get-url", "origin"],
                cwd=project_dir,
                capture_output=True,
            ).returncode
            == 0
        )
        if not remote_exists:
            run_command(
                ["git", "remote", "add", "origin", remote_url],
                project_dir,
                "GitHub remote configured",
            )
        run_command(
            ["git", "push", "-u", "origin", "main"],
            project_dir,
            "Pushed main to existing GitHub repo",
        )
        return True

    run_command(
        [
            "gh",
            "repo",
            "create",
            repo,
            "--private",
            "--description",
            description,
            "--source",
            str(project_dir),
            "--remote",
            "origin",
            "--push",
        ],
        project_dir,
        "GitHub repository created and pushed",
    )
    return True


def write_frontend_files(project_dir: Path, variables: dict[str, str]) -> None:
    """Write Vite + React starter files."""
    fe = project_dir / "frontend"

    (fe / "package.json").write_text(
        json.dumps(
            {
                "name": variables["PROJECT_NAME"],
                "private": True,
                "version": "0.1.0",
                "type": "module",
                "scripts": {
                    "dev": "vite",
                    "build": "tsc -b && vite build",
                    "preview": "vite preview",
                },
                "dependencies": {
                    "react": "^19.0.0",
                    "react-dom": "^19.0.0",
                },
                "devDependencies": {
                    "@types/react": "^19.0.0",
                    "@types/react-dom": "^19.0.0",
                    "@vitejs/plugin-react": "^4.3.0",
                    "typescript": "~5.7.0",
                    "vite": "^6.0.0",
                },
            },
            indent=2,
        )
        + "\n"
    )

    (fe / "tsconfig.json").write_text(
        json.dumps(
            {
                "compilerOptions": {
                    "target": "ES2020",
                    "useDefineForClassFields": True,
                    "lib": ["ES2020", "DOM", "DOM.Iterable"],
                    "module": "ESNext",
                    "skipLibCheck": True,
                    "moduleResolution": "bundler",
                    "allowImportingTsExtensions": True,
                    "isolatedModules": True,
                    "moduleDetection": "force",
                    "noEmit": True,
                    "jsx": "react-jsx",
                    "strict": True,
                },
                "include": ["src"],
            },
            indent=2,
        )
        + "\n"
    )

    name = variables["PROJECT_NAME"]
    backend_port = variables["BACKEND_PORT"]
    (fe / "vite.config.ts").write_text(
        "import { defineConfig } from 'vite'\nimport react from '@vitejs/plugin-react'\n\n"
        "export default defineConfig({\n  plugins: [react()],\n"
        "  server: {\n    proxy: {\n"
        f"      '/api': 'http://localhost:{backend_port}',\n"
        "    },\n  },\n})\n"
    )

    (fe / "index.html").write_text(
        f'<!doctype html>\n<html lang="en">\n<head>\n'
        f'  <meta charset="UTF-8" />\n  <meta name="viewport" '
        f'content="width=device-width, initial-scale=1.0" />\n'
        f"  <title>{name}</title>\n</head>\n<body>\n"
        f'  <div id="root"></div>\n'
        f'  <script type="module" src="/src/main.tsx"></script>\n'
        f"</body>\n</html>\n"
    )

    (fe / "src" / "main.tsx").write_text(
        "import React from 'react'\nimport ReactDOM from 'react-dom/client'\n"
        "import App from './App'\nimport './index.css'\n\n"
        "ReactDOM.createRoot(document.getElementById('root')!).render(\n"
        "  <React.StrictMode><App /></React.StrictMode>\n)\n"
    )

    (fe / "src" / "App.tsx").write_text(
        "import { useState, useEffect } from 'react'\nimport './App.css'\n\n"
        "function App() {\n  const [health, setHealth] = useState<string>('loading...')\n\n"
        "  useEffect(() => {\n    fetch('/api/health')\n"
        "      .then(r => r.json())\n      .then(d => setHealth(d.status))\n"
        "      .catch(() => setHealth('error'))\n  }, [])\n\n"
        f'  return (\n    <div className="app">\n'
        f"      <h1>{name}</h1>\n"
        f'      <p>API Status: <span className="status">{{health}}</span></p>\n'
        f"    </div>\n  )\n}}\n\nexport default App\n"
    )

    (fe / "src" / "App.css").write_text(
        ".app { max-width: 800px; margin: 4rem auto; text-align: center; }\n"
        ".status { font-weight: bold; color: #4caf50; }\n"
    )

    (fe / "src" / "index.css").write_text(
        ":root { font-family: Inter, system-ui, sans-serif; }\n"
        "body { margin: 0; background: #0a0a0a; color: #ededed; }\n"
    )


def main():
    parser = argparse.ArgumentParser(description="Scaffold a new VPS project")
    parser.add_argument(
        "--name", required=True, help="Project name (lowercase, hyphenated)"
    )
    parser.add_argument("--description", required=True, help="Project description")
    parser.add_argument(
        "--infisical-project-id",
        default="",
        help="Infisical project ID (auto-created if omitted)",
    )
    parser.add_argument(
        "--skip-infisical", action="store_true", help="Skip Infisical project creation"
    )
    parser.add_argument(
        "--skip-install", action="store_true", help="Skip uv sync and npm install"
    )
    parser.add_argument(
        "--skip-github",
        action="store_true",
        help="Skip GitHub repository creation and push",
    )
    parser.add_argument(
        "--github-owner", default="kjdragan", help="GitHub owner for the new repository"
    )
    parser.add_argument(
        "--base-dir",
        default=_default_base_dir(),
        help="Base directory for projects (auto-detected)",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Print plan without creating files"
    )
    args = parser.parse_args()

    name = args.name.lower().strip()
    # Normalize: underscores → hyphens for DNS/subdomain compatibility
    dns_name = name.replace("_", "-")
    module = slugify(name)
    project_dir = Path(args.base_dir).expanduser().resolve() / name
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Allocate ports
    backend_port = find_free_port(PORT_RANGE_START, PORT_RANGE_END)
    frontend_port = find_free_port(FRONTEND_PORT_RANGE_START, FRONTEND_PORT_RANGE_END)
    db_port = find_free_port(DB_PORT_RANGE_START, DB_PORT_RANGE_END)
    docs_port = find_free_port(DOCS_PORT_RANGE_START, DOCS_PORT_RANGE_END)

    if not args.dry_run:
        check_host_dependencies(
            skip_infisical=args.skip_infisical,
            skip_github=args.skip_github,
        )

    # Auto-provision Infisical project if not provided
    infisical_project_id = args.infisical_project_id
    if not infisical_project_id and not args.skip_infisical and not args.dry_run:
        try:
            print("\n🔑 Provisioning Infisical project...")
            infisical_project_id = create_infisical_project(dns_name)
        except Exception as exc:
            print(f"  ⚠ Infisical auto-creation failed: {exc}")
            print("  ℹ Continuing without Infisical. Set up manually later.")

    variables = {
        "PROJECT_NAME": name,
        "PROJECT_MODULE": module,
        "PROJECT_DESCRIPTION": args.description,
        "PROJECT_DIR": str(project_dir),
        "DNS_NAME": dns_name,
        "BACKEND_PORT": str(backend_port),
        "FRONTEND_PORT": str(frontend_port),
        "DB_PORT": str(db_port),
        "DOCS_PORT": str(docs_port),
        "INFISICAL_PROJECT_ID": infisical_project_id,
        "CREATED_DATE": now,
    }

    print("\n🏗  Project Scaffolder")
    print(f"   Name:     {name}")
    print(f"   Module:   {module}")
    print(f"   Dir:      {project_dir}")
    print(f"   Backend:  port {backend_port}")
    print(f"   Frontend: port {frontend_port}")
    print(f"   Database: port {db_port}")
    print(f"   Docs:     port {docs_port}")
    print(f"   Prod URL: https://{dns_name}.clearspringcg.com")
    print(f"   Dev URL:  https://dev-{dns_name}.clearspringcg.com")
    if infisical_project_id:
        print(f"   Infisical: {infisical_project_id}")

    if args.dry_run:
        print("\n🔍 Dry run — no files created.")
        return

    if project_dir.exists():
        print(f"\n❌ Directory already exists: {project_dir}")
        sys.exit(1)

    # Create root dir (with sudo fallback for /opt)
    print("\n📁 Creating project directory...")
    ensure_project_dir(project_dir)

    print("📁 Creating directory structure...")
    create_directory_structure(project_dir, module)

    print("📝 Writing __init__.py files...")
    write_init_files(project_dir, module)

    print("📄 Rendering templates...")
    render_and_write_templates(project_dir, variables)

    print("📄 Writing static files...")
    write_static_files(project_dir, variables)

    print("⚛️  Writing frontend files...")
    write_frontend_files(project_dir, variables)

    print("🔗 Symlinking skills...")
    symlink_skills(project_dir)

    if not args.skip_install:
        print("\n📦 Installing dependencies...")
        install_dependencies(project_dir)

    print("\n🔧 Initializing git...")
    initialize_git(project_dir)

    github_published = False
    if not args.skip_github:
        print("\n📦 Creating GitHub repository...")
        github_published = publish_github_repo(
            project_dir,
            args.github_owner,
            name,
            args.description,
        )

    print(f"\n✅ Project scaffolded at {project_dir}")
    print("\n📋 Next steps:")
    print("")
    print("   1. 🌐 DNS — Add A records at your domain registrar:")
    print(f"      {dns_name}.clearspringcg.com     → A → {VPS_IP}")
    print(f"      dev-{dns_name}.clearspringcg.com  → A → {VPS_IP}")
    print("")
    if github_published:
        print(f"   2. 📦 GitHub — Created and pushed: {args.github_owner}/{name}")
    else:
        print("   2. 📦 GitHub — Create repo and push when ready:")
        print(
            f"      gh repo create {args.github_owner}/{name} --private --source={project_dir} --push"
        )
    print("")
    if infisical_project_id:
        print(f"   3. 🔑 Infisical — Project auto-created (ID: {infisical_project_id})")
        print("      Seed secrets: ANTHROPIC_API_KEY, DATABASE_URL, APP_SECRET_KEY")
    else:
        print(
            "   3. 🔑 Infisical — Create project at app.infisical.com and seed secrets"
        )
    print("")
    print("   4. 🔧 VPS setup:")
    print(f"      sudo cp {project_dir}/_nginx/{name}.conf /etc/nginx/sites-enabled/")
    print(
        f"      sudo certbot --nginx -d {dns_name}.clearspringcg.com -d dev-{dns_name}.clearspringcg.com"
    )
    print(f"      sudo cp {project_dir}/_systemd/*.service /etc/systemd/system/")
    if args.skip_install:
        print(f"      cd {project_dir} && bash scripts/bootstrap.sh")
    print(
        f"      sudo systemctl enable --now {name}-db {name}-api {name}-web {name}-docs"
    )


if __name__ == "__main__":
    main()

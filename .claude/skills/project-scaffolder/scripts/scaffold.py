#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Project Scaffolder — creates fully-provisioned VPS projects.

Usage:
    uv run .agents/skills/project-scaffolder/scripts/scaffold.py \
        --name my-project --description "My new project"
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = SKILL_DIR / "templates"
UA_SKILLS_DIR = Path("/home/kjdragan/lrepos/universal_agent/.agents/skills")

CURATED_SKILLS = [
    "clean-code", "systematic-debugging", "verification-before-completion",
    "git-commit", "github", "dependency-management", "task-forge", "skill-creator",
    "coding-agent", "deep-research", "defuddle", "deepwiki", "image-generation",
    "webapp-testing", "pdf", "media-processing", "json-canvas", "grill-me",
    "ideation", "just", "obsidian", "weather",
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


def render_template(template_path: Path, variables: dict[str, str]) -> str:
    """Render a template file with variable substitution."""
    content = template_path.read_text()
    for key, value in variables.items():
        content = content.replace(f"{{{{{key}}}}}", value)
    return content


def create_directory_structure(project_dir: Path, module_name: str) -> None:
    """Create the full project directory tree."""
    dirs = [
        ".claude/skills", ".agents/skills",
        ".github/workflows",
        f"backend/src/{module_name}/routers",
        f"backend/src/{module_name}/services",
        f"backend/src/{module_name}/models",
        f"backend/src/{module_name}/agent",
        "backend/tests",
        "backend/alembic/versions",
        "frontend/src/components", "frontend/public",
        "docs/01_Architecture", "docs/02_API", "docs/03_Operations",
        "docs/04_Agents", "docs/deployment",
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
        "__pycache__/\n*.pyc\n.venv/\nnode_modules/\ndist/\nsite/\n"
        ".env\n*.egg-info/\n.ruff_cache/\n.mypy_cache/\n_nginx/\n_systemd/\n"
    )

    # .env.sample
    (project_dir / ".env.sample").write_text(
        "# Bootstrap credentials — only these go in .env\n"
        "# All other secrets are loaded from Infisical at runtime\n"
        "INFISICAL_CLIENT_ID=\nINFISICAL_CLIENT_SECRET=\n"
        f"INFISICAL_PROJECT_ID=\nINFISICAL_ENVIRONMENT=development\n"
    )

    # .infisical.json
    (project_dir / ".infisical.json").write_text(json.dumps({
        "workspaceId": variables.get("INFISICAL_PROJECT_ID", ""),
        "defaultEnvironment": "development",
    }, indent=2) + "\n")

    # README.md
    (project_dir / "README.md").write_text(
        f"# {name}\n\n{variables['PROJECT_DESCRIPTION']}\n\n"
        f"## Quick Start\n\n```bash\n# Start database\n"
        f"docker compose up -d\n\n# Backend\ncd backend && uv sync && "
        f"uv run uvicorn {module}.main:app --reload --port {variables['BACKEND_PORT']}\n\n"
        f"# Frontend\ncd frontend && npm install && npm run dev\n```\n\n"
        f"## Documentation\n\nSee [docs/README.md](docs/README.md)\n"
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
        ("docs/01_Architecture/01_System_Overview.md",
         f"# System Overview\n\n{name} architecture overview.\n\n## Components\n\n"
         "- **FastAPI Backend** — REST API + agent services\n"
         "- **React Frontend** — Vite-powered SPA\n"
         "- **PostgreSQL** — primary data store\n"
         "- **Claude Agent SDK** — AI agent capabilities\n"),
        ("docs/02_API/01_Endpoints.md",
         f"# API Endpoints\n\nSee FastAPI auto-generated docs at `/api/docs`.\n\n"
         "## Health\n\n- `GET /api/health` — returns `{{\"status\": \"ok\"}}`\n"),
        ("docs/03_Operations/01_Deployment.md",
         f"# Deployment\n\n## Production\n\nPush to `main` triggers automated deploy.\n\n"
         f"## Services\n\n- `{name}-api` — FastAPI backend\n"
         f"- `{name}-db` — PostgreSQL container\n"
         f"- `{name}-docs` — MkDocs server\n"),
        ("docs/04_Agents/01_Agent_Architecture.md",
         f"# Agent Architecture\n\n{name} uses the Anthropic Python SDK for AI agent capabilities.\n\n"
         "## Key Files\n\n- `backend/src/*/agent/core.py` — agent execution loop\n"
         "- `backend/src/*/agent/tools.py` — tool definitions and dispatcher\n"),
        ("docs/deployment/secrets_and_environments.md",
         f"# Secrets and Environments\n\nAll secrets managed via Infisical.\n\n"
         "## Bootstrap\n\nOnly `.env` contains Infisical credentials.\n"
         "All other secrets loaded at runtime via `config.py`.\n"),
        ("docs/deployment/ci_cd_pipeline.md",
         "# CI/CD Pipeline\n\n## Trigger\n\nPush to `main` → deploy via GitHub Actions.\n\n"
         "## Steps\n\n1. Connect Tailscale\n2. Write bootstrap .env\n"
         "3. Pull + sync deps\n4. Run migrations\n5. Build frontend\n6. Restart services\n"),
        ("docs/Glossary.md", "# Glossary\n\nProject terminology reference.\n"),
    ]:
        p = project_dir / doc
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)

    # .claude/settings.json
    (project_dir / ".claude" / "settings.json").write_text(json.dumps({
        "permissions": {"allow": ["Read", "Edit", "Write", "Command"]},
    }, indent=2) + "\n")

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
        "echo '📦 Setting up backend...'\ncd backend\nuv sync\n"
        "uv run alembic upgrade head\ncd ..\n\n"
        "echo '⚛️  Setting up frontend...'\ncd frontend\nnpm install\ncd ..\n\n"
        "echo '✅ Bootstrap complete. Run scripts/dev.sh to start.'\n"
    )
    bootstrap.chmod(0o755)


def symlink_skills(project_dir: Path) -> None:
    """Symlink curated skills from UA to the new project."""
    for dest_parent in [".claude/skills", ".agents/skills"]:
        dest_dir = project_dir / dest_parent
        dest_dir.mkdir(parents=True, exist_ok=True)
        for skill in CURATED_SKILLS:
            src = UA_SKILLS_DIR / skill
            dst = dest_dir / skill
            if src.exists() and not dst.exists():
                dst.symlink_to(src)
                print(f"  ✓ {dest_parent}/{skill} → {src}")
            elif not src.exists():
                print(f"  ⚠ Skill not found: {skill}")


def write_frontend_files(project_dir: Path, variables: dict[str, str]) -> None:
    """Write Vite + React starter files."""
    fe = project_dir / "frontend"

    (fe / "package.json").write_text(json.dumps({
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
    }, indent=2) + "\n")

    (fe / "tsconfig.json").write_text(json.dumps({
        "compilerOptions": {
            "target": "ES2020", "useDefineForClassFields": True,
            "lib": ["ES2020", "DOM", "DOM.Iterable"],
            "module": "ESNext", "skipLibCheck": True,
            "moduleResolution": "bundler", "allowImportingTsExtensions": True,
            "isolatedModules": True, "moduleDetection": "force",
            "noEmit": True, "jsx": "react-jsx", "strict": True,
        },
        "include": ["src"],
    }, indent=2) + "\n")

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
        f'  <title>{name}</title>\n</head>\n<body>\n'
        f'  <div id="root"></div>\n'
        f'  <script type="module" src="/src/main.tsx"></script>\n'
        f'</body>\n</html>\n'
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
        f"  return (\n    <div className=\"app\">\n"
        f"      <h1>{name}</h1>\n"
        f"      <p>API Status: <span className=\"status\">{{health}}</span></p>\n"
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
    parser.add_argument("--name", required=True, help="Project name (lowercase, hyphenated)")
    parser.add_argument("--description", required=True, help="Project description")
    parser.add_argument("--infisical-project-id", default="", help="Infisical project ID")
    parser.add_argument("--base-dir", default="/opt", help="Base directory for projects")
    parser.add_argument("--dry-run", action="store_true", help="Print plan without creating files")
    args = parser.parse_args()

    name = args.name.lower().strip()
    module = slugify(name)
    project_dir = Path(args.base_dir) / name
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Allocate ports
    backend_port = find_free_port(PORT_RANGE_START, PORT_RANGE_END)
    frontend_port = find_free_port(FRONTEND_PORT_RANGE_START, FRONTEND_PORT_RANGE_END)
    db_port = find_free_port(DB_PORT_RANGE_START, DB_PORT_RANGE_END)
    docs_port = find_free_port(DOCS_PORT_RANGE_START, DOCS_PORT_RANGE_END)

    variables = {
        "PROJECT_NAME": name,
        "PROJECT_MODULE": module,
        "PROJECT_DESCRIPTION": args.description,
        "BACKEND_PORT": str(backend_port),
        "FRONTEND_PORT": str(frontend_port),
        "DB_PORT": str(db_port),
        "DOCS_PORT": str(docs_port),
        "INFISICAL_PROJECT_ID": args.infisical_project_id,
        "CREATED_DATE": now,
    }

    print(f"\n🏗  Project Scaffolder")
    print(f"   Name:     {name}")
    print(f"   Module:   {module}")
    print(f"   Dir:      {project_dir}")
    print(f"   Backend:  port {backend_port}")
    print(f"   Frontend: port {frontend_port}")
    print(f"   Database: port {db_port}")
    print(f"   Docs:     port {docs_port}")
    print(f"   Prod URL: https://{name}.clearspringcg.com")
    print(f"   Dev URL:  https://dev-{name}.clearspringcg.com")

    if args.dry_run:
        print("\n🔍 Dry run — no files created.")
        return

    if project_dir.exists():
        print(f"\n❌ Directory already exists: {project_dir}")
        sys.exit(1)

    print(f"\n📁 Creating directory structure...")
    create_directory_structure(project_dir, module)

    print(f"📝 Writing __init__.py files...")
    write_init_files(project_dir, module)

    print(f"📄 Rendering templates...")
    render_and_write_templates(project_dir, variables)

    print(f"📄 Writing static files...")
    write_static_files(project_dir, variables)

    print(f"⚛️  Writing frontend files...")
    write_frontend_files(project_dir, variables)

    print(f"🔗 Symlinking skills...")
    symlink_skills(project_dir)

    print(f"\n🔧 Initializing git...")
    subprocess.run(["git", "init"], cwd=project_dir, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=project_dir, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", f"Initial scaffold for {name}"],
        cwd=project_dir, capture_output=True,
    )

    print(f"\n✅ Project scaffolded at {project_dir}")
    print(f"\n📋 Next steps:")
    print(f"   1. Add DNS A records for {name}.clearspringcg.com")
    print(f"   2. Create GitHub repo: gh repo create kjdragan/{name} --private --source={project_dir} --push")
    print(f"   3. Seed Infisical secrets (ANTHROPIC_API_KEY, DATABASE_URL, APP_SECRET_KEY)")
    print(f"   4. Install nginx config: sudo cp {project_dir}/_nginx/{name}.conf /etc/nginx/sites-enabled/")
    print(f"   5. SSL: sudo certbot --nginx -d {name}.clearspringcg.com -d dev-{name}.clearspringcg.com")
    print(f"   6. Install systemd: sudo cp {project_dir}/_systemd/*.service /etc/systemd/system/")
    print(f"   7. Bootstrap: cd {project_dir} && bash scripts/bootstrap.sh")
    print(f"   8. Start: sudo systemctl enable --now {name}-db {name}-api {name}-docs")


if __name__ == "__main__":
    main()

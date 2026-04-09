# Infisical CLI Reference and Lessons Learned

> Source of truth for using Infisical CLI in the Universal Agent project — authentication, secret management, environment setup, and agent integration patterns.

## Quick Reference

| Task | Command |
|------|---------|
| Login (browser-based) | `infisical login` |
| Link project | `infisical init` (interactive, creates `.infisical.json`) |
| List all secrets | `infisical secrets --env=development` |
| Get one secret (value only) | `infisical secrets get SECRET_NAME --env=development --plain` |
| Set a secret | `infisical secrets set KEY=value --env=development` |
| Run command with secrets injected | `infisical run --env=development -- command` |
| Export to .env file | `infisical export --env=development --output-file=.env.local` |
| Export as JSON | `infisical export --env=development --format=json` |

## Project Configuration

### Infisical Project
- **Org**: Clearspring CG
- **Project**: Clearspring CG Secrets
- **Project ID**: `9970e5b7-d48a-4ed8-a8af-43e923e67572`

### Environment Mapping

| Infisical Env | UA Runtime Stage | Used By |
|---------------|-----------------|---------|
| `development` | development | Kevin's desktop, local dev |
| `staging` | staging | VPS staging at `/opt/universal-agent-staging` |
| `production` | production | VPS production at `/opt/universal_agent` |

### Config File: `.infisical.json`

Located at repo root. Created by `infisical init`:

```json
{
    "workspaceId": "9970e5b7-d48a-4ed8-a8af-43e923e67572",
    "defaultEnvironment": "",
    "gitBranchToEnvironmentMapping": null
}
```

> [!NOTE]
> This file is `.gitignored`. Each machine creates its own via `infisical init`.

## Authentication Methods

### Interactive Login (Desktop)

```bash
infisical login
# Opens browser → log in with Google → CLI auto-authenticates
# Token is cached locally by the CLI
```

### Machine Identity (CI/CD, VPS)

Used by GitHub Actions and VPS for non-interactive auth. Requires:
- `INFISICAL_CLIENT_ID`
- `INFISICAL_CLIENT_SECRET`
- `INFISICAL_PROJECT_ID`

Set in `.env` bootstrap file or as GitHub secrets. The `infisical_loader.py` handles this at runtime.

## Common Operations

### Getting a Specific Secret

```bash
# Get just the value (for scripting)
infisical secrets get AGENTMAIL_API_KEY --env=development --plain --silent

# Get with metadata
infisical secrets get AGENTMAIL_API_KEY --env=development
```

### Running Commands with Secrets

The **recommended pattern** for injecting all secrets into a process:

```bash
# Inject all development secrets into a Python script
infisical run --env=development -- python3 my_script.py

# Inject production secrets into a command
infisical run --env=production -- curl -s https://api.example.com

# Use --silent to suppress the update nag message
infisical run --env=development --silent -- python3 -c "import os; print(os.environ['MY_SECRET'])"
```

`infisical run` injects **all** secrets from the specified environment as environment variables before executing the command.

### Setting Secrets

```bash
# Single secret
infisical secrets set MY_KEY=my_value --env=development

# Multiple secrets
infisical secrets set DB_HOST=localhost DB_PORT=5432 --env=development

# Setting across all environments (development, staging, production)
# IMPORTANT: When adding new system-level secrets, ensure they are present in all stages
for ENV in development staging production; do
  infisical secrets set --env=$ENV NEW_SECRET_KEY="the_value"
done

# From file content
infisical secrets set CERT=@/path/to/cert.pem --env=development
```

### Exporting Secrets

```bash
# To stdout as dotenv
infisical export --env=development

# To file
infisical export --env=development --output-file=.env.local

# As JSON
infisical export --env=development --format=json
```

## Integration with Universal Agent

### How the Runtime Loads Secrets

At startup, `src/universal_agent/infisical_loader.py` calls `initialize_runtime_secrets()` which:

1. Reads bootstrap `.env` for auth credentials (`INFISICAL_CLIENT_ID`, etc.)
2. Authenticates with Infisical using machine identity (VPS) or falls back to dotenv (desktop)
3. Injects all secrets from the resolved environment into `os.environ`

### Key Secrets for Agent Operations

| Secret | Purpose | Used By |
|--------|---------|---------|
| `AGENTMAIL_API_KEY` | AgentMail API access | AgentMail service |
| `UA_AGENTMAIL_INBOX_ADDRESS` | Simone's inbox (`oddcity216@agentmail.to`) | AgentMail service |
| `ANTHROPIC_API_KEY` | Claude API | Agent runtime |
| `UA_OPS_TOKEN` | Ops API authentication | Gateway server |
| `NOTEBOOKLM_AUTH_COOKIE_HEADER` | NLM API cookies | NLM MCP server |

### For Agents and Agentic Tools (Me)

To access Infisical secrets from agentic context:

```bash
# Pattern 1: Get a single secret value for use in a command
API_KEY=$(infisical secrets get AGENTMAIL_API_KEY --env=development --plain --silent 2>/dev/null)
curl -H "Authorization: Bearer $API_KEY" https://api.agentmail.to/v0/inboxes

# Pattern 2: Run a full script with all secrets injected
infisical run --env=development --silent -- python3 myscript.py

# Pattern 3: Export to temporary env file (for complex multi-step operations)
infisical export --env=development --output-file=/tmp/.env.infisical
```

## Lessons Learned

### 1. Interactive Init Required

`infisical init` is interactive — it presents a list of orgs and projects. Cannot be run non-interactively without `--projectId`. For automated setups, create `.infisical.json` manually with the known project ID.

### 2. `--plain` and `--silent` Are Essential for Scripting

Without `--plain`, `infisical secrets get` returns formatted output with headers. Without `--silent`, every command prints an upgrade nag message. Always use both for scripting:
```bash
infisical secrets get SECRET_NAME --env=development --plain --silent 2>/dev/null
```

### 3. `infisical run` Is the Recommended Pattern

Rather than loading individual secrets, `infisical run --env=development -- command` injects **all** secrets as environment variables. The UA project's `infisical_loader.py` effectively does the same thing programmatically.

### 4. Environment Name Matters

The Infisical CLI uses `--env=dev` as default, but our project uses full names: `development`, `staging`, `production`. Always specify `--env=development` explicitly.

### 5. SDK vs CLI vs curl — Debugging Sequence

- **curl**: Most reliable for quick API tests. No dependency issues.
- **Infisical CLI**: Best for pulling individual secrets. `infisical secrets get KEY --plain --silent` is fast and scriptable.
- **AgentMail Python SDK (v0.4.5)**: Uses `httpx` (not `requests`). Works correctly when the API key is non-empty. An empty key causes `httpx.LocalProtocolError: Illegal header value b'Bearer '` — this looks like a hang but is actually an immediate protocol error.
- **Debugging pattern**:
  1. Test API endpoint with curl first (isolate auth from SDK)
  2. If curl works but SDK doesn't, check the API key is non-empty
  3. If shell commands hang, check for stale terminal sessions

### 6. Secret Count and Infisical Latency

The `development` environment contains **179 secrets** as of 2026-03-14. `infisical run` injects all of them, which is why it's the preferred approach over individual lookups. However, `infisical run` and `infisical secrets get` latency is variable — sometimes completes in 2-3 seconds, sometimes takes 30+ seconds on first call (auth token refresh). For time-sensitive scripting, cache the key:

```bash
# Cache key in shell variable (fast for subsequent uses)
export AGENTMAIL_API_KEY=$(infisical secrets get AGENTMAIL_API_KEY --env=development --plain --silent 2>/dev/null)
```

### 7. Stale Shell Sessions

`source .venv/bin/activate` in long-running terminal sessions can become unresponsive. Use `.venv/bin/python3` directly instead:

```bash
# GOOD — direct invocation
.venv/bin/python3 my_script.py

# AVOID in long-running sessions
source .venv/bin/activate && python3 my_script.py
```

### 8. Desktop Bootstrap Path

For first-time desktop setup:
1. `sudo apt-get update && sudo apt-get install infisical` (if not installed)
2. `infisical login` (browser-based, caches token locally)
3. `cd ~/lrepos/universal_agent && infisical init` (select org and project)
4. Verify: `infisical secrets get AGENTMAIL_API_KEY --env=development --plain --silent`

No `.env` file needed for agent work — the CLI handles auth via cached browser login.

## Related Files

| File | Purpose |
|------|---------|
| `.infisical.json` | Local project link (gitignored) |
| `.infisical.example.json` | Template for `.infisical.json` |
| `.env.sample` | Bootstrap-only env template |
| `src/universal_agent/infisical_loader.py` | Runtime secret injection |
| `scripts/bootstrap_local_hq_dev.sh` | Desktop bootstrap script |
| `docs/deployment/infisical_factories.md` | Stage environment architecture |

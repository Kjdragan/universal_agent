# Archon Integration and Operations Guide

**Last Updated:** 2026-04-25

This document serves as the canonical operations guide for **Archon**, an open-source workflow engine for AI coding agents that we have integrated into our infrastructure alongside the Universal Agent (UA).

## Architectural Strategy

Archon is designed to make AI coding workflows deterministic (e.g., plan → implement → validate → review → PR). It operates via a web UI and CLI, shelling out to an underlying AI assistant, primarily **Claude Code**.

Rather than muddying the Universal Agent repository with Node/Bun dependencies and a secondary workflow engine, Archon is maintained in a strictly isolated, parallel environment. It connects to the same ecosystem (Infisical, Z.ai proxy) but executes in its own boundary. 

## 1. Local Environment Setup

### Full Local Path
The dedicated local repository is located at:
`/home/kjdragan/lrepos/archon`

This is a fork of the upstream `coleam00/Archon` repository.

### Runtime Requirements
Archon is built on **Bun**. It does not use Python natively. 
- To install its dependencies, use `bun install`.
- If any custom python automation scripts are added to the Archon repo in the future, they **must** use the `uv` package manager to align with UA standards.

### Running Locally (The Infisical Wrapper)
Archon requires access to the same secrets as UA (specifically for API tokens and environment context). However, to avoid `.env` file drift, we do not store secrets on disk for Archon.

Instead, Archon is wrapped in an Infisical launcher script located at the repository root:
`/home/kjdragan/lrepos/archon/archon-start.sh`

```bash
cd ~/lrepos/archon
./archon-start.sh
```

This script executes:
`infisical run --projectId="9970e5b7-d48a-4ed8-a8af-43e923e67572" --env=dev -- bun run dev`

This injects all Universal Agent secrets into the Archon process seamlessly.

## 2. Claude Code and Z.ai Model Mapping

Archon does not natively know about Z.ai; it natively shells out to the Anthropic CLI (`claude`).

We emulate Anthropic endpoints by leveraging the **existing global Claude Code configuration**. The `claude` CLI automatically reads from `~/.claude/settings.json`.

Because UA already heavily populates this file with the correct `env` overrides:
```json
"env": {
  "ANTHROPIC_BASE_URL": "https://api.z.ai/api/anthropic",
  "ANTHROPIC_DEFAULT_SONNET_MODEL": "glm-5-turbo"
}
```
Archon automatically inherits this behavior. When you trigger a workflow in Archon, the spawned `claude` subprocess reads `settings.json`, connects to the Z.ai endpoint, and utilizes the proxy models (e.g., `glm-5-turbo`). No custom Archon-level injection is required beyond running the standard `archon-start.sh`.

## 3. VPS Deployment Strategy

Archon is configured for deployment to the VPS mirroring the UA deployment contract, emphasizing isolation and Infrastructure as Code.

### Deployment Artifacts
All deployment artifacts are tracked within the Archon repository:
- **Deploy Workflow**: `.github/workflows/deploy.yml` triggers via Tailscale SSH on pushes to `main`.
- **Systemd Unit**: `deployment/archon.service` establishes the daemon. It uses `infisical run --env=prod` to fetch production secrets.
- **Reverse Proxy**: `deployment/nginx.conf` sets up the `archon.clearspringcg.com` routing.

### Security on the VPS
Because the Archon Web UI allows for arbitrary code execution (it is, by design, an autonomous coding agent), and because it lacks robust multi-tenant authentication, the Nginx reverse proxy configuration enforces **HTTP Basic Authentication**. This ensures unauthorized users cannot reach the dashboard and execute shell commands on the VPS.

### Future Integration with Universal Agent
This isolated setup lays the foundation for future orchestration. When UA needs to execute a deterministic coding workflow, it can invoke Archon via CLI subprocess (`archon run ...`), treating Archon the same way it treats an external VP Runtime.

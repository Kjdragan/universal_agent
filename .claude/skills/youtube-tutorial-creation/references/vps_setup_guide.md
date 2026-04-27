# VPS Infrastructure Context Guide

> **Purpose:** Share this document with agents in other projects so they can properly deploy and operate a separate application on our shared VPS.

---

## 1. Server Specifications

| Property | Value |
|---|---|
| **Provider** | Hostinger |
| **OS** | Ubuntu 24.04.4 LTS |
| **Raw Hostname** | `srv1360701` |
| **Tailscale MagicDNS** | `uaonvps` |
| **Public IP** | `187.77.16.29` |
| **Tailscale IP** | `100.106.113.93` |
| **RAM** | 16 GB |
| **Disk** | 193 GB (currently ~58% used) |
| **Service User** | `ua` (uid=1001, group `ua`, supplementary group `universal_agent`) |

## 2. Access Model

### SSH Access

The VPS is accessed exclusively over **Tailscale** (a private WireGuard mesh VPN). There is no direct public SSH.

```bash
# Primary access method (from any Tailscale-connected machine)
ssh ua@uaonvps

# Root access (when needed for system admin)
ssh root@uaonvps
```

The SSH auth mode can be either:
- **Key-based** (`UA_SSH_AUTH_MODE=keys`): Traditional `~/.ssh/id_ed25519` authentication
- **Tailscale SSH** (`UA_SSH_AUTH_MODE=tailscale_ssh`): Tailscale-managed authentication via ACL policy

### Tailscale Tags & Policy

| Node | Tag |
|---|---|
| VPS (`srv1360701`) | `tag:vps` |
| Desktop (`mint-desktop`) | `tag:operator-workstation` |
| GitHub Actions CI | `tag:ci-gha` |

The ACL policy grants SSH access from `tag:ci-gha` → `tag:vps` for users `root` and `ua`.

### SSHFS Cross-Machine File Bridge

The VPS transparently mounts the operator desktop's `/home/kjdragan` directory at the same path on the VPS via SSHFS over Tailscale. This means:
- Files at `/home/kjdragan/lrepos/...` on the desktop are readable from the VPS at the same path.
- **Do not build custom file sync tools.** Use standard OS file operations.

> [!WARNING]
> SSHFS depends on the desktop being powered on and Tailscale-connected. If the desktop is offline, the mount is unavailable. Agent-generated code on the VPS should be saved to `/home/ua/vpsrepos/` (locally writable) rather than relying on the SSHFS mount.

---

## 3. Software Stack

| Tool | Version | Notes |
|---|---|---|
| **Python** | 3.12.3 | System default |
| **uv** | 0.10.2 | Canonical Python package manager (replaces pip/venv) |
| **Node.js** | 20.20.2 | For Next.js web UI |
| **Tailscale** | 1.96.4 | Mesh VPN |
| **Nginx** | System default | Reverse proxy for public domains |
| **Certbot / Let's Encrypt** | System default | TLS certificate management |
| **Infisical CLI** | Installed | Secrets management |
| **systemd** | System default | Service management |

---

## 4. Directory Layout

```
/opt/universal_agent/            # Primary UA repo checkout (owned by ua:ua)
/opt/universal_agent_repo/       # Fallback checkout location
/home/ua/                        # Service user home
/home/ua/vpsrepos/               # Canonical output dir for agent-generated projects
/home/ua/.local/bin/             # User-installed CLI tools (goplaces, nlm, etc.)
/home/kjdragan/                  # SSHFS mount from desktop (when available)
```

### For a New Project

A new project should be deployed to its own directory under `/opt/`:

```
/opt/<your-project-name>/        # Git checkout for the new project
```

The `ua` service user should own this directory:
```bash
sudo mkdir -p /opt/<your-project-name>
sudo chown ua:ua /opt/<your-project-name>
```

---

## 5. Reverse Proxy (Nginx)

Nginx handles public HTTPS ingress. Two domains are currently configured:

| Domain | Proxy Target | Purpose |
|---|---|---|
| `app.clearspringcg.com` | `http://127.0.0.1:3000` | Next.js web UI |
| `api.clearspringcg.com` | `http://127.0.0.1:8002` | Python gateway/API |

TLS certificates are managed automatically by **Certbot (Let's Encrypt)**.

### Adding a New Site

To add a new public domain for your project:

1. **Create an Nginx site config:**
   ```bash
   sudo nano /etc/nginx/sites-available/<your-project>
   ```

   Example config:
   ```nginx
   server {
     server_name <your-domain.com>;

     location / {
       proxy_pass http://127.0.0.1:<YOUR_PORT>;
       proxy_set_header Host $host;
       proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
       proxy_set_header X-Forwarded-Proto $scheme;
     }

     # WebSocket support (if needed)
     location /ws/ {
       proxy_pass http://127.0.0.1:<YOUR_PORT>;
       proxy_http_version 1.1;
       proxy_set_header Upgrade $http_upgrade;
       proxy_set_header Connection "upgrade";
       proxy_read_timeout 3600;
     }

     listen 187.77.16.29:443 ssl;
     # Certbot will fill these in
   }
   ```

2. **Enable the site and get a TLS certificate:**
   ```bash
   sudo ln -s /etc/nginx/sites-available/<your-project> /etc/nginx/sites-enabled/
   sudo nginx -t
   sudo certbot --nginx -d <your-domain.com>
   sudo systemctl reload nginx
   ```

3. **DNS:** Point the domain's A record to `187.77.16.29`.

### Tailscale-Only Access (No Public Domain)

If you only need tailnet-private access (no public domain), use `tailscale serve` instead of Nginx:

```bash
# Expose a local port via Tailscale HTTPS (tailnet only)
sudo tailscale serve --bg https / http://127.0.0.1:<YOUR_PORT>
```

This makes your app available at `https://uaonvps.taildcc090.ts.net/` from any Tailscale-connected device.

### Currently Reserved Ports

| Port | Service | Owner |
|---|---|---|
| 80 | Nginx (HTTP redirect) | Shared |
| 443 | Nginx (HTTPS) | UA |
| 3000 | Next.js Web UI | UA |
| 3001 | (reserved) | — |
| 8001 | UA Python API | UA |
| 8002 | UA Gateway | UA |
| 8080 | (internal) | UA |
| 8091 | (internal) | UA |
| 8100 | MkDocs documentation | UA |
| 8443 | Tailscale serve → Gateway | UA |
| 9443 | Tailscale serve (reserved) | UA |

**Your new project should pick an unused port** (e.g., 5000, 5001, 8200, 9000, etc.).

---

## 6. Secrets Management (Infisical)

Secrets are centrally managed via **Infisical**. See the separate [Infisical Integration Guide](./infisical_context_guide.md) for full details.

**Key points for a new project:**

- Do NOT store secrets in `.env` files except for bare-minimum Infisical bootstrap credentials.
- Use `infisical run -- <command>` to inject secrets at runtime.
- The VPS has the Infisical CLI pre-installed and authenticated for the `ua` user.
- Your project can share the same Infisical project (project ID: `9970e5b7-d48a-4ed8-a8af-43e923e67572`) or create its own.
- The `GEMINI_API_KEY` is already provisioned in the existing Infisical project.

### Bootstrap `.env` for a New Project

Create a minimal `.env` in your project root:
```bash
# Infisical authentication (from the shared project or your own)
INFISICAL_CLIENT_ID="..."
INFISICAL_CLIENT_SECRET="..."
INFISICAL_PROJECT_ID="..."
INFISICAL_ENVIRONMENT="production"
```

---

## 7. Systemd Service Management

All long-running services on this VPS are managed via **systemd**. This is the canonical way to run persistent services.

### Currently Running Services

| Unit Name | Purpose |
|---|---|
| `universal-agent-gateway` | Python gateway (port 8002) |
| `universal-agent-api` | Python API server (port 8001) |
| `universal-agent-webui` | Next.js web UI (port 3000) |
| `universal-agent-docs` | MkDocs docs server (port 8100) |
| `universal-agent-telegram` | Telegram bot |
| `ua-discord-cc-bot` | Discord command bot |
| `ua-discord-intelligence` | Discord intelligence worker |
| `universal-agent-vp-worker@vp.coder.primary` | CODIE VP worker |
| `universal-agent-vp-worker@vp.general.primary` | ATLAS VP worker |

### Creating a Systemd Unit for a New Project

1. **Create the unit file:**
   ```bash
   sudo nano /etc/systemd/system/<your-project>.service
   ```

   Example for a Python project using `uv`:
   ```ini
   [Unit]
   Description=<Your Project Name>
   After=network.target

   [Service]
   Type=simple
   User=ua
   Group=ua
   WorkingDirectory=/opt/<your-project-name>
   EnvironmentFile=/opt/<your-project-name>/.env
   ExecStart=/opt/<your-project-name>/.venv/bin/python -m <your_module>
   Restart=on-failure
   RestartSec=5
   TasksMax=256

   [Install]
   WantedBy=multi-user.target
   ```

2. **Enable and start:**
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable <your-project>
   sudo systemctl start <your-project>
   ```

3. **Monitor:**
   ```bash
   sudo systemctl status <your-project>
   sudo journalctl -u <your-project> -f
   ```

> [!IMPORTANT]
> Set `TasksMax=256` (or higher) in your unit file if your service spawns subprocesses (e.g., AI agent SDK calls). The default of 50 can cause "can't start new thread" errors.

---

## 8. CI/CD Deployment Pattern

The existing Universal Agent project uses a **branch-driven GitHub Actions** deployment pipeline:

1. Push to `main` → GitHub Actions SSH into VPS via Tailscale → `git pull` + rebuild + restart services.
2. Deploy runs as `ua` user via SSH from `tag:ci-gha`.

### Required GitHub Secrets for CI/CD

If your new project wants to use the same CI/CD pattern:

| Secret | Purpose |
|---|---|
| `TAILSCALE_OAUTH_CLIENT_ID` | Tailscale CI auth |
| `TAILSCALE_OAUTH_SECRET` | Tailscale CI auth |
| `VPS_SSH_HOST` | VPS hostname (e.g., `uaonvps`) |
| `VPS_SSH_USER` | SSH user (e.g., `ua`) |
| `VPS_SSH_KEY` | SSH private key for the `ua` user |
| `INFISICAL_CLIENT_ID` | Infisical machine identity |
| `INFISICAL_CLIENT_SECRET` | Infisical machine identity |
| `INFISICAL_PROJECT_ID` | Infisical project |

### Minimal Deploy Workflow

```yaml
name: Deploy
on:
  push:
    branches: [main]

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - name: Connect to Tailscale
        uses: tailscale/github-action@v3
        with:
          oauth-client-id: ${{ secrets.TAILSCALE_OAUTH_CLIENT_ID }}
          oauth-secret: ${{ secrets.TAILSCALE_OAUTH_SECRET }}
          tags: tag:ci-gha

      - name: Deploy via SSH
        run: |
          ssh -o StrictHostKeyChecking=no ${{ secrets.VPS_SSH_USER }}@${{ secrets.VPS_SSH_HOST }} << 'EOF'
            cd /opt/<your-project-name>
            git pull origin main
            uv sync
            sudo systemctl restart <your-project>
          EOF
```

---

## 9. New Project Setup Checklist

Here is the complete checklist for deploying a new project on this VPS:

- [ ] **Create project directory:** `sudo mkdir -p /opt/<project> && sudo chown ua:ua /opt/<project>`
- [ ] **Clone repository:** `cd /opt/<project> && git clone <repo-url> .`
- [ ] **Install dependencies:** `uv sync` (Python) or `npm install` (Node.js)
- [ ] **Configure secrets:** Create a minimal `.env` with Infisical bootstrap credentials, then use `infisical run` for runtime secrets
- [ ] **Pick an unused port** (see Reserved Ports table above)
- [ ] **Create systemd unit:** `/etc/systemd/system/<project>.service` with `User=ua`, `TasksMax=256`
- [ ] **Enable and start:** `sudo systemctl daemon-reload && sudo systemctl enable --now <project>`
- [ ] **Configure public access (if needed):** Create Nginx site config + Certbot TLS certificate
- [ ] **Or configure Tailscale-only access:** `sudo tailscale serve --bg https:<port> / http://127.0.0.1:<local-port>`
- [ ] **Set up CI/CD (optional):** Add GitHub Actions workflow with Tailscale + SSH deploy steps
- [ ] **Verify health:** `curl http://127.0.0.1:<port>/health` from the VPS

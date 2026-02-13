# 32. VPS FileBrowser Setup and Access (2026-02-13)

## Purpose
Web-based file browser for viewing, downloading, and inspecting files on the production VPS — especially agent workspace outputs, logs, and artifacts — without SSH.

## What is FileBrowser
[FileBrowser](https://github.com/filebrowser/filebrowser) is a lightweight single-binary Go application that provides a web UI for browsing, viewing, and downloading files. It has built-in authentication and runs as a systemd service.

## Access

### Option A: Public URL (behind FileBrowser auth)
Open in any browser:
```
https://app.clearspringcg.com/files/
```
Log in with credentials stored in VPS `.env` (`FILEBROWSER_ADMIN_PASSWORD` or `FILEBROWSER_VIEWER_PASSWORD`).

### Option B: SSH tunnel (no public exposure)
```bash
ssh -i ~/.ssh/id_ed25519 -L 8080:127.0.0.1:8080 root@187.77.16.29
```
Then open: `http://localhost:8080/files/`

This bypasses the public internet entirely. FileBrowser still requires login.

## Accounts

| Username | Role | Permissions |
|---|---|---|
| `admin` | Full access | Browse, create, rename, modify, delete, download, share |
| `viewer` | Read-only | Browse and download only |

Passwords are stored in `/opt/universal_agent/.env` on the VPS:
- `FILEBROWSER_ADMIN_PASSWORD`
- `FILEBROWSER_VIEWER_PASSWORD`

**Recommendation:** Use the `viewer` account for routine browsing. Use `admin` only when you need to edit or upload files.

## What you can see

FileBrowser is rooted at `/opt/universal_agent`, so you can browse:

| Path | Contents |
|---|---|
| `AGENT_RUN_WORKSPACES/` | Agent session outputs (transcripts, work products, media) |
| `artifacts/` | Durable artifacts across sessions |
| `src/` | Application source code |
| `web-ui/` | Next.js frontend source |
| `.claude/agents/` | Subagent definitions |
| `scripts/` | Deployment and ops scripts |
| `OFFICIAL_PROJECT_DOCUMENTATION/` | Project docs |
| `config/` | Configuration files |

## Architecture

```
Browser
  |
  v
nginx (app.clearspringcg.com:443, TLS)
  |  location /files/ ->
  v
FileBrowser (127.0.0.1:8080)
  |
  v
/opt/universal_agent (filesystem root)
```

## VPS Service Details

- **Binary:** `/usr/local/bin/filebrowser` (v2.57.1)
- **Config:** `/etc/filebrowser/filebrowser.json`
- **Database:** `/var/lib/filebrowser/filebrowser.db`
- **Systemd unit:** `filebrowser.service`
- **Listening:** `127.0.0.1:8080` (not publicly exposed directly)
- **Nginx proxy:** `app.clearspringcg.com/files/` -> `127.0.0.1:8080`

## Management Commands

### Check status
```bash
ssh -i ~/.ssh/id_ed25519 root@187.77.16.29 'systemctl status filebrowser --no-pager'
```

### Restart
```bash
ssh -i ~/.ssh/id_ed25519 root@187.77.16.29 'systemctl restart filebrowser'
```

### Reset a user password
Must stop the service first (SQLite DB lock):
```bash
ssh -i ~/.ssh/id_ed25519 root@187.77.16.29 '
  systemctl stop filebrowser
  NEW_PASS=$(openssl rand -base64 18)
  filebrowser users update admin --password "$NEW_PASS" -c /etc/filebrowser/filebrowser.json
  systemctl start filebrowser
  echo "New admin password: $NEW_PASS"
'
```
Then update `FILEBROWSER_ADMIN_PASSWORD` in `/opt/universal_agent/.env`.

### Add a new user
```bash
ssh -i ~/.ssh/id_ed25519 root@187.77.16.29 '
  systemctl stop filebrowser
  filebrowser users add USERNAME "PASSWORD" \
    -c /etc/filebrowser/filebrowser.json \
    --perm.admin=false \
    --perm.download=true \
    --perm.create=false \
    --perm.rename=false \
    --perm.modify=false \
    --perm.delete=false
  systemctl start filebrowser
'
```

## Security Notes
1. FileBrowser has its own username/password auth (independent of dashboard auth).
2. The nginx proxy adds TLS encryption for public access.
3. The `viewer` account cannot modify, delete, or create files — browse and download only.
4. FileBrowser is bound to `127.0.0.1:8080` — not directly reachable from the internet even if UFW were disabled.
5. To disable public access entirely, remove the `/files/` location block from nginx and use SSH tunnel only.

## Nginx Config Reference
Location block in `/etc/nginx/sites-enabled/universal-agent-app`:
```nginx
  # FileBrowser web file manager
  location /files/ {
    proxy_pass http://127.0.0.1:8080;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Real-IP $remote_addr;
    client_max_body_size 100M;
  }
```

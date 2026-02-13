# 29. YouTube Hook Mirroring (VPS -> Local) via Reverse SSH Tunnel Runbook (2026-02-13)

## Goal
When Composio triggers a YouTube playlist event into the production VPS, we want:
- Normal VPS processing to occur (create the UA session and run the YouTube tutorial workflow).
- Best-effort mirroring of that same event into the local dev stack, automatically, so local development stays “live” without reconfiguring Composio.

This is designed to be safe and non-disruptive when the laptop is offline.

---

## High-level flow
1. Composio sends a YouTube webhook to the VPS gateway hook endpoint.
2. VPS gateway validates/authenticates the webhook and transforms it into a UA “action message”.
3. VPS gateway dispatches the action (normal behavior).
4. If mirroring is enabled, VPS gateway also forwards a normalized payload to the local gateway’s manual YouTube hook endpoint, through a reverse SSH tunnel.

Key concept: **reverse** tunnel.
- The local machine opens an SSH connection to the VPS.
- That SSH connection creates a loopback-only port on the VPS (`127.0.0.1:18002` by default).
- Requests to that VPS loopback port are forwarded back to the local gateway (`127.0.0.1:8002` by default).

---

## Why reverse tunnel (security model)
We do not expose the laptop publicly.
- The VPS forwarding target is `http://127.0.0.1:18002/...` (loopback on the VPS).
- Nginx/public internet cannot reach `127.0.0.1` on the VPS.
- Traffic from VPS loopback to the laptop rides inside the SSH connection initiated by the laptop.

There is also **application-layer auth** on the forwarded request:
- VPS sends `Authorization: Bearer <token>` to the local manual hook endpoint.
- Local gateway requires the same token (`UA_HOOKS_TOKEN`) for manual hooks.

---

## What is mirrored (payload contract)
VPS forwards a JSON payload to the local gateway:
```json
{
  "video_url": "https://www.youtube.com/watch?v=...",
  "video_id": "...",
  "mode": "explainer_plus_code",
  "allow_degraded_transcript_only": true
}
```

This hits local:
- `POST /api/v1/hooks/youtube/manual`

---

## Configuration

### Local
1. Ensure the local gateway stack runs on port `8002` (default).
2. Ensure local `.env` has a token:
   - `UA_HOOKS_TOKEN=...`

Start local stack:
- `./start_gateway.sh`
or:
- `./start_local_dev_with_youtube_forwarding.sh` (starts tunnel + stack)

### VPS
Mirroring is disabled unless these are set in `/opt/universal_agent/.env`:
- `UA_HOOKS_FORWARD_YOUTUBE_MANUAL_URL=http://127.0.0.1:18002/api/v1/hooks/youtube/manual`
- `UA_HOOKS_FORWARD_YOUTUBE_TOKEN=<must match local UA_HOOKS_TOKEN>`

Then restart:
- `systemctl restart universal-agent-gateway`

---

## Reverse tunnel: one-shot and “always on”

### One-shot (foreground)
Run locally:
```bash
./scripts/forward_youtube_hooks_to_local.sh
```

### Recommended: auto-reconnect (systemd user unit)
Install and enable locally:
```bash
./scripts/install_ua_youtube_forward_tunnel_user_service.sh
```

This creates:
- `~/.config/systemd/user/ua-youtube-forward-tunnel.service`

And enables:
- `systemctl --user enable --now ua-youtube-forward-tunnel.service`

Note:
- systemd user services start when you log in.
- If you need it to start at boot even without login, use:
  - `sudo loginctl enable-linger <username>`

---

## Failure behavior (when laptop is offline)
Mirroring is best-effort and must not degrade the VPS hook path.
- If forwarding fails repeatedly, the gateway temporarily disables forwarding for 5 minutes to avoid log spam.
- Normal VPS processing continues either way.

---

## Verification checklist

### 1) Local stack up
```bash
./start_gateway.sh --no-browser
```

### 2) Tunnel up
```bash
systemctl --user status ua-youtube-forward-tunnel.service --no-pager
```
or (one-shot):
```bash
./scripts/forward_youtube_hooks_to_local.sh
```

### 3) VPS sees Composio hook ingress
On VPS:
```bash
journalctl -u universal-agent-gateway --since '10 minutes ago' --no-pager | grep -E 'Hook ingress accepted|composio-youtube-trigger'
```

### 4) VPS forwarding succeeded
On VPS:
```bash
journalctl -u universal-agent-gateway --since '10 minutes ago' --no-pager | grep -E 'Hook forward ok|Hook forward failed|Hook forward error'
```

### 5) Local manual hook ingress happened
On local:
- Watch the gateway logs for a manual YouTube hook session being created.

---

## Troubleshooting
Common failure modes:
- Tunnel is down:
  - Fix: `systemctl --user restart ua-youtube-forward-tunnel.service`
- Port conflict on VPS (`127.0.0.1:18002` already in use):
  - Fix: set `REMOTE_PORT` in `scripts/forward_youtube_hooks_to_local.sh` environment, update VPS URL accordingly.
- Token mismatch (VPS -> local returns 401/403):
  - Fix: ensure `UA_HOOKS_FORWARD_YOUTUBE_TOKEN` (VPS) matches `UA_HOOKS_TOKEN` (local).
- Local gateway not running:
  - Fix: start local stack (`./start_gateway.sh`) and retry.


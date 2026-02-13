# 30. Local Dev Startup With YouTube Forwarding Tunnel (2026-02-13)

## Goal
Start a production-like local dev stack (gateway + API + Web UI) while also keeping the reverse SSH tunnel online so the VPS can mirror YouTube Composio playlist events into the local stack.

---

## One-command local dev
Run:
```bash
./start_local_dev_with_youtube_forwarding.sh
```

This will:
1. Start the reverse tunnel (via systemd user unit if installed, otherwise fallback to a background `ssh` process).
2. Start the local stack using `./start_gateway.sh`.

Pass through args to `./start_gateway.sh` using `--`:
```bash
./start_local_dev_with_youtube_forwarding.sh -- --no-browser
```

Start tunnel only:
```bash
./start_local_dev_with_youtube_forwarding.sh --tunnel-only
```

Start stack only (no tunnel):
```bash
./start_local_dev_with_youtube_forwarding.sh --stack-only
```

---

## Recommended: install the auto-reconnect tunnel unit
Install + enable the systemd user service:
```bash
./scripts/install_ua_youtube_forward_tunnel_user_service.sh
```

Status:
```bash
systemctl --user status ua-youtube-forward-tunnel.service --no-pager
```

Logs:
```bash
journalctl --user -u ua-youtube-forward-tunnel.service -n 200 --no-pager
```

---

## Notes
- If your machine is off, VPS forwarding will fail (expected) and will self-throttle to avoid spamming logs.
- For the full architecture, configuration, and troubleshooting, see:
  - `OFFICIAL_PROJECT_DOCUMENTATION/03_Operations/29_YouTube_Hook_Mirroring_VPS_To_Local_Reverse_Tunnel_Runbook_2026-02-13.md`


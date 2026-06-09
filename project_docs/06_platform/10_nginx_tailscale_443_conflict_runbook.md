---
title: "Runbook: Nginx ⇄ Tailscale Serve Port-443 Conflict"
status: active
canonical: false
subsystem: plat-networking
code_paths:
  - "deploy/nginx/universal-agent-app"
  - "scripts/deploy/install_nginx_app_config.sh"
  - "scripts/publish_scratch.sh"
last_verified: 2026-06-09
---

# Runbook: Nginx ⇄ Tailscale Serve Port-443 Conflict

> **Stub.** The full runbook lives in the `clearspringcg-landing` repo:
> `docs/operations/nginx-tailscale-443-conflict.md`. This stub exists so the
> conflict is discoverable from the platform networking docs, since the
> offending nginx config and the scratchpad are owned here.

## The rule

On `uaonvps`, **nginx** (public site on `187.77.16.29:443`) and **Tailscale
Serve** (the HTML scratchpad on the tailnet IP `100.106.113.93:443`) both use
port 443 on different interfaces. They coexist **only** because every nginx
`:443` server block is pinned to the public IP. A **wildcard `listen 443`**
binds `0.0.0.0:443`, overlaps the tailnet IP, and nginx fails to start:

```
nginx: [emerg] bind() to 0.0.0.0:443 failed (98: Address already in use)
```

→ the **entire public site** (`clearspringcg.com`, `app.`, `api.`) goes dark.
This is an availability bug, **not** a breach. See
[`06_networking_tailscale_proxy_sshfs.md`](./06_networking_tailscale_proxy_sshfs.md)
for the Tailscale Serve / scratchpad architecture.

## ⚠️ Source-template landmine (fix owed)

`deploy/nginx/universal-agent-app` carries `listen 443 ssl;` (wildcard). The
live VPS file was hand-fixed to `listen 187.77.16.29:443 ssl;` on 2026-06-09,
but **this template was not** — the next
`scripts/deploy/install_nginx_app_config.sh` run will overwrite the live file
and **re-break prod**. Change the template's `:443` block to
`listen 187.77.16.29:443 ssl;` and keep it that way. Also re-check after any
`certbot --nginx` run, which can rewrite `listen` back to a wildcard.

## Fast triage

```bash
ssh uaonvps 'systemctl is-active nginx; sudo ss -tlnp | grep ":443 "'   # nginx failed + tailscaled on tailnet:443
ssh uaonvps 'sudo nginx -T 2>/dev/null | grep -E "listen|server_name"'  # find the wildcard 443 block
```

Fix = pin the block to `187.77.16.29:443`, `sudo systemctl restart nginx`
(a *reload* reuses the stale wildcard socket — restart rebinds cleanly), then
restore Tailscale Serve `:443` if it was turned off. Full steps in the
canonical runbook above.

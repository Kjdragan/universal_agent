# CI/CD Pipeline & Troubleshooting

Our CI/CD pipeline is built on GitHub Actions and automates deployment to staging and production over Tailscale.

## Workflows

| Name | Trigger | Target |
|------|---------|--------|
| `Deploy Staging` | Push to `develop` | Staging Service |
| `Deploy Production` | Push to `main` | Production Service |

## Required GitHub Secrets

- `TAILSCALE_AUTHKEY` (reusable + ephemeral + preauthorized, tag identity `tag:ci-gha`)
- `VPS_SSH_HOST`
- `VPS_SSH_USER`
- `VPS_SSH_KEY`
- `INFISICAL_CLIENT_ID` (staging workflow)
- `INFISICAL_CLIENT_SECRET` (staging workflow)
- `INFISICAL_PROJECT_ID` (staging workflow)

## Required Tailscale Policy Model

CI runs must authenticate as a dedicated tagged principal and use non-interactive SSH authorization.

### Tags

- CI runner tag: `tag:ci-gha`
- VPS tag: `tag:vps`

### SSH Policy (required)

```json
{
  "ssh": [
    { "action": "accept", "src": ["tag:ci-gha"], "dst": ["tag:vps"], "users": ["root", "ua"] }
  ]
}
```

### Network Policy (required)

Allow `tag:ci-gha` to reach `tag:vps` on TCP/22 in your current ACL/grants model.

## Pipeline Steps

1. **Connect to Tailscale** using OAuth credentials and `tag:ci-gha`.
2. **Ping preflight** validates runner-to-VPS tailnet connectivity.
3. **SSH preflight** runs `timeout 60s ssh ... "echo SSH_OK"` in batch mode.
4. **Deploy over SSH** runs remote commands with a hard timeout (`timeout 15m`).
5. **Staging only** provisions Infisical environment and bootstraps `.env`.
6. **Dependency sync** runs `uv sync`.
7. **Service restart** restarts target systemd units.

## Troubleshooting

### SSH Preflight Fails Fast

If preflight exits before deploy, inspect error output in the workflow log.

#### Signature: interactive Tailscale check

If stderr includes either:

- `Tailscale SSH requires an additional check`
- `https://login.tailscale.com/...`

then CI identity is not matching the required non-interactive SSH policy. Verify:

- GitHub Action uses OAuth credentials and `tags: tag:ci-gha`.
- Tailscale node(s) are tagged correctly (`tag:ci-gha` for runner identity, `tag:vps` on destination).
- SSH rule is `action: "accept"` from `tag:ci-gha` to `tag:vps` for `root`/`ua`.
- Network policy allows TCP/22 from `tag:ci-gha` to `tag:vps`.

### SSH Key or VPS Authentication Fails

- Verify the `VPS_SSH_KEY` secret in GitHub.
- Ensure the matching public key is present in target user `authorized_keys`.
- Check SSH auth logs on the VPS for rejected keys.

### Tailscale Connection Issues

- Ensure `TAILSCALE_AUTHKEY` is valid and not expired/revoked.
- Ensure the auth key was created with tag identity `tag:ci-gha` (reusable + ephemeral + preauthorized).
- Check the [Tailscale Admin Console](https://login.tailscale.com/admin/machines) to see if the GitHub Runner is joining properly.
- Verify ACL/grants permit runner-to-VPS traffic on SSH.

### Service Startup Errors

- Tailing logs on the VPS:
  ```bash
  sudo journalctl -u universal-agent-staging-gateway -f
  ```
- Verify the `.env` file exists in the installation directory.

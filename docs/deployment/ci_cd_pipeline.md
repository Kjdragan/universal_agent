# CI/CD Pipeline & Troubleshooting

Our CI/CD pipeline is built on GitHub Actions and automates the deployment of code and secrets to the VPS.

## Workflows

| Name | Trigger | Target |
|------|---------|--------|
| `Deploy Staging` | Push to `develop` | Staging Service |
| `Deploy Prod` | Merge to `main` | Production Service |

## Pipeline Steps

1.  **Connect to Tailscale**: Joins the private overlay network to reach the VPS.
2.  **SSH Connection**: Connects to the VPS using direct IP (`100.106.113.93`) to avoid DNS resolution hangs.
3.  **Git Sync**: Fetches the branch and performs a `git reset --hard` to synchronize the working directory.
4.  **Infisical Provision**: Runs the provisioning script to sync secrets to the specific environment.
5.  **Bootstrap .env**: Creates a local `.env` file containing the Infisical credentials and environment identifiers.
6.  **UV Sync**: Installs the exact versions of dependencies from the lockfile.
7.  **Service Restart**: Restarts the systemd services (`universal-agent-staging-gateway`, etc.).

## Troubleshooting

### SSH Key or Authentication Fails
- Verify the `VPS_SSH_KEY` secret in GitHub.
- Ensure the public key is present in `/root/.ssh/authorized_keys` on the VPS.
- Check the VPS `auth.log` for rejected keys.

### Tailscale Connectivity Issues
- Ensure the `TAILSCALE_AUTHKEY` is valid and not expired.
- Check the [Tailscale Admin Console](https://login.tailscale.com/admin/machines) to see if the GitHub Runner is joining properly.
- Verify ACLs are not blocking traffic between the Runner and the VPS.

### Service Startup Errors
- Tailing logs on the VPS:
  ```bash
  sudo journalctl -u universal-agent-staging-gateway -f
  ```
- Verify the `.env` file exists in the installation directory.


## Tailscale SSH Note
If deployment hangs at the SSH step, ensure that 'Check-in' is disabled in the Tailscale ACLs for the runner's identity/tag.

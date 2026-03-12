# CI/CD Pipeline & Troubleshooting

Our CI/CD pipeline is built on GitHub Actions and automates PR review, staging validation, and production deployment over Tailscale.

## Canonical Rule

This is the only supported app deployment path in this repository.

- Open a pull request to `develop` to run Codex review on the proposed change.
- Merge to `develop` to deploy to staging automatically.
- Promote the exact validated `develop` SHA to `main` via the promotion workflow to deploy to production automatically.
- Do not treat `scripts/deploy_vps.sh`, `scripts/vpsctl.sh`, or manual SSH deploy steps as the primary deployment path.

## Workflows

| Name | Trigger | Target |
|------|---------|--------|
| `Codex Review Develop PR` | Pull request to `develop` | Automated PR review |
| `Deploy Staging` | Push to `develop` | Staging Service |
| `Promote Validated Develop To Main` | Manual workflow dispatch | Fast-forward `main` to validated `develop` SHA |
| `Deploy Production` | Push to `main` | Production Service |

## Current Targets

| Area | Staging | Production |
|------|---------|------------|
| Git branch | `develop` | `main` |
| VPS checkout | `/opt/universal-agent-staging` | `/opt/universal_agent` |
| Legacy/fallback checkout | n/a | `/opt/universal_agent_repo` if `/opt/universal_agent` is occupied by a non-git legacy directory |
| Runtime secrets | `staging-hq` when provisioning succeeds, otherwise temporary fallback to `dev` | production-managed secrets only; no auto-clone from `dev` |

## Required GitHub Secrets

- `OPENAI_API_KEY` (Codex PR review workflow)
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

1. **Open PR to `develop`** from a `feature/...` branch.
2. **Codex review** runs on that PR and comments directly on the diff.
3. **Merge to `develop`** only after review and normal checks are acceptable.
4. **Staging deploy** runs automatically on the merge result in `develop`.
5. **Validate staging** against the exact merged `develop` SHA.
6. **Promote validated SHA** using the `Promote Validated Develop To Main` workflow.
7. **Production deploy** runs automatically when the promotion workflow fast-forwards `main`.

## Review and Promotion Rule

- There is exactly one Codex review gate: the PR into `develop`.
- There is no second Codex review on `main`.
- Production promotion must use the exact validated `develop` SHA.
- The promotion workflow refuses to run if `develop` has moved since the validated SHA.
- To make the review gate enforceable, configure GitHub branch protection on `develop` to require the `Codex Review Develop PR` check before merge.

## Temporary Missing-Secret Behavior

If `OPENAI_API_KEY` is not configured yet:

- the `Codex Review Develop PR` workflow posts a warning comment and exits successfully
- the PR can still merge to `develop`
- staging and production promotion can still proceed

Once `OPENAI_API_KEY` is configured, the same workflow becomes the real blocking Codex review gate again.

## Recommended GitHub Branch Protection

Configure these settings in GitHub repository settings.

### `develop`

- Require a pull request before merging
- Require status checks to pass before merging
- Required status check: `Codex Review Develop PR / codex-review`
- Require branches to be up to date before merging
- Restrict direct pushes if you want review to be mandatory in practice
- If `OPENAI_API_KEY` is still missing, this required check will pass in "review skipped" mode rather than enforcing a real Codex review

### `main`

- Optional: require a pull request before merging
- Do not require the Codex review check on `main`
- Restrict direct pushes except for trusted release operators if you want production promotion to happen only via the promotion workflow or explicit release action

## Operational Meaning

- While you code, `develop` is the automated VPS-backed dev/staging lane.
- `main` is a separately deployable production lane and is currently deployable.
- Production and staging both have passing workflow runs as of March 11, 2026.

## Troubleshooting

### Promotion Workflow Refuses To Run

If the promotion workflow fails before pushing `main`, inspect the validation step.

#### Signature: develop moved

If the workflow reports that `origin/develop` has moved, the staging-validated SHA is no longer the current `develop` head.

Required action:

1. decide whether the newer `develop` head should be staged and validated
2. if yes, revalidate the newer SHA in staging
3. run promotion again with the new validated SHA

#### Signature: main cannot fast-forward

If the workflow reports that `main` cannot fast-forward cleanly to the requested SHA, the branch history has diverged and requires manual investigation before release.

### SSH Preflight Fails Fast

If preflight exits before deploy, inspect error output in the workflow log.

#### Signature: interactive Tailscale check

If stderr includes either:

- `Tailscale SSH requires an additional check`
- `https://login.tailscale.com/...`

then CI identity is not matching the required non-interactive SSH policy. Verify:

- GitHub Action uses `TAILSCALE_AUTHKEY` with `tags: tag:ci-gha`.
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

### Production `uv sync` Fails With Python Interpreter Permission Errors

If production deploy logs show either:

- `failed to canonicalize path /opt/universal_agent/.venv/bin/python3: Permission denied`
- `Failed to execute /opt/universal_agent/.venv/bin/python3: Permission denied`

then the existing `.venv` was created against a Python interpreter path that the `ua` service user cannot traverse.

Current production workflow behavior:

1. chowns the repo to `ua`
2. checks whether `ua` can resolve `.venv/bin/python3`
3. removes `.venv` only if that check fails
4. rebuilds dependencies as `ua` with `uv`

This is intended to self-heal stale virtualenvs created against inaccessible Python cache paths.

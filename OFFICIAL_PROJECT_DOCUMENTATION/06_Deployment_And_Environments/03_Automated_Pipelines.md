# CI/CD Automated Pipelines

## Pipeline Overview

The deployment lifecycle is managed via automated runners (e.g., GitHub Actions or equivalent). These pipelines listen to branch events and execute deployment operations over SSH to the VPS exactly as an engineer would, but consistently and without error.

There are two primary pipelines:

1.  **Deploy Staging** (Triggered on pushes/merges to `develop`)
2.  **Deploy Production** (Triggered on pushes/merges to `main`)

## Step-by-Step Execution

A deployment pipeline runs the following steps automatically:

### 1. VPS SSH Connection
The runner connects securely to the VPS using an SSH deploy key stored securely in the runner's secrets (e.g., GitHub Secrets).

### 2. Code Fetching
The pipeline commands the VPS to `git fetch` and `git checkout` the target branch into the specific directory for that service (e.g., `~/universal_agent_staging` or `~/universal_agent_prod`).

### 3. Factory Provisioning (Staging Only)
The pipeline automatically runs `infisical_provision_factory_env.py` to ensure the `staging-hq` Infisical environment has the latest variables cloned from `dev`. 

### 4. Dependency Management (`uv`)
The runner commands the VPS to execute `uv sync`. `uv` deterministically builds the precise Python environment defined in `uv.lock`. This step is heavily cached and takes only a few seconds.

### 5. Service Restart
Finally, the runner commands `systemctl --user restart <service-name>`.

*   If deploying `develop`: `systemctl --user restart universal-agent-staging`
*   If deploying `main`: `systemctl --user restart universal-agent-prod`

## Rollbacks

Because deployments are fully automated and tied to Git state, rolling back is trivial. If an issue is found in Staging, simply revert the problematic commit locally and push back to `develop`. The pipeline will automatically rebuild the exact previous state of the application.

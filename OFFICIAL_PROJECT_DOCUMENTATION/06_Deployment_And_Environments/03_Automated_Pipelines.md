# CI/CD Automated Pipelines

## Pipeline Overview

The deployment lifecycle is managed via automated runners (e.g., GitHub Actions or equivalent). These pipelines listen to branch events and execute deployment operations over SSH to the VPS exactly as an engineer would, but consistently and without error.

There are two primary pipelines:

1. **Deploy Staging** (Triggered on pushes/merges to `develop`)
2. **Deploy Production** (Triggered on pushes/merges to `main`)

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

* If deploying `develop`: `systemctl --user restart universal-agent-staging`
* If deploying `main`: `systemctl --user restart universal-agent-prod`

## Rollbacks

Because deployments are fully automated and tied to Git state, rolling back is trivial. If an issue is found in Staging, simply revert the problematic commit locally and push back to `develop`. The pipeline will automatically rebuild the exact previous state of the application.

## How to Trigger a Deployment (Pull Request Workflow)

To trigger the `Deploy Staging` pipeline, you simply need to merge your code changes from a feature branch (e.g., `dev-parallel`) into the `develop` branch. You can do this using the GitHub website or the GitHub CLI.

### Option 1: Using the GitHub Website

1. **Open your Web Browser** and go to your repository on GitHub: `https://github.com/Kjdragan/universal_agent`
2. **Create a Pull Request:** At the top of the code page, GitHub often shows a banner for recently pushed branches with a green **"Compare & pull request"** button. Click it.
   * *(Alternatively, click the "Pull requests" tab, then click the green "New pull request" button. Set the "base" dropdown to `develop`, and the "compare" dropdown to your feature branch.)*
3. **Submit the PR:** On the next screen, enter a title and description, then click the green **"Create pull request"** button. This tells GitHub you are ready to merge the code.
4. **Merge It:** At the bottom of the Pull Request page, click the green **"Merge pull request"** button, and then click **"Confirm merge"**. This officially triggers the deployment pipeline.
5. **Watch the Pipeline:** Click the **"Actions"** tab at the top of the repository. You will see a workflow running called "Deploy Staging" with a spinning yellow circle. Click on it to watch the live logs as it securely connects to the VPS and updates the Staging server.

### Option 2: Using the GitHub CLI (Terminal)

If you prefer staying in your terminal, you can perform the exact same steps using the `gh` command-line tool.

1. **Create the Pull Request:**
   Type this into your terminal and hit Enter (replace `dev-parallel` with your current branch name if different):

   ```bash
   gh pr create --base develop --head dev-parallel --title "Deploy Staging: Feature Name" --body "Merging changes to staging."
   ```

2. **Merge the Pull Request:**
   Once created, you can merge it directly from the terminal. This command merges the PR and can automatically delete the feature branch if you are done with it.

   ```bash
   gh pr merge --merge
   ```

   *(Add `--delete-branch` if you want to delete the branch after merging.)*

3. **Watch the Live Logs:**
   To watch the automated deployment pipeline run without leaving the terminal, use:

   ```bash
   gh run watch
   ```

   Select the "Deploy Staging" workflow from the interactive list. It will stream the live deployment logs directly to your screen!

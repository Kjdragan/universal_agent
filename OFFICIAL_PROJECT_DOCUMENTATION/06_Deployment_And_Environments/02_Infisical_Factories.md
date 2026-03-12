# Infisical Factories

Because Universal Agent operates via a fleet of distinct "Agents" or "Capabilities" (known as **Factories**), every deployment node requires careful secret curation.

## The Factory Secret Model

Instead of an engineer manually logging into the Infisical Dashboard to create `staging-hq` and pasting fifty different API keys by hand, we use **Automated Factory Provisioning**.

We have three primary Factory Roles (defined in `scripts/infisical_provision_factory_env.py`):
1.  **HEADQUARTERS:** The main dispatcher. Needs cron jobs, VP Coder dispatching, signaling engines, etc.
2.  **LOCAL_WORKER:** A satellite node (like a desktop or tablet) that processes tasks but disables redundant ingress like CSI monitoring to avoid duplicating work.
3.  **STANDALONE_NODE:** A deeply isolated node that doesn't talk to the primary Redis delegation bus.

## Local Desktop Split

Kevin's desktop now has two intentional local environments:

1. `kevins-desktop`
   - role: `LOCAL_WORKER`
   - deployment profile: `local_workstation`
   - checkout: `~/universal_agent_factory`
2. `kevins-desktop-hq-dev`
   - role: `HEADQUARTERS`
   - deployment profile: `local_workstation`
   - checkout: `/home/kjdragan/lrepos/universal_agent`

Do not point the main repo checkout at `kevins-desktop` anymore. That makes localhost behave like a worker and blocks HQ-only dashboard routes by design.

Current constraint as of March 12, 2026:

- the Infisical project has reached its environment limit
- preferred dedicated HQ dev env: `kevins-desktop-hq-dev`
- temporary operational fallback: bootstrap the repo checkout against `dev` while keeping `UA_DEPLOYMENT_PROFILE=local_workstation`

### How Provisioning Works

When a new environment is needed (for example, when the CI/CD pipeline deploys Staging for the first time), the pipeline runs the provisioning script:

```bash
python scripts/infisical_provision_factory_env.py \
  --machine-name "Staging VPS HQ" \
  --machine-slug staging-hq \
  --factory-role HEADQUARTERS \
  --source-env dev 
```

Local HQ development uses the same script with an explicit deployment profile override:

```bash
python scripts/infisical_provision_factory_env.py \
  --machine-name "Kevin's Desktop HQ Dev" \
  --machine-slug kevins-desktop-hq-dev \
  --factory-role HEADQUARTERS \
  --deployment-profile local_workstation \
  --source-env dev
```

**Under the Hood:**
1. The script authenticates with Infisical.
2. It lists all secrets in the **Source Environment** (e.g., `dev`).
3. It creates the **Target Environment** (e.g., `staging-hq`) if it doesn't exist.
4. It clones all secrets from `dev` directly into `staging-hq`.
5. *Crucially*, it applies the **Factory Overrides**. For `HEADQUARTERS`, it enforces variables like `UA_ENABLE_CRON=1` and `FACTORY_ROLE=HEADQUARTERS`. It overwrites the target secrets with these configurations.

## Benefits for Development

Because of this automated cloning, developers never have to worry about creating new secrets across three environments. 
If you add a new API Key (e.g., `STRIPE_API_KEY`) to the `dev` environment locally, the next time the Staging Pipeline runs, it automatically provisions that key into `staging-hq`.

Production environments, however, are typically kept isolated from this rapid cloning (e.g. `prod-hq`), so developers must intentionally add highly sensitive live production keys to the `prod-hq` environment manually, preserving a strict security boundary.

## Local HQ Bootstrap

After the `kevins-desktop-hq-dev` environment exists, bootstrap the repo checkout into HQ dev mode with:

```bash
bash scripts/bootstrap_local_hq_dev.sh
```

Temporary fallback if the dedicated HQ dev environment cannot be created yet:

```bash
TARGET_ENV=dev bash scripts/bootstrap_local_hq_dev.sh
```

That script:

1. writes the repo-root `.env` for `kevins-desktop-hq-dev`
2. renders `web-ui/.env.local`
3. verifies the runtime resolves to `FACTORY_ROLE=HEADQUARTERS`
4. warns if the separate local worker service is still running

## Local Worker Service Control

The Corporation page now exposes two different controls for the same-machine desktop worker:

1. `Pause Intake` / `Resume Intake`
   - keeps the bridge alive
   - only changes mission consumption
2. `Stop Local Factory` / `Start Local Factory`
   - controls `universal-agent-local-factory.service`
   - intended for preserving desktop/API budget during HQ development

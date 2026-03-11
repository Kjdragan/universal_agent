# Infisical Factories

Because Universal Agent operates via a fleet of distinct "Agents" or "Capabilities" (known as **Factories**), every deployment node requires careful secret curation.

## The Factory Secret Model

Instead of an engineer manually logging into the Infisical Dashboard to create `staging-hq` and pasting fifty different API keys by hand, we use **Automated Factory Provisioning**.

We have three primary Factory Roles (defined in `scripts/infisical_provision_factory_env.py`):
1.  **HEADQUARTERS:** The main dispatcher. Needs cron jobs, VP Coder dispatching, signaling engines, etc.
2.  **LOCAL_WORKER:** A satellite node (like a desktop or tablet) that processes tasks but disables redundant ingress like CSI monitoring to avoid duplicating work.
3.  **STANDALONE_NODE:** A deeply isolated node that doesn't talk to the primary Redis delegation bus.

### How Provisioning Works

When a new environment is needed (for example, when the CI/CD pipeline deploys Staging for the first time), the pipeline runs the provisioning script:

```bash
python scripts/infisical_provision_factory_env.py \
  --machine-name "Staging VPS HQ" \
  --machine-slug staging-hq \
  --factory-role HEADQUARTERS \
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

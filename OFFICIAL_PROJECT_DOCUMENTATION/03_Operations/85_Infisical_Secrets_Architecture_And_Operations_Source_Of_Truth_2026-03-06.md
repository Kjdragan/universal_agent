# 85. Infisical Secrets Architecture and Operations Source of Truth (2026-03-06)

## Purpose

This document is the canonical source of truth for how Universal Agent loads, trusts, and operationalizes secrets from Infisical.

It describes the runtime bootstrap model, the strict-versus-fallback behavior by deployment profile, the difference between bootstrap credentials and runtime secrets, and the scripts/operators involved in provisioning and validating Infisical-backed environments.

## Executive Summary

Universal Agent uses an **Infisical-first** runtime secret model.

The canonical loader is:
- `src/universal_agent/infisical_loader.py`

At startup, the runtime attempts to:
1. determine deployment profile
2. decide whether strict mode applies
3. fetch secrets from Infisical using machine identity credentials
4. inject those values into the process environment
5. optionally fall back to local `.env` only when policy allows it

The critical distinction is:
- `.env` contains **bootstrap information** and local dev knobs
- **Infisical is the intended system of record for real runtime secrets**

## Core Model

## Bootstrap Credentials vs Runtime Secrets

Universal Agent needs a small set of local bootstrap credentials so it can authenticate to Infisical.

These bootstrap values include:
- `INFISICAL_CLIENT_ID`
- `INFISICAL_CLIENT_SECRET`
- `INFISICAL_PROJECT_ID`
- optionally `INFISICAL_ENVIRONMENT`
- optionally `INFISICAL_SECRET_PATH`
- optionally `INFISICAL_API_URL`

These are not the same as application secrets like API keys and service tokens.

Runtime secrets are fetched from Infisical and then injected into `os.environ` for the running process.

## Canonical Loader

Primary implementation:
- `src/universal_agent/infisical_loader.py`

Key responsibilities:
- resolve runtime profile
- determine strict mode
- fetch secrets through SDK when available
- fall back to REST fetch when SDK is unavailable or fails
- inject secrets into process env
- optionally fall back to local dotenv in approved local scenarios
- sanitize error reporting so secret values are not leaked

## Deployment Profiles and Strictness

The loader recognizes these deployment profiles:
- `local_workstation`
- `standalone_node`
- `vps`

### Default Strictness

Default policy:
- `vps` -> strict mode by default
- `standalone_node` -> strict mode by default
- `local_workstation` -> non-strict by default

Env override:
- `UA_INFISICAL_STRICT`

Meaning:
- **strict mode** = fail closed if Infisical bootstrap cannot complete
- **non-strict mode** = allow fallback behavior when configured

### Dotenv Fallback Policy

Env flag:
- `UA_INFISICAL_ALLOW_DOTENV_FALLBACK`

Default intent:
- allowed for local workstation development when appropriate
- not appropriate for VPS production operation

This gives the project a practical balance:
- production-like nodes fail closed
- local development can keep moving during temporary Infisical issues

## Fetch Strategy

## SDK First

If available, the loader tries to use:
- `infisical_client`

This is the preferred high-level integration path.

## REST Fallback

If the SDK is unavailable or fails, the loader falls back to direct REST bootstrap.

REST sequence:
1. authenticate via universal auth login endpoint
2. retrieve an access token
3. fetch raw secrets from Infisical secrets API
4. normalize and inject values

This means runtime secret bootstrap does not depend on the SDK being present in every environment.

## Runtime Bootstrap Integration

Primary integration point:
- `src/universal_agent/runtime_bootstrap.py`

This module calls:
- `initialize_runtime_secrets(...)`

and then continues with:
- env alias application
- runtime policy resolution
- LLM provider override normalization

There is also explicit Infisical loading in:
- `src/universal_agent/delegation/bridge_main.py`
- `CSI_Ingester/development/csi_ingester/infisical_bootstrap.py` (optional, `CSI_INFISICAL_ENABLED=1`)

This ensures standalone execution surfaces (factory bridge, CSI ingester) can self-bootstrap secrets before reading env-driven configuration.

### CSI Infisical Bootstrap

CSI now has an optional Infisical-first bootstrap (`csi_ingester/infisical_bootstrap.py`) that mirrors the UA pattern. It uses the same `INFISICAL_CLIENT_ID`, `INFISICAL_CLIENT_SECRET`, `INFISICAL_PROJECT_ID` credentials but defaults to `INFISICAL_ENVIRONMENT=csi`. The bootstrap is disabled by default (`CSI_INFISICAL_ENABLED=0`) to preserve backward compatibility with existing env-file deployments. When enabled, secrets are injected into `os.environ` before `CSIConfig` reads them.

## Environment Variables

Canonical Infisical env surface from `.env.sample`:
- `UA_INFISICAL_ENABLED`
- `UA_INFISICAL_STRICT`
- `UA_INFISICAL_ALLOW_DOTENV_FALLBACK`
- `INFISICAL_CLIENT_ID`
- `INFISICAL_CLIENT_SECRET`
- `INFISICAL_PROJECT_ID`
- `INFISICAL_ENVIRONMENT`
- `INFISICAL_SECRET_PATH`
- `UA_DOTENV_PATH`

### Current Intended Model

- `UA_INFISICAL_ENABLED=1`
- local workstation may use `UA_INFISICAL_STRICT=0`
- local workstation may use `UA_INFISICAL_ALLOW_DOTENV_FALLBACK=1`
- VPS should effectively run strict/fail-closed behavior

## Operational Meaning of `.env.sample` Guidance

The `.env.sample` guidance is clear about the intended system model:
- local and VPS both use the same Infisical-backed secret authority
- raw secrets should be added to Infisical rather than copied into `.env`
- Infisical-loaded values are intended to win over raw values present in `.env`

This is a deliberate design choice that prevents configuration drift between environments.

## Control-Plane Secrets

Control-plane credentials are not the same as normal application runtime secrets.

Examples include:
- `TAILSCALE_ADMIN_API_TOKEN`
- future infrastructure tokens such as provider, DNS, or GitHub admin credentials

These secrets can modify external infrastructure and therefore should not be injected into the default VPS app runtime just because the application is running on that node.

The canonical posture is:
- Infisical remains the source of truth
- control-plane secrets live in a dedicated infrastructure-oriented environment
- normal UA runtime processes do not automatically receive those credentials

Preferred environment for Tailscale admin automation:
- environment: `infra-admin`
- path: `/tailscale`

Current live fallback for this project:
- environment: `prod`
- path: `/tailscale`

This fallback is in use because the Infisical project is currently at its environment limit and could not create `infra-admin`.
In both cases the canonical secrets are:
- `TAILSCALE_TAILNET`
- `TAILSCALE_ADMIN_API_TOKEN`

The operational helper for writing these secrets is:
- `scripts/infisical_upsert_secret.py`

## Provisioning and Support Scripts

Key script:
- `scripts/infisical_provision_factory_env.py`

Purpose:
- create or update machine-specific Infisical environments
- clone from a source environment
- apply role-specific override maps
- keep environment provisioning idempotent

This script documents and operationalizes the split between:
- a shared source environment such as `dev`
- machine-specific target environments such as `kevins-desktop`
- runtime policy overrides by role

Related setup script:
- `scripts/install_vps_infisical_sdk.sh`
- `scripts/infisical_upsert_secret.py`

This is part of the deployment/runtime preparation path for VPS nodes.

## Testing and Validation

Primary test file:
- `tests/unit/test_infisical_loader.py`

Current test coverage includes:
- strict mode failure when bootstrap credentials are missing
- strict mode failure when Infisical fetch fails
- local profile fallback to dotenv when allowed
- sanitization of errors so secret values are not logged
- REST fallback when SDK import is unavailable

These tests are part of the canonical truth because they capture the intended safety behavior of the secret loader.

## Operator Health Model

A healthy Infisical-backed runtime should satisfy all of the following:
- bootstrap credentials are present
- `initialize_runtime_secrets(...)` succeeds
- runtime source is `infisical` in normal production conditions
- secret values are present in process env without being duplicated into raw `.env`
- production nodes are not silently booting from local fallback when strict mode is expected

## Failure Modes to Watch

1. **Missing bootstrap credentials**
   - runtime cannot authenticate to Infisical

2. **SDK unavailable**
   - should fall back to REST, not silently fail

3. **REST fetch failure**
   - strict environments must fail closed

4. **Unexpected dotenv fallback in production**
   - indicates policy/config drift

5. **Leaky errors**
   - secret values must never appear in logs or surfaced errors

## Security Policy

The current intended policy is:
- Infisical is the authoritative runtime secret source
- `.env` is bootstrap-oriented, not the place for long-lived application secrets in production
- strict mode should protect VPS and other production-like nodes from partial or unsafe startup
- loader logging must avoid secret disclosure
- secret overrides loaded from Infisical should win over weaker local sources
- control-plane secrets should be segregated from default app runtime environments when the app does not need infrastructure-admin authority

## Current Gaps and Follow-Up Items

1. **Canonical ops visibility**
   - a richer operator-facing status endpoint for secret bootstrap source/mode could improve diagnosis

2. **Documentation sprawl**
   - some secret practices currently live in `.env.sample`, scripts, and tests rather than one canonical place
   - this document is intended to fix that

3. **Provisioning + runtime narrative gap**
   - provisioning scripts and runtime bootstrap are both correct, but previously required reading multiple files to understand together

## Source Files That Define Current Truth

Primary implementation:
- `src/universal_agent/infisical_loader.py`
- `src/universal_agent/runtime_bootstrap.py`
- `src/universal_agent/delegation/bridge_main.py`

Provisioning/support:
- `scripts/infisical_provision_factory_env.py`
- `scripts/install_vps_infisical_sdk.sh`
- `.env.sample`

Tests:
- `tests/unit/test_infisical_loader.py`

## Bottom Line

The canonical Infisical story in Universal Agent is:
- **Infisical is the runtime system of record for secrets**
- **`.env` provides bootstrap credentials and local dev support, not the final authority**
- **VPS and production-like nodes should fail closed if Infisical cannot load**
- **local workstations may fall back to dotenv only when explicitly allowed**
- **the loader is designed to stay resilient via SDK-first, REST-fallback bootstrap**

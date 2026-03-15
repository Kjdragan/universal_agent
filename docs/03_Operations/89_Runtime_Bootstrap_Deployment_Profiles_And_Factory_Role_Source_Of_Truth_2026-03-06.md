# 89. Runtime Bootstrap, Deployment Profiles, and Factory Role Source of Truth (2026-03-06)

> [!NOTE]
> **Start here instead → [Secrets and Environments](../deployment/secrets_and_environments.md)**
> This document is the deep-dive reference for deployment profiles, factory roles, and runtime policy matrix.
## Purpose

This document is the canonical source of truth for how Universal Agent bootstraps its runtime environment: how deployment profile is resolved, how secrets are loaded, how factory role is translated into runtime policy, and how bootstrap behavior changes between headquarters, local-worker, standalone, and VPS-oriented deployments.

## Executive Summary

The current bootstrap contract has three major stages:

1. **load secrets and env values** through Infisical-first runtime bootstrap
2. **normalize selected runtime env** such as XAI aliases and LLM provider override
3. **derive factory runtime policy** from `FACTORY_ROLE`

The current implementation is intentionally centralized in:
- `src/universal_agent/runtime_bootstrap.py`
- `src/universal_agent/infisical_loader.py`
- `src/universal_agent/runtime_role.py`

This means runtime mode is not determined by scattered feature flags alone.

It is determined by the combination of:
- deployment profile
- secret bootstrap success/failure mode
- factory role policy

## Core Bootstrap Pipeline

Primary implementation:
- `src/universal_agent/runtime_bootstrap.py`

Current bootstrap order:
1. `initialize_runtime_secrets(profile=...)`
2. `apply_xai_key_aliases()`
3. `normalize_llm_provider_override()`
4. `build_factory_runtime_policy()`

The bootstrap result currently returns:
- secret bootstrap result
- factory runtime policy
- normalized LLM provider override

## 1. Deployment Profile Resolution

Primary implementation:
- `src/universal_agent/infisical_loader.py`
- `src/universal_agent/gateway_server.py`

Current valid deployment profiles:
- `local_workstation`
- `standalone_node`
- `vps`

Resolution behavior:
- use explicit `profile` parameter if provided
- else use `UA_DEPLOYMENT_PROFILE`
- else default to `local_workstation`
- invalid values fall back to `local_workstation`

### Why Deployment Profile Matters

Deployment profile currently controls at least:
- whether Infisical strict mode is on by default
- some gateway defaults and auth posture on VPS
- the intended runtime environment model for startup

## 2. Infisical-First Secret Bootstrap

Primary implementation:
- `src/universal_agent/infisical_loader.py`

The runtime secret bootstrap is currently **Infisical-first**.

Behavior model:
- try Infisical first when `UA_INFISICAL_ENABLED` is enabled
- fail closed in strict profiles if Infisical cannot load
- allow dotenv fallback for local-style development if configured
- otherwise preserve environment-only startup behavior

### Strict Mode Defaults

Current default strictness:
- `vps` -> strict by default
- `standalone_node` -> strict by default
- `local_workstation` -> non-strict by default

Override control:
- `UA_INFISICAL_STRICT`

### Dotenv Fallback

Fallback control:
- `UA_INFISICAL_ALLOW_DOTENV_FALLBACK`

Default behavior:
- allowed by default on `local_workstation`
- not the default on strict profiles

Local dotenv path resolution:
- `UA_DOTENV_PATH`
- else repo-root `.env`

### Infisical Fetch Strategies

Current implementation supports two fetch paths:
- Infisical SDK path
- REST fallback path if SDK is unavailable or fails

Required Infisical settings:
- `INFISICAL_CLIENT_ID`
- `INFISICAL_CLIENT_SECRET`
- `INFISICAL_PROJECT_ID`

Additional controls:
- `INFISICAL_ENVIRONMENT`
- `INFISICAL_SECRET_PATH`
- `INFISICAL_API_URL`

### Secret Bootstrap Result Surface

The bootstrap result currently records:
- `ok`
- `source`
- `strict_mode`
- `loaded_count`
- `fallback_used`
- `errors`

Typical sources:
- `infisical`
- `dotenv`
- `environment`
- `none` on strict-mode failure path

## 3. Factory Role Resolution and Runtime Policy

Primary implementation:
- `src/universal_agent/runtime_role.py`

Current factory roles:
- `HEADQUARTERS`
- `LOCAL_WORKER`
- `STANDALONE_NODE`

Resolution behavior:
- read `FACTORY_ROLE`
- default to `HEADQUARTERS`
- invalid value falls back to `LOCAL_WORKER` fail-safe mode

### Current Runtime Policy Matrix

#### HEADQUARTERS

Current policy:
- `gateway_mode=full`
- `start_ui=True`
- `enable_telegram_poll=True`
- `heartbeat_scope=global`
- `delegation_mode=publish_and_listen`
- `enable_csi_ingest=True`
- `enable_agentmail=True`

Meaning:
- full gateway/API surface is available
- UI is expected
- HQ can publish and listen on delegation control plane

#### LOCAL_WORKER

Current policy:
- `gateway_mode=health_only`
- `start_ui=False`
- `enable_telegram_poll=False`
- `heartbeat_scope=local`
- `delegation_mode=listen_only`
- `enable_csi_ingest=False`
- `enable_agentmail=False`

Meaning:
- worker node does not expose full interactive API/UI surfaces
- worker consumes delegated work rather than acting as headquarters

#### STANDALONE_NODE

Current policy:
- `gateway_mode=full`
- `start_ui=True`
- `enable_telegram_poll` controlled by `UA_STANDALONE_ENABLE_TELEGRAM_POLL`
- `heartbeat_scope=local`
- `delegation_mode=disabled`
- `enable_csi_ingest=True` (default)
- `enable_agentmail=True` (default)

Meaning:
- standalone node is interactive like HQ in local behavior
- but does not participate as a normal delegation publisher/listener pair

## 4. LLM Provider Override Normalization

Primary implementation:
- `src/universal_agent/runtime_role.py`

Current allowed overrides:
- `ZAI`
- `ANTHROPIC`
- `OPENAI`
- `OLLAMA`

Behavior:
- reads `LLM_PROVIDER_OVERRIDE`
- normalizes to uppercase
- rejects unsupported values
- removes invalid override from env when unsupported

This means provider override is a controlled normalization step, not an open free-form setting.

## 5. Bootstrap Call Sites

Primary implementation call sites currently include:
- `src/universal_agent/agent_setup.py`
- `src/universal_agent/agent_core.py`
- `src/universal_agent/main.py`
- `src/universal_agent/gateway_server.py`
- `src/universal_agent/delegation/bridge_main.py`

### Current Meaning of Those Call Sites

- `agent_setup.py` uses bootstrap to initialize runtime policy before setup decisions such as role/capability shaping
- `agent_core.py` runs bootstrap before broader runtime initialization
- `main.py` bootstraps before primary execution and logfire flag refresh
- `gateway_server.py` bootstraps in lifespan using the resolved deployment profile
- `bridge_main.py` loads runtime secrets before bridge components read env

This makes bootstrap a shared contract across user-facing runtime, gateway runtime, and factory bridge runtime.

## 6. Gateway Profile/Role Enforcement

Primary implementation:
- `src/universal_agent/gateway_server.py`

The gateway applies runtime profile and role in two main ways:
- deployment-profile-aware auth/session defaults
- factory-policy-based HTTP/WebSocket surface restriction

Current important behavior:
- `LOCAL_WORKER` gateway mode is `health_only`
- most routes are blocked in that mode except health-oriented allowlisted paths
- WebSocket API is explicitly disabled for `LOCAL_WORKER`
- HQ-only fleet routes require `FACTORY_ROLE=HEADQUARTERS`

This means the role policy is operational, not only descriptive.

## Canonical Environment Controls

Deployment profile and role:
- `UA_DEPLOYMENT_PROFILE`
- `FACTORY_ROLE`
- `UA_STANDALONE_ENABLE_TELEGRAM_POLL`
- `LLM_PROVIDER_OVERRIDE`

Infisical bootstrap:
- `UA_INFISICAL_ENABLED`
- `UA_INFISICAL_STRICT`
- `UA_INFISICAL_ALLOW_DOTENV_FALLBACK`
- `UA_DOTENV_PATH`
- `INFISICAL_CLIENT_ID`
- `INFISICAL_CLIENT_SECRET`
- `INFISICAL_PROJECT_ID`
- `INFISICAL_ENVIRONMENT`
- `INFISICAL_SECRET_PATH`
- `INFISICAL_API_URL`

Related operational env used by consumers:
- `UA_GATEWAY_URL`
- `UA_BASE_URL`
- `UA_HQ_BASE_URL`

## What Is Actually Implemented Today

### Implemented and Current

- centralized runtime bootstrap helper
- Infisical-first secret loading
- strict-mode enforcement for VPS and standalone profiles by default
- optional local dotenv fallback
- role-derived runtime policy
- gateway role-based surface restriction
- controlled LLM provider override normalization

### Intentional Fail-Safe Behavior

- invalid deployment profile -> `local_workstation`
- invalid factory role -> `LOCAL_WORKER`
- invalid provider override -> ignored and removed

These are safety-oriented fallback choices.

### Important Current Constraint

Bootstrap is centralized, but some downstream capability toggles still depend on direct env reads after bootstrap.

So bootstrap defines the runtime baseline, but it is not the only source of all final feature enablement.

## Current Gaps and Follow-Up Items

1. **Role policy and feature toggles are both active**
   - some behavior is policy-derived while other behavior still reads env flags directly in consumers

2. **Profile vocabulary is intentionally small**
   - this keeps behavior comprehensible, but specialized modes still get expressed through additional env flags rather than richer profile objects

3. **Strict-mode failure is correct but operationally sharp**
   - VPS and standalone nodes fail closed when Infisical is unavailable unless explicitly relaxed

4. **Bootstrap is central, but documentation lag can still occur**
   - many downstream operational docs still describe behavior in a subsystem-specific way rather than as part of the single bootstrap contract

## Source Files That Define Current Truth

Primary implementation:
- `src/universal_agent/runtime_bootstrap.py`
- `src/universal_agent/infisical_loader.py`
- `src/universal_agent/runtime_role.py`

Primary consumers:
- `src/universal_agent/agent_setup.py`
- `src/universal_agent/agent_core.py`
- `src/universal_agent/main.py`
- `src/universal_agent/gateway_server.py`
- `src/universal_agent/delegation/bridge_main.py`

Related operations docs:
- `docs/03_Operations/85_Infisical_Secrets_Architecture_And_Operations_Source_Of_Truth_2026-03-06.md`

## Bottom Line

The canonical current runtime bootstrap model is:
- **deployment profile decides the strictness posture**
- **Infisical-first loading establishes runtime secrets**
- **factory role determines the main runtime policy shape**
- **gateway and bridge consumers apply that shared bootstrap contract at startup**

This is one of the most important central abstractions in the current system because it defines what kind of node is starting, what it is allowed to expose, and how strictly it must source secrets.

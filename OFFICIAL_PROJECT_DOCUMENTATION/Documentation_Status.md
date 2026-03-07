# Documentation Status

**Last updated:** 2026-03-06

## Canonical Source-of-Truth Documents

These are the authoritative references for each subsystem. When older documents conflict with these, **the canonical doc wins**.

| # | Subject | Canonical Doc |
|---|---------|--------------|
| 07 | WebSocket Architecture | `02_Flows/07_WebSocket_Architecture_And_Operations_Source_Of_Truth_2026-03-06.md` |
| 08 | Auth & Session Security | `02_Flows/08_Gateway_And_Web_UI_Auth_And_Session_Security_Source_Of_Truth_2026-03-06.md` |
| 82 | Email / AgentMail | `03_Operations/82_Email_Architecture_And_AgentMail_Source_Of_Truth_2026-03-06.md` |
| 83 | Webhooks | `03_Operations/83_Webhook_Architecture_And_Operations_Source_Of_Truth_2026-03-06.md` |
| 85 | Infisical Secrets | `03_Operations/85_Infisical_Secrets_Architecture_And_Operations_Source_Of_Truth_2026-03-06.md` |
| 86 | Residential Proxy | `03_Operations/86_Residential_Proxy_Architecture_And_Usage_Policy_Source_Of_Truth_2026-03-06.md` |
| 87 | Tailscale | `03_Operations/87_Tailscale_Architecture_And_Operations_Source_Of_Truth_2026-03-06.md` |
| 88 | Factory Delegation | `03_Operations/88_Factory_Delegation_Heartbeat_And_Registry_Source_Of_Truth_2026-03-06.md` |
| 89 | Runtime Bootstrap | `03_Operations/89_Runtime_Bootstrap_Deployment_Profiles_And_Factory_Role_Source_Of_Truth_2026-03-06.md` |
| 90 | Artifacts & Workspaces | `03_Operations/90_Artifacts_Workspaces_And_Remote_Sync_Source_Of_Truth_2026-03-06.md` |
| 91 | Telegram | `03_Operations/91_Telegram_Architecture_And_Operations_Source_Of_Truth_2026-03-06.md` |
| 92 | CSI Architecture | `03_Operations/92_CSI_Architecture_And_Operations_Source_Of_Truth_2026-03-06.md` |

## Review & Decision Documents

| # | Subject |
|---|---------|
| 93 | Prioritized Cleanup Plan from Canonical Review |
| 94 | Architectural Integration Review |
| 95 | Integration Architectural Decisions (ADRs) |

## Deleted Documents (2026-03-06 Cleanup)

26 outdated documents were deleted as part of the canonical review cleanup. Their content is fully covered by the canonical source-of-truth documents above. Deleted docs included: webhook implementation notes (15, 18, 29, 30, 42, 75), Telegram implementation plan (44), Running The Agent (46), Tailnet DevOps phases (63, 66-73), CSI strategy (74), security hardening (21), and stale guides (Configuration_Guide, AgentMail_Digest_Email_Plan, Advanced_CLI_Harnessing, Skill_Development, Testing_Strategy).

## Not Yet Canonicalized

These older docs cover topics not yet addressed by canonical docs and remain valid:

- `13_Skill_Dependency_Setup_Guide.md` — skill setup
- `15_Execution_Lock_Concurrency_Architecture_2026-03-02.md` — concurrency model
- `24_VPS_Service_Recovery_System_Runbook_2026-02-12.md` — service watchdog
- `76_Sandbox_Permissioning_And_Exception_Profile_2026-02-23.md` — sandbox config
- `79_Golden_Run_Research_Report_Pipeline_Reference_2026-02-28.md` — pipeline reference
- `80_Google_Workspace_Integration_Retrospective_Memo_2026-03-06.md` — GWS integration
- `81_Google_Workspace_CLI_Integration_Implementation_Plan_2026-03-06.md` — GWS plan

# Autonomous Health Repair Memory (2026-04-29)

> Moved verbatim from `memory/HEARTBEAT.md` (R4 context diet, 2026-07-18). Read this
> whenever a heartbeat health check comes back non-OK and you need to decide
> fix-it-yourself vs. delegate vs. escalate.

- Heartbeat Health Review exists to find system issues early and clear safe issues before they become larger problems.
- Simone should make an active decision on each non-OK heartbeat: fix autonomously/direct Cody through Task Hub, or refer to Kevin.
- Simone and Cody are advanced coding agents with significant capability. Start from the assumption that they can likely make required self-healing codebase fixes.
- Before deciding, search memory for the error signature, classification, file/function names, and prior repairs. Use memory as guidance plus current evidence, not as blind authority.
- Prefer autonomous remediation for bounded, reversible, testable system fixes such as code regressions, hook failures, prompt/schema mismatches, noisy known-rule cleanup, bounded refactors, and local repairs with tests.
- Refer to Kevin only as an extreme safety net: destructive changes, public/private data-boundary exposure, secrets/credentials/security policy, unusually complex design decisions, unique unfamiliar failures with weak evidence, or production deployment approval.
- For autonomous fixes, write `autonomous_remediation_approved=true`, a confidence level, rationale, memory evidence, and concrete proposed changes in `heartbeat_investigation_summary.json` so Task Hub/Cody can apply and verify the repair.

# Ghost.build — Cody Demo Ephemeral Postgres

**Canonical source of truth** for the Ghost.build MCP integration that gives Cody on-demand Postgres databases inside `/opt/ua_demos/<demo-id>/` workspaces.

**Last Updated:** 2026-05-16

> **One-line summary:** Ghost is Timescale's "database for agents" — hosted Postgres with pgvector, TimescaleDB, PostGIS, JSONB, exposed to Cody as MCP tool calls (`ghost_create`, `ghost_sql`, `ghost_fork`, `ghost_delete`). Wired into the demo scaffold so any Cody demo that needs a real database gets one with zero operator setup, with a documented cleanup contract so we don't burn the 100 hr/mo free cap on orphaned DBs.

---

## 1. Why Ghost, why demo-only

UA evaluated Ghost (2026-05-16) and decided to adopt it for a narrowly-scoped use case: Cody demo workspaces. The decision was driven by what Ghost is good at and what it's bad at.

| | Ghost is good for this | Ghost is bad for this |
|---|---|---|
| Hosted Postgres with pgvector, TimescaleDB, PostGIS, JSONB out-of-the-box | ✅ | |
| Fork-and-discard model — agents `ghost_create` and `ghost_delete` freely | ✅ | |
| Zero operator setup per database | ✅ | |
| MCP-native — drops into any Claude Code `.mcp.json` and Cody calls tools directly | ✅ | |
| Production data plane (Task Hub, CSI state, vault, memory) | | ❌ — hosted-only by Timescale, no published SLA or durability story, WAN latency over the internet on every claim/update would suffocate the heartbeat-driven dispatch loop |
| Long-lived data we cannot lose | | ❌ — no documented backup/restore procedure |
| Storage we control geographically | | ❌ — no data-residency selection |

The decision logic: Ghost solves a real problem for Cody (real Postgres without operator setup), and the demo workspace lane has the right risk profile for hosted external infra (ephemeral, low-stakes data, OK to recreate). The same trade-offs make it categorically unsuitable for any UA production-state store.

This bounds the integration: **only `templates/ua_demos_scaffold/.mcp.json` exposes Ghost.** The top-level UA `.mcp.json` does not, and the `_smoke` workspace does not.

## 2. End-to-end flow

```mermaid
sequenceDiagram
    autonumber
    participant Op as Operator
    participant Inf as Infisical
    participant Daemon as UA Gateway Daemon
    participant Cody as Cody Subprocess
    participant MCP as Ghost MCP Server
    participant Ghost as ghost.build API
    participant Demo as Demo workspace<br/>/opt/ua_demos/&lt;id&gt;/

    Op->>Inf: Upsert GHOST_API_KEY (production env)
    Note over Daemon: Service start
    Daemon->>Inf: initialize_runtime_secrets()
    Inf-->>Daemon: All secrets → os.environ (incl. GHOST_API_KEY)
    Note over Daemon: Simone enqueues a cody_demo_task
    Daemon->>Cody: spawn claude CLI subprocess<br/>cwd=/opt/ua_demos/&lt;id&gt;/<br/>_build_cli_env propagates os.environ
    Cody->>Demo: read .mcp.json (scaffold-shipped)
    Cody->>MCP: launch via "npx -y @ghost.build/cli mcp start"<br/>(first call: ~2 MB download; subsequent: cached)
    Note over MCP: ${GHOST_API_KEY} resolves from Cody's env
    Cody->>MCP: ghost_create
    MCP->>Ghost: POST /databases (with API key)
    Ghost-->>MCP: db name + connection string
    MCP-->>Cody: { name, connection_string }
    Cody->>Demo: record name in manifest.json.ghost_databases
    Cody->>MCP: ghost_sql / ghost_schema / etc.
    Note over Cody,Demo: ...demo work happens...
    Cody->>MCP: ghost_delete (on success)
    MCP->>Ghost: DELETE /databases/&lt;name&gt;
    Cody->>Demo: write final manifest.json
```

## 3. File integration map

The integration is intentionally small. These files implement and document the contract:

| File | Role | Code-verified citation |
|---|---|---|
| `src/universal_agent/templates/ua_demos_scaffold/.mcp.json` | Declares the Ghost MCP server with `${GHOST_API_KEY}` placeholder. Copied into every demo workspace by the provisioner. | [file](../../src/universal_agent/templates/ua_demos_scaffold/.mcp.json) |
| `src/universal_agent/templates/ua_demos_scaffold/README.md` | § "Ephemeral databases via Ghost" — the cleanup contract Cody must follow. | [file](../../src/universal_agent/templates/ua_demos_scaffold/README.md) |
| `src/universal_agent/services/demo_workspace.py` | Provisioner that copies the scaffold (including `.mcp.json`) into `/opt/ua_demos/<id>/`. | [`_copy_template`](../../src/universal_agent/services/demo_workspace.py#L148) |
| `src/universal_agent/services/demo_workspace.py` | `verify_vanilla_settings` audits `.claude/settings.json` only — does NOT inspect `.mcp.json`. This is intentional: MCP servers are demo capabilities, not pollution. | [`verify_vanilla_settings`](../../src/universal_agent/services/demo_workspace.py#L299) |
| `src/universal_agent/vp/clients/claude_cli_client.py` | `_build_cli_env` inherits `os.environ` (scrubbing `ANTHROPIC_*` in anthropic mode). `GHOST_API_KEY` flows through because it lives in the parent daemon's env. | [`_build_cli_env`](../../src/universal_agent/vp/clients/claude_cli_client.py#L695) |
| `src/universal_agent/infisical_loader.py` | `initialize_runtime_secrets()` fetches every Infisical secret into `os.environ` at daemon startup. `GHOST_API_KEY` is one of those secrets after the 2026-05-16 upsert. | [`initialize_runtime_secrets`](../../src/universal_agent/infisical_loader.py) |
| `.claude/skills/cody-implements-from-brief/SKILL.md` | § "Ephemeral Postgres via Ghost" — restates the cleanup contract from Cody's perspective. | [file](../../.claude/skills/cody-implements-from-brief/SKILL.md) |
| `docs/operations/demo_workspace_provisioning.md` | § Step 6 — operator setup runbook (Infisical key registration, first-run npx cost, troubleshooting). | [file](../operations/demo_workspace_provisioning.md) |
| `docs/06_Deployment_And_Environments/09_Demo_Execution_Environments.md` | § "Demo-only capabilities (MCP servers in the scaffold)" — why Ghost is in the scaffold but not in the top-level `.mcp.json`. | [file](../06_Deployment_And_Environments/09_Demo_Execution_Environments.md) |
| `docs/deployment/secrets_and_environments.md` | § "MCP Server Credentials" — `GHOST_API_KEY` row in the per-key consumption table. | [file](../deployment/secrets_and_environments.md) |
| `tests/unit/test_demo_workspace.py` | Regression guards: scaffold ships Ghost with `${VAR}` placeholder (not literal); smoke workspace does NOT ship Ghost. | [`test_provision_ships_ghost_mcp_server`](../../tests/unit/test_demo_workspace.py) · [`test_smoke_workspace_does_not_ship_ghost_mcp`](../../tests/unit/test_demo_workspace.py) |

## 4. Capability matrix (what Cody can do)

Tools exposed by the Ghost MCP server (per the [Ghost CLI reference](https://ghost.build/docs/) as of v0.14.0):

| Tool | Use case in a demo |
|---|---|
| `ghost_create` | Spin up a fresh empty Postgres database. Default extensions: pgvector, TimescaleDB, PostGIS. |
| `ghost_create --from-share <token>` | Spin up a database pre-populated from a shared snapshot (good for demos that need a known starting schema/data). |
| `ghost_sql` | Execute arbitrary SQL. The first call Cody makes after `ghost_create` is usually a schema bootstrap. |
| `ghost_schema` | Introspect a database's schema. Cody uses this to verify schema changes landed. |
| `ghost_fork` | Branch a database — useful for demos that show migration-safety patterns (fork, apply migration, compare). |
| `ghost_logs` | Pull recent query logs. Useful when a demo step doesn't behave as expected. |
| `ghost_delete` | Tear down a database. **Cody MUST call this on success** — see § 5. |
| `ghost_share` / `ghost_share_revoke` | Generate a share token. Niche — only needed by demos that explicitly demonstrate database sharing. |
| `ghost_pause` / `ghost_resume` | Pause a database to stop billing time. Niche — most demos should `ghost_delete` rather than pause. |

Capabilities that exist in Ghost but Cody should not touch in demos:
- `ghost_create dedicated` (always-on billed instance) — would burn the operator's payment method without a budget review. Demos use the free shared tier exclusively.
- `ghost_api_key create/delete` — operator-only via the Infisical workflow documented in `secrets_and_environments.md`. Cody must not rotate keys.

## 5. The cleanup contract (the load-bearing convention)

Ghost's free tier is **100 hours/month across the entire UA account**. A demo that creates 3 databases and forgets to delete them burns roughly 3× faster than one that cleans up. Within a few weeks of routine demo activity, the cap can be exhausted, and Cody demos start failing at `ghost_create` time.

There is **no automated reaper** today. Cleanup is enforced by convention. The contract has three roles:

### 5.1 Cody's responsibility (during the demo run)

```jsonc
// manifest.json (Cody writes this at end-of-demo)
{
  "demo_id": "...",
  "endpoint_hit": "anthropic_native",
  "ghost_databases": [
    "demo-foo-bar-abc",   // every name Cody passed to ghost_create lands here
    "demo-foo-baz-def"
  ],
  // ... other manifest fields ...
}
```

- **Before any `ghost_sql` against a DB:** record its name in the in-progress manifest, even if the manifest isn't written to disk yet.
- **On successful run:** call `ghost_delete` on each name in `ghost_databases`, then write the final `manifest.json`. (Optional: keep the names with a `"deleted_at"` annotation if the demo needs to evidence the delete step.)
- **On failed run:** leave the databases intact AND keep the names in `manifest.json`. Operators will reclaim them in the weekly audit.
- **Never** invent a workaround if `${GHOST_API_KEY}` resolves empty. That's a `kind="blocker"` build note in `BUILD_NOTES.md`, full stop.

### 5.2 Operator's responsibility (weekly)

```bash
# On the desktop or VPS — both have ghost CLI access if GHOST_API_KEY is exported
ghost list                                                              # all DBs in the UA Ghost space
jq -r '.ghost_databases[]?' /opt/ua_demos/*/manifest.json | sort -u     # all DBs claimed by demos
# Diff the two — anything in `ghost list` but not in any manifest is orphaned
# For each orphan:
ghost delete <name>
```

If the orphan count is non-trivial (say >5/week), revisit § 8 and consider building the automated reaper.

### 5.3 What this protects against

The original 2026-05-08 Hostinger incident in this repo (literal token in `.mcp.json`) is a cautionary tale about MCP credentials leaking. The cleanup contract here is the same kind of "convention as a safety net" pattern for cost rather than secrets: a small, well-documented rule that prevents an external dependency from quietly failing in a way that's expensive to undo.

## 6. Operator one-time setup

Done as of 2026-05-16:

1. ✅ Operator signed in at https://ghost.build via GitHub OAuth.
2. ✅ Operator created an API key with `ghost api-key create --name "ua-vps-prod" --env`.
3. ✅ Operator upserted `GHOST_API_KEY` into Infisical `production` and `development` environments via `scripts/infisical_upsert_secret.py`.
4. ✅ UA gateway restarted (deployed PR #300); `initialize_runtime_secrets()` injected the new key.

To rotate the key in the future:

```bash
# On the desktop with the ghost CLI installed (npm install -g @ghost.build/cli)
ghost api-key create --name "ua-vps-prod-rotated-YYYYMMDD" --env > ~/.ghost_key_tmp
# Upsert (overwrites existing key)
set -a && . ~/.ghost_key_tmp && set +a
uv run python scripts/infisical_upsert_secret.py --environment production --secret-env GHOST_API_KEY
uv run python scripts/infisical_upsert_secret.py --environment development --secret-env GHOST_API_KEY
shred -uz ~/.ghost_key_tmp
# Restart UA daemon
sudo systemctl restart 'universal-agent-*.service'
# Then revoke the old key in Ghost
ghost api-key list
ghost api-key delete <old_prefix>
```

## 7. Failure modes catalog

| Symptom | Probable cause | Fix |
|---|---|---|
| Cody build note: `Ghost MCP server fails to start with "missing GHOST_API_KEY"` | `GHOST_API_KEY` not in Infisical, OR UA daemon was started before the key was registered | Verify with `scripts/infisical_upsert_secret.py` listing, then `sudo systemctl restart 'universal-agent-*.service'`. |
| First demo of the day takes ~10s longer than usual at the MCP-launch step | Cold `npx` cache — first run after VPS reboot downloads `@ghost.build/cli`. Expected, one-time per reboot. | No action. Optional: pre-warm with `npx -y @ghost.build/cli --help` in a deploy hook. |
| `ghost list` shows databases not in any `manifest.json` | Abandoned by a failed/orphaned demo run | Cross-reference and `ghost delete <name>` per § 5.2. |
| Cody reports "could not connect to database" mid-demo | `ghost.build` API timeout or transient hosting blip | Cody should retry once, then write `kind="blocker"` build note and stop. Not our infra. |
| Demo `endpoint_hit` regresses to `zai` after this change | Unrelated — Ghost integration does not affect Anthropic vs ZAI routing | Diagnose via `09_Demo_Execution_Environments.md` decision tree, not via this doc. |
| Multiple demos race-create databases simultaneously | Ghost handles concurrent creates fine — each gets a unique name | Not a real failure mode; informational. |

## 8. Known gaps and future hardening

- **No automated reaper.** The weekly operator audit (§ 5.2) is the current safety net. If demo throughput grows past ~5 demos/day, add a cron job that does the manifest-vs-`ghost list` reconciliation and `ghost_delete`s orphans, gated by a stale-age threshold (e.g. > 24h).
- **No per-demo cost telemetry.** We don't know which demos burn the most Ghost hours. If we hit the cap, the only diagnostic is `ghost list` historical timestamps. Could be fixed by recording `ghost_create` and `ghost_delete` timestamps into `manifest.json` and aggregating in a dashboard tile.
- **No data-residency control.** Ghost stores data wherever Timescale chooses. If a demo needs to demonstrate data sovereignty, Ghost is the wrong tool — use a dedicated Postgres for that specific demo.
- **Free tier dependency.** We're using the unpaid tier. If Ghost changes pricing or removes the free tier, we lose the capability silently — Cody demos start failing at `ghost_create`. Monitor Ghost's blog or follow `@ghostdotbuild` on X.
- **Self-host story.** Ghost is not currently self-hostable (per Timescale's repo). If they ship a self-hostable variant, revisit the "demo-only" scope — UA could potentially adopt Ghost for production data with a self-hosted deployment.

## 9. Relationship to other UA subsystems

| Subsystem | Interaction |
|---|---|
| [Demo Execution Environments](../06_Deployment_And_Environments/09_Demo_Execution_Environments.md) | Ghost is the first entry in the "Demo-only capabilities (MCP servers in the scaffold)" registry. Future MCP-server additions should follow the same demo-only pattern (separate `.mcp.json`, not top-level). |
| [ClaudeDevs Intelligence v2](../proactive_signals/claudedevs_intel_v2_design.md) | Demos generated by the v2 pipeline (Phase 3, Cody implementation) are the primary consumers of Ghost. Any feature demo where the underlying Anthropic/SDK capability *requires* Postgres should now use Ghost rather than scaffolding SQLite. |
| Task Hub | No relationship. Ghost has zero presence in Task Hub. The cleanup contract lives in `manifest.json` per-demo, not in Task Hub rows. |
| CSI / vault | No relationship. Ghost does not store CSI signals, vault entities, or any UA production state. |
| Infisical | Stores `GHOST_API_KEY` in `production` and `development` environments. Consumed by `initialize_runtime_secrets()` at UA daemon startup. |
| Memory system | No relationship. Cody-generated demos may write to a Ghost DB to demonstrate a "memory pattern" feature, but UA's own memory system uses file-based `.claude/memory/` storage. |

## 10. Related docs

- [`docs/operations/demo_workspace_provisioning.md`](../operations/demo_workspace_provisioning.md) — § Step 6 has the operator runbook (mirrors § 6 above but with more troubleshooting depth).
- [`docs/06_Deployment_And_Environments/09_Demo_Execution_Environments.md`](../06_Deployment_And_Environments/09_Demo_Execution_Environments.md) — § "Demo-only capabilities" — the scope rationale.
- [`docs/deployment/secrets_and_environments.md`](../deployment/secrets_and_environments.md) — § "MCP Server Credentials" — the consumption table.
- [`src/universal_agent/templates/ua_demos_scaffold/README.md`](../../src/universal_agent/templates/ua_demos_scaffold/README.md) — the demo workspace contract (Cody-facing).
- [`.claude/skills/cody-implements-from-brief/SKILL.md`](../../.claude/skills/cody-implements-from-brief/SKILL.md) — the Cody skill that consumes this capability.
- [Ghost.build documentation](https://ghost.build/docs/) — upstream reference.
- [Ghost on GitHub](https://github.com/timescale/ghost) — upstream source.

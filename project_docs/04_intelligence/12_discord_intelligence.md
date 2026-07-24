---
title: Discord Intelligence (DECOMMISSIONED)
status: archived
canonical: true
subsystem: intel-discord
code_paths:
  - "discord_intelligence/**/*.py"
  - "discord_intelligence/config.yaml"
  - "discord_intelligence/ua-discord-intelligence.service"
last_verified: 2026-07-24
---

# Discord Intelligence — DECOMMISSIONED (2026-07-24)

> **This subsystem is retired.** It no longer runs, is not installed by the deploy,
> and is not surfaced in the dashboard, the gateway API, or the MCP tool set. The
> `discord_intelligence/` Python package and its 217&nbsp;MB SQLite DB remain on
> disk as a **dormant archive** (nothing imports or executes them). This doc is kept
> as the historical record and the revival runbook.

## Why it was decommissioned

A full flow-and-value audit (2026-07-24) found the subsystem produced very little
operator value for its footprint: two always-on daemons monitoring 47 servers /
3,310 channels, ~620 messages/day, ~20k rule signals (93% pure "activity" noise),
~12k LLM insights that reached the operator only as an anonymous count in an email
or an unopened dashboard tab — and its one default-on human-facing delivery (Google
Calendar sync) had been failing on every attempt (0 of 148 events synced). An
earlier pass had already cut the biggest cost (the 5-minute LLM relevance filter,
~40% of all ZAI calls). The operator elected to retire the whole subsystem rather
than keep maintaining it.

## What was removed

| Layer | Change |
|---|---|
| Runtime services | The two systemd units (`ua-discord-intelligence`, `ua-discord-cc-bot`) are no longer rendered/enabled/restarted by `scripts/install_vps_systemd_units.sh` or `scripts/deploy/remote_deploy.sh`; their `.service.template` files were deleted; the discord deploy health-gate was removed. Both units were stopped, disabled, and masked on the VPS. |
| Gateway API | All `/api/v1/dashboard/discord/*` endpoints and their helpers were deleted from `gateway_server.py`; the `/api/v1/csi/discord` watchlist router (`api/routers/csi_discord_watchlist.py`) was deleted and unmounted from `gateway_server.py` and `api/server.py`. |
| Proactive cards | Discord card generation was removed from `proactive_signals.py::generate_signal_cards` (the `discord_db_path` param is retained as a no-op; the `"discord"` count is always 0). The YouTube card path is unaffected. |
| Web UI | The Discord Intel and CSI-Discord dashboard pages were deleted; their nav entries, the `discord` source filter on the Proactive Signals page, and the `discord` session channel were removed. |
| MCP | The `discord` and `discord-intelligence-bridge` server entries were removed from `.mcp.json`. |

## What remains (dormant archive)

The `discord_intelligence/` package is untouched on disk — the daemon
(`discord_intelligence/daemon.py::main`), the C&C bot (`discord_intelligence/cc_bot.py::main`),
`config.yaml`, and the accumulated `discord_intelligence.db` are all still present but
never loaded or executed. Nothing in `src/universal_agent/` imports the package.

## Reviving it (if ever needed)

1. Re-add the two `render_template` blocks + `units_to_enable` entries in
   `scripts/install_vps_systemd_units.sh`, and the restart/interpreter/health-gate
   lines in `scripts/deploy/remote_deploy.sh` (see this PR's diff for the exact
   removals). Restore the two `.service.template` files.
2. On the VPS: `sudo systemctl unmask --now ua-discord-intelligence ua-discord-cc-bot`
   then deploy so the units re-render and enable.
3. Re-surface as desired: the gateway endpoints, the web-UI pages, the proactive
   Discord card generator, and the MCP entries were all deleted — restore from
   history. Note the prior operator concern: the LLM triage/relevance loops and the
   calendar sync (broken `gws` OAuth) should be re-evaluated for value before
   re-enabling, not restored blindly.

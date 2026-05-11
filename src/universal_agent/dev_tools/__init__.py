"""Dev-mode CLI helpers for the local-dev workflow.

These are intentionally narrow inspection / single-iteration triggers
operators can run from a terminal while ``just dev`` is up (or even
without it). Not meant for production use.

Entry point: ``python -m universal_agent.dev_tools <subcommand>``.

Subcommands:

* ``env-report`` — print the same per-loop summary that gets logged at
  gateway startup in dev mode. Useful for checking what loops would
  run BEFORE you start services.
* ``loop-status <name>`` — explain why a specific loop is on/off.
* ``cron-list`` — list persisted cron jobs from
  ``AGENT_RUN_WORKSPACES/cron_jobs.json`` (if present).

See: ``docs/06_Deployment_And_Environments/12_Local_Dev_Environment.md``.
"""

__all__ = []

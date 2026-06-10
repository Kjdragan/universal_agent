"""Autonomous proactive-signal-card generation tick.

Generates/refreshes ``proactive_signal_cards`` (YouTube diamond + Discord cards)
from the continuously-ingested CSI/Discord feedstock, so the card list stays
fresh **without anyone opening the dashboard**. Before this, card generation had
no autonomous trigger — it only ran on a dashboard load with ``?sync`` — so if
no operator opened the proactive-signals tab the card queue went stale even while
CSI kept flowing, and the 03:15 ``nightly_wiki`` consumer found nothing to build.

Substrate: a deploy-independent **systemd timer**
(``universal-agent-proactive-signal-card-sync.timer``) fires this hourly. The
work is **pure SQLite — no LLM, no secrets, no delivery** — it calls
``proactive_signals.generate_signal_cards`` (the card-only core), NOT the full
``sync_generated_cards`` (which also runs the LLM-bearing convergence/tutorial
syncs that already have their own ``csi-convergence-sync`` timer).

Dormancy: this is an interval job, so it observes the Houston active window by
default (06:00–22:00 America/Chicago) and no-ops overnight. Set
``UA_PROACTIVE_CARD_SYNC_24_7=true`` to run around the clock.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
import sys

from universal_agent.durable.db import connect_runtime_db, get_activity_db_path
from universal_agent.proactive_signals import generate_signal_cards
from universal_agent.services.dormancy import should_run

logger = logging.getLogger(__name__)

# Truthy spellings for the 24/7 escape hatch.
_TRUTHY = {"1", "true", "yes", "on"}


def _csi_db_path() -> Path:
    return Path(os.getenv("CSI_DB_PATH", "/var/lib/universal-agent/csi/csi.db"))


def _discord_db_path() -> Path:
    # Mirror gateway_server._discord_intelligence_db_path():
    #   <repo-root>/discord_intelligence/discord_intelligence.db
    # This module is src/universal_agent/scripts/…, so the repo root is parents[3].
    repo_root = Path(__file__).resolve().parents[3]
    return repo_root / "discord_intelligence" / "discord_intelligence.db"


def main() -> int:
    logging.basicConfig(level=logging.INFO)

    # Runtime dormancy gate. Interval job → active-window only by default; flip
    # UA_PROACTIVE_CARD_SYNC_24_7=true (Infisical/.env) to run 24/7.
    run_24_7 = str(os.environ.get("UA_PROACTIVE_CARD_SYNC_24_7", "")).strip().lower() in _TRUTHY
    if not should_run(mode="always" if run_24_7 else "dormancy_aware"):
        logger.info(
            "Dormant window — skipping proactive signal card sync "
            "(set UA_PROACTIVE_CARD_SYNC_24_7=true to run 24/7)."
        )
        return 0

    csi_db_path = _csi_db_path()
    discord_db_path = _discord_db_path()

    conn = connect_runtime_db(get_activity_db_path())
    try:
        counts = generate_signal_cards(
            conn, csi_db_path=csi_db_path, discord_db_path=discord_db_path
        )
    except Exception as exc:  # noqa: BLE001 — log + non-zero exit for the unit
        logger.error("proactive signal card sync failed: %s", exc, exc_info=True)
        return 1
    finally:
        conn.close()

    logger.info(
        "proactive signal card sync: youtube=%d discord=%d expired=%d purged=%d",
        counts.get("youtube", 0),
        counts.get("discord", 0),
        counts.get("expired", 0),
        counts.get("purged", 0),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

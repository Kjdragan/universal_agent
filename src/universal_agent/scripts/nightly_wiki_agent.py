import asyncio
from datetime import datetime, timezone
import json
import logging
import os
import sqlite3
import subprocess
import sys

from universal_agent.durable.db import connect_runtime_db, get_activity_db_path
from universal_agent.infisical_loader import initialize_runtime_secrets
from universal_agent.proactive_signals import CARD_STATUS_PENDING, list_cards

logger = logging.getLogger(__name__)

# Project-wide HARD cap on wiki-notebook creation per day. The intended output
# is 1 wiki/night; 3 leaves headroom for a legitimate bounded retry while
# preventing a runaway. On 2026-06-14 a single flailing mission created ~33
# NotebookLM notebooks (the inner per-mission loop creating a *new* notebook on
# every retry instead of reusing one) — this cap + the create-once objective
# hardening below close that. See
# project_docs/08_operations/07_proactive_lane_runaway_protection.md.
WIKI_DAILY_HARD_CAP_DEFAULT = 3


def _wiki_daily_hard_cap() -> int:
    """Read the project-wide daily wiki-notebook cap (env-overridable)."""
    raw = os.getenv("UA_DAILY_PROACTIVE_WIKI_HARD_CAP", "").strip()
    if not raw:
        return WIKI_DAILY_HARD_CAP_DEFAULT
    try:
        value = int(raw)
    except ValueError:
        return WIKI_DAILY_HARD_CAP_DEFAULT
    return value if value >= 0 else WIKI_DAILY_HARD_CAP_DEFAULT


def _count_wikis_today_from_list(notebooks, today: str) -> int:
    """Count today's *wiki* notebooks from an ``nlm notebook list`` payload.

    Pure + unit-testable. ``today`` is a ``YYYY-MM-DD`` (UTC) date string. Excludes
    the paper-to-podcast lane (its own notebooks are titled "Paper to Podcast: …")
    so this counts only the proactive-wiki lane's notebooks.
    """
    count = 0
    for nb in notebooks or []:
        if not isinstance(nb, dict):
            continue
        stamp = str(
            nb.get("updated_at") or nb.get("created") or nb.get("create_time") or ""
        )
        if not stamp.startswith(today):
            continue
        title = str(nb.get("title") or nb.get("name") or "").strip().lower()
        if title.startswith("paper to podcast"):
            continue  # different lane (paper_to_podcast), not a wiki
        count += 1
    return count


def _count_wiki_notebooks_today(today: str) -> int:
    """Best-effort count of wiki notebooks created today via the ``nlm`` CLI.

    Fail-OPEN (returns 0) on any error so a transient ``nlm`` hiccup never blocks
    the nightly — the create-once objective hardening + the PR-A anomaly
    invariants remain as backstops.
    """
    cli = (os.getenv("UA_NOTEBOOKLM_CLI_COMMAND") or "nlm").strip() or "nlm"
    try:
        proc = subprocess.run(
            [cli, "notebook", "list", "--json"],
            capture_output=True,
            text=True,
            timeout=90,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            logger.warning(
                "wiki cap pre-flight: `%s notebook list` failed (rc=%s) — failing open",
                cli, proc.returncode,
            )
            return 0
        data = json.loads(proc.stdout)
        notebooks = data if isinstance(data, list) else data.get("notebooks", [])
        return _count_wikis_today_from_list(notebooks, today)
    except Exception as exc:  # noqa: BLE001 — pre-flight must never crash the nightly
        logger.warning("wiki cap pre-flight: count failed (%s) — failing open", exc)
        return 0

async def main():
    # 1. Initialize runtime secrets via Infisical (allowing dotenv fallback).
    # One-shot subprocess: make sure the Infisical-backed secrets (NotebookLM
    # auth cookie, LLM keys, etc.) are present before the VP mission dispatches.
    # Use NO hardcoded profile so UA_DEPLOYMENT_PROFILE is honored: under the
    # S5 Phase A batch A4 systemd unit it is `vps` -> strict Infisical production
    # load (a hardcoded profile="local_workstation" would override that backstop
    # and silently run keyless under systemd — the batch-3 csi_convergence_sync
    # trap). Dev leaves the var unset -> local_workstation, so dev is unchanged.
    initialize_runtime_secrets()
    logging.basicConfig(level=logging.INFO)
    
    # Ensure artifacts directory for wikis exists — use the canonical resolver
    # that works on both desktop and VPS (resolves relative to repo root).
    from universal_agent.artifacts import resolve_artifacts_dir
    artifacts_dir = str(resolve_artifacts_dir())
    wiki_artifacts_dir = os.path.join(artifacts_dir, "nightly_wikis")
    os.makedirs(wiki_artifacts_dir, exist_ok=True)
    
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Connect to DB to load pending cards.
    # IMPORTANT: proactive_signal_cards lives in activity_state.db, NOT
    # runtime_state.db. The dashboard endpoint /api/v1/dashboard/proactive-signals
    # and the "Create Wiki" button both write to activity_state.db via
    # gateway_server._activity_connect. Reading runtime_state.db here will
    # silently return zero pending cards even when the dashboard shows dozens.
    # Same DB-path pattern as the May-20 watchdog incident (PRs #389/#390/#392/#396).
    db_path = get_activity_db_path()
    conn = connect_runtime_db(db_path)
    
    # Read the pending signals
    try:
        cards = list_cards(conn, status=CARD_STATUS_PENDING, limit=100)
    except Exception as exc:
        logger.error(f"Failed to load proactive signals: {exc}")
        sys.exit(1)
    finally:
        conn.close()

    # Determine desired count of wikis to create nightly
    wiki_count_str = os.getenv("UA_DAILY_PROACTIVE_WIKI_COUNT", "1")
    try:
        wiki_count = int(wiki_count_str)
    except ValueError:
        wiki_count = 1

    if not cards:
        logger.info("No pending cards available for proactive wiki generation.")
        sys.exit(0)

    # Project-wide HARD daily cap (deterministic pre-flight). Count wiki
    # notebooks already created today on the account; if we're at/over the cap,
    # do not dispatch at all. Otherwise clamp this run's wiki_count to the
    # remaining budget so the cron + any rescue re-dispatch can never push the
    # day's total past the cap.
    hard_cap = _wiki_daily_hard_cap()
    created_today = _count_wiki_notebooks_today(today)
    remaining = hard_cap - created_today
    if remaining <= 0:
        logger.warning(
            "Project-wide daily wiki cap reached (%d created today >= cap %d) — "
            "skipping nightly dispatch to protect NotebookLM/ZAI quota.",
            created_today, hard_cap,
        )
        sys.exit(0)
    if wiki_count > remaining:
        logger.info(
            "Clamping wiki_count %d -> %d to stay within daily cap %d (%d already created).",
            wiki_count, remaining, hard_cap, created_today,
        )
        wiki_count = remaining

    cards_context = json.dumps([
        {
            "card_id": c.get("card_id"),
            "source": c.get("source"),
            "title": c.get("title"),
            "summary": c.get("summary")
        } for c in cards[:20]  # Pass top 20 candidates
    ], indent=2)
    
    objective = f"""You are executing the Nightly Proactive Wiki Creation routine.
Your target is to generate {wiki_count} complete Wiki knowledge bases from the pending signal cards provided below.

⛔ HARD LIMITS (non-negotiable — violating these caused a 2026-06-14 runaway that
created ~130 NotebookLM notebooks from one intended wiki):
- The project caps total wiki-notebook creation at {hard_cap} per day. You may create at
  most {wiki_count} NEW notebook(s) in this run. Before EACH `nlm notebook create`, run
  `nlm notebook list` and verify you have not already created {hard_cap} notebooks today;
  if you have, STOP immediately and write the summary file.
- ONE notebook PER TOPIC. Create the topic's notebook EXACTLY ONCE, capture its `<id>`,
  and reuse that SAME `<id>` for every later step. If ANY later step (research/studio/
  download) fails, RETRY USING THE SAME `<id>` — NEVER run `nlm notebook create` again for
  a topic you already created. Creating a second notebook for the same topic is the exact
  defect that caused the runaway.
- If a topic's pipeline fails 3 times, ABANDON that topic (do NOT recreate it) and either
  move to the next selected topic or stop. Never loop `nlm notebook create`.

INSTRUCTIONS:
1. Review the following {len(cards[:20])} pending proactive signal candidates.
2. Select the {wiki_count} MOST interesting topics, prioritizing topics related to AI, LLMs, Agents, coding, or our recent focus areas.
3. For EACH selected topic, follow the NLM-FIRST pipeline:
   a. Create the topic's NotebookLM notebook ONCE: `nlm notebook create "Topic Title"`.
      Capture the returned `<id>` and use that SAME `<id>` for every step below. Do NOT
      run `nlm notebook create` again for this topic under any circumstance (see HARD LIMITS).
   b. Run NLM research: `nlm research start "topic query" --notebook-id <id>` (use fast mode by default).
      GROUNDING: the wiki must be about the topic the CARD actually describes — not an unrelated
      entity sharing a keyword/proper noun. Build the query from the card's summary/evidence with
      DISAMBIGUATING context (e.g. "Claude 5 Hermes multi-agent orchestration workflow", NOT a bare
      ambiguous name like "Olympus Protocol"). Add the source channel/author when known.
   c. Poll: `nlm research status <id> --max-wait 0` with adaptive sleep intervals (sleep 5 for fast, sleep 20 for deep) until completed
   d. Import sources SELECTIVELY to prevent topic drift. Prefer `nlm research import <id> <task-id> --cited-only`
      (imports only sources the research report actually cited — a built-in relevance filter). If you must
      hand-pick, inspect the discovered source titles and pass `--indices <comma,separated,on-topic indices>`,
      dropping any source about a different entity that merely shares the keyword. Prefer fewer on-topic
      sources over a larger polluted set; if the query keeps returning collisions, build from the
      card-anchored sources alone.
   e. Generate artifacts via NLM studio — fire ALL creates first, then poll once:
      - `nlm report create <id> --confirm`
      - `nlm infographic create <id> --orientation landscape --style professional --confirm`
   f. Poll `nlm studio status <id>` with sleep 10 until all artifacts are completed
   g. Download artifacts:
      - `nlm download report <id> --output {wiki_artifacts_dir}/{today}_wiki_report_[TOPIC].md`
      - `nlm download infographic <id> --output {wiki_artifacts_dir}/{today}_wiki_infographic_[TOPIC].png`
   h. Register the KB: use the `kb_register` tool with slug, notebook_id, title, and tags
   i. Ingest the report into the Wiki using `wiki_ingest_external_source`

   IMPORTANT: Do NOT use `generate_image` or HTML-to-PDF for infographics.
   NLM studio generates source-grounded infographics that are higher quality.

4. When finished, format a "Nightly Wiki Report" documenting the selected topics, notebook URLs, absolute paths to downloaded artifacts, and the core themes added to the Wiki.
5. Save exactly one payload file named 'nightly_wiki_{today}.md' to {wiki_artifacts_dir} containing just this summary so the Morning Briefing agent can surface it to the user.

RAW PENDING CARDS:
```json
{cards_context}
```
"""
    
    logger.info(f"Dispatching nightly wiki mission to vp.general.primary for {wiki_count} wiki(s)...")

    from universal_agent.tools.vp_orchestration import dispatch_vp_mission

    try:
        await dispatch_vp_mission(
            objective=objective,
            mission_type="proactive_wiki",
            idempotency_key=f"nightly-wiki-{today}",
            # Routine dispatch — executes no Task Hub task; never auto-link one
            # (the 2026-06-10 hijack: this dispatch stole a freshly-approved
            # tutorial_build card and falsely closed it).
            link_task=False,
        )
    except RuntimeError as exc:
        logger.error(f"Dispatch failed: {exc}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())

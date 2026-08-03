"""Operator-DIRECTED demo-build lane (S5).

"Kevin says: build X" arriving from anywhere — the gateway
``POST /api/v1/directed-demo`` endpoint, the dashboard free-text form, or the
Telegram ``/demo`` command — flows through the SAME demo_factory engine path as
the proactive ``tutorial_build`` lane, but as ``source_kind == "directed_build"``
with two deliberate differences:

  * **No buildability judge.** Operator direction IS the judgment — the seed is
    queued verbatim; there is no candidate-scoring gate and no preference gate.
  * **Its own daily budget.** Directed builds dispatch under
    :func:`feature_flags.directed_demo_daily_cap` (``UA_DIRECTED_DEMO_DAILY_CAP``,
    default 3), SEPARATE from the proactive cap — but they still SERIALIZE
    against proactive builds via the single coder VP slot (one build at a time).

Everything downstream is shared with the proactive lane: the build command is
``services/proactive_tutorial_builds.build_demo_factory_command_line`` (full
land: verify + fidelity-eval + vault entry + EXHIBIT + private GitHub backup +
``--skill-tier library`` graduation + ``--video``), and completion flows the
same worker_loop terminal-sync → ``tutorial_demo_finalize`` → demo-built email
path (worker_loop treats ``directed_build`` alongside ``tutorial_build``).

The whole lane is gated OFF by default behind ``UA_DIRECTED_DEMO_ENABLED``
(:func:`feature_flags.directed_demo_enabled`): intake refuses to queue and the
dispatcher defers every directed build until the operator flips it on.
"""

from __future__ import annotations

import hashlib
import logging
import re
import sqlite3
from typing import Any, Optional

from universal_agent import task_hub
from universal_agent.feature_flags import directed_demo_enabled
from universal_agent.wiki.core import _slugify as _base_slugify

logger = logging.getLogger(__name__)

# On-disk landing prefix for a directed build: build_demo.py --slug directed-<s>
# lands the repo at /home/ua/lrepos/demo-directed-<s>. worker_loop's finalize
# resolves the same dir from metadata.directed_slug; keep the two aligned.
_LANDED_PREFIX = "directed"

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_URL_RE = re.compile(r"^https?://\S+$", re.IGNORECASE)
# "/demo <seed>" (optionally "/demo@BotName <seed>") or "build [me] a demo of
# <seed>" — the Telegram / natural-language intake. Kept as pure functions so
# they are unit-testable without a bot. The command regex requires a word
# boundary after ``/demo`` so ``/demonstrate`` is NOT treated as a demo command.
_CMD_DEMO_RE = re.compile(r"^/demo(?:@\w+)?(?:\s+(.*))?$", re.IGNORECASE | re.DOTALL)
_NL_DEMO_RE = re.compile(r"^build(?:\s+me)?\s+a\s+demo\s+of[:\s]+(.+)$", re.IGNORECASE | re.DOTALL)


def _slugify(text: str, *, fallback: str) -> str:
    # Canonical charset/case work lives in wiki.core._slugify; only the
    # truncation bound is local. The 40-char bound is a CROSS-MODULE contract:
    # directed_demo_builds and tutorial_demo_finalize must produce
    # byte-identical slugs for the same input, because vp/worker_loop.py's
    # legacy fallback recomputes a directed-demo dir name with the
    # proactive-lane function. Change both together or not at all.
    base = _base_slugify(str(text or ""), fallback=fallback)
    return base[:40].strip("-") or fallback


def directed_demo_slug(seed: str) -> str:
    """Deterministic on-disk slug for a directed demo repo, derived from the
    operator's seed. The build-time ``--slug directed-<slug>`` and the
    finalize-time dir resolution both use this value, so they stay aligned."""
    return _slugify(str(seed or ""), fallback="directed")


def _normalize_seed(seed: str) -> str:
    """Whitespace-collapsed, lowercased seed for the dedup key ONLY (the stored
    seed keeps its original casing/spacing)."""
    return " ".join(str(seed or "").split()).lower()


def directed_build_task_id(seed: str) -> str:
    """Deterministic dedup id: ``directed-build:sha256("directed:"+norm)[:16]``.

    Re-queueing the same seed upserts the same Task Hub row (idempotent intake).
    """
    digest = hashlib.sha256(
        ("directed:" + _normalize_seed(seed)).encode("utf-8")
    ).hexdigest()[:16]
    return f"directed-build:{digest}"


def is_directed_demo_command(text: str) -> bool:
    """True when the line is an explicit ``/demo`` command (with or without a
    seed argument) — ``/demonstrate`` and other ``/demo*`` words are NOT
    matched."""
    return bool(_CMD_DEMO_RE.match(str(text or "").strip()))


def parse_directed_demo_seed(text: str) -> Optional[str]:
    """Extract a directed-build seed from a chat/command line, or None.

    Recognizes ``/demo <seed>`` (and ``/demo@Bot <seed>``) and
    ``build [me] a demo of <seed>`` (case-insensitive). Pure + side-effect-free
    so the Telegram handler and its unit tests share one parser. Returns the
    seed text (stripped) or None when the line is not a directed-demo command
    OR is a bare ``/demo`` with no seed."""
    t = str(text or "").strip()
    if not t:
        return None
    m = _CMD_DEMO_RE.match(t)
    if m:
        seed = (m.group(1) or "").strip()
        return seed or None
    m = _NL_DEMO_RE.match(t)
    if m:
        seed = m.group(1).strip()
        return seed or None
    return None


def _build_directed_description(
    *,
    seed: str,
    seed_url: str,
    demo_id: str,
    build_slug: str,
    directed_slug: str,
    requested_by: str,
    channel: str,
) -> str:
    """The Cody mission objective for a directed build: the shared demo_factory
    override command + the binding DEMO_BUILD_CONTRACT."""
    # Imported lazily to avoid a heavy import at module load (this module is
    # imported by the gateway app and the Telegram bot).
    from universal_agent.services.proactive_tutorial_builds import (
        DEMO_BUILD_CONTRACT,
        _sanitize_one_line,
        build_demo_factory_command_line,
    )

    clean_seed = _sanitize_one_line(seed) or "the requested capability"
    if seed_url:
        prompt = f"Build a runnable demo of the capability at this source: {clean_seed}"
    else:
        prompt = clean_seed
    command = build_demo_factory_command_line(
        prompt=prompt,
        demo_id=demo_id,
        slug=build_slug,
        title=clean_seed,
        seed_url=seed_url,
    )
    return "\n".join(
        [
            "Cody should build a runnable demo of exactly what the operator "
            "directly asked for — a standalone mini-app that exercises the "
            "capability end-to-end against the real thing (never mocked).",
            "",
            f"Operator request (verbatim seed): {seed}",
            f"Source URL: {seed_url or '(none — seed is a text instruction)'}",
            f"Requested by: {requested_by or '(unknown)'} via {channel or '(unknown)'}",
            "",
            DEMO_BUILD_CONTRACT.rstrip(),
            "",
            "── DEMO ENGINE OVERRIDE: demo_factory (the /demo engine) ──",
            "- Build this demo with the demo_factory headless driver. The driver "
            "runs the full /goal build + verify + fidelity-eval on the good "
            "engine and LANDS the demo — vault entry + EXHIBIT + private GitHub "
            "backup — exactly like the operator's /demo.",
            "- From the workspace, run this SINGLE command (a full land, not a "
            "build-only pass). ``--cody-mode hybrid`` runs the build on "
            "Anthropic-Max for higher-quality code while verify/runtime "
            "inference runs on the ZAI/GLM coding-plan proxy; ``--video`` fires "
            "the ClearSpring explainer render after a passing land (degrades "
            "harmlessly if the toolchain is absent):",
            command,
            f"  → lands the repo at /home/ua/lrepos/demo-{_LANDED_PREFIX}-{directed_slug} "
            "(--skill-tier library graduates the skill into dragan-plugins as a "
            "zero-context /dragan:<name> library entry).",
            "- Do NOT fall back to a bespoke flow if the driver is missing — STOP "
            "and record it in BUILD_NOTES.md (demo_factory must be cloned on this "
            "host first).",
            "",
            "Dispatcher note (Simone): when delegating to Cody via "
            "vp_dispatch_mission, include this description VERBATIM in the "
            "mission objective — the Demo build contract above is binding.",
        ]
    )


def queue_directed_demo_build(
    seed: str,
    *,
    requested_by: str = "",
    channel: str = "",
    conn: Optional[sqlite3.Connection] = None,
) -> dict[str, Any]:
    """Queue an operator-directed demo build. Returns a result dict.

    ``result["status"]`` is one of:
      * ``"queued"``   — a new Task Hub row was created (or an existing one
        re-armed); ``result["task_id"]`` / ``result["slug"]`` are set.
      * ``"disabled"`` — the master flag ``UA_DIRECTED_DEMO_ENABLED`` is OFF; no
        task was created.
      * ``"invalid"``  — the seed was empty.

    Idempotent: the same seed maps to the same ``task_id`` (see
    :func:`directed_build_task_id`), so a duplicate request upserts the same row.
    There is NO buildability judge and NO preference gate — operator direction
    is the judgment, so the task is written straight to Task Hub as
    ``source_kind="directed_build"``, dispatch-eligible.

    When *conn* is None a runtime Task Hub connection is opened and closed here,
    so the function is safe to call from the gateway, the dashboard proxy, or the
    Telegram bot without threading a connection.
    """
    clean_seed = str(seed or "").strip()
    if not clean_seed:
        return {"status": "invalid", "reason": "empty_seed"}

    task_id = directed_build_task_id(clean_seed)
    slug = directed_demo_slug(clean_seed)

    if not directed_demo_enabled():
        logger.info(
            "directed_demo: intake refused (UA_DIRECTED_DEMO_ENABLED off) "
            "task_id=%s slug=%s", task_id, slug,
        )
        return {"status": "disabled", "task_id": task_id, "slug": slug}

    seed_url = clean_seed if _URL_RE.match(clean_seed) else ""
    demo_id = f"{_LANDED_PREFIX}-{slug}"
    build_slug = f"{_LANDED_PREFIX}-{slug}"  # build_demo.py --slug ⇒ demo-directed-<slug>

    description = _build_directed_description(
        seed=clean_seed,
        seed_url=seed_url,
        demo_id=demo_id,
        build_slug=build_slug,
        directed_slug=slug,
        requested_by=requested_by,
        channel=channel,
    )

    _own_conn = conn is None
    if _own_conn:
        from universal_agent.durable.db import (
            connect_runtime_db,
            get_activity_db_path,
        )

        conn = connect_runtime_db(get_activity_db_path())
    try:
        task_hub.ensure_schema(conn)
        # Direct upsert (NOT queue_proactive_task) so the preference/buildability
        # gate is bypassed — operator direction is the judgment. Fields mirror the
        # tutorial_build lane so the dispatcher/worker treat the two identically:
        # coder-lane routing, use_goal_loop, private repo.
        item = task_hub.upsert_item(
            conn,
            {
                "task_id": task_id,
                "source_kind": "directed_build",
                "source_ref": slug,
                "title": f"Directed demo build: {clean_seed[:80]}",
                "description": description,
                "project_key": "proactive",
                "priority": 2,
                "labels": [
                    task_hub.TASK_LABEL_AGENT_READY,
                    "directed-build",
                    "codie",
                    "code",
                ],
                "status": task_hub.TASK_STATUS_OPEN,
                "agent_ready": True,
                "trigger_type": "operator_directed",
                "metadata": {
                    "source": "directed_demo_build",
                    "requested_by": str(requested_by or ""),
                    "channel": str(channel or ""),
                    "use_goal_loop": True,
                    "directed_seed": clean_seed,
                    "directed_slug": slug,
                    "seed_url": seed_url,
                    "demo_id": demo_id,
                    "repo_visibility": "private",
                    "public_publication_allowed": False,
                    "workflow_manifest": {
                        "workflow_kind": "code_change",
                        "delivery_mode": "interactive_chat",
                        "requires_pdf": False,
                        "final_channel": "chat",
                        "canonical_executor": "simone_first",
                        "repo_mutation_allowed": True,
                    },
                },
            },
        )
    finally:
        if _own_conn:
            try:
                conn.close()
            except Exception:
                pass

    logger.info(
        "directed_demo: queued task_id=%s slug=%s requested_by=%s channel=%s url=%s",
        task_id, slug, requested_by, channel, bool(seed_url),
    )
    return {
        "status": "queued",
        "task_id": task_id,
        "slug": slug,
        "demo_id": demo_id,
        "title": str(item.get("title") or ""),
        "seed_url": seed_url,
    }

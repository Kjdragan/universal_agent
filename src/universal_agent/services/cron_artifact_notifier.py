"""Cron artifact disclosure notifier.

When a cron LLM run exits ``clean_exit_zero`` AND the cron is opted in
via ``metadata.notify_on_artifact == true``, this module:

  1. Scans the run workspace for artifacts (``manifest.json`` preferred;
     falls back to a recursive scan of ``work_products/``).
  2. Calls a single short Sonnet-tier LLM to produce a "why this matters"
     summary suitable for an email body.
  3. Upserts a row in ``proactive_artifacts`` (status=PRODUCED,
     delivery_state=NOT_SURFACED).
  4. Sends an initial email immediately via ``mail_service.send_email``
     (regardless of dormancy — operator chose this trade-off explicitly).
  5. Records the delivery via ``proactive_artifacts.record_email_delivery``
     and seeds reminder state in ``metadata_json.reminder`` so the
     ``cron_artifact_reminders`` sweep can fire the same-day nudge / Day-3
     / Day-7 follow-ups.

All operations are best-effort. A failure anywhere in this module must
never propagate back into ``cron_service.py`` and disrupt the cron's
own close-out / observability instrumentation.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
import sqlite3
import time
from typing import Any, Optional

from universal_agent.services import proactive_artifacts
from universal_agent.services.email_tags import ActionTag, KindTag

logger = logging.getLogger(__name__)


# ── Configuration ──────────────────────────────────────────────────────

DEFAULT_ARTIFACT_TYPE = "cron_run_output"
DEFAULT_SOURCE_KIND = "cron_artifact"

# Files to skip when falling back to a recursive scan of work_products/.
# Cron LLM runs ship a lot of internal scaffolding — the agent
# context-snapshot family (AGENTS.md, SOUL.md, IDENTITY.md, USER.md,
# capabilities.md, HEARTBEAT.md, BOOTSTRAP.md, TOOLS.md, ...), each emitted
# both bare and numbered (AGENTS_31.md), plus run bookkeeping (sync_ready_*,
# cron_result_*, run_manifest_*, context_brief_*, description_*). None of it
# is an "artifact" by any reasonable definition. Prefixes are matched bare
# (no trailing "_") so both ``AGENTS.md`` and ``AGENTS_31.md`` are skipped.
# Without this, ~40 numbered AGENTS_*.md scaffolding files (uppercase, so
# they sort first) exhaust the file cap before the scan ever descends into
# the skill's output subdir where the real deliverables live.
_SCAN_SKIP_NAME_PREFIXES = (
    "sync_ready",
    "BOOTSTRAP",
    "HEARTBEAT",
    "TOOLS",
    "AGENTS",
    "SOUL",
    "IDENTITY",
    "USER",
    "capabilities",
    "context_brief",
    "cron_result",
    "run_manifest",
    "description_",
    "_internal",
    ".",  # dotfiles
)
_SCAN_SKIP_NAMES = {
    "manifest.json",  # we read this separately and don't list it
    "run.log",
    "sync_ready.json",
}

# Recursive scan depth + per-dir file cap to keep email bodies tractable
# when a cron dumps hundreds of files.
_SCAN_MAX_DEPTH = 4
_SCAN_MAX_FILES = 25


# ── Cross-run coalescing config ─────────────────────────────────────────
# A cron that fails or partially-completes repeatedly (canonical case:
# paper_to_podcast hitting expired NotebookLM auth and being re-run) would
# otherwise mint a brand-new artifact + initial email + reminder cadence on
# EVERY run, because the artifact id is seeded on ``f"{job_id}:{int(started_at)}"``
# (a per-run epoch) so it never collides on upsert. Coalescing suppresses the
# duplicate email and refreshes the existing same-cron disclosure in place.
# Keyed on the STABLE cron task id (``cron:<system_job>``), NOT the per-run
# (often LLM-varied) title. Default ON; flip the env flag to disable.
_COALESCE_FLAG_ENV = "UA_CRON_ARTIFACT_COALESCE_SAME_DAY"
_DEDUP_WINDOW_ENV = "UA_CRON_ARTIFACT_DEDUP_WINDOW_HOURS"
_DEDUP_WINDOW_DEFAULT_HOURS = 24.0
# Statuses that still count as an "open" (unacknowledged) disclosure. Once
# Kevin accepts/rejects/archives a row, the next run is allowed to surface a
# fresh artifact so a recurrence is not silently hidden.
_OPEN_ARTIFACT_STATUSES = ("produced", "candidate", "surfaced")


def _coalesce_enabled() -> bool:
    return os.getenv(_COALESCE_FLAG_ENV, "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def _dedup_window_hours() -> float:
    raw = os.getenv(_DEDUP_WINDOW_ENV, "").strip()
    if not raw:
        return _DEDUP_WINDOW_DEFAULT_HOURS
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return _DEDUP_WINDOW_DEFAULT_HOURS
    return val if val > 0 else _DEDUP_WINDOW_DEFAULT_HOURS


def _find_recent_open_artifact(
    conn: sqlite3.Connection,
    *,
    source_kind: str,
    linked_task_id: str,
    job_id: str,
    window_hours: float,
) -> Optional[dict[str, Any]]:
    """Return the most-recent unacknowledged cron-disclosure artifact from the
    SAME cron (matched on the stable ``task_id``, falling back to ``job_id``)
    created within ``window_hours``, or ``None``.

    Used to coalesce repeated runs of one cron into a single disclosure
    instead of one-email-per-run. Best-effort: any DB error returns ``None``
    so the caller falls back to the normal create-and-email path.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=window_hours)).isoformat()
    placeholders = ",".join("?" for _ in _OPEN_ARTIFACT_STATUSES)
    try:
        rows = conn.execute(
            f"""
            SELECT artifact_id, metadata_json
            FROM proactive_artifacts
            WHERE source_kind = ?
              AND status IN ({placeholders})
              AND created_at >= ?
            ORDER BY created_at DESC
            """,
            (source_kind, *_OPEN_ARTIFACT_STATUSES, cutoff),
        ).fetchall()
    except sqlite3.Error:
        return None
    want_task = str(linked_task_id or "").strip()
    want_job = str(job_id or "").strip()
    for row in rows:
        try:
            meta = json.loads(row["metadata_json"] or "{}")
        except (json.JSONDecodeError, TypeError, KeyError):
            continue
        if not isinstance(meta, dict):
            continue
        meta_task = str(meta.get("task_id") or "").strip()
        meta_job = str(meta.get("job_id") or "").strip()
        if (want_task and meta_task == want_task) or (want_job and meta_job == want_job):
            return proactive_artifacts.get_artifact(conn, str(row["artifact_id"]))
    return None


def _refresh_coalesced_artifact(
    conn: sqlite3.Connection,
    *,
    existing: dict[str, Any],
    summary: str,
    artifacts_listing: list[dict[str, Any]],
    started_at: float,
    finished_at: float,
) -> None:
    """Refresh an existing same-cron artifact in place when a later run is
    coalesced onto it: update summary + latest artifacts listing + run
    counters, WITHOUT resetting status/delivery_state/created_at or the
    existing reminder cadence (which keeps progressing on the original row).
    """
    artifact_id = str(existing.get("artifact_id") or "").strip()
    if not artifact_id:
        return
    raw_meta = existing.get("metadata")
    meta = dict(raw_meta) if isinstance(raw_meta, dict) else {}
    meta["artifacts_listing"] = artifacts_listing
    meta["last_run_started_at_epoch"] = started_at
    meta["last_run_finished_at_epoch"] = finished_at
    meta["coalesced_run_count"] = int(meta.get("coalesced_run_count", 0) or 0) + 1
    now = datetime.now(timezone.utc).isoformat()
    try:
        conn.execute(
            "UPDATE proactive_artifacts SET summary = ?, metadata_json = ?, "
            "updated_at = ? WHERE artifact_id = ?",
            (str(summary or "").strip(), json.dumps(meta, default=str), now, artifact_id),
        )
        conn.commit()
    except sqlite3.Error as exc:  # noqa: BLE001 — best-effort
        logger.debug(
            "cron_artifact_notifier: coalesce refresh failed for %s: %s",
            artifact_id,
            exc,
        )


# ── Public API ─────────────────────────────────────────────────────────


async def notify_cron_artifact(
    *,
    conn: sqlite3.Connection,
    mail_service: Any,
    job_id: str,
    job_metadata: dict[str, Any],
    job_command: str,
    workspace_dir: Path,
    started_at: float,
    finished_at: float,
    recipient: str,
    dashboard_base_url: str = "",
) -> Optional[dict[str, Any]]:
    """Top-level entry. Returns the delivered artifact row, or None on
    opt-out / no artifacts / failure (all logged at debug)."""

    # Opt-in gate — default OFF. Cron skill must declare
    # ``metadata.notify_on_artifact: true`` to participate.
    notify_flag = bool((job_metadata or {}).get("notify_on_artifact"))
    if not notify_flag:
        return None

    try:
        manifest = _load_manifest(workspace_dir)
        artifacts_listing = _build_artifacts_listing(workspace_dir, manifest)
        if not artifacts_listing:
            logger.info(
                "cron_artifact_notifier: %s opted in but found no artifacts in %s",
                job_id,
                workspace_dir,
            )
            return None

        title, summary = await _compose_title_and_summary(
            job_id=job_id,
            job_command=job_command,
            manifest=manifest,
            artifacts_listing=artifacts_listing,
        )

        artifact_id = proactive_artifacts.make_artifact_id(
            source_kind=DEFAULT_SOURCE_KIND,
            source_ref=f"{job_id}:{int(started_at)}",
            artifact_type=DEFAULT_ARTIFACT_TYPE,
            title=title,
        )

        # Task Hub cross-reference. The F.1 close-out path creates a
        # ``cron:<system_job>`` row in ``task_hub_items`` via
        # ``derive_cron_task_id`` (cron_task_hub_link.py:82). Use that
        # same helper so our linkage matches — using ``cron:<job_id>``
        # (the hash) would point at a row that doesn't exist and the
        # Proactive Task History tab's cross-join would silently miss
        # the artifact.
        from universal_agent.services.cron_task_hub_link import (
            derive_cron_task_id,
        )
        _system_job = str((job_metadata or {}).get("system_job") or "").strip()
        linked_task_id = derive_cron_task_id(
            system_job=_system_job or None,
            job_id=job_id,
        ) or f"cron:{job_id}"

        proactive_artifacts.ensure_schema(conn)

        # ── Cross-run coalescing ───────────────────────────────────────
        # If an unacknowledged disclosure from this SAME cron already exists
        # within the dedup window, refresh it in place and suppress the
        # duplicate email + reminder. Stops the inbox flood when a cron is
        # re-run repeatedly against the same blocker (e.g. paper_to_podcast
        # vs expired NotebookLM auth). Matched on the stable task_id, not the
        # per-run title.
        if _coalesce_enabled():
            existing = _find_recent_open_artifact(
                conn,
                source_kind=DEFAULT_SOURCE_KIND,
                linked_task_id=linked_task_id,
                job_id=job_id,
                window_hours=_dedup_window_hours(),
            )
            if existing is not None:
                _refresh_coalesced_artifact(
                    conn,
                    existing=existing,
                    summary=summary,
                    artifacts_listing=artifacts_listing,
                    started_at=started_at,
                    finished_at=finished_at,
                )
                logger.info(
                    "cron_artifact_notifier: coalesced job %s onto existing "
                    "artifact %s (task_id=%s, within %.0fh) — suppressing "
                    "duplicate email/reminder",
                    job_id,
                    existing.get("artifact_id"),
                    linked_task_id,
                    _dedup_window_hours(),
                )
                return proactive_artifacts.get_artifact(
                    conn, str(existing.get("artifact_id") or "")
                )

        artifact = proactive_artifacts.upsert_artifact(
            conn,
            artifact_id=artifact_id,
            artifact_type=DEFAULT_ARTIFACT_TYPE,
            source_kind=DEFAULT_SOURCE_KIND,
            source_ref=f"{job_id}:{int(started_at)}",
            title=title,
            summary=summary,
            status=proactive_artifacts.ARTIFACT_STATUS_PRODUCED,
            delivery_state=proactive_artifacts.DELIVERY_NOT_SURFACED,
            artifact_path=str(workspace_dir),
            metadata={
                "job_id": job_id,
                "task_id": linked_task_id,
                "started_at_epoch": started_at,
                "finished_at_epoch": finished_at,
                "workspace_dir": str(workspace_dir),
                "artifacts_listing": artifacts_listing,
                "reminder": _seed_reminder_state(finished_at),
            },
        )

        # Promote the cron's Task Hub row from ``project_key='immediate'``
        # to ``project_key='proactive'`` so the Proactive Task History
        # tab's WHERE clause picks it up. Bounded scope: only opted-in
        # crons (this code path requires notify_on_artifact=true) get
        # promoted, so non-disclosure crons stay out of the proactive
        # surface. Best-effort UPDATE; missing row is a no-op.
        try:
            conn.execute(
                "UPDATE task_hub_items SET project_key = 'proactive', "
                "updated_at = ? WHERE task_id = ? AND project_key != 'proactive'",
                (datetime.now(timezone.utc).isoformat(), linked_task_id),
            )
            conn.commit()
        except Exception as exc:  # noqa: BLE001 — best-effort
            logger.debug(
                "cron_artifact_notifier: project_key promotion skipped for %s: %s",
                linked_task_id,
                exc,
            )

        ack_url = _build_ack_url(artifact_id, dashboard_base_url)
        subject, text_body, html_body = _compose_initial_email(
            job_id=job_id,
            artifact=artifact,
            artifacts_listing=artifacts_listing,
            ack_url=ack_url,
            dashboard_base_url=dashboard_base_url,
        )

        result = await mail_service.send_email(
            to=recipient,
            subject=subject,
            text=text_body,
            html=html_body,
            force_send=True,
            require_approval=False,
            action=ActionTag.FYI,
            kind=KindTag.PROACTIVE,
            source="cron_artifact_notifier.notify_cron_artifact",
            related=[f"artifact_id={artifact_id}", f"job_id={job_id}"],
        )

        proactive_artifacts.record_email_delivery(
            conn,
            artifact_id=artifact_id,
            message_id=str((result or {}).get("message_id") or ""),
            thread_id=str((result or {}).get("thread_id") or ""),
            subject=subject,
            recipient=recipient,
            metadata={
                "kind": "initial",
                "mail_status": str((result or {}).get("status") or ""),
            },
        )
        logger.info(
            "cron_artifact_notifier: initial email sent for job %s, artifact %s, recipient %s",
            job_id,
            artifact_id,
            recipient,
        )
        return proactive_artifacts.get_artifact(conn, artifact_id)
    except Exception as exc:  # noqa: BLE001 — best-effort; never disrupt cron
        logger.warning(
            "cron_artifact_notifier: failed for job %s: %s",
            job_id,
            exc,
            exc_info=True,
        )
        return None


def notify_cron_artifact_fire_and_forget(
    *,
    conn: sqlite3.Connection,
    mail_service: Any,
    job_id: str,
    job_metadata: dict[str, Any],
    job_command: str,
    workspace_dir: Path,
    started_at: float,
    finished_at: float,
    recipient: str,
    dashboard_base_url: str = "",
) -> None:
    """Schedule ``notify_cron_artifact`` on the running event loop without
    waiting for completion. Safe to call from synchronous contexts."""

    async def _wrapper() -> None:
        try:
            await notify_cron_artifact(
                conn=conn,
                mail_service=mail_service,
                job_id=job_id,
                job_metadata=job_metadata,
                job_command=job_command,
                workspace_dir=workspace_dir,
                started_at=started_at,
                finished_at=finished_at,
                recipient=recipient,
                dashboard_base_url=dashboard_base_url,
            )
        except Exception:  # noqa: BLE001 — already logged inside
            pass

    try:
        asyncio.get_running_loop().create_task(_wrapper())
    except RuntimeError:
        # No event loop — best-effort run-then-return.
        try:
            asyncio.run(_wrapper())
        except Exception:  # noqa: BLE001
            logger.debug(
                "cron_artifact_notifier: fire-and-forget could not schedule for job %s",
                job_id,
            )


# ── Artifact discovery ─────────────────────────────────────────────────


def _load_manifest(workspace_dir: Path) -> Optional[dict[str, Any]]:
    """Load the most recent ``manifest*.json`` from the workspace (root
    or ``work_products/<skill>/``).

    Cron workspaces are reused across runs and accumulate one manifest
    per run. Some pipelines (e.g. paper-to-podcast) write a date-stamped
    copy each run — ``manifest_20260531.json`` — *without* overwriting
    the canonical ``manifest.json``, so the undated file stays frozen at
    a prior run's content. Returning the first literal ``manifest.json``
    therefore discloses yesterday's topic/artifacts for today's run.

    We instead collect every ``manifest*.json`` (date-stamped included)
    and pick the newest by mtime, so the disclosure always reflects the
    run that just finished. Older manifests are only consulted if the
    newest fails to parse.
    """
    work_products = workspace_dir / "work_products"
    seen: set[Path] = set()
    candidates: list[Path] = []
    for root, pattern in (
        (workspace_dir, "manifest*.json"),
        (work_products, "manifest*.json"),
        (work_products, "*/manifest*.json"),
    ):
        try:
            for path in root.glob(pattern):
                if path.is_file() and path not in seen:
                    seen.add(path)
                    candidates.append(path)
        except OSError:
            continue

    # Newest run wins; fall through to older manifests only on parse
    # failure. stat() is taken once up front so a vanished file can't
    # crash the sort.
    scored: list[tuple[float, Path]] = []
    for path in candidates:
        try:
            scored.append((path.stat().st_mtime, path))
        except OSError:
            continue
    scored.sort(key=lambda t: t[0], reverse=True)

    for _, path in scored:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (OSError, json.JSONDecodeError):
            continue
    return None


def _build_artifacts_listing(
    workspace_dir: Path, manifest: Optional[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Return a list of ``{title, path, kind?}`` dicts.

    Prefers ``manifest.json`` entries; falls back to scanning
    ``work_products/`` for files.
    """
    items: list[dict[str, Any]] = []
    seen_paths: set[str] = set()

    if manifest:
        manifest_items = _extract_manifest_items(manifest)
        for item in manifest_items:
            path = str(item.get("path") or "").strip()
            if path and path not in seen_paths:
                seen_paths.add(path)
                items.append(item)

    if not items:
        items.extend(_scan_work_products(workspace_dir))

    return items[:_SCAN_MAX_FILES]


def _extract_manifest_items(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """Read a heterogeneous manifest shape into a flat artifacts list.

    Tolerates: ``manifest["artifacts"] = [...]``, ``manifest["outputs"] = [...]``,
    ``manifest["artifacts"] = {name: descriptor, ...}``, or a top-level dict
    where each value is an artifact descriptor.
    """
    for key in ("artifacts", "outputs", "files", "items"):
        raw = manifest.get(key)
        if isinstance(raw, list):
            return [_coerce_item(entry) for entry in raw if entry]
        # A dict-of-descriptors keyed by artifact name, e.g. the
        # paper_to_podcast manifest's
        # ``{"podcast": {...}, "quiz": {...}, "flashcards": {...}}``.
        if isinstance(raw, dict) and raw:
            return [
                _coerce_item({"title": name, **descriptor})
                if isinstance(descriptor, dict)
                else _coerce_item(descriptor)
                for name, descriptor in raw.items()
                if descriptor
            ]
    # Top-level dict of {name: descriptor}.
    if all(isinstance(v, dict) for v in manifest.values()):
        return [
            _coerce_item({"title": k, **v}) for k, v in manifest.items() if v
        ]
    return []


def _coerce_item(raw: Any) -> dict[str, Any]:
    if isinstance(raw, str):
        return {"title": Path(raw).name, "path": raw}
    if isinstance(raw, dict):
        title = str(raw.get("title") or raw.get("name") or "").strip()
        path = str(raw.get("path") or raw.get("file") or raw.get("uri") or "").strip()
        kind = str(raw.get("kind") or raw.get("type") or "").strip()
        if not title and path:
            title = Path(path).name
        out = {"title": title or "(unnamed)", "path": path}
        if kind:
            out["kind"] = kind
        return out
    return {"title": str(raw), "path": ""}


def _scan_work_products(workspace_dir: Path) -> list[dict[str, Any]]:
    """Recursively list files under ``work_products/``, skipping the
    cron-scaffolding noise (sync_ready_*.json, BOOTSTRAP_*.md, etc.)."""
    work_products = workspace_dir / "work_products"
    if not work_products.exists() or not work_products.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for path in sorted(work_products.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(workspace_dir)
        depth = len(rel.parts)
        if depth > _SCAN_MAX_DEPTH:
            continue
        name = path.name
        if name in _SCAN_SKIP_NAMES:
            continue
        if any(name.startswith(p) for p in _SCAN_SKIP_NAME_PREFIXES):
            continue
        items.append({"title": name, "path": str(rel)})
        if len(items) >= _SCAN_MAX_FILES:
            break
    return items


# ── LLM summary ────────────────────────────────────────────────────────


async def _compose_title_and_summary(
    *,
    job_id: str,
    job_command: str,
    manifest: Optional[dict[str, Any]],
    artifacts_listing: list[dict[str, Any]],
) -> tuple[str, str]:
    """Return ``(title, summary)`` describing what was made.

    Single Sonnet-tier call. Falls back to a deterministic structured
    string if the LLM is unavailable or env flag disables LLM.
    """
    fallback_title = _fallback_title(job_id, manifest, artifacts_listing)
    fallback_summary = _fallback_summary(job_command, artifacts_listing)

    if (os.getenv("UA_CRON_ARTIFACT_LLM_SUMMARY") or "1").strip().lower() in {
        "0",
        "false",
        "no",
        "off",
    }:
        return fallback_title, fallback_summary

    try:
        from universal_agent.services.llm_classifier import (
            _call_llm,  # noqa: PLC2701 — reuse the project's ZAI/Anthropic helper
        )
        from universal_agent.utils.model_resolution import resolve_sonnet

        prompt = _build_llm_prompt(job_id, job_command, manifest, artifacts_listing)
        raw = await _call_llm(
            system=(
                "You are Simone summarizing a cron job's output for the operator. "
                "Output STRICT JSON with two fields: "
                '"title" (concise headline, <80 chars, no quotes) and '
                '"summary" (2-4 sentences explaining what was produced and why '
                "it might matter, no markdown). Never apologize or hedge. "
                "If the artifacts look routine, say so plainly."
            ),
            user=prompt,
            model=resolve_sonnet(),
            max_tokens=400,
        )
        parsed = _parse_llm_json(raw)
        title = str(parsed.get("title") or "").strip() or fallback_title
        summary = str(parsed.get("summary") or "").strip() or fallback_summary
        return title[:160], summary[:1200]
    except Exception as exc:  # noqa: BLE001 — fall back silently
        logger.debug(
            "cron_artifact_notifier: LLM summary failed for %s (%s); using fallback",
            job_id,
            exc,
        )
        return fallback_title, fallback_summary


def _build_llm_prompt(
    job_id: str,
    job_command: str,
    manifest: Optional[dict[str, Any]],
    artifacts_listing: list[dict[str, Any]],
) -> str:
    lines = [
        f"Cron job: {job_id}",
        "",
        "Cron prompt (the work the agent was told to do):",
        (job_command or "")[:1500],
        "",
        f"Artifacts produced ({len(artifacts_listing)}):",
    ]
    for item in artifacts_listing[:15]:
        title = str(item.get("title") or "")
        path = str(item.get("path") or "")
        kind = str(item.get("kind") or "")
        lines.append(f"  - {title}  ({kind or 'file'})  {path}")
    if manifest:
        lines.append("")
        lines.append("Manifest excerpt:")
        lines.append(json.dumps(manifest, indent=2)[:1200])
    return "\n".join(lines)


def _parse_llm_json(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    # Tolerate markdown code-fence wrapping.
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    text = text.strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    return {}


def _fallback_title(
    job_id: str,
    manifest: Optional[dict[str, Any]],
    artifacts_listing: list[dict[str, Any]],
) -> str:
    if manifest:
        m_title = str(manifest.get("title") or manifest.get("topic") or "").strip()
        if m_title:
            return f"{job_id}: {m_title}"[:160]
    return f"{job_id}: {len(artifacts_listing)} artifact(s) produced"[:160]


def _fallback_summary(
    job_command: str,
    artifacts_listing: list[dict[str, Any]],
) -> str:
    head = (job_command or "").strip().splitlines()[:2]
    head_text = " ".join(s.strip() for s in head)[:200]
    count = len(artifacts_listing)
    return (
        f"Cron run produced {count} artifact(s). "
        f"Task: {head_text} "
        "See the attached listing or open the workspace for details."
    )[:1200]


# ── Reminder state ─────────────────────────────────────────────────────


def _seed_reminder_state(finished_at_epoch: float) -> dict[str, Any]:
    """Seed reminder cadence state stored in ``metadata_json.reminder``.

    Cadence (driven by ``cron_artifact_reminders.py`` sweep):
      - T+0:    initial email (this module sends it; sets count=1)
      - T+4h:   same-day nudge (count→2)
      - T+72h:  Day-3 reminder (count→3)
      - T+168h: Day-7 reminder (count→4)
      - Then:   stop
    """
    now = time.time()
    return {
        "count": 1,  # initial counts
        "schedule_state": "sent_initial",
        "next_reminder_at_epoch": finished_at_epoch + 4 * 3600,
        "last_sent_at_epoch": now,
        "stopped": False,
    }


# ── Email composition ──────────────────────────────────────────────────


def _compose_initial_email(
    *,
    job_id: str,
    artifact: dict[str, Any],
    artifacts_listing: list[dict[str, Any]],
    ack_url: str,
    dashboard_base_url: str,
) -> tuple[str, str, str]:
    """Return ``(subject, text_body, html_body)``."""
    title = str(artifact.get("title") or job_id).strip()
    summary = str(artifact.get("summary") or "").strip()
    artifact_id = str(artifact.get("artifact_id") or "").strip()
    workspace = str(artifact.get("artifact_path") or "").strip()

    subject_prefix = f"[{job_id}]"
    subject = f"{subject_prefix} {title}"[:200]

    # Plain-text rendering for safe transport. HTML mirrors content.
    text_lines = [
        f"Cron job '{job_id}' produced {len(artifacts_listing)} artifact(s).",
        "",
        summary,
        "",
        "Artifacts:",
    ]
    for item in artifacts_listing:
        t = str(item.get("title") or "")
        p = str(item.get("path") or "")
        text_lines.append(f"  - {t}  {p}".rstrip())
    text_lines.extend([
        "",
        f"Workspace: {workspace}",
    ])
    if ack_url:
        text_lines.append(f"Acknowledge: {ack_url}")
    if dashboard_base_url and artifact_id:
        text_lines.append(
            f"Dashboard: {dashboard_base_url.rstrip('/')}/dashboard/todolist?artifact={artifact_id}"
        )
    text_body = "\n".join(text_lines)

    # Minimal HTML — AgentMail handles richer renderers itself.
    items_html = "".join(
        f"<li><strong>{_escape(str(item.get('title') or ''))}</strong>"
        f" &mdash; <code>{_escape(str(item.get('path') or ''))}</code></li>"
        for item in artifacts_listing
    )
    ack_block = (
        f'<p><a href="{_escape(ack_url)}" '
        'style="background:#0a7;color:#fff;padding:8px 16px;'
        'text-decoration:none;border-radius:4px;">Acknowledge</a></p>'
        if ack_url
        else ""
    )
    dashboard_block = (
        f'<p>Open in dashboard: '
        f'<a href="{_escape(dashboard_base_url.rstrip("/"))}/dashboard/todolist?artifact={_escape(artifact_id)}">'
        "Task Hub</a></p>"
        if dashboard_base_url and artifact_id
        else ""
    )
    html_body = (
        f"<p>Cron job <code>{_escape(job_id)}</code> produced "
        f"{len(artifacts_listing)} artifact(s).</p>"
        f"<p>{_escape(summary)}</p>"
        f"<ul>{items_html}</ul>"
        f"<p>Workspace: <code>{_escape(workspace)}</code></p>"
        f"{ack_block}"
        f"{dashboard_block}"
    )
    return subject, text_body, html_body


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


# ── Ack URL signing ────────────────────────────────────────────────────


def _ack_secret() -> bytes:
    raw = (
        os.getenv("UA_ARTIFACT_ACK_SECRET")
        or os.getenv("UA_OPS_TOKEN")
        or os.getenv("UA_INTERNAL_API_TOKEN")
        or ""
    ).strip()
    if not raw:
        return b""
    return raw.encode("utf-8")


def sign_ack_token(artifact_id: str) -> str:
    """HMAC-SHA256(secret, artifact_id) truncated to 16 hex chars.

    Public so the gateway endpoint and tests share the exact algorithm.
    """
    secret = _ack_secret()
    if not secret:
        return ""
    mac = hmac.new(secret, artifact_id.encode("utf-8"), hashlib.sha256)
    return mac.hexdigest()[:16]


def verify_ack_token(artifact_id: str, token: str) -> bool:
    """Verify an HMAC acknowledgement token for a proactive artifact."""
    expected = sign_ack_token(artifact_id)
    if not expected or not token:
        return False
    return hmac.compare_digest(expected, token.strip())


def _build_ack_url(artifact_id: str, dashboard_base_url: str) -> str:
    # Prefer FRONTEND_URL — that's the canonical operator-facing base in
    # ``intelligence_reporter.py`` and ``link_notifier.py``. Fall back to
    # UA_PUBLIC_BASE_URL for parity with other proactive surfaces. Final
    # fallback is the ClearSpring app domain.
    base = (
        dashboard_base_url
        or os.getenv("FRONTEND_URL", "")
        or os.getenv("UA_PUBLIC_BASE_URL", "")
        or "https://app.clearspringcg.com"
    ).strip().rstrip("/")
    if not base:
        return ""
    token = sign_ack_token(artifact_id)
    if not token:
        return ""
    return f"{base}/api/v1/artifacts/{artifact_id}/ack?t={token}"


# ── Feedback / digest-pause HMAC helpers (PR B insight pipeline) ───────
#
# These reuse the same secret material as sign_ack_token /
# verify_ack_token so we never introduce a new env var or rotation
# surface.  They sign different payloads (artifact_id:vote vs hours)
# so an ack token cannot be replayed as a feedback token, and vice
# versa.


def _feedback_payload(artifact_id: str, vote: str) -> bytes:
    return f"{artifact_id}:{vote}".encode("utf-8")


def sign_feedback_token(artifact_id: str, vote: str) -> str:
    """HMAC-SHA256 over f"{artifact_id}:{vote}", truncated to 16 hex.

    vote must be "up" or "down" — anything else returns the empty
    string so the token cannot accidentally validate against a foreign
    vote shape.
    """
    secret = _ack_secret()
    if not secret:
        return ""
    clean_vote = (vote or "").strip().lower()
    if clean_vote not in {"up", "down"}:
        return ""
    clean_id = (artifact_id or "").strip()
    if not clean_id:
        return ""
    mac = hmac.new(secret, _feedback_payload(clean_id, clean_vote), hashlib.sha256)
    return mac.hexdigest()[:16]


def verify_feedback_token(artifact_id: str, vote: str, token: str) -> bool:
    """Constant-time HMAC verification.  False on any error."""
    expected = sign_feedback_token(artifact_id, vote)
    if not expected:
        return False
    supplied = (token or "").strip()
    if not supplied:
        return False
    return hmac.compare_digest(expected, supplied)


def _digest_pause_payload(hours: int) -> bytes:
    return f"digest_pause:{int(hours)}".encode("utf-8")


def sign_digest_pause_token(hours: int) -> str:
    """HMAC-SHA256 over f"digest_pause:{hours}" truncated to 16 hex."""
    secret = _ack_secret()
    if not secret:
        return ""
    try:
        clean_hours = int(hours)
    except (TypeError, ValueError):
        return ""
    if clean_hours <= 0:
        return ""
    mac = hmac.new(secret, _digest_pause_payload(clean_hours), hashlib.sha256)
    return mac.hexdigest()[:16]


def verify_digest_pause_token(hours: int, token: str) -> bool:
    """Constant-time HMAC verification.  False on any error."""
    expected = sign_digest_pause_token(hours)
    if not expected:
        return False
    supplied = (token or "").strip()
    if not supplied:
        return False
    return hmac.compare_digest(expected, supplied)


# ── Digest pause state helpers (small runtime-state table) ─────────────


def is_digest_paused(conn: sqlite3.Connection) -> bool:
    """True iff digest_state.paused_until is a future timestamp."""
    try:
        row = conn.execute(
            "SELECT paused_until FROM digest_state WHERE id = 1"
        ).fetchone()
    except sqlite3.OperationalError:
        return False
    if not row:
        return False
    try:
        paused_until = row["paused_until"] if hasattr(row, "keys") else row[0]
    except (KeyError, IndexError):
        return False
    text = (paused_until or "").strip()
    if not text:
        return False
    try:
        from datetime import datetime as _dt, timezone as _tz
        cleaned = text[:-1] + "+00:00" if text.endswith("Z") else text
        dt = _dt.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_tz.utc)
        return dt > _dt.now(_tz.utc)
    except (ValueError, TypeError):
        return False


def set_digest_pause(conn: sqlite3.Connection, paused_until_iso: str) -> None:
    """UPSERT digest_state.paused_until with the supplied ISO timestamp.

    Caller is responsible for ensuring the schema exists (the gateway runs
    _ensure_digest_state_schema at startup).  This helper is a thin
    write — no validation of the timestamp shape.
    """
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO digest_state (id, paused_until, updated_at)
        VALUES (1, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            paused_until = excluded.paused_until,
            updated_at = excluded.updated_at
        """,
        (str(paused_until_iso or "").strip(), now),
    )
    conn.commit()


__all__ = [
    "is_digest_paused",
    "notify_cron_artifact",
    "notify_cron_artifact_fire_and_forget",
    "set_digest_pause",
    "sign_ack_token",
    "sign_digest_pause_token",
    "sign_feedback_token",
    "verify_ack_token",
    "verify_digest_pause_token",
    "verify_feedback_token",
]

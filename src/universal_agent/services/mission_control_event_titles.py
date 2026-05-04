"""Mission Control — smart event titles + visibility filter (Phase 7).

Replaces the noisy `/dashboard/events` titles ("Autonomous Task Completed",
"Mission Completed", repeated per heartbeat tick) with informative,
metadata-driven titles, and applies a smart-default filter that hides
routine green/info noise unless the operator explicitly toggles "Show
All".

Title-template strategy
=======================
Per the Phase 7 design:
  - Each `(event_kind, metadata_shape_signature)` pair gets ONE
    LLM-generated Jinja-style template (e.g.
    "Cron Complete · {job_id} · {duration_seconds}s · {status}").
  - The template lives in the `event_title_templates` table created
    in Phase 0.
  - Subsequent events of the same shape use the cached template
    deterministically — zero LLM cost.
  - Weekly re-validation can refresh templates whose metadata shape
    has evolved.

Smart-filter strategy
=====================
Code-side rules decide whether to flag an event as `hide_by_default`
based on its `(severity, source_domain, kind, metadata)` shape. The
endpoint surfaces the flag; the frontend uses it to hide routine
events unless the user toggles "Show All". Hidden events are NEVER
deleted — they remain queryable, just default-collapsed.

See docs/02_Subsystems/Mission_Control_Intelligence_System.md §8.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import logging
import os
import re
import sqlite3
from typing import Any

logger = logging.getLogger(__name__)


# ── Metadata shape signatures ───────────────────────────────────────────


def _type_marker(value: Any) -> str:
    """Single-char classifier for the value's type — used in shape sigs."""
    if value is None:
        return "n"
    if isinstance(value, bool):
        return "b"
    if isinstance(value, int):
        return "i"
    if isinstance(value, float):
        return "f"
    if isinstance(value, str):
        return "s"
    if isinstance(value, list):
        return "L"
    if isinstance(value, dict):
        return "D"
    return "?"


def metadata_shape_signature(metadata: Any) -> str:
    """Hash of the metadata's KEY+TYPE structure.

    Two events with the same kind and metadata-shape get the same
    signature, regardless of values. This is what makes template
    caching effective: the structure is the cache key, not the data.
    """
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except (TypeError, json.JSONDecodeError):
            metadata = {}
    if not isinstance(metadata, dict):
        return "shape:non-dict"
    parts: list[str] = []
    for key in sorted(metadata.keys()):
        parts.append(f"{key}:{_type_marker(metadata[key])}")
    payload = "|".join(parts)
    return "shape:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


# ── Template apply (Jinja-lite renderer) ────────────────────────────────


_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z0-9_\.]+)\}")


def apply_template(template: str, event: dict[str, Any]) -> str:
    """Render a stored template against an event.

    Uses a deliberately simple `{key}` substitution rather than full
    Jinja so we don't introduce a templating-library dependency or
    a code-injection surface from LLM output. Supported placeholders:
      - {kind}, {source_domain}, {severity}, {status}, {session_id}
      - {metadata.<key>} where <key> is a top-level metadata field
    Unknown placeholders render literally as `?`. Non-string values
    are str()-ified.
    """
    if not template:
        return ""
    metadata_raw = event.get("metadata") or event.get("metadata_json") or {}
    if isinstance(metadata_raw, str):
        try:
            metadata = json.loads(metadata_raw)
        except (TypeError, json.JSONDecodeError):
            metadata = {}
    else:
        metadata = metadata_raw if isinstance(metadata_raw, dict) else {}

    def _sub(match: "re.Match[str]") -> str:
        key = match.group(1)
        if key.startswith("metadata."):
            sub = key.split(".", 1)[1]
            return str(metadata.get(sub, "?"))
        # Top-level event field
        return str(event.get(key, "?"))

    return _PLACEHOLDER_RE.sub(_sub, template).strip()


# ── Template cache ──────────────────────────────────────────────────────


def _template_id(event_kind: str, shape_sig: str) -> str:
    return "tpl_" + hashlib.sha256(f"{event_kind}|{shape_sig}".encode("utf-8")).hexdigest()[:24]


def get_cached_template(
    conn: sqlite3.Connection, event_kind: str, shape_sig: str
) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT * FROM event_title_templates
        WHERE event_kind = ? AND metadata_shape_signature = ?
        """,
        (event_kind, shape_sig),
    ).fetchone()
    return dict(row) if row else None


def store_template(
    conn: sqlite3.Connection,
    *,
    event_kind: str,
    shape_sig: str,
    title_template: str,
    generated_by_model: str | None = None,
) -> dict[str, Any]:
    """Upsert a (kind, shape) → template entry."""
    now = datetime.now(timezone.utc).isoformat()
    template_id = _template_id(event_kind, shape_sig)
    conn.execute(
        """
        INSERT INTO event_title_templates
            (template_id, event_kind, metadata_shape_signature,
             title_template, generated_by_model, generated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(event_kind, metadata_shape_signature) DO UPDATE SET
            title_template = excluded.title_template,
            generated_by_model = excluded.generated_by_model,
            generated_at = excluded.generated_at
        """,
        (template_id, event_kind, shape_sig, title_template,
         generated_by_model, now),
    )
    return {
        "template_id": template_id,
        "event_kind": event_kind,
        "metadata_shape_signature": shape_sig,
        "title_template": title_template,
        "generated_by_model": generated_by_model,
        "generated_at": now,
    }


# ── LLM template generation ─────────────────────────────────────────────


_TEMPLATE_PROMPT = """\
You are designing a one-line title template for an operator dashboard.

Input: a single event from a Universal Agent activity stream. Your task
is to design a SHORT, INFORMATIVE Jinja-style template that operators
can scan in <2 seconds and understand what happened.

Rules:
  - Output ONLY the template string — no explanation, no JSON wrapping.
  - Length: aim for 40-90 characters when rendered.
  - Use `{key}` placeholders for top-level event fields:
    {kind}, {source_domain}, {severity}, {status}, {session_id}
  - Use `{metadata.<key>}` for metadata fields: e.g. {metadata.job_id},
    {metadata.run_id}, {metadata.duration_seconds}.
  - Lead with the most informative metadata field, NOT the source_domain.
  - Use middle-dot " · " as separator.
  - Do NOT include "Autonomous", "Activity", or other generic boilerplate.
  - Prefer concrete identifiers (job_id, task_id, run_id, count) over
    status words.
  - If the event is genuinely informational with no useful metadata,
    a short noun phrase is fine (e.g. "Heartbeat tick").

Examples (for reference, do NOT copy verbatim):
  Kind=cron_run_completed, metadata={{job_id, duration_seconds, status}}
  → "Cron Complete · {metadata.job_id} · {metadata.duration_seconds}s · {metadata.status}"

  Kind=mission_complete, metadata={{session_id, duration_seconds, ...}}
  → "Mission Complete · {metadata.session_id} · {metadata.duration_seconds}s"

  Kind=heartbeat_investigation_completed, metadata={{finding, session_id}}
  → "Heartbeat Investigation · {metadata.finding}"

Now generate a template for this event:
"""


async def generate_template_via_llm(
    sample_event: dict[str, Any],
) -> tuple[str, str | None]:
    """Call the dedicated glm-4.7 lane to design a title template for
    this event's (kind, metadata_shape) pair.

    Returns (template_string, model_used). On failure returns a
    sensible fallback template + None for the model.
    """
    kind = str(sample_event.get("kind") or "").strip() or "unknown"
    fallback = _fallback_template(sample_event)

    api_key = (
        os.getenv("ANTHROPIC_API_KEY")
        or os.getenv("ANTHROPIC_AUTH_TOKEN")
        or os.getenv("ZAI_API_KEY")
    )
    if not api_key:
        return fallback, None

    try:
        from anthropic import AsyncAnthropic
    except Exception as exc:
        logger.info("anthropic SDK unavailable for title-template generation: %s", exc)
        return fallback, None

    from universal_agent.utils.model_resolution import (
        mission_control_call_timeout_seconds,
        resolve_mission_control_model,
    )

    model = resolve_mission_control_model()
    client_kwargs: dict[str, Any] = {
        "api_key": api_key,
        "max_retries": 0,
        "timeout": mission_control_call_timeout_seconds(),
    }
    base_url = os.getenv("ANTHROPIC_BASE_URL")
    if base_url:
        client_kwargs["base_url"] = base_url
    elif os.getenv("ZAI_API_KEY") and api_key == os.getenv("ZAI_API_KEY"):
        client_kwargs["base_url"] = "https://api.z.ai/api/anthropic"

    client = AsyncAnthropic(**client_kwargs)
    prompt = _TEMPLATE_PROMPT + json.dumps({
        "kind": sample_event.get("kind"),
        "source_domain": sample_event.get("source_domain"),
        "severity": sample_event.get("severity"),
        "status": sample_event.get("status"),
        "title": sample_event.get("title"),
        "summary": sample_event.get("summary"),
        "metadata_keys_and_types": _metadata_shape_summary(sample_event.get("metadata") or {}),
        "metadata_sample": sample_event.get("metadata") or {},
    }, indent=2, default=str)

    try:
        response = await client.messages.create(
            model=model,
            max_tokens=200,  # template is short — don't burn budget
            temperature=0.2,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:
        logger.warning("title-template LLM call failed for kind=%s: %s", kind, exc)
        return fallback, model

    text = "".join(getattr(b, "text", "") for b in response.content).strip()
    template = _sanitize_template(text)
    if not template or "{" not in template:
        return fallback, model
    return template, model


def _fallback_template(event: dict[str, Any]) -> str:
    """Best-effort code-only template for when the LLM is unavailable.

    Used on first encounter with a (kind, shape) pair if the LLM call
    fails; the cached entry can be re-generated later when the lane
    recovers.
    """
    kind = str(event.get("kind") or "?").strip()
    pretty_kind = kind.replace("_", " ").title()
    metadata = event.get("metadata") or {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except (TypeError, json.JSONDecodeError):
            metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}
    parts = [pretty_kind]
    for k in ("job_id", "task_id", "run_id", "session_id"):
        if k in metadata:
            parts.append("{metadata." + k + "}")
            break
    for k in ("status", "duration_seconds"):
        if k in metadata:
            suffix = "{metadata." + k + "}"
            if k == "duration_seconds":
                suffix += "s"
            parts.append(suffix)
    return " · ".join(parts)


def _metadata_shape_summary(metadata: Any) -> dict[str, str]:
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except (TypeError, json.JSONDecodeError):
            metadata = {}
    if not isinstance(metadata, dict):
        return {}
    return {k: type(v).__name__ for k, v in metadata.items()}


def _sanitize_template(text: str) -> str:
    """Strip code-fences, line wrapping, and any sentence wrappers the
    LLM might have included despite instructions."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        # Strip leading + trailing fence
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith(("jinja", "text", "template")):
            cleaned = cleaned.split("\n", 1)[-1]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()
    # Take just the first line if the LLM emitted multiple
    cleaned = cleaned.split("\n", 1)[0].strip()
    # Strip enclosing quotes
    if (cleaned.startswith('"') and cleaned.endswith('"')) or \
       (cleaned.startswith("'") and cleaned.endswith("'")):
        cleaned = cleaned[1:-1].strip()
    return cleaned


# ── Smart filter logic (Python-side) ────────────────────────────────────


def hide_by_default(event: dict[str, Any]) -> bool:
    """Return True if this event should be HIDDEN under the default
    operator filter on /dashboard/events.

    Hidden ≠ deleted. Operator can toggle "Show All" to see them.

    Hidden categories:
      - severity=info heartbeat ticks with no findings
      - autonomous_run_completed for cron syncs with metadata.changed=false
      - mission_complete events older than 1h that produced no new artifacts
      - Repeated successful cron runs (same job_id, multiple greens today)
        — Phase 7B will collapse these via grouping; for now just hide
        non-most-recent same-day greens.

    Shown by default:
      - severity ≥ warning
      - requires_action=true
      - any event that produced a new artifact / PR / email / dispatch
      - state-change events
      - heartbeat ticks that emitted findings
    """
    severity = str(event.get("severity") or "").lower()
    source = str(event.get("source_domain") or "").lower()
    kind = str(event.get("kind") or "").lower()
    requires_action = bool(event.get("requires_action"))

    # Always show: high-severity / action-required / state-change indicators
    if severity in {"warning", "error", "critical"}:
        return False
    if requires_action:
        return False

    # Check metadata for findings/artifacts/dispatches
    metadata = event.get("metadata") or {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except (TypeError, json.JSONDecodeError):
            metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}

    # Hide: heartbeat ticks with no findings
    if source == "heartbeat" and severity in {"", "info", "success"}:
        finding = metadata.get("finding") or metadata.get("findings")
        if not finding and "investigation" not in kind and "review" not in kind:
            return True

    # Hide: routine cron syncs with changed=false
    if source == "cron" and severity in {"", "info", "success"}:
        if metadata.get("changed") is False:
            return True
        if "no_change" in str(metadata.get("status", "")).lower():
            return True

    # Hide: bare autonomous_run_completed without artifact production
    if kind == "autonomous_run_completed" and severity in {"", "info", "success"}:
        artifact_count = metadata.get("artifact_count") or metadata.get("artifacts_created")
        if not artifact_count:
            return True

    # Hide: cron runs cancelled by service restart. These fire on every
    # deploy for any in-flight job and are operational noise, not real
    # incidents. Operator can toggle "Show All" if investigating restarts.
    if kind == "cron_run_cancelled":
        return True

    # Default: show
    return False


# ── Annotate events with smart_title + visibility ────────────────────────


def annotate_event(
    template_conn: sqlite3.Connection,
    event: dict[str, Any],
) -> dict[str, Any]:
    """Add `smart_title` and `hide_by_default` fields to an event.

    Pure-cache path: only reads the existing template store; if no
    template exists for this (kind, shape), uses the code-side fallback.
    LLM generation is deliberately NOT triggered inline — it would block
    the events endpoint. A separate background pre-warm pass generates
    templates lazily.
    """
    kind = str(event.get("kind") or "")
    shape_sig = metadata_shape_signature(event.get("metadata"))

    template_row = get_cached_template(template_conn, kind, shape_sig)
    if template_row and template_row.get("title_template"):
        smart_title = apply_template(template_row["title_template"], event)
        template_source = template_row.get("generated_by_model") or "code"
    else:
        smart_title = apply_template(_fallback_template(event), event)
        template_source = "fallback"

    annotated = dict(event)
    annotated["smart_title"] = smart_title
    annotated["hide_by_default"] = hide_by_default(event)
    annotated["title_template_source"] = template_source
    return annotated

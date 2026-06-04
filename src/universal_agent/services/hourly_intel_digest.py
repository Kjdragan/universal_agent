"""Hourly intel digest packaging — Simone's consolidated delivery path.

This module powers the ``hourly-intel-digest`` skill. It is pure packaging:
no editorial LLM pass, no scoring decisions, no candidate filtering beyond
what's already in the artifact row (``verdict='ship'`` written by Atlas).

The skill invokes these helpers in order:

  1. :func:`is_paused`  — abort early if operator paused the digest.
  2. :func:`is_throttled`  — abort early if a digest already went out
     this clock hour (hour-bucket comparison via SQLite ``strftime``).
  3. :func:`select_candidates_for_current_hour`  — pull qualifying
     ``proactive_artifacts`` rows.
  4. :func:`render_subject` + :func:`render_digest_html`  — produce the
     subject + ``(text, html)`` bodies.
  5. Skill then calls ``mcp__agentmail__send_message``.
  6. :func:`mark_all_delivered`  — stamp ``delivered_at`` and
     ``delivery_channel='hourly_digest'`` per artifact.

PR B was supposed to ship the ``verdict`` column, ``delivery_channel``
column, the ``digest_state`` table, and the HMAC helpers. To keep this
PR independently shippable, :func:`ensure_schema_addons` lazily adds
the columns + table if they're missing — idempotent ``ALTER TABLE`` and
``CREATE TABLE IF NOT EXISTS``.

Houston-time formatting per project memory: operator-facing timestamps
render in ``America/Chicago``.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import html as html_lib
import json
import logging
import os
import sqlite3
from typing import Any, Optional

try:  # Python 3.9+
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore

from universal_agent.services import proactive_artifacts as _pa

logger = logging.getLogger(__name__)

# ── Configuration ──────────────────────────────────────────────────────

DELIVERY_CHANNEL_HOURLY_DIGEST = "hourly_digest"

DEFAULT_RECIPIENT = "kevinjdragan@gmail.com"
ESCALATION_CC = "kevinjdragan@gmail.com"
SENDER_INBOX = "oddcity216@agentmail.to"

ARTIFACT_TYPES = ("intel_brief",)

# Visual band thresholds (composite_score). Aligned with spec §7.2.
GOLD_THRESHOLD = 0.75
SILVER_THRESHOLD = 0.55

# Inline CSS palette (Gmail-safe per HEARTBEAT.md).
COLOR_OUTER = "#ffffff"
COLOR_CARD = "#f6f8fa"
COLOR_BORDER = "#d1d9e0"
COLOR_BODY = "#1f2328"
COLOR_MUTED = "#59636e"
COLOR_ACCENT = "#0969da"


# ── Time helpers ───────────────────────────────────────────────────────


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _houston_now() -> datetime:
    if ZoneInfo is None:
        return _now_utc()
    try:
        return _now_utc().astimezone(ZoneInfo("America/Chicago"))
    except Exception:  # noqa: BLE001
        return _now_utc()


def _format_houston(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d · %H:%M CT")


def _format_houston_hhmm(dt: datetime) -> str:
    return dt.strftime("%H:%M CT")


# ── Schema bootstrap (PR-B shim) ───────────────────────────────────────


def ensure_schema_addons(conn: sqlite3.Connection) -> None:
    """Idempotently add the columns + table this PR depends on.

    PR B was supposed to ship these; the spec calls them "already
    available" but in practice they aren't yet. We add them defensively
    so PR D can ship standalone. All operations are idempotent.
    """
    _pa.ensure_schema(conn)
    # New columns on proactive_artifacts. SQLite's `IF NOT EXISTS` on
    # `ALTER TABLE ADD COLUMN` landed in 3.35; use try/except for older
    # bundles, mirroring the existing `delivered_at` migration pattern
    # in `proactive_artifacts.ensure_schema`.
    for ddl in (
        "ALTER TABLE proactive_artifacts ADD COLUMN verdict TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE proactive_artifacts ADD COLUMN verdict_reasoning TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE proactive_artifacts ADD COLUMN delivery_channel TEXT NOT NULL DEFAULT ''",
    ):
        try:
            conn.execute(ddl)
        except sqlite3.OperationalError:
            pass
    # digest_state singleton row (PR §6.4).
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS digest_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            paused_until TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT ''
        );
        INSERT OR IGNORE INTO digest_state (id) VALUES (1);
        """
    )
    conn.commit()


# ── Pause / throttle gates ─────────────────────────────────────────────


def is_paused(conn: sqlite3.Connection) -> bool:
    """True if ``digest_state.paused_until`` is in the future."""
    ensure_schema_addons(conn)
    row = conn.execute(
        "SELECT paused_until FROM digest_state WHERE id = 1"
    ).fetchone()
    if not row:
        return False
    raw = (row[0] if not isinstance(row, sqlite3.Row) else row["paused_until"]) or ""
    raw = str(raw).strip()
    if not raw:
        return False
    try:
        until = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return False
    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    return until > _now_utc()


def set_pause(conn: sqlite3.Connection, paused_until_iso: str) -> None:
    """Set the pause horizon. Empty string clears the pause."""
    ensure_schema_addons(conn)
    now_iso = _now_utc().isoformat()
    conn.execute(
        "UPDATE digest_state SET paused_until = ?, updated_at = ? WHERE id = 1",
        (str(paused_until_iso or "").strip(), now_iso),
    )
    conn.commit()


def is_throttled(conn: sqlite3.Connection) -> bool:
    """True if a digest already went out in the current clock-hour bucket.

    Hour-bucket comparison via SQLite ``strftime`` so the test is
    timezone-agnostic (delivered_at is UTC, and hour buckets are
    purely a "have we delivered THIS hour" question — semantics work
    in UTC just as well as in Houston time).
    """
    ensure_schema_addons(conn)
    row = conn.execute(
        """
        SELECT MAX(delivered_at) AS last_delivered
        FROM proactive_artifacts
        WHERE delivery_state = 'emailed'
          AND delivery_channel = ?
        """,
        (DELIVERY_CHANNEL_HOURLY_DIGEST,),
    ).fetchone()
    if not row:
        return False
    last = row[0] if not isinstance(row, sqlite3.Row) else row["last_delivered"]
    if not last:
        return False
    bucket_row = conn.execute(
        "SELECT strftime('%Y-%m-%d %H', ?) AS prev, strftime('%Y-%m-%d %H', 'now') AS curr",
        (last,),
    ).fetchone()
    if not bucket_row:
        return False
    prev = bucket_row[0]
    curr = bucket_row[1]
    return bool(prev) and prev == curr


# ── Candidate selection ────────────────────────────────────────────────


def _brief_lookback_hours() -> int:
    """How far back the digest looks for undelivered ``verdict='ship'`` briefs.

    Was an exact current-clock-hour gate, which orphaned ~40% of ship briefs:
    a brief authored at HH:MM is only eligible during clock-hour HH, so any run
    that didn't catch it in its authoring hour lost it forever. A lookback window
    surfaces any recent undelivered ship brief on the next digest run instead
    (``delivered_at IS NULL`` still guarantees each brief is emailed at most
    once). Override with ``UA_DIGEST_BRIEF_LOOKBACK_HOURS`` (default 24)."""
    raw = str(os.getenv("UA_DIGEST_BRIEF_LOOKBACK_HOURS", "24")).strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 24


def select_candidates_for_current_hour(
    conn: sqlite3.Connection,
) -> list[dict[str, Any]]:
    """Pull qualifying ``verdict='ship'`` briefs that are undelivered and were
    authored within the last ``_brief_lookback_hours`` (default 24h).

    Sort order pins ``needs_attention=true`` to the top regardless of
    composite_score; ties break by composite_score descending.
    """
    ensure_schema_addons(conn)
    placeholders = ",".join("?" for _ in ARTIFACT_TYPES)
    lookback_modifier = f"-{_brief_lookback_hours()} hours"
    rows = conn.execute(
        f"""
        SELECT artifact_id, title, summary, artifact_path, metadata_json,
               created_at
        FROM proactive_artifacts
        WHERE verdict = 'ship'
          AND (delivered_at IS NULL OR delivered_at = '')
          AND artifact_type IN ({placeholders})
          -- Compare numerically: stored created_at is ISO-8601 ('T' + offset)
          -- while datetime('now', ?) is space-separated, so a raw string ``>=``
          -- mis-orders them ('T' > ' ') and leaks >lookback briefs whenever both
          -- land on the same calendar date. julianday() parses both forms.
          AND julianday(created_at) >= julianday('now', ?)
        ORDER BY
          CASE WHEN json_extract(metadata_json, '$.needs_attention') = 1
               THEN 0 ELSE 1 END,
          CAST(json_extract(metadata_json, '$.composite_score') AS REAL) DESC
        """,
        (*ARTIFACT_TYPES, lookback_modifier),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        row_dict = dict(row) if isinstance(row, sqlite3.Row) else {
            "artifact_id": row[0],
            "title": row[1],
            "summary": row[2],
            "artifact_path": row[3],
            "metadata_json": row[4],
            "created_at": row[5],
        }
        meta_raw = row_dict.get("metadata_json") or "{}"
        try:
            meta = json.loads(meta_raw) if isinstance(meta_raw, str) else dict(meta_raw)
        except (json.JSONDecodeError, ValueError, TypeError):
            meta = {}
        out.append(
            {
                "artifact_id": str(row_dict.get("artifact_id") or "").strip(),
                "title": str(row_dict.get("title") or "").strip(),
                "summary": str(row_dict.get("summary") or "").strip(),
                "artifact_path": str(row_dict.get("artifact_path") or "").strip(),
                "metadata": meta,
                "created_at": str(row_dict.get("created_at") or "").strip(),
            }
        )
    return out


# ── Render helpers ─────────────────────────────────────────────────────


def _clean_title(title: str) -> str:
    text = (title or "").strip()
    for prefix in ("ATLAS insight brief: ", "ATLAS convergence brief: "):
        if text.startswith(prefix):
            return text[len(prefix):]
    return text


def _ribbon_for_score(score: float, needs_attention: bool) -> tuple[str, str, str]:
    """Return (label, fg, bg) for the confidence ribbon."""
    if needs_attention:
        return ("⚠ NEEDS ATTENTION", "#b91c1c", "#fee2e2")
    if score >= GOLD_THRESHOLD:
        return ("★ HIGH CONFIDENCE", "#0a7d3e", "#e9f7ee")
    if score >= SILVER_THRESHOLD:
        return ("◆ MEDIUM", "#5b21b6", "#ede9fe")
    return ("◇ STANDARD", "#374151", "#f3f4f6")


def _gateway_base_url() -> str:
    base = (
        os.getenv("UA_GATEWAY_BASE_URL")
        or os.getenv("FRONTEND_URL")
        or os.getenv("UA_PUBLIC_BASE_URL")
        or "https://app.clearspringcg.com"
    ).strip().rstrip("/")
    return base


def _inline_feedback_enabled() -> bool:
    """Whether the digest mints in-email per-brief 👍/👎 links (Phase 5).

    Default ON. Set ``UA_DIGEST_INLINE_FEEDBACK_LINKS=0`` to disable (the
    rollback lever) — briefs still ship, and the operator can rate them
    from the ``/briefs/{id}`` viewer, which mints its own fresh tokens."""
    raw = str(os.getenv("UA_DIGEST_INLINE_FEEDBACK_LINKS", "1")).strip().lower()
    return raw not in {"0", "false", "no", "off", ""}


def _attach_feedback_urls(briefs: list[dict[str, Any]], base_url: str) -> None:
    """Mint signed per-brief thumbs URLs into each brief's metadata in place.

    ``_render_card`` reads ``metadata.feedback_url_up`` /
    ``feedback_url_down`` to emit the in-email buttons, but nothing in the
    authoring pipeline populates them (Atlas writes thesis/entities, not
    feedback URLs — and the URL base + HMAC secret are send-time concerns).
    So we mint fresh HMAC-signed URLs here at send time, mirroring the
    ``/briefs/{id}`` viewer which mints fresh tokens per request.

    Best-effort: a brief with no ``artifact_id``, a disabled flag, or a
    missing signing secret simply renders without buttons (falls back to
    the "Read full brief →" link)."""
    if not _inline_feedback_enabled():
        return
    try:
        from universal_agent.services.cron_artifact_notifier import (
            sign_feedback_token,
        )
    except Exception:  # noqa: BLE001 — never block a send on import edge cases
        return
    for brief in briefs:
        artifact_id = str(brief.get("artifact_id") or "").strip()
        if not artifact_id:
            continue
        up_token = sign_feedback_token(artifact_id, "up")
        down_token = sign_feedback_token(artifact_id, "down")
        if not (up_token and down_token):
            # No secret configured → degrade gracefully (no buttons).
            continue
        meta = brief.get("metadata")
        if not isinstance(meta, dict):
            meta = {}
            brief["metadata"] = meta
        meta["feedback_url_up"] = (
            f"{base_url}/api/v1/briefs/{artifact_id}/feedback?v=up&t={up_token}"
        )
        meta["feedback_url_down"] = (
            f"{base_url}/api/v1/briefs/{artifact_id}/feedback?v=down&t={down_token}"
        )


def render_subject(briefs: list[dict[str, Any]], now_ct: Optional[datetime] = None) -> str:
    """Produce the digest subject line per spec §7.3.

    Houston time on operator-facing strings. When any brief has
    ``needs_attention=True`` the subject gets the ``NEEDS ATTENTION``
    prefix; the `(+N more)` tail is omitted for single-brief digests.
    """
    if not briefs:
        return "[Intel] (no signals)"
    when = now_ct or _houston_now()
    hhmm = _format_houston_hhmm(when)
    top = briefs[0]
    top_title = _clean_title(str(top.get("title") or ""))
    # Compact the headline to ≤55 chars for the subject; keep room for
    # the prefix + " (+N more)" tail.
    if len(top_title) > 55:
        top_title = top_title[:54].rstrip() + "…"
    any_attention = any(bool((b.get("metadata") or {}).get("needs_attention")) for b in briefs)
    if any_attention:
        return f"[Intel · NEEDS ATTENTION · {hhmm}] {top_title}"
    if len(briefs) == 1:
        return f"[Intel · {hhmm}] {top_title}"
    return f"[Intel · {hhmm}] {top_title} (+{len(briefs) - 1} more)"


def _render_card(brief: dict[str, Any], rank: int, base_url: str) -> str:
    meta = brief.get("metadata") or {}
    artifact_id = str(brief.get("artifact_id") or "")
    title = html_lib.escape(_clean_title(str(brief.get("title") or "(untitled)")))
    thesis = html_lib.escape(str(meta.get("thesis") or brief.get("summary") or "").strip())
    key_actions = meta.get("key_actions") or []
    why_matters = html_lib.escape(str(key_actions[0]).strip()) if key_actions else ""
    key_entities = meta.get("key_entities") or []
    try:
        score = float(meta.get("composite_score") or 0.0)
    except (TypeError, ValueError):
        score = 0.0
    needs_attention = bool(meta.get("needs_attention"))
    ribbon_label, ribbon_fg, ribbon_bg = _ribbon_for_score(score, needs_attention)

    feedback_url_up = html_lib.escape(str(meta.get("feedback_url_up") or "").strip())
    feedback_url_down = html_lib.escape(str(meta.get("feedback_url_down") or "").strip())
    brief_url = html_lib.escape(f"{base_url}/briefs/{artifact_id}")

    # Density taper per spec §7.2.
    heading_font_size = 18 if rank <= 1 else (16 if rank == 2 else 15)
    compact = rank >= 3

    tag_html_parts: list[str] = []
    for tag in (key_entities or [])[:6]:
        tag_text = html_lib.escape(str(tag).strip())
        if tag_text:
            tag_html_parts.append(
                f'<span style="background:#f3f4f6;padding:2px 8px;'
                f'border-radius:10px;margin-right:4px;display:inline-block;'
                f'margin-bottom:4px;">{tag_text}</span>'
            )
    tag_block = (
        f'<div style="font-size:12px;color:{COLOR_MUTED};margin-bottom:14px;">'
        f'{"".join(tag_html_parts)}</div>'
        if tag_html_parts and not compact
        else ""
    )

    why_block = (
        f'<p style="font-size:13px;color:#4b5563;margin:0 0 12px 0;'
        f'border-left:3px solid #cbd5e1;padding-left:10px;">'
        f'<strong>Why it matters:</strong> {why_matters}</p>'
        if why_matters and not compact
        else ""
    )

    thesis_block = (
        f'<p style="font-size:14px;color:#374151;margin:0 0 10px 0;">{thesis}</p>'
        if thesis
        else ""
    )

    feedback_buttons = ""
    if feedback_url_up:
        feedback_buttons += (
            f'<td style="padding-right:6px;">'
            f'<a href="{feedback_url_up}" '
            f'style="display:inline-block;padding:9px 12px;background:#dafbe1;'
            f'color:#1a7f37;text-decoration:none;font-size:13px;font-weight:600;'
            f'border-radius:6px;">👍 More</a></td>'
        )
    if feedback_url_down:
        feedback_buttons += (
            f'<td><a href="{feedback_url_down}" '
            f'style="display:inline-block;padding:9px 12px;background:#ffebe9;'
            f'color:#cf222e;text-decoration:none;font-size:13px;font-weight:600;'
            f'border-radius:6px;">👎 Less</a></td>'
        )

    return (
        f'<div style="background:{COLOR_CARD};border:1px solid {COLOR_BORDER};'
        f'border-radius:8px;padding:16px;margin-bottom:16px;">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">'
        f'<tr><td><span style="font-size:11px;font-weight:700;color:{ribbon_fg};'
        f'background:{ribbon_bg};padding:3px 8px;border-radius:10px;">{ribbon_label}</span></td>'
        f'<td align="right" style="font-size:11px;color:{COLOR_MUTED};">score {score:.2f}</td></tr>'
        f'</table>'
        f'<h3 style="font-size:{heading_font_size}px;line-height:1.3;margin:10px 0 6px 0;'
        f'color:{COLOR_BODY};">{title}</h3>'
        f'{thesis_block}'
        f'{why_block}'
        f'{tag_block}'
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>'
        f'<td style="padding-right:8px;">'
        f'<a href="{brief_url}" style="display:inline-block;padding:9px 14px;'
        f'background:{COLOR_BODY};color:#ffffff;text-decoration:none;font-size:13px;'
        f'font-weight:600;border-radius:6px;">Read full brief →</a></td>'
        f'{feedback_buttons}'
        f'</tr></table>'
        f'</div>'
    )


def render_digest_html(
    briefs: list[dict[str, Any]],
    base_url: str,
    pause_token: str,
    now_ct: Optional[datetime] = None,
) -> tuple[str, str]:
    """Return ``(text_body, html_body)`` for the digest.

    Plain-text body is a simple per-brief listing; HTML body follows
    spec §7.1 with the inline-CSS Gmail-safe palette.
    """
    when = now_ct or _houston_now()
    when_str = _format_houston(when)
    n = len(briefs)
    plural = "" if n == 1 else "s"
    headline = f"{n} signal{plural} worth a look"

    # Plain-text body.
    text_lines = [f"Hourly Intel · {when_str}", "", headline, ""]
    for idx, brief in enumerate(briefs, start=1):
        title = _clean_title(str(brief.get("title") or "(untitled)"))
        meta = brief.get("metadata") or {}
        thesis = str(meta.get("thesis") or brief.get("summary") or "").strip()
        artifact_id = str(brief.get("artifact_id") or "")
        text_lines.append(f"{idx}. {title}")
        if thesis:
            text_lines.append(f"   {thesis}")
        text_lines.append(f"   Read: {base_url}/briefs/{artifact_id}")
        text_lines.append("")
    text_body = "\n".join(text_lines).rstrip() + "\n"

    # HTML body.
    cards_html = "".join(_render_card(b, idx, base_url) for idx, b in enumerate(briefs, start=1))
    pause_url = html_lib.escape(
        f"{base_url}/api/v1/digest/pause?hours=24&t={pause_token}"
    )
    prefs_url = html_lib.escape(f"{base_url}/dashboard/proactive?tab=preferences")
    scoring_url = html_lib.escape(f"{base_url}/dashboard/proactive?tab=scoring")
    footer = (
        f'<div style="font-size:11px;color:{COLOR_MUTED};margin-top:24px;'
        f'border-top:1px solid #e6e8eb;padding-top:12px;">'
        f'Author: ATLAS · Delivered by Simone ({html_lib.escape(SENDER_INBOX)})<br>'
        f'<a href="{prefs_url}" style="color:#4b5563;">Adjust intel preferences</a> · '
        f'<a href="{pause_url}" style="color:#4b5563;">Pause digest 24h</a> · '
        f'<a href="{scoring_url}" style="color:#4b5563;">Why these?</a>'
        f'</div>'
    )

    html_body = (
        f'<div style="background:{COLOR_OUTER};padding:24px;'
        f'font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',Roboto,sans-serif;">'
        f'<div style="max-width:680px;margin:0 auto;color:{COLOR_BODY};line-height:1.6;">'
        f'<div style="font-size:12px;letter-spacing:0.08em;text-transform:uppercase;'
        f'color:#6b7280;">Hourly Intel · {html_lib.escape(when_str)}</div>'
        f'<div style="font-size:18px;font-weight:600;margin-top:4px;color:{COLOR_BODY};">'
        f'{n} signal{plural} worth a look</div>'
        f'<hr style="border:0;border-top:1px solid #e6e8eb;margin:12px 0 16px 0;">'
        f'{cards_html}'
        f'{footer}'
        f'</div></div>'
    )
    return text_body, html_body


# ── Stamping ───────────────────────────────────────────────────────────


def mark_all_delivered(conn: sqlite3.Connection, artifact_ids: list[str]) -> None:
    """Stamp ``delivered_at``, ``delivery_state='emailed'``, and
    ``delivery_channel='hourly_digest'`` on each artifact.

    The base ``mark_artifact_delivered`` helper only writes
    ``delivered_at``; we need to set the channel column too so
    :func:`is_throttled` can find this delivery on the next tick.
    """
    ensure_schema_addons(conn)
    now_iso = _now_utc().isoformat()
    for raw_id in artifact_ids:
        artifact_id = str(raw_id or "").strip()
        if not artifact_id:
            continue
        try:
            _pa.mark_artifact_delivered(conn, artifact_id=artifact_id, delivered_at=now_iso)
        except KeyError:
            logger.warning("hourly_intel_digest: artifact %s missing at stamp", artifact_id)
            continue
        conn.execute(
            """
            UPDATE proactive_artifacts
            SET delivery_state = ?,
                delivery_channel = ?,
                updated_at = ?
            WHERE artifact_id = ?
            """,
            (_pa.DELIVERY_EMAILED, DELIVERY_CHANNEL_HOURLY_DIGEST, now_iso, artifact_id),
        )
    conn.commit()


def mark_superseded(conn: sqlite3.Connection, artifact_ids: list[str]) -> None:
    """Durably suppress near-duplicate briefs collapsed by the dedup backstop.

    Stamps ``delivered_at`` so :func:`select_candidates_for_current_hour` stops
    surfacing them (otherwise a dropped near-duplicate would re-appear in the
    next hour's digest once its kept twin is delivered). Sets
    ``delivery_state='superseded'`` (NOT ``'emailed'``) so it is not counted as a
    digest delivery by :func:`is_throttled`."""
    ensure_schema_addons(conn)
    now_iso = _now_utc().isoformat()
    for raw_id in artifact_ids:
        artifact_id = str(raw_id or "").strip()
        if not artifact_id:
            continue
        try:
            _pa.mark_artifact_delivered(conn, artifact_id=artifact_id, delivered_at=now_iso)
        except KeyError:
            logger.warning("hourly_intel_digest: superseded artifact %s missing at stamp", artifact_id)
            continue
        conn.execute(
            """
            UPDATE proactive_artifacts
            SET delivery_state = 'superseded',
                updated_at = ?
            WHERE artifact_id = ?
            """,
            (now_iso, artifact_id),
        )
    conn.commit()


# ── Pause-token signing (PR-B shim) ────────────────────────────────────


def _digest_pause_secret() -> bytes:
    raw = (
        os.getenv("UA_FEEDBACK_HMAC_SECRET")
        or os.getenv("UA_ARTIFACT_ACK_SECRET")
        or os.getenv("UA_OPS_TOKEN")
        or os.getenv("UA_INTERNAL_API_TOKEN")
        or ""
    ).strip()
    return raw.encode("utf-8") if raw else b""


def sign_digest_pause_token(hours: int) -> str:
    """HMAC over ``f"digest_pause:{hours}"`` truncated to 16 hex."""
    secret = _digest_pause_secret()
    if not secret:
        return ""
    msg = f"digest_pause:{int(hours)}".encode("utf-8")
    return hmac.new(secret, msg, hashlib.sha256).hexdigest()[:16]


def verify_digest_pause_token(hours: int, token: str) -> bool:
    """Verify an HMAC token authorising a digest pause of *hours* hours."""
    expected = sign_digest_pause_token(hours)
    if not expected or not token:
        return False
    return hmac.compare_digest(expected, token.strip())


# ── Near-duplicate suppression (Phase 4 — digest deterministic backstop) ──


def _dedup_jaccard_threshold() -> float:
    """Token-overlap threshold above which two briefs are near-duplicates.

    Decision D: Atlas's recent-briefs index is the primary dedup (it skips a
    near-identical second cluster at authoring time); this is the deterministic
    digest-side backstop for any that slip through into the same email. The
    default (0.6) is intentionally conservative — a false collapse hides a
    distinct brief, which is worse than letting a near-duplicate through (the
    operator just sees two similar items). Set ``UA_DIGEST_DEDUP_JACCARD`` to
    ``1.0`` to disable the backstop entirely."""
    raw = str(os.getenv("UA_DIGEST_DEDUP_JACCARD", "0.6")).strip()
    try:
        return max(0.0, min(1.0, float(raw)))
    except ValueError:
        return 0.6


def _brief_dedup_tokens(brief: dict[str, Any]) -> frozenset[str]:
    """Normalized token set over a brief's title + thesis + key entities."""
    meta = brief.get("metadata") or {}
    entities = meta.get("key_entities") or []
    parts = [
        str(brief.get("title") or ""),
        str(meta.get("thesis") or ""),
        " ".join(str(e) for e in entities),
    ]
    text = "".join(c if c.isalnum() else " " for c in " ".join(parts).lower())
    return frozenset(t for t in text.split() if len(t) > 2)


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def dedup_near_duplicate_briefs(
    briefs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Collapse near-duplicate briefs, keeping the first of each group.

    Input is pre-sorted best-first (needs_attention pinned, then composite_score
    desc), so the higher-priority brief in a near-duplicate group is the one
    kept. Fail-open: always returns >= 1 brief when given >= 1."""
    threshold = _dedup_jaccard_threshold()
    if threshold >= 1.0 or len(briefs) <= 1:
        return briefs
    kept: list[dict[str, Any]] = []
    kept_tokens: list[frozenset[str]] = []
    for brief in briefs:
        toks = _brief_dedup_tokens(brief)
        if toks and any(_jaccard(toks, kt) >= threshold for kt in kept_tokens):
            logger.info(
                "digest dedup: collapsing near-duplicate brief %s",
                brief.get("artifact_id"),
            )
            continue
        kept.append(brief)
        kept_tokens.append(toks)
    return kept


# ── High-level summary helper for skill ────────────────────────────────


def compose_send_payload(
    conn: sqlite3.Connection,
    *,
    recipient: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Compose the full send payload or return ``None`` if no send.

    Returns a dict containing ``subject``, ``text``, ``html``,
    ``recipient``, ``cc``, ``artifact_ids``, ``status``. The skill
    treats ``status`` values ``paused``/``throttled``/``no_candidates``
    as "exit silent". Only ``ready`` triggers a real send.
    """
    if is_paused(conn):
        return {"status": "paused"}
    if is_throttled(conn):
        return {"status": "throttled"}
    selected = select_candidates_for_current_hour(conn)
    briefs = dedup_near_duplicate_briefs(selected)
    kept_ids = {b["artifact_id"] for b in briefs}
    superseded = [b["artifact_id"] for b in selected if b["artifact_id"] not in kept_ids]
    if superseded:
        mark_superseded(conn, superseded)
    if not briefs:
        return {"status": "no_candidates"}

    base_url = _gateway_base_url()
    # Mint signed in-email 👍/👎 links per ship brief (Phase 5). Done here at
    # send time — the authoring pipeline never populates these (the URL base +
    # HMAC secret are send-time concerns), which is why the loop was unexercised.
    _attach_feedback_urls(briefs, base_url)
    pause_token = sign_digest_pause_token(24)
    now_ct = _houston_now()
    subject = render_subject(briefs, now_ct=now_ct)
    text_body, html_body = render_digest_html(briefs, base_url, pause_token, now_ct=now_ct)

    recipient_addr = (
        recipient
        or os.getenv("UA_INTEL_DIGEST_RECIPIENT")
        or DEFAULT_RECIPIENT
    ).strip()

    any_attention = any(
        bool((b.get("metadata") or {}).get("needs_attention")) for b in briefs
    )
    cc = [ESCALATION_CC] if any_attention and ESCALATION_CC != recipient_addr else []

    return {
        "status": "ready",
        "subject": subject,
        "text": text_body,
        "html": html_body,
        "recipient": recipient_addr,
        "cc": cc,
        "inbox_id": SENDER_INBOX,
        "artifact_ids": [b["artifact_id"] for b in briefs],
        "needs_attention": any_attention,
        "brief_count": len(briefs),
    }


__all__ = [
    "DELIVERY_CHANNEL_HOURLY_DIGEST",
    "DEFAULT_RECIPIENT",
    "SENDER_INBOX",
    "ensure_schema_addons",
    "is_paused",
    "set_pause",
    "is_throttled",
    "select_candidates_for_current_hour",
    "dedup_near_duplicate_briefs",
    "mark_superseded",
    "render_subject",
    "render_digest_html",
    "mark_all_delivered",
    "sign_digest_pause_token",
    "verify_digest_pause_token",
    "compose_send_payload",
]

"""Bounded tag vocabulary for outbound UA email subjects + body banners.

This module exposes a closed 2-dimension tag scheme so the operator can
eyeball-triage their inbox without opening individual messages:

    [<ACTION>/<KIND>] <existing subject>

`ActionTag` captures *what response, if any, is required* (FYI vs. ACTION
vs. DECISION vs. QUESTION). `KindTag` captures *what kind of message it is*
(digest, tutorial, proactive proposal, incident, cron, system, deploy).

The enum is intentionally closed:

- 4 ActionTag values × 7 KindTag values = 28 combos total.
- Callers must use the enum members; typos fail at import time.
- If a brand-new source comes online, it either picks the closest existing
  KIND or **one** entry is appended to `KindTag` — never improvise free-form
  strings, that defeats the whole point of the scheme.

Public API:
    ActionTag                 — closed Enum (4 values)
    KindTag                   — closed Enum (7 values)
    format_tagged_subject     — idempotent subject prefixer
    format_body_header        — (html, text) banner pair
    SUBJECT_TAG_RE            — regex matching an already-tagged subject

Houston time is used in any timestamp rendered in the banner so the operator
sees their local clock without a mental conversion.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
import re
from typing import Iterable, Optional

try:  # Python 3.9+ has zoneinfo in stdlib
    from zoneinfo import ZoneInfo  # type: ignore
except ImportError:  # pragma: no cover — repo requires modern Python
    ZoneInfo = None  # type: ignore


__all__ = [
    "ActionTag",
    "KindTag",
    "format_tagged_subject",
    "format_body_header",
    "SUBJECT_TAG_RE",
]


# ---------------------------------------------------------------------------
# Closed enums
# ---------------------------------------------------------------------------


class ActionTag(str, Enum):
    """What response, if any, the operator needs to take.

    Values are intentionally short so the inbox preview keeps room for the
    underlying subject. The class inherits from ``str`` so members serialize
    cleanly via ``str(ActionTag.FYI)`` and compare equal to their bare value
    in tests.
    """

    FYI = "FYI"
    ACTION = "ACTION"
    DECISION = "DECISION"
    QUESTION = "QUESTION"


class KindTag(str, Enum):
    """What kind of content the email contains."""

    DIGEST = "DIGEST"
    TUTORIAL = "TUTORIAL"
    PROACTIVE = "PROACTIVE"
    INCIDENT = "INCIDENT"
    CRON = "CRON"
    SYSTEM = "SYSTEM"
    DEPLOY = "DEPLOY"


# Pre-compiled regex matching `[ACTION/KIND]` at the start of a subject.
# Tolerates any of the enum values; if you add a new member, the regex
# automatically picks it up because it's built from the enum at import time.
_ACTION_ALTERNATION = "|".join(a.value for a in ActionTag)
_KIND_ALTERNATION = "|".join(k.value for k in KindTag)
SUBJECT_TAG_RE = re.compile(
    rf"^\[\s*({_ACTION_ALTERNATION})\s*/\s*({_KIND_ALTERNATION})\s*\]\s*",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Subject prefix
# ---------------------------------------------------------------------------


def _coerce_action(action: ActionTag | str) -> ActionTag:
    """Normalize an ``ActionTag`` or its string value; raise on bad input."""
    if isinstance(action, ActionTag):
        return action
    if isinstance(action, str):
        try:
            return ActionTag(action.upper().strip())
        except ValueError as exc:
            raise ValueError(
                f"unknown ActionTag: {action!r}; valid: {[a.value for a in ActionTag]}"
            ) from exc
    raise TypeError(f"action must be ActionTag or str, got {type(action).__name__}")


def _coerce_kind(kind: KindTag | str) -> KindTag:
    """Normalize a ``KindTag`` or its string value; raise on bad input."""
    if isinstance(kind, KindTag):
        return kind
    if isinstance(kind, str):
        try:
            return KindTag(kind.upper().strip())
        except ValueError as exc:
            raise ValueError(
                f"unknown KindTag: {kind!r}; valid: {[k.value for k in KindTag]}"
            ) from exc
    raise TypeError(f"kind must be KindTag or str, got {type(kind).__name__}")


def format_tagged_subject(
    action: ActionTag | str,
    kind: KindTag | str,
    subject: str,
) -> str:
    """Prepend ``[ACTION/KIND]`` to *subject*; idempotent.

    Re-tagging an already-tagged subject is a no-op — the original tag is
    preserved, even if it differs from what the caller passed. This protects
    against accidental double-tagging when subjects flow through multiple
    layers (e.g., reply chains, re-sends, forwarded digests).

    Args:
        action: ActionTag enum member or matching string value.
        kind: KindTag enum member or matching string value.
        subject: The pre-existing subject line. ``None`` is normalized to
            an empty string before prefixing.

    Returns:
        ``"[ACTION/KIND] subject"`` — or the original subject untouched
        if it already begins with a valid tag.

    Raises:
        ValueError: action or kind is not a recognized enum value.
    """
    action_enum = _coerce_action(action)
    kind_enum = _coerce_kind(kind)
    raw = "" if subject is None else str(subject)

    if SUBJECT_TAG_RE.match(raw):
        # Already tagged — leave it alone. The first tagger wins.
        return raw

    return f"[{action_enum.value}/{kind_enum.value}] {raw}".rstrip()


# ---------------------------------------------------------------------------
# Body banner
# ---------------------------------------------------------------------------


def _houston_now_iso() -> str:
    """Return the current time as an operator-friendly Houston-local string.

    Format: ``5:05 PM Wed May 20 CDT`` (or ``CST`` outside DST).  This
    matches the operator memory entry that mandates Houston-time display
    rather than raw UTC or ISO offset format.  The helper retains its
    historical name (``..._iso``) for backwards compatibility — the
    suffix is misleading, but renaming would touch unrelated callers.
    """
    if ZoneInfo is None:  # pragma: no cover
        return datetime.now().strftime("%-I:%M %p %a %b %-d")
    local = datetime.now(ZoneInfo("America/Chicago"))
    # %-I drops the leading zero on the hour (POSIX).  Some non-POSIX
    # platforms reject the GNU extensions; fall back to manual stripping.
    try:
        return local.strftime("%-I:%M %p %a %b %-d %Z")
    except ValueError:  # pragma: no cover
        return local.strftime("%I:%M %p %a %b %d %Z").lstrip("0")


def _related_to_str(related: Optional[Iterable[str]]) -> str:
    """Render a Related: line value from a list or string."""
    if related is None:
        return ""
    if isinstance(related, str):
        return related.strip()
    items = [str(r).strip() for r in related if str(r).strip()]
    return ", ".join(items)


def format_body_header(
    action: ActionTag | str,
    kind: KindTag | str,
    source: str,
    related: Optional[Iterable[str] | str] = None,
    *,
    include_timestamp: bool = True,
) -> tuple[str, str]:
    """Build the (html, text) banner pair injected at the top of the body.

    Layout (plaintext)::

        Tags: ACTION/KIND
        Source: <source>
        Related: <related-1>, <related-2>      (omitted if no related items)
        Time: 3:42 PM Mon May 19 CDT             (omitted if include_timestamp=False)
        ---

    Args:
        action: ActionTag enum or string value.
        kind: KindTag enum or string value.
        source: Free-form short string identifying the producer
            (e.g. ``"youtube_daily_digest cron"``,
            ``"ci-failure-watcher (cron / GitHub Actions)"``).
        related: Optional list of related references (PR numbers, ticket
            IDs, file paths). May also be a single pre-joined string.
        include_timestamp: Include the Houston-time stamp line. Tests can
            disable this to get a deterministic banner.

    Returns:
        A ``(html, text)`` tuple. Either can be concatenated with an
        existing body — neither contains a trailing blank line beyond the
        ``---`` separator.

    Raises:
        ValueError: action or kind is not a recognized enum value.
    """
    action_enum = _coerce_action(action)
    kind_enum = _coerce_kind(kind)
    src = (source or "").strip()
    rel = _related_to_str(related)
    ts = _houston_now_iso() if include_timestamp else ""

    # ---- plaintext ----
    lines = [f"Tags: {action_enum.value}/{kind_enum.value}"]
    if src:
        lines.append(f"Source: {src}")
    if rel:
        lines.append(f"Related: {rel}")
    if ts:
        lines.append(f"Time: {ts}")
    lines.append("---")
    text_banner = "\n".join(lines) + "\n"

    # ---- html ----
    # Lightweight monospace block — no external CSS dependency, no scripts.
    # Style mirrors the GitHub PR-checklist look so it doesn't clash with
    # rich markdown bodies underneath.
    html_lines = [
        '<div style="font-family: -apple-system, BlinkMacSystemFont, '
        '\'Segoe UI\', sans-serif; font-size: 13px; color: #444; '
        "background: #f6f8fa; border-left: 4px solid #0969da; "
        'padding: 10px 14px; margin: 0 0 16px 0; border-radius: 4px;">',
        f'  <div><strong>Tags:</strong> {action_enum.value}/{kind_enum.value}</div>',
    ]
    if src:
        html_lines.append(f'  <div><strong>Source:</strong> {_html_escape(src)}</div>')
    if rel:
        html_lines.append(f'  <div><strong>Related:</strong> {_html_escape(rel)}</div>')
    if ts:
        html_lines.append(f'  <div><strong>Time:</strong> {_html_escape(ts)}</div>')
    html_lines.append("</div>")
    html_banner = "\n".join(html_lines) + "\n"

    return html_banner, text_banner


def _html_escape(value: str) -> str:
    """Minimal HTML escape — avoids importing the whole html module."""
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )

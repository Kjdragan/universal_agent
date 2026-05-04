"""Pre-triage deterministic email security screening.

Pure-Python module — **no LLM calls**.  Every function here runs in
microseconds and produces a deterministic result.  This is the first
line of defense: it fires *before* any email content reaches the triage
LLM, closing the gap where the auto-quarantine guard previously
depended on successful triage completion.

Capabilities:
  1. Injection pattern scanner  — regex-based detection of CLI commands,
     prompt injection phrases, YAML frontmatter injection, and other
     attack vectors commonly found in agent-targeted phishing.
  2. AgentMail sender quarantine — unknown @agentmail.to senders are
     auto-quarantined as high-risk agent-to-agent traffic.
  3. Sender reputation tracker  — SQLite-backed blocklist with auto-
     escalation (2+ quarantines → auto-blocked).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
import re
import sqlite3
from typing import Any, Sequence

logger = logging.getLogger(__name__)

# ── Injection Pattern Scanner ───────────────────────────────────────────

# Each tuple is (compiled_regex, threat_type).
# Patterns are intentionally broad — false positives are acceptable
# because the consequence is quarantine-with-notification, not deletion.

_RAW_PATTERNS: list[tuple[str, str]] = [
    # Remote code / package fetch
    (r"curl\s+https?://", "remote_code_fetch"),
    (r"wget\s+https?://", "remote_code_fetch"),
    (r"git\s+clone\s+https?://", "remote_code_fetch"),
    # Package installation commands
    (r"npx\s+", "package_execution"),
    (r"npm\s+install", "npm_install"),
    (r"pip\s+install", "pip_install"),
    (r"uv\s+add\s+", "uv_install"),
    (r"apt(?:-get)?\s+install", "apt_install"),
    (r"brew\s+install", "brew_install"),
    # Skill / MCP / tool injection
    (r"skill_url\s*:", "skill_injection"),
    (r"mcp\s*:\s*https?://", "mcp_endpoint_injection"),
    (r"install\s*:\s*\n\s*-\s+", "structured_install_block"),
    # Prompt injection phrases
    (r"ignore\s+(?:all\s+)?previous\s+instructions", "prompt_injection"),
    (r"system\s*prompt\s*:", "prompt_injection"),
    (r"you\s+are\s+now\b", "role_hijack"),
    (r"act\s+as\s+(?:a|an)\b", "role_hijack"),
    (r"as\s+an?\s+ai\s+assistant", "role_hijack"),
    (r"pretend\s+(?:you(?:'re|\s+are)\s+)", "role_hijack"),
    # Code execution
    (r"eval\s*\(", "code_execution"),
    (r"\$\([^)]+\)", "shell_injection"),
    (r"`[^`]*`", "backtick_execution"),
    # YAML frontmatter injection (structured metadata designed to be parsed)
    (r"---\s*\n\s*name\s*:", "yaml_frontmatter_injection"),
]

_COMPILED_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(pattern, re.IGNORECASE | re.MULTILINE), threat)
    for pattern, threat in _RAW_PATTERNS
]

# Patterns that alone are HIGH confidence (no second match needed)
_HIGH_CONFIDENCE_THREATS = frozenset({
    "prompt_injection",
    "role_hijack",
    "skill_injection",
    "mcp_endpoint_injection",
    "yaml_frontmatter_injection",
    "structured_install_block",
})


@dataclass
class ScanResult:
    """Result of a deterministic injection scan."""

    is_suspicious: bool = False
    threats: list[str] = field(default_factory=list)
    confidence: str = "none"  # "none", "medium", "high"
    matched_patterns: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_suspicious": self.is_suspicious,
            "threats": self.threats,
            "confidence": self.confidence,
            "matched_patterns": self.matched_patterns,
        }


def scan_for_injection(subject: str, body: str) -> ScanResult:
    """Scan email subject and body for injection patterns.

    Returns a :class:`ScanResult` indicating whether the content
    contains suspicious patterns.  This is a **deterministic** check —
    it does not call any LLM.
    """
    text = f"{subject or ''}\n{body or ''}"
    if not text.strip():
        return ScanResult()

    threats: list[str] = []
    matched: list[str] = []

    for pattern, threat_type in _COMPILED_PATTERNS:
        if pattern.search(text):
            if threat_type not in threats:
                threats.append(threat_type)
            # Record a human-readable label of what matched
            label = f"{threat_type}:{pattern.pattern[:40]}"
            if label not in matched:
                matched.append(label)

    if not threats:
        return ScanResult()

    # Confidence: HIGH if any single high-confidence threat, or 3+ total
    has_high = any(t in _HIGH_CONFIDENCE_THREATS for t in threats)
    confidence = "high" if has_high or len(threats) >= 3 else "medium"

    return ScanResult(
        is_suspicious=True,
        threats=threats,
        confidence=confidence,
        matched_patterns=matched,
    )


# ── AgentMail Sender Quarantine ─────────────────────────────────────────


def should_auto_quarantine_agentmail_sender(
    sender_email: str,
    trusted_senders: Sequence[str],
) -> bool:
    """Return True if this is an unknown @agentmail.to sender.

    Agent-to-agent email from external agents we don't control is a
    higher-risk vector (potential prompt injection from another AI).
    """
    email_lower = (sender_email or "").strip().lower()
    if not email_lower.endswith("@agentmail.to"):
        return False
    trusted_lower = {addr.lower() for addr in trusted_senders}
    return email_lower not in trusted_lower


# ── Sender Reputation Tracker ───────────────────────────────────────────

_REPUTATION_SCHEMA = """\
CREATE TABLE IF NOT EXISTS email_sender_reputation (
    sender_email TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'unknown',
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    total_emails INTEGER NOT NULL DEFAULT 0,
    quarantine_count INTEGER NOT NULL DEFAULT 0,
    threat_types_json TEXT NOT NULL DEFAULT '[]',
    operator_note TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);
"""

# After this many quarantines, auto-block the sender
_AUTO_BLOCK_THRESHOLD = 2


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ensure_reputation_schema(conn: sqlite3.Connection) -> None:
    """Idempotently create the email_sender_reputation table."""
    try:
        conn.executescript(_REPUTATION_SCHEMA)
    except Exception as exc:
        logger.debug("Reputation schema creation (non-fatal): %s", exc)


def record_sender_seen(
    conn: sqlite3.Connection,
    sender_email: str,
) -> None:
    """Record that we received an email from this sender."""
    email = (sender_email or "").strip().lower()
    if not email:
        return
    now = _now_iso()
    try:
        ensure_reputation_schema(conn)
        existing = conn.execute(
            "SELECT sender_email FROM email_sender_reputation WHERE sender_email = ?",
            (email,),
        ).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE email_sender_reputation
                SET last_seen_at = ?, total_emails = total_emails + 1, updated_at = ?
                WHERE sender_email = ?
                """,
                (now, now, email),
            )
        else:
            conn.execute(
                """
                INSERT INTO email_sender_reputation
                    (sender_email, status, first_seen_at, last_seen_at,
                     total_emails, quarantine_count, threat_types_json,
                     operator_note, updated_at)
                VALUES (?, 'unknown', ?, ?, 1, 0, '[]', '', ?)
                """,
                (email, now, now, now),
            )
        conn.commit()
    except Exception as exc:
        logger.debug("record_sender_seen failed (non-fatal): %s", exc)


def record_sender_quarantined(
    conn: sqlite3.Connection,
    sender_email: str,
    threats: list[str] | None = None,
) -> None:
    """Record a quarantine event for this sender, with auto-escalation."""
    email = (sender_email or "").strip().lower()
    if not email:
        return
    now = _now_iso()
    try:
        ensure_reputation_schema(conn)

        # Ensure the sender row exists
        record_sender_seen(conn, email)

        # Fetch current state
        row = conn.execute(
            "SELECT quarantine_count, threat_types_json FROM email_sender_reputation WHERE sender_email = ?",
            (email,),
        ).fetchone()
        if not row:
            return

        qcount = int(row[0] or 0) + 1
        existing_threats: list[str] = []
        try:
            existing_threats = json.loads(str(row[1] or "[]"))
        except (json.JSONDecodeError, TypeError):
            pass
        merged_threats = list(set(existing_threats + (threats or [])))

        # Auto-escalation: block after threshold
        new_status = "blocked" if qcount >= _AUTO_BLOCK_THRESHOLD else "watched"

        conn.execute(
            """
            UPDATE email_sender_reputation
            SET quarantine_count = ?,
                threat_types_json = ?,
                status = ?,
                updated_at = ?
            WHERE sender_email = ?
            """,
            (qcount, json.dumps(merged_threats), new_status, now, email),
        )
        conn.commit()

        if new_status == "blocked":
            logger.warning(
                "📧🔒 Sender auto-blocked after %d quarantines: %s threats=%s",
                qcount, email, merged_threats,
            )
        else:
            logger.info(
                "📧👁️ Sender quarantine recorded: %s count=%d threats=%s",
                email, qcount, merged_threats,
            )
    except Exception as exc:
        logger.debug("record_sender_quarantined failed (non-fatal): %s", exc)


def is_sender_blocked(
    conn: sqlite3.Connection,
    sender_email: str,
) -> bool:
    """Check if a sender is blocked in the reputation table."""
    email = (sender_email or "").strip().lower()
    if not email:
        return False
    try:
        ensure_reputation_schema(conn)
        row = conn.execute(
            "SELECT status FROM email_sender_reputation WHERE sender_email = ?",
            (email,),
        ).fetchone()
        return bool(row and str(row[0] or "").strip().lower() == "blocked")
    except Exception:
        return False


def get_sender_reputation(
    conn: sqlite3.Connection,
    sender_email: str,
) -> dict[str, Any]:
    """Return the full reputation record for a sender, or empty dict."""
    email = (sender_email or "").strip().lower()
    if not email:
        return {}
    try:
        ensure_reputation_schema(conn)
        row = conn.execute(
            "SELECT * FROM email_sender_reputation WHERE sender_email = ?",
            (email,),
        ).fetchone()
        if not row:
            return {}
        return {
            "sender_email": str(row[0] or ""),
            "status": str(row[1] or "unknown"),
            "first_seen_at": str(row[2] or ""),
            "last_seen_at": str(row[3] or ""),
            "total_emails": int(row[4] or 0),
            "quarantine_count": int(row[5] or 0),
            "threat_types": json.loads(str(row[6] or "[]")),
            "operator_note": str(row[7] or ""),
            "updated_at": str(row[8] or ""),
        }
    except Exception:
        return {}

"""Publish HTML to the tailnet HTML scratchpad from deterministic Python pipelines.

Background: the operator (Kevin) runs Claude Code terminal-only and reads mail in
clients that strip hyperlinks and flatten HTML/PDF attachments. That kills the two
things that make a report useful — real rendering (styling, diagrams) and an in-page
table of contents whose entries actually *jump* to a section. The tailnet HTML
scratchpad fixes both: it serves an HTML file at a URL reachable ONLY from the
operator's own tailnet devices, so a published report renders perfectly and all
anchors are live.

``scripts/publish_scratch.sh`` is the single source of truth for the mechanism (it
auto-detects VPS-vs-remote and prints the URL). An LLM-driven agent reaches for it via
the ``publish-to-scratchpad`` skill, but a cron script or service has no LLM in the loop
and can't invoke a skill — so this thin helper wraps the SAME script, giving Python
callers one mechanism with no logic duplication or drift.

This is for OPERATOR-FACING artifacts only. The scratchpad is tailnet-only; the link is
dead off-tailnet, so never use it for mail to external recipients.
"""

from __future__ import annotations

import logging
from pathlib import Path
import re
import secrets
import subprocess
import tempfile

from universal_agent.artifacts import repo_root

logger = logging.getLogger(__name__)

_SLUG_SANITIZE_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _publish_script() -> Path:
    """Absolute path to the canonical publish script for this checkout."""
    return repo_root() / "scripts" / "publish_scratch.sh"


def _sanitize_slug(slug: str) -> str:
    """Coerce an arbitrary string into the ``[A-Za-z0-9._-]+`` slug the script accepts."""
    cleaned = _SLUG_SANITIZE_RE.sub("-", slug).strip("-._")
    return cleaned or "report"


def publish_html_to_scratch(
    html: str,
    *,
    slug: str | None = None,
    filename: str = "report.html",
    timeout: float = 90.0,
) -> str | None:
    """Publish an HTML string to the tailnet scratchpad; return its URL or ``None``.

    Args:
        html: The rendered HTML document, as a string.
        slug: Optional human-readable prefix for the URL subdir. A short random suffix
            is always appended so repeated runs never collide and the URL stays
            unguessable. When ``None``, a fully random slug is used.
        filename: The file name within the slug dir; becomes the last URL segment.
        timeout: Seconds to wait for the publish (covers the ``ssh``/``scp`` path when
            run off the VPS).

    Returns:
        The published ``https://`` URL on success, or ``None`` on any failure (missing
        script, non-zero exit, timeout, unexpected output).

    This never raises for an ordinary publish error — callers should treat ``None`` as
    "fall back to attaching the artifact". A raw-but-delivered report beats a dropped
    one.
    """
    script = _publish_script()
    if not script.exists():
        logger.warning("scratch publish skipped: %s not found", script)
        return None

    # A random suffix keeps slugs collision-free across runs and unguessable, while a
    # caller-supplied prefix stays human-readable in the URL.
    suffix = secrets.token_hex(3)
    final_slug = f"{_sanitize_slug(slug)}-{suffix}" if slug else suffix

    safe_name = Path(filename).name or "report.html"
    tmp_dir = Path(tempfile.mkdtemp(prefix="ua_scratch_"))
    tmp_file = tmp_dir / safe_name
    try:
        tmp_file.write_text(html, encoding="utf-8")
        proc = subprocess.run(
            [str(script), str(tmp_file), final_slug],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        logger.warning("scratch publish timed out after %ss", timeout)
        return None
    except Exception:  # noqa: BLE001 — publishing is best-effort; never break the caller
        logger.warning("scratch publish raised", exc_info=True)
        return None
    finally:
        # Best-effort temp cleanup; the published copy lives on the VPS, not here.
        try:
            tmp_file.unlink(missing_ok=True)
            tmp_dir.rmdir()
        except OSError:
            pass

    if proc.returncode != 0:
        logger.warning(
            "scratch publish failed (rc=%s): %s",
            proc.returncode,
            (proc.stderr or "").strip()[-500:],
        )
        return None

    url = (proc.stdout or "").strip()
    if not url.startswith("https://"):
        logger.warning("scratch publish produced unexpected output: %r", url[:200])
        return None
    return url
